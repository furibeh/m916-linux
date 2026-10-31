# SPDX-License-Identifier: GPL-3.0-or-later
"""Translations: every marked string is translated, catalogs are up to date, and the
program switches language with the environment."""
import ast
import gettext
import glob
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
os.environ["LANGUAGE"] = "en"  # tests compare the English source strings
from m916 import i18n  # noqa: E402

MODULES = sorted(glob.glob(os.path.join(ROOT, "m916", "*.py")))
FORMAT = re.compile(r"%(?:\([a-z_]+\))?[-#0 +]*\d*(?:\.\d+)?[sdrxX%]")


def read_po(path):
    """{msgid: msgstr or [msgstr[0], msgstr[1], ...]} and the set of fuzzy msgids, for the
    subset of the .po format that msgmerge writes (no msgctxt)."""
    catalog, fuzzy = {}, set()
    entry, key, flags = {}, None, ""

    def flush():
        if "msgid" in entry and entry["msgid"]:
            plural = sorted(k for k in entry if k.startswith("msgstr["))
            catalog[entry["msgid"]] = [entry[k] for k in plural] if plural else entry["msgstr"]
            if "fuzzy" in flags:
                fuzzy.add(entry["msgid"])

    for line in open(path, encoding="utf-8").read().splitlines() + [""]:
        if line.startswith("#~"):
            raise AssertionError("%s has obsolete entries (#~)" % path)
        if not line.strip():
            flush()
            entry, key, flags = {}, None, ""
        elif line.startswith("#,"):
            flags += line
        elif line.startswith("#"):
            continue
        elif line.startswith('"'):
            entry[key] += ast.literal_eval(line)
        else:
            key, value = line.split(" ", 1)
            entry[key] = ast.literal_eval(value)
    return catalog, fuzzy


def marked_strings():
    """msgid -> plural (or None) for every _() / ngettext() call in the package."""
    found = {}
    for path in MODULES:
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("_", "ngettext")):
                continue
            n = 1 if node.func.id == "_" else 2
            args = node.args[:n]
            where = "%s:%d" % (os.path.basename(path), node.lineno)
            if not all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args):
                raise AssertionError("%s: %s() needs string literals" % (where, node.func.id))
            found[args[0].value] = args[1].value if n == 2 else None
    return found


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.code = marked_strings()
        self.pos = sorted(glob.glob(os.path.join(ROOT, "po", "*.po")))

    def test_there_is_a_spanish_catalog(self):
        self.assertIn(os.path.join(ROOT, "po", "es.po"), self.pos)

    def test_every_string_is_translated(self):
        for po in self.pos:
            catalog, fuzzy = read_po(po)
            for msgid, plural in self.code.items():
                with self.subTest(po=os.path.basename(po), msgid=msgid):
                    self.assertIn(msgid, catalog, "not in the catalog: run tools/i18n.sh")
                    self.assertNotIn(msgid, fuzzy, "marked fuzzy: review it")
                    msgstr = catalog[msgid]
                    if plural is None:
                        self.assertTrue(msgstr, "untranslated")
                    else:
                        self.assertTrue(isinstance(msgstr, list) and all(msgstr), "untranslated")

    def test_no_unused_entries(self):
        for po in self.pos:
            catalog, _fuzzy = read_po(po)
            self.assertEqual(sorted(set(catalog) - set(self.code)), [],
                             "%s: entries no longer in the code: run tools/i18n.sh" % po)

    def test_placeholders_match(self):
        for po in self.pos:
            catalog, _fuzzy = read_po(po)
            for msgid, msgstr in catalog.items():
                plural = self.code.get(msgid)
                for i, text in enumerate([msgstr] if isinstance(msgstr, str) else msgstr):
                    source = plural if plural is not None and i > 0 else msgid
                    with self.subTest(po=os.path.basename(po), msgid=msgid, form=i):
                        expected = FORMAT.findall(source)
                        if "%(" not in source:  # positional: same order
                            self.assertEqual(FORMAT.findall(text), expected)
                        else:
                            self.assertEqual(sorted(FORMAT.findall(text)), sorted(expected))

    def test_compiled_catalogs_are_up_to_date(self):
        for po in self.pos:
            lang = os.path.basename(po)[:-3]
            mo = os.path.join(ROOT, "m916", "locale", lang, "LC_MESSAGES", "m916.mo")
            with self.subTest(lang=lang):
                self.assertTrue(os.path.isfile(mo), "%s is missing: run tools/i18n.sh" % mo)
                with open(mo, "rb") as f:
                    compiled = gettext.GNUTranslations(f)
                catalog, _fuzzy = read_po(po)
                for msgid, msgstr in catalog.items():
                    if isinstance(msgstr, str):
                        got = compiled.gettext(msgid)
                    else:
                        got = [compiled.ngettext(msgid, self.code[msgid], n) for n in (1, 2)]
                    self.assertEqual(got, msgstr, "%s is stale: run tools/i18n.sh" % mo)


class LanguageTest(unittest.TestCase):
    def spanish(self):
        return gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=["es"])

    def test_spanish(self):
        es = self.spanish()
        self.assertEqual(es.gettext("Apply"), "Aplicar")
        self.assertEqual(es.ngettext("%d time", "%d times", 1) % 1, "1 vez")
        self.assertEqual(es.ngettext("%d time", "%d times", 3) % 3, "3 veces")
        self.assertEqual(es.info()["language"], "es")

    def test_english_is_the_source(self):
        self.assertIsNone(i18n.language())
        self.assertEqual(i18n._("Apply"), "Apply")

    def test_cli_follows_the_environment(self):
        def help_text(language):
            env = dict(os.environ, LANGUAGE=language)
            return subprocess.run([sys.executable, "-m", "m916", "--help"], cwd=ROOT, env=env,
                                  capture_output=True, text=True, check=True).stdout
        self.assertIn("show device status and configuration", help_text("en"))
        self.assertIn("muestra el estado y la configuración del dispositivo", help_text("es"))

    def test_underscore_is_not_a_variable(self):
        # A function that assigns `_` cannot call _(): Python makes it a local name
        for path in MODULES:
            tree = ast.parse(open(path, encoding="utf-8").read())
            for func in ast.walk(tree):
                if not isinstance(func, ast.FunctionDef):
                    continue
                for node in ast.walk(func):
                    if isinstance(node, ast.Name) and node.id == "_" and isinstance(node.ctx, ast.Store):
                        self.fail("%s:%d assigns _" % (os.path.basename(path), node.lineno))


if __name__ == "__main__":
    unittest.main()
