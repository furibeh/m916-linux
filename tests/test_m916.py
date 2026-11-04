# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests against a simulated mouse backed by a real flash dump.

Run: python3 -m unittest discover -s tests
"""
import argparse
import contextlib
import io
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LANGUAGE"] = "en"  # tests compare the English source strings
from m916 import cli, config as cfg  # noqa: E402
from m916.fake import FakeMouse  # noqa: E402
from m916.protocol import FLASH_SIZE, MouseAsleep, ProtocolError, on_slot, open_mouse  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "m916_flash.bin")


class M916Test(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, "rb") as f:
            self.original = f.read()
        self.fake = FakeMouse(self.original)
        self.mouse = self.fake.mouse()

    def tearDown(self):
        self.fake.close()

    def run_cmd(self, func, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            func(self.mouse, argparse.Namespace(**kwargs))
        return out.getvalue()

    def changed(self):
        return [i for i in range(FLASH_SIZE) if self.fake.flash[i] != self.original[i]]

    def test_dpi_codec(self):
        for dpi, raw in [(50, (0x00, 0x00)), (1000, (0x13, 0x00)), (26000, (0x07, 0x88))]:
            self.assertEqual(cfg.dpi_encode(dpi), raw)
            self.assertEqual(cfg.dpi_decode(*raw), dpi)

    def test_packet_checksum(self):
        # SetPCDriverStatus(1) as built by hidusb.dll
        _, resp = self.mouse.transact(0x02, data=b"\x01")
        self.assertEqual(resp.hex(" "), "08 02 00 00 00 01 01 00 00 00 00 00 00 00 00 00 49")

    def test_config_roundtrip(self):
        image = self.original[:cfg.CONFIG_SIZE]
        conf = cfg.Config.decode(image)
        self.assertEqual(conf.encode(image), image)
        self.assertEqual(conf.validate(), [])
        self.assertEqual(cfg.bad_checks(image), [])
        self.assertEqual([lv.x for lv in conf.levels[:5]], [1000, 2000, 4000, 8000, 26000])

    def test_validate(self):
        conf = cfg.Config.decode(self.original)
        conf.levels[0].x, conf.levels[0].y = 12800, 12850  # different DPIex bands
        conf.dpi_active = 7
        self.assertEqual(len(conf.validate()), 2)

    def test_changed_fields_order(self):
        image = self.original[:cfg.CONFIG_SIZE]
        conf = cfg.Config.decode(image)
        conf.dpi_count, conf.dpi_active = 7, 6  # grow and move to a new level
        fields = cfg.changed_fields(image, conf.encode(image))
        self.assertEqual(fields, [(cfg.OFF_DPI_COUNT, 2), (cfg.OFF_DPI_ACTIVE, 2)])
        conf.dpi_count, conf.dpi_active = 2, 0  # shrink below the active level
        fields = cfg.changed_fields(image, conf.encode(image))
        self.assertEqual(fields, [(cfg.OFF_DPI_ACTIVE, 2), (cfg.OFF_DPI_COUNT, 2)])

    def test_info_decodes_fixture(self):
        out = self.run_cmd(cli.cmd_info)
        self.assertIn("lod                    1 (1 mm)", out)
        self.assertIn("dpi-led-mode           2 (breathing)", out)
        self.assertIn("dpi-led-brightness     128 (level 5)", out)
        self.assertIn("led-off-time           6 (1 min)", out)
        self.assertIn("sensor-mode            0 (LP)", out)
        self.assertIn("report rate  : 1000 Hz", out)
        self.assertIn("* 2:  2000 DPI  colour #0000ff", out)
        self.assertIn("5: 26000 DPI", out)
        self.assertIn("12: fire (10 ms down, 10 ms up, 3 shots)", out)
        self.assertIn("slot      : 1 of 4", out)
        self.assertNotIn("warning", out)

    def test_writes_touch_only_expected_bytes(self):
        self.run_cmd(cli.cmd_dpi, level=3, dpi=3200, y=None)
        self.run_cmd(cli.cmd_dpi_color, level=3, color="00ff00")
        self.run_cmd(cli.cmd_rate, hz=500)
        self.run_cmd(cli.cmd_button, button=7, action="middle")
        self.run_cmd(cli.cmd_option, name="debounce", value=4)
        self.assertEqual(self.changed(), [0x00, 0x01, 0x14, 0x15, 0x17, 0x35, 0x37,
                                          0x78, 0x79, 0x7B, 0xA9, 0xAA])

    def test_shortcut_and_macro_codec(self):
        image = self.original
        # Ctrl+C as written by the Windows app in block 4, and its "jitter" macro
        self.assertEqual(cfg.encode_shortcut(cfg.parse_combo("ctrl+c")),
                         image[0x180:0x18E])
        macro = cfg.Macro.decode(image[cfg.macro_addr(4):cfg.macro_addr(5)])
        self.assertEqual(macro.name, "jitter")
        self.assertEqual(cfg.format_steps(macro.steps[:2]), "+mouse-left 10 -mouse-left 10")
        encoded = macro.encode()
        self.assertEqual(encoded, image[cfg.macro_addr(4):cfg.macro_addr(4) + len(encoded)])
        self.assertEqual(cfg.parse_steps("+ctrl 5 5 -ctrl"),
                         [cfg.Event(True, 0, 1, 10), cfg.Event(False, 0, 1)])
        self.assertEqual(cfg.format_combo(cfg.parse_combo("shift+alt+f4")), "shift+alt+f4")
        with self.assertRaises(ValueError):
            cfg.parse_combo("a+b+c+d")

    def test_steps_roundtrip(self):
        text = "+ctrl 10 +c 10 -c 10 -ctrl +move(5,-3) 20 -move(0,0) +key:0x99 -media:0x0123"
        steps = cfg.parse_steps(text.replace("move(5,-3)", "move(5, -3)"))
        self.assertEqual(steps[4], cfg.Event(True, 5, 0xFD05, 20))
        self.assertEqual(cfg.format_steps(steps), text.replace("0x0123", "0x123"))
        self.assertEqual(cfg.parse_steps(cfg.format_steps(steps)), steps)
        with self.assertRaises(ValueError):
            cfg.parse_steps("+move(200,0)")

    def test_cli_shortcut_writes_block_first(self):
        writes = []
        write_flash = self.mouse.write_flash
        self.mouse.write_flash = lambda a, d: (writes.append(a), write_flash(a, d))
        self.run_cmd(cli.cmd_button, button=7, action="key:ctrl+c")
        self.assertEqual(writes, [cfg.shortcut_addr(6), 0x78])
        out = self.run_cmd(cli.cmd_info)
        self.assertIn("7: shortcut ctrl+c", out)

    def test_cli_macro(self):
        self.run_cmd(cli.cmd_macro, button=8, name="copy", repeat=cfg.REPEAT_TOGGLE,
                     steps=["+ctrl 10 +c 10 -c 10 -ctrl"])
        flash = bytes(self.fake.flash)
        self.assertEqual(flash[0x7C:0x80], bytes([6, 7, 253, 0x55 - 6 - 7 - 253 & 0xFF]))
        block = flash[cfg.macro_addr(7):cfg.macro_addr(8)]
        self.assertEqual(block[:5], b"\x04copy")
        self.assertEqual(block[0x1F:0x2A].hex(" "), "04 80 01 00 00 0a 81 06 00 00 0a")
        out = self.run_cmd(cli.cmd_info)
        self.assertIn("8: macro 'copy', until pressed again: +ctrl 10 +c 10 -c 10 -ctrl", out)

    def test_cli_actions(self):
        self.assertEqual(cli.parse_action("fire:20:0"), (4, 20, 0))
        self.assertEqual(cli.parse_action("dpi-lock:800"), (0x0A, 15, 0))
        self.assertEqual(cli.parse_action("scroll-up"), (0x0B, 1, 0))
        self.assertEqual(cli.parse_action("lights"), (0x08, 0, 0))
        self.assertEqual(cli.parse_action("next-slot"), (0x09, 0, 0))
        self.assertEqual(cli.parse_action("key:volume-up")[0].raw(), bytes([0x82, 0xE9, 0]))

    def test_restore_after_macro(self):
        # The mouse ignores raw 0xFF writes in the macro area: an erased block in the backup
        # must be restored as a deleted macro, and then count as equal
        self.run_cmd(cli.cmd_macro, button=6, name="hola", repeat=1, steps=["+h 20 -h 20"])
        self.assertIsNotNone(cfg.Macro.decode(self.fake.flash[cfg.macro_addr(5):]))
        with tempfile.NamedTemporaryFile(suffix=".bin") as f:
            f.write(self.original)
            f.flush()
            self.run_cmd(cli.cmd_restore, file=f.name, yes=True)
            out = self.run_cmd(cli.cmd_restore, file=f.name, yes=True)
        self.assertIn("nothing to do", out)
        block = self.fake.flash[cfg.macro_addr(5):cfg.macro_addr(6)]
        self.assertEqual(block[:0x21], cfg.EMPTY_MACRO)
        self.assertEqual(self.fake.flash[:cfg.OFF_MACROS], self.original[:cfg.OFF_MACROS])

    def test_fake_drops_partial_macro_writes(self):
        a = cfg.macro_addr(4)
        self.mouse.write_flash(a + 1, b"\xff")
        self.mouse.write_flash(a, b"\xff" * cfg.MACRO_SIZE)
        self.assertEqual(bytes(self.fake.flash[a:a + 7]), b"\x06jitter")

    def test_restore(self):
        self.run_cmd(cli.cmd_dpi_color, level=1, color="123456")
        with tempfile.NamedTemporaryFile(suffix=".bin") as f:
            f.write(self.original)
            f.flush()
            self.run_cmd(cli.cmd_restore, file=f.name, yes=True)
        self.assertEqual(bytes(self.fake.flash), self.original)

    def test_asleep(self):
        self.fake.asleep = True
        self.assertFalse(self.mouse.online())
        with self.assertRaises(MouseAsleep):
            self.mouse.transact(0x04, retries=1, timeout=0.1)

    def test_receiver_commands_while_asleep(self):
        # Like the hardware: 0x03 and 0x06 come from the receiver, 0x0E and 0x17 from the mouse
        self.fake.asleep = True
        _pkt, resp = self.mouse.transact(0x06)
        self.assertEqual(resp[5:8], b"\x02\x00\x00")
        for cmd in (0x0E, 0x17):
            with self.assertRaises(MouseAsleep):
                self.mouse.transact(cmd, retries=1, timeout=0.1)
        self.fake.asleep = False
        self.assertEqual(self.mouse.transact(0x0E)[1][5:7], b"\x01\x00")
        self.assertEqual(self.mouse.transact(0x17)[1][5:7], b"\x0a\x00")

    def test_buttons_write_the_flash(self):
        # Verified on the hardware: the mouse stores what its DPI and Lights buttons change
        self.fake.press(5)  # slot 6 is "dpi-loop" in the fixture: level 2 -> 3
        self.assertEqual(self.mouse.read_flash(0x04, 2), bytes([2, 0x53]))
        self.fake.flash[0x60 + 4 * 6:0x60 + 4 * 7] = bytes([0x08, 0, 0, 0x4D])  # slot 7: lights
        self.fake.press(6)
        self.assertEqual(self.mouse.read_flash(0x52, 2), bytes([0, 0x55]))
        self.fake.press(6)
        self.assertEqual(self.mouse.read_flash(0x52, 2), bytes([1, 0x54]))
        time.sleep(0.1)
        self.assertEqual(self.mouse.poll_notifications(), [0x01, 0x08, 0x08])

    def test_profiles_have_their_own_flash(self):
        # Verified on the hardware: SetCurrentConfig (0x0F) swaps the whole flash image
        self.mouse.write_verified(0x4E, bytes([0x10, 0x45]))  # profile 1: brightness 16
        _pkt, resp = self.mouse.transact(0x0F, data=b"\x01")
        self.assertEqual(resp[6], 1)
        self.assertEqual(self.mouse.transact(0x0E)[1][6], 1)
        self.assertEqual(self.mouse.read_flash(0x4E, 2), self.original[0x4E:0x50])
        self.mouse.transact(0x0F, data=b"\x00")
        self.assertEqual(self.mouse.read_flash(0x4E, 2), bytes([0x10, 0x45]))
        # A "switch config" button goes to the next slot
        self.fake.flash[0x6C:0x70] = bytes([0x09, 0, 0, 0x4C])
        self.fake.press(3)
        self.assertEqual(self.mouse.transact(0x0E)[1][6], 1)

    def test_clear_setting_resets_active_profile(self):
        # Verified on the hardware: 0x09 resets 0x00-0xBF of the active profile only
        self.mouse.write_verified(0x4E, bytes([0x10, 0x45]))
        self.mouse.write_verified(0x100, bytes([0x55, 0x55]))  # a shortcut block byte
        self.mouse.transact(0x0F, data=b"\x01")
        self.mouse.write_verified(0x4E, bytes([0xFF, 0x56]))
        self.mouse.transact(0x0F, data=b"\x00")
        self.mouse.transact(0x09)
        self.assertEqual(self.mouse.read_flash(0, 0xC0), self.original[:0xC0])
        self.assertEqual(self.mouse.read_flash(0x100, 2), bytes([0x55, 0x55]))
        self.mouse.transact(0x0F, data=b"\x01")
        self.assertEqual(self.mouse.read_flash(0x4E, 2), bytes([0xFF, 0x56]))

    def test_notifications(self):
        self.fake.notify(0x41)
        self.mouse.battery()  # arrives while waiting for another reply
        self.fake.notify(0x01)
        time.sleep(0.1)
        self.assertEqual(self.mouse.poll_notifications(), [0x41, 0x01])
        self.assertEqual(self.mouse.poll_notifications(), [])


class SlotTest(unittest.TestCase):
    """The 4 onboard slots (GetCurrentConfig 0x0E / SetCurrentConfig 0x0F): each one has its own
    flash image, verified on the hardware."""

    def setUp(self):
        with open(FIXTURE, "rb") as f:
            self.original = f.read()
        self.fake = FakeMouse(self.original)
        self.fake.flashes[1][0x4E:0x50] = bytes([0x10, 0x45])  # slot 2: brightness 16
        self.mouse = self.fake.mouse()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.mouse.close()
        self.fake.close()
        self.tmp.cleanup()

    def main(self, *argv):
        """Run the CLI on the fake mouse; returns (exit code or None, stdout)."""
        out = io.StringIO()
        with mock.patch.object(cli, "open_mouse", lambda: self.fake.mouse()), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli.main(list(argv))
            except SystemExit as e:
                return e.code, out.getvalue()
        return None, out.getvalue()

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def slot2_backup(self):
        """A dump of slot 2, written directly (a real dump takes ~5 s)."""
        with open(self.path("s2.bin"), "wb") as f:
            f.write(self.fake.flashes[1])
        return self.path("s2.bin")

    def test_get_and_set(self):
        self.assertEqual(self.mouse.slot(), 0)
        self.mouse.set_slot(3)
        self.assertEqual(self.fake.slot, 3)
        self.assertEqual(self.mouse.slot(), 3)
        with self.assertRaises(ValueError):
            self.mouse.set_slot(4)
        self.assertEqual(self.fake.slot, 3)

    def test_slot_changes_are_notified(self):
        # Verified on the hardware (receiver): 0x0F and a "switch config" button (type 9) send
        # StatusChanged with isConfigChanged, and the button cycles through the 4 slots
        self.mouse.set_slot(2)
        time.sleep(0.1)
        self.assertEqual(self.mouse.poll_notifications(), [0x04])
        self.fake.press(4)  # button 5 is "forward" in every slot of the fixture
        self.assertEqual(self.fake.slot, 2)
        for flash in self.fake.flashes:
            flash[0x70:0x74] = bytes([0x09, 0, 0, 0x4C])  # button 5: switch config
        seen = []
        for _press in range(4):
            self.fake.press(4)
            seen.append(self.mouse.slot())
        self.assertEqual(seen, [3, 0, 1, 2])
        time.sleep(0.1)
        self.assertEqual(self.mouse.poll_notifications(), [0x04] * 4)

    def test_on_slot_goes_back(self):
        with on_slot(self.mouse, 2):
            self.assertEqual(self.fake.slot, 2)
        self.assertEqual(self.fake.slot, 0)
        with on_slot(self.mouse, None):
            self.assertEqual(self.fake.slot, 0)

    def test_on_slot_goes_back_when_the_block_fails(self):
        with self.assertRaises(ProtocolError):
            with on_slot(self.mouse, 1):
                raise ProtocolError("write failed")
        self.assertEqual(self.fake.slot, 0)

    def test_on_slot_reports_when_it_cannot_go_back(self):
        with self.assertRaises(ProtocolError) as cm:
            with on_slot(self.mouse, 1):
                self.mouse.transact = mock.Mock(side_effect=MouseAsleep("no answer"))
        self.assertIn("the mouse stayed on slot 2", str(cm.exception))
        self.assertEqual(self.fake.slot, 1)

    def test_slot_command(self):
        self.assertEqual(self.main("slot"), (None, "active slot: 1 of 4\n"))
        self.assertEqual(self.main("slot", "3"), (None, "active slot set to 3\n"))
        self.assertEqual(self.fake.slot, 2)
        code, _out = self.main("slot", "5")
        self.assertEqual(code, 2)  # argparse rejects it before touching the mouse
        self.assertEqual(self.fake.slot, 2)

    def test_dump_slot(self):
        code, out = self.main("dump", "--slot", "2", self.path("s2.bin"))
        self.assertIsNone(code)
        self.assertIn("of slot 2 to", out)
        with open(self.path("s2.bin"), "rb") as f:
            self.assertEqual(f.read(), bytes(self.fake.flashes[1]))
        self.assertEqual(self.fake.slot, 0)

    def test_restore_into_another_slot(self):
        # Copy slot 2 into slot 3, without touching the active slot 1
        code, out = self.main("restore", "--slot", "3", "--yes", self.slot2_backup())
        self.assertIsNone(code)
        self.assertIn("slot 3 restored and verified", out)
        self.assertEqual(self.fake.flashes[2], self.fake.flashes[1])
        self.assertEqual(bytes(self.fake.flashes[0]), self.original)
        self.assertEqual(self.fake.slot, 0)

    def test_restore_failure_goes_back(self):
        backup = self.slot2_backup()
        with mock.patch("m916.protocol.Mouse.write_flash",
                        side_effect=ProtocolError("write not acknowledged")):
            code, _out = self.main("restore", "--slot", "3", "--yes", backup)
        self.assertIn("write not acknowledged", code)
        self.assertEqual(self.fake.slot, 0)
        self.assertEqual(bytes(self.fake.flashes[2]), self.original)

    def test_profile_slot(self):
        code, _out = self.main("profile", "save", "--slot", "2", self.path("p.json"))
        self.assertIsNone(code)
        code, out = self.main("profile", "load", "--slot", "4", self.path("p.json"))
        self.assertIsNone(code)
        self.assertIn("profile applied and verified", out)
        self.assertEqual(self.fake.flashes[3][0x4E:0x50], bytes([0x10, 0x45]))
        self.assertEqual(bytes(self.fake.flashes[0]), self.original)
        self.assertEqual(self.fake.slot, 0)


class LongRangeTest(unittest.TestCase):
    """Long range mode (SetLongRangeMode 0x16 / GetLongRangeMode 0x17), verified on the
    hardware: the mouse answers it and keeps it outside the flash images of the slots."""

    def setUp(self):
        with open(FIXTURE, "rb") as f:
            self.original = f.read()
        self.fake = FakeMouse(self.original)
        self.mouse = self.fake.mouse()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.mouse.close()
        self.fake.close()
        self.tmp.cleanup()

    main = SlotTest.main

    def flashes(self):
        return [bytes(f) for f in self.fake.flashes]

    def test_get_and_set(self):
        self.assertFalse(self.mouse.long_range())
        before = self.flashes()
        self.mouse.set_long_range(True)
        self.assertEqual(self.fake.long_range, 1)
        self.assertTrue(self.mouse.long_range())
        self.assertEqual(self.flashes(), before)  # not in any slot image
        self.mouse.set_long_range(False)
        self.assertEqual(self.fake.long_range, 0)

    def test_set_echoes_the_packet(self):
        pkt, resp = self.mouse.transact(0x16, data=b"\x01" + bytes(9))
        self.assertEqual(pkt[5], 10)
        self.assertEqual(resp, pkt)
        self.assertEqual(self.mouse.transact(0x17)[1][5:7], b"\x0a\x01")

    def test_no_reply_while_asleep(self):
        self.fake.asleep = True
        for cmd, data in ((0x16, b"\x01" + bytes(9)), (0x17, b"")):
            with self.assertRaises(MouseAsleep):
                self.mouse.transact(cmd, data=data, retries=1, timeout=0.1)
        self.assertEqual(self.fake.long_range, 0)

    def test_not_stored_is_an_error(self):
        with mock.patch.object(type(self.mouse), "long_range", return_value=False):
            with self.assertRaises(ProtocolError):
                self.mouse.set_long_range(True)

    def test_slots_restore_and_profiles_leave_it_alone(self):
        self.mouse.set_long_range(True)
        self.mouse.set_slot(2)
        self.assertTrue(self.mouse.long_range())
        self.mouse.set_slot(0)
        backup = os.path.join(self.tmp.name, "b.bin")
        with open(backup, "wb") as f:
            f.write(self.original)
        self.mouse.write_verified(0x4E, bytes([0x10, 0x45]))  # something for restore to undo
        self.assertEqual(self.main("restore", "-y", backup)[0], None)
        prof = os.path.join(self.tmp.name, "p.json")
        self.assertEqual(self.main("profile", "save", prof)[0], None)
        self.assertNotIn("long", open(prof).read())
        self.assertEqual(self.main("profile", "load", prof)[0], None)
        self.assertEqual(self.fake.long_range, 1)

    def test_cli(self):
        self.assertEqual(self.main("long-range"), (None, "long range: off\n"))
        self.assertEqual(self.main("long-range", "on"),
                         (None, "long range mode turned on and verified\n"))
        self.assertEqual(self.fake.long_range, 1)
        self.assertIn("long range: on", self.main("info")[1])
        self.assertEqual(self.main("long-range", "off")[1],
                         "long range mode turned off and verified\n")
        self.assertEqual(self.fake.long_range, 0)
        self.assertEqual(self.main("long-range", "maybe")[0], 2)

    def test_over_the_cable(self):
        # Verified on the hardware: 0x16 and 0x17 work over the cable as over the air
        self.mouse.set_long_range(True)
        self.fake.plug_cable()
        cable = self.fake.wired()
        try:
            self.assertTrue(cable.long_range())
            cable.set_long_range(False)
            self.assertEqual(self.fake.long_range, 0)
        finally:
            cable.close()


class CableTest(unittest.TestCase):
    """Verified on the hardware: with the cable plugged in the mouse answers only through it
    (3554:F55E) and does not sleep; the receiver reports it as not linked."""

    def setUp(self):
        with open(FIXTURE, "rb") as f:
            self.original = f.read()
        self.fake = FakeMouse(self.original)
        self.fake.plug_cable()
        self.receiver = self.fake.mouse()
        self.cable = self.fake.wired()

    def tearDown(self):
        self.receiver.close()
        self.cable.close()
        self.fake.close()

    def openers(self):
        return [opener for _path, opener in self.fake.openers()]

    def test_receiver_loses_the_mouse(self):
        self.assertFalse(self.receiver.online())
        self.assertEqual(self.receiver.transact(0x06)[1][5:8], b"\x02\x00\x00")
        with self.assertRaises(MouseAsleep):
            self.receiver.transact(0x08, length=2, retries=1, timeout=0.1)

    def test_cable_reads_and_writes(self):
        self.fake.asleep = True  # what the mouse would do over the radio; not on the cable
        self.assertTrue(self.cable.online())
        self.assertEqual(self.cable.read_flash(0x4E, 2), self.original[0x4E:0x50])
        self.cable.write_verified(0x4E, bytes([0x10, 0x45]))
        self.assertEqual(bytes(self.fake.flash[0x4E:0x50]), bytes([0x10, 0x45]))
        self.assertEqual(self.cable.transact(0x06)[1][2], 1)  # pairing is the receiver's job

    def test_notifications_come_through_the_cable(self):
        self.fake.notify(0x01)
        time.sleep(0.1)
        self.assertEqual(self.cable.poll_notifications(), [0x01])
        self.assertEqual(self.receiver.poll_notifications(), [])

    def test_unplugging_breaks_the_cable_connection(self):
        self.fake.unplug_cable()
        with self.assertRaises(OSError):
            self.cable.transact(0x03, retries=1, timeout=0.1)
        self.assertTrue(self.receiver.online())

    def test_open_mouse_prefers_the_linked_connection(self):
        m = open_mouse(self.openers())
        self.assertEqual(m.link, "wired")
        m.close()
        self.fake.unplug_cable()
        m = open_mouse(self.openers())
        self.assertEqual(m.link, "dongle")
        m.close()

    def test_open_mouse_falls_back_to_the_receiver(self):
        self.fake.unplug_cable()
        self.fake.asleep = True
        m = open_mouse(self.openers())
        self.assertEqual(m.link, "dongle")  # the caller waits for the mouse to wake up
        m.close()
        self.assertIsNone(open_mouse(self.openers(), fallback=False))

    def test_open_mouse_skips_broken_nodes(self):
        def broken():
            raise OSError(32, "Broken pipe")

        def denied():
            raise PermissionError(13, "Permission denied", "/dev/hidraw9")

        m = open_mouse([broken, denied] + self.openers())
        self.assertEqual(m.link, "wired")
        m.close()
        with self.assertRaises(PermissionError) as ctx:
            open_mouse([broken, denied])
        self.assertEqual(ctx.exception.filename, "/dev/hidraw9")
        self.assertIsNone(open_mouse([]))


class PackagingTest(unittest.TestCase):
    ROOT = os.path.join(os.path.dirname(__file__), "..")

    def test_desktop_icon_is_shipped(self):
        with open(os.path.join(self.ROOT, "data", "io.github.furibeh.m916-linux.desktop")) as f:
            entry = dict(line.strip().split("=", 1) for line in f if "=" in line)
        self.assertEqual(entry["Icon"], "io.github.furibeh.m916-linux")
        self.assertTrue(os.path.isfile(os.path.join(self.ROOT, "m916", "icons",
                                                    entry["Icon"] + ".svg")))


if __name__ == "__main__":
    unittest.main()
