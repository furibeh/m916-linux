# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive the Qt interface against the simulated mouse (skipped without PySide6)."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "m916_flash.bin")


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from m916 import gui
        with open(FIXTURE, "rb") as f:
            self.original = f.read()
        self.worker = gui.DeviceWorker(self.original)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.window = gui.MainWindow(self.worker)
        self.thread.start()
        self.wait_for(lambda: self.window.image is not None)

    def tearDown(self):
        self.worker.stop()
        self.thread.quit()
        self.thread.wait(3000)
        self.window.deleteLater()

    def wait_for(self, cond, timeout=10):
        deadline = time.monotonic() + timeout
        while not cond():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("timed out")
            time.sleep(0.01)

    def flash(self):
        return bytes(self.worker._fake.flash)

    def test_loads_fixture(self):
        w = self.window
        self.assertEqual(w.state, "online")
        self.assertEqual(w.dpi.count.value(), 5)
        self.assertEqual(w.dpi.active.checkedId(), 1)
        self.assertEqual(w.dpi.rows[4][2].value(), 26000)
        self.assertEqual(w.performance.rate.currentText(), "1000 Hz")
        self.assertEqual(w._current_config().buttons[11], (4, 10, 3))
        self.assertEqual(w.buttons.rows[11].combo.currentData(), "fire")
        self.assertFalse(w.apply_btn.isEnabled())

    def test_edit_apply_and_revert(self):
        w = self.window
        w.dpi.rows[2][2].setValue(3200)
        w.dpi.rows[2][4].set_color((0, 255, 0))
        w.dpi.rows[2][4].changed.emit()
        w.buttons.rows[6].combo.setCurrentIndex(w.buttons.rows[6].combo.findData("middle"))
        w.performance.debounce.setValue(4)
        self.assertTrue(w.apply_btn.isEnabled())
        w.apply_btn.click()
        self.wait_for(lambda: not w._busy)
        changed = [i for i in range(len(self.original)) if self.flash()[i] != self.original[i]]
        self.assertEqual(changed, [0x14, 0x15, 0x17, 0x35, 0x37, 0x78, 0x79, 0x7B, 0xA9, 0xAA])
        self.assertFalse(w.apply_btn.isEnabled())

        w.performance.debounce.setValue(10)
        self.assertTrue(w.revert_btn.isEnabled())
        w.revert_btn.click()
        self.assertEqual(w.performance.debounce.value(), 4)
        self.assertFalse(w._dirty())

    def test_shortcut_media_and_macro(self):
        from m916 import config as cfg
        w = self.window
        rows = w.buttons.rows
        rows[6].shortcut.setText("ctrl+c")
        rows[6].combo.setCurrentIndex(rows[6].combo.findData("shortcut"))
        rows[7].combo.setCurrentIndex(rows[7].combo.findData("media"))
        rows[7].media.setCurrentIndex(rows[7].media.findData("mute"))
        rows[12].macro = cfg.Macro("jitter", cfg.parse_steps("+mouse-left 10 -mouse-left 10"))
        rows[12].repeat = cfg.REPEAT_WHILE_HELD
        rows[12].combo.setCurrentIndex(rows[12].combo.findData("macro"))
        rows[13].combo.setCurrentIndex(rows[13].combo.findData("dpi-lock"))
        rows[13].lock_dpi.setValue(400)
        self.assertEqual(w.buttons.errors(), [])
        w.apply_btn.click()
        self.wait_for(lambda: not w._busy)
        flash = self.flash()
        self.assertEqual(flash[0x78:0x7C], bytes([5, 0, 0, 0x50]))
        self.assertEqual(flash[0x1C0:0x1CE], self.original[0x180:0x18E])  # same bytes as Windows
        self.assertEqual(flash[0x1E0:0x1E8].hex(" "), "02 82 e2 00 42 e2 00 cb")
        self.assertEqual(flash[0x90:0x93], bytes([6, 12, cfg.REPEAT_WHILE_HELD]))
        self.assertEqual(cfg.Macro.decode(flash[0x1500:0x1680]).name, "jitter")
        self.assertEqual(flash[0x94:0x97], bytes([0x0A, 7, 0]))

        # Reload from the mouse: the rows show what was written
        w._submit("load")
        self.wait_for(lambda: not w._busy)
        self.assertEqual(rows[6].shortcut.text(), "ctrl+c")
        self.assertEqual(rows[7].combo.currentData(), "media")
        self.assertEqual(rows[12].macro.name, "jitter")
        self.assertEqual(rows[13].lock_dpi.value(), 400)
        self.assertFalse(w._dirty())

    def test_invalid_shortcut_blocks_apply(self):
        row = self.window.buttons.rows[6]
        row.shortcut.setText("ctrl+nokey")
        row.combo.setCurrentIndex(row.combo.findData("shortcut"))
        self.assertEqual(len(self.window.buttons.errors()), 1)
        self.assertEqual(self.window._current_config().buttons[6], (0, 0, 0))

    def test_capture_and_record(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from m916 import gui
        row = self.window.buttons.rows[6]
        row.combo.setCurrentIndex(row.combo.findData("shortcut"))  # starts capturing
        self.assertTrue(row.shortcut.capturing)
        QTest.keyPress(row.shortcut, Qt.Key.Key_Control)
        QTest.keyPress(row.shortcut, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        QTest.keyRelease(row.shortcut, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        QTest.keyRelease(row.shortcut, Qt.Key.Key_Control)
        self.assertFalse(row.shortcut.capturing)
        self.assertEqual(row.shortcut.text(), "ctrl+s")

        dialog = gui.MacroDialog(self.window, None, 1)
        dialog.record.setChecked(True)
        QTest.keyPress(dialog.area, Qt.Key.Key_A)
        QTest.keyRelease(dialog.area, Qt.Key.Key_A, delay=30)
        QTest.mouseClick(dialog.area, Qt.MouseButton.LeftButton)
        steps = dialog.steps()
        self.assertEqual(" ".join(str(e).split()[0] for e in steps),
                         "+a -a +mouse-left -mouse-left")
        self.assertGreaterEqual(steps[0].delay, 25)
        dialog.name.setText("test")
        dialog._accept()
        self.assertEqual(dialog.macro.name, "test")

    def test_sensor_and_light_options(self):
        w = self.window
        p, l = w.performance, w.lights
        self.assertEqual(p.lod.currentText(), "1 mm")
        self.assertEqual(p.mode.currentData(), 0)
        self.assertFalse(p.full.isChecked())
        self.assertFalse(p.full_time.isEnabled())
        self.assertEqual(p.full_time.currentText(), "1 min")
        self.assertEqual(l.effect.currentText(), "Parpadeo")
        self.assertEqual(l.brightness.currentText(), "5")  # 128
        self.assertEqual(l.speed.currentData(), 3)
        self.assertEqual(l.off_time.currentText(), "1 min")
        self.assertFalse(w._dirty())

        p.lod.setCurrentIndex(p.lod.findData(2))
        p.mode.setCurrentIndex(p.mode.findData(1))
        p.full.setChecked(True)
        l.effect.setCurrentIndex(l.effect.findData(0))   # off keeps the stored mode
        l.off_time.setCurrentIndex(l.off_time.findData(30))
        w.apply_btn.click()
        self.wait_for(lambda: not w._busy)
        changed = [i for i in range(len(self.original)) if self.flash()[i] != self.original[i]]
        self.assertEqual(changed, [0x0A, 0x0B, 0x52, 0x53, 0xAD, 0xAE, 0xB5, 0xB6, 0xB9, 0xBA])
        self.assertEqual(self.flash()[0x52], 0)
        self.assertEqual(self.flash()[0xAD], 30)

    def test_fewer_levels_moves_active(self):
        w = self.window
        w.dpi.rows[3][0].setChecked(True)   # level 4 active
        w.dpi.count.setValue(2)             # must fall back to level 2
        self.assertEqual(w.dpi.active.checkedId(), 1)

    def test_dpi_notification_updates_active(self):
        self.worker._fake.flash[4:6] = bytes([3, 0x52])  # mouse switched to level 4
        self.worker._fake.notify(0x01)
        self.wait_for(lambda: self.window.dpi.active.checkedId() == 3)
        self.assertFalse(self.window._dirty())

    def test_profiles(self):
        import json
        import tempfile
        from m916 import config as cfg
        w = self.window
        with tempfile.TemporaryDirectory() as tmp:
            # Saved from the screen, unapplied edits included
            w.dpi.rows[0][2].setValue(1200)
            saved = os.path.join(tmp, "oficina.json")
            w.save_profile(saved)
            with open(saved) as f:
                self.assertEqual(json.load(f)["dpi"]["levels"][0]["x"], 1200)
            w.revert_btn.click()

            path = os.path.join(tmp, "juegos.json")
            with open(path, "w") as f:
                json.dump({"format": "m916-profile", "version": 1,
                           "model": "Redragon M916-PRO-1K", "report_rate": 500,
                           "buttons": {"7": {"action": "macro", "name": "hola",
                                             "steps": ["+h 20", "-h"]},
                                       "8": {"action": "shortcut",
                                             "events": ["+ctrl", "+a", "-ctrl", "-a"]}}}, f)
            w.load_profile(path)
        # Shown as unapplied changes: nothing written yet
        self.assertEqual(w.performance.rate.currentText(), "500 Hz")
        self.assertEqual(w.buttons.rows[6].macro.name, "hola")
        self.assertTrue(w._dirty())
        self.assertTrue(w.apply_btn.isEnabled())
        self.assertEqual(self.flash(), self.original)
        w.apply_btn.click()
        self.wait_for(lambda: not w._busy)
        flash = self.flash()
        self.assertEqual(flash[0], cfg.RATES[500])
        self.assertEqual(cfg.Macro.decode(flash[cfg.macro_addr(6):]).name, "hola")
        self.assertEqual(cfg.format_steps(cfg.decode_shortcut(flash[cfg.shortcut_addr(7):])),
                         "+ctrl +a -ctrl -a")
        self.assertFalse(w._dirty())

    def test_invalid_profile_changes_nothing(self):
        import json
        import tempfile
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        w = self.window
        with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
            json.dump({"format": "m916-profile", "version": 1, "model": "Redragon M916-PRO-1K",
                       "report_rate": 500, "buttons": {"7": "jump"}}, f)
            f.flush()
            with mock.patch.object(QMessageBox, "warning") as warning:
                w.load_profile(f.name)
        self.assertIn("unknown action 'jump'", warning.call_args[0][2])
        self.assertEqual(w.performance.rate.currentText(), "1000 Hz")
        self.assertFalse(w._dirty())

    def test_app_icon(self):
        from m916 import gui
        self.assertEqual(gui.APP_ID, "io.github.furibeh.m916-linux")
        self.assertFalse(gui.app_icon().isNull())
        self.assertFalse(gui.app_icon().pixmap(48, 48).isNull())


if __name__ == "__main__":
    unittest.main()
