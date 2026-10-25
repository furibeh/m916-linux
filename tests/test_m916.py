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
from m916 import cli, config as cfg  # noqa: E402
from m916.fake import FakeMouse  # noqa: E402
from m916.protocol import FLASH_SIZE, MouseAsleep  # noqa: E402

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
        self.assertIn("report rate  : 1000 Hz", out)
        self.assertIn("* 2:  2000 DPI  colour #0000ff", out)
        self.assertIn("5: 26000 DPI", out)
        self.assertIn("12: fire (every 10 ms, 3 shots)", out)
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

    def test_notifications(self):
        self.fake.notify(0x41)
        self.mouse.battery()  # arrives while waiting for another reply
        self.fake.notify(0x01)
        time.sleep(0.1)
        self.assertEqual(self.mouse.poll_notifications(), [0x41, 0x01])
        self.assertEqual(self.mouse.poll_notifications(), [])


if __name__ == "__main__":
    unittest.main()
