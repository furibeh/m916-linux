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
        self.assertEqual(w.buttons.rows[11].value(), (4, 10, 3))
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


if __name__ == "__main__":
    unittest.main()
