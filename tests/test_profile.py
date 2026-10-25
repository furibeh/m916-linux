# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON profiles: format, validation and loading against the simulated mouse."""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from m916 import cli, config as cfg, profile  # noqa: E402
from m916.fake import FakeMouse  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "m916_flash.bin")
HEADER = {"format": "m916-profile", "version": 1, "model": "Redragon M916-PRO-1K"}


def custom_image(image):
    """The fixture with every kind of button function."""
    conf = cfg.Config.decode(image)
    conf.set_shortcut(6, cfg.parse_combo("ctrl+c"))
    conf.set_macro(7, cfg.Macro("copiar", cfg.parse_steps(
        "+ctrl 10 +c 10 -c 10 -ctrl +move(5,-3) 4 -move(5,-3) +key:0x99 -key:0x99")),
        cfg.REPEAT_TOGGLE)
    conf.buttons[12] = (cfg.KEY_DPI_LOCK, 7, 0)
    conf.set_shortcut(13, cfg.parse_combo("volume-up"))
    conf.buttons[14] = (8, 1, 0)
    conf.set_shortcut(15, cfg.parse_steps("+ctrl +a -ctrl -a"))
    conf.levels[1].y = 2400
    return conf.encode(image)


class ProfileFormatTest(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, "rb") as f:
            self.original = f.read()

    def errors(self, data):
        conf = cfg.Config.decode(self.original)
        with self.assertRaises(profile.ProfileError) as cm:
            profile.apply(conf, data)
        return "\n".join(cm.exception.errors)

    def test_roundtrip(self):
        image = custom_image(self.original)
        text = profile.dumps(cfg.Config.decode(image))
        data = json.loads(text)
        self.assertEqual(data["buttons"]["7"], {"action": "shortcut", "keys": "ctrl+c"})
        self.assertEqual(data["buttons"]["8"]["repeat"], "toggle")
        self.assertIn("+move(5,-3) 4", data["buttons"]["8"]["steps"])
        self.assertEqual(data["buttons"]["12"], {"action": "fire", "interval": 10, "shots": 3})
        self.assertEqual(data["buttons"]["13"], {"action": "dpi-lock", "dpi": 400})
        self.assertEqual(data["buttons"]["14"], {"action": "media", "key": "volume-up"})
        self.assertEqual(data["buttons"]["15"], {"raw": [8, 1, 0]})
        self.assertEqual(data["buttons"]["16"]["events"], ["+ctrl", "+a", "-ctrl", "-a"])
        self.assertIs(data["options"]["motion-sync"], True)
        self.assertEqual(data["dpi"]["active"], 2)
        self.assertIn('      {"x": 1000, "y": 1000, "color": "#ff0000"},\n', text)
        conf = cfg.Config.decode(self.original)
        profile.apply(conf, data)
        self.assertEqual(conf.encode(self.original), image)

    def test_partial(self):
        conf = cfg.Config.decode(self.original)
        profile.apply(conf, dict(HEADER, dpi={"count": 1}, buttons={"7": "middle"}))
        new = conf.encode(self.original)
        self.assertEqual(cfg.changed_fields(self.original, new),
                         [(cfg.OFF_DPI_ACTIVE, 2), (cfg.OFF_DPI_COUNT, 2), (0x78, 4)])
        self.assertEqual((conf.dpi_count, conf.dpi_active), (1, 0))

    def test_partial_levels(self):
        conf = cfg.Config.decode(self.original)
        profile.apply(conf, dict(HEADER, dpi={"levels": [None, {"x": 1600}]}))
        self.assertEqual([(lv.x, lv.y) for lv in conf.levels[:3]],
                         [(1000, 1000), (1600, 1600), (4000, 4000)])
        self.assertEqual(conf.levels[1].color, (0, 0, 255))

    def test_invalid(self):
        cases = [
            (dict(HEADER, version=2), "unsupported profile version 2"),
            (dict(HEADER, model="M908"), "profile is for \"M908\""),
            ({"version": 1}, "missing field 'format'"),
            (dict(HEADER, repot_rate=1000), "unknown field 'repot_rate'"),
            (dict(HEADER, report_rate=333), "report_rate: must be one of"),
            (dict(HEADER, dpi={"levels": [{"x": 123}]}), "level 1: DPI must be a multiple of 50"),
            (dict(HEADER, dpi={"levels": [{"x": 800, "color": "red"}]}),
             "dpi.levels.1.color: must be \"#rrggbb\""),
            (dict(HEADER, dpi={"count": 9}), "dpi.count: must be 1..8"),
            (dict(HEADER, dpi={"active": 7}), "active DPI level must be one of the enabled"),
            (dict(HEADER, options={"debounce": "4"}), "options.debounce: must be an integer"),
            (dict(HEADER, options={"bogus": 1}), "options.bogus: unknown option"),
            (dict(HEADER, buttons={"7": {"action": "shortcut", "keys": "ctrl+nokey"}}),
             "buttons.7: unknown key name 'nokey'"),
            (dict(HEADER, buttons={"7": {"action": "fire", "interval": 5, "shots": 1}}),
             "buttons.7.interval: must be 10..255"),
            (dict(HEADER, buttons={"7": {"action": "macro", "name": "m", "repeat": 300,
                                         "steps": ["+a"]}}), "buttons.7.repeat: must be 1..252"),
            (dict(HEADER, buttons={"7": {"action": "macro", "name": "m", "steps": ["+ctlr"]}}),
             "buttons.7.steps: unknown key name 'ctlr'"),
            (dict(HEADER, buttons={"7": {"action": "media", "key": "a"}}),
             "buttons.7.key: \"a\" is not a media key"),
            (dict(HEADER, buttons={"17": "left"}), "buttons.17: buttons are numbered 1..16"),
            (dict(HEADER, buttons={"7": "jump"}), "buttons.7: unknown action 'jump'"),
            (dict(HEADER, buttons={"7": {"raw": [1, 2]}}), "buttons.7.raw: must be [type, p1, p2]"),
        ]
        for data, message in cases:
            with self.subTest(message=message):
                self.assertIn(message, self.errors(data))

    def test_errors_are_collected(self):
        text = self.errors(dict(HEADER, report_rate=1, buttons={"1": "x", "2": "y"}))
        self.assertEqual(len(text.splitlines()), 3)

    def test_check_without_mouse(self):
        profile.check(dict(HEADER, buttons={"7": {"action": "shortcut", "keys": "ctrl+c"}}))
        with self.assertRaises(profile.ProfileError):
            profile.check(dict(HEADER, buttons={"7": {"action": "shortcut", "keys": "ctrl+zz"}}))

    def test_bad_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
            f.write("{\"format\": ")
            f.flush()
            with self.assertRaises(profile.ProfileError) as cm:
                profile.load(f.name)
        self.assertIn("invalid JSON", str(cm.exception))


