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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LANGUAGE"] = "en"  # tests compare the English source strings
from m916 import cli, config as cfg  # noqa: E402
from m916.fake import FakeMouse  # noqa: E402
from m916.protocol import FLASH_SIZE, MouseAsleep, open_mouse  # noqa: E402

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
        # A "switch config" button goes to the next profile
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