class ProfileMouseTest(unittest.TestCase):
    """`m916 profile save/load` against simulated mice."""

    def setUp(self):
        with open(FIXTURE, "rb") as f:
            self.original = f.read()
        self.fake = FakeMouse(self.original)
        self.mouse = self.fake.mouse()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "perfil.json")

    def tearDown(self):
        self.fake.close()
        self.tmp.cleanup()

    def run_cmd(self, func, mouse=None, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            func(mouse or self.mouse, argparse.Namespace(**kwargs))
        return out.getvalue()

    def spy_writes(self):
        writes = []
        write_flash = self.mouse.write_flash
        self.mouse.write_flash = lambda a, d: (writes.append(a), write_flash(a, d))
        return writes

    def write_profile(self, data):
        with open(self.path, "w") as f:
            json.dump(data, f)

    def test_save_and_load_on_another_mouse(self):
        other = FakeMouse(custom_image(self.original))
        try:
            m = other.mouse()
            self.run_cmd(cli.cmd_profile_save, mouse=m, file=self.path, description=None)
            expected = cfg.read_image(m)
        finally:
            other.close()
        out = self.run_cmd(cli.cmd_profile_load, file=self.path, dry_run=False)
        self.assertIn("applied and verified", out)
        self.assertEqual(cfg.read_image(self.mouse), expected)
        # The macro was stored: the simulated firmware drops anything else
        self.assertEqual(cfg.Macro.decode(self.fake.flash[cfg.macro_addr(7):]).name, "copiar")

        writes = self.spy_writes()
        out = self.run_cmd(cli.cmd_profile_load, file=self.path, dry_run=False)
        self.assertIn("nothing to do", out)
        self.assertEqual(writes, [])

    def test_load_writes_only_changes(self):
        self.write_profile(dict(HEADER, report_rate=500, options={"debounce": 8, "lod": 2}))
        writes = self.spy_writes()
        self.run_cmd(cli.cmd_profile_load, file=self.path, dry_run=False)
        self.assertEqual(writes, [cfg.OFF_REPORT_RATE, cfg.OPTIONS["lod"]])
        changed = [i for i in range(len(self.original)) if self.fake.flash[i] != self.original[i]]
        self.assertEqual(changed, [0, 1, 0x0A, 0x0B])

    def test_macro_block_before_button(self):
        self.write_profile(dict(HEADER, buttons={"7": {
            "action": "macro", "name": "hola", "repeat": "while-held",
            "steps": ["+h 20", "-h 20", "+o 20", "-o"]}}))
        writes = self.spy_writes()
        self.run_cmd(cli.cmd_profile_load, file=self.path, dry_run=False)
        a = cfg.macro_addr(6)
        self.assertEqual(writes[0], a)  # whole block from its start
        self.assertEqual(writes[-1], cfg.OFF_KEYS + 4 * 6)
        self.assertEqual(cfg.Macro.decode(self.fake.flash[a:a + cfg.MACRO_SIZE]).name, "hola")
        self.assertEqual(self.fake.flash[0x78:0x7B], bytes([cfg.KEY_MACRO, 6, cfg.REPEAT_WHILE_HELD]))

    def test_dry_run(self):
        self.write_profile(dict(HEADER, dpi={"count": 2}))
        out = self.run_cmd(cli.cmd_profile_load, file=self.path, dry_run=True)
        self.assertIn("1 field(s) to write: 0x0002", out)
        self.assertEqual(bytes(self.fake.flash), self.original)

    def test_invalid_profile_writes_nothing(self):
        self.write_profile(dict(HEADER, dpi={"count": 2}, buttons={"7": "jump"}))
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd(cli.cmd_profile_load, file=self.path, dry_run=False)
        self.assertIn("unknown action 'jump'", str(cm.exception.code))
        self.assertEqual(bytes(self.fake.flash), self.original)

    def test_main_checks_before_opening(self):
        self.write_profile(dict(HEADER, version=3))
        with self.assertRaises(SystemExit) as cm:
            cli.main(["-d", "/nonexistent", "profile", "load", self.path])
        self.assertIn("unsupported profile version 3", str(cm.exception.code))


class ProfilesDirTest(unittest.TestCase):
    """Bare names live in $XDG_CONFIG_HOME/m916/profiles."""

    def setUp(self):
        with open(FIXTURE, "rb") as f:
            self.original = f.read()
        self.fake = FakeMouse(self.original)
        self.mouse = self.fake.mouse()
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.dir = os.path.join(self.tmp.name, "m916", "profiles")

    def tearDown(self):
        self.fake.close()
        self.tmp.cleanup()

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["-d", "/nonexistent", *argv])
        return out.getvalue()

    def run_cmd(self, func, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            func(self.mouse, argparse.Namespace(**kwargs))
        return out.getvalue()

    def test_dir_and_resolve(self):
        self.assertEqual(profile.profiles_dir(), self.dir)
        self.assertEqual(profile.resolve("juegos"), os.path.join(self.dir, "juegos.json"))
        self.assertEqual(profile.resolve("juegos.json"), "juegos.json")
        self.assertEqual(profile.resolve("perfiles/juegos"), "perfiles/juegos")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "", "HOME": "/home/u"}):
            self.assertEqual(profile.profiles_dir(), "/home/u/.config/m916/profiles")

    def test_save_list_and_load_by_name(self):
        self.assertIn("no profiles in %s" % self.dir, self.run_main("profile", "list"))
        out = self.run_cmd(cli.cmd_profile_save, file="juegos", description="Para jugar")
        path = os.path.join(self.dir, "juegos.json")
        self.assertIn("saved to %s" % path, out)
        with open(path) as f:
            self.assertEqual(json.load(f)["description"], "Para jugar")
        self.run_cmd(cli.cmd_profile_save, file="oficina", description=None)
        with open(os.path.join(self.dir, "roto.json"), "w") as f:
            f.write("{")
        with open(os.path.join(self.dir, "notas.txt"), "w") as f:
            f.write("not a profile")
        # Listing does not need the mouse (-d /nonexistent)
        out = self.run_main("profile", "list")
        self.assertEqual(out.splitlines()[1:], ["  juegos   Para jugar", "  oficina",
                                                "  roto     (unreadable)"])
        out = self.run_cmd(cli.cmd_profile_load, file="juegos", dry_run=False)
        self.assertIn("nothing to do", out)

    def test_load_missing_name(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main("profile", "load", "nada")
        self.assertIn(os.path.join(self.dir, "nada.json"), str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
