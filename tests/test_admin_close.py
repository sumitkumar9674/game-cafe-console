"""Admin close stays in QML until confirmed; no Win32 desktop actions."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QMetaObject, Qt, QTimer
from PySide6.QtWidgets import QApplication

from game_cafe.qt_app import Application
from game_cafe.runtime import Runtime
from game_cafe.storage import Store


class AdminCloseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "cafe.sqlite3")
        self.runtime = Mock()
        self.runtime.default_handle = None
        self.runtime.console_handle = None
        self.runtime.console_available = False
        self.store_patch = patch("game_cafe.qt_app.Store", return_value=self.store)
        self.runtime_patch = patch("game_cafe.qt_app.Runtime", return_value=self.runtime)
        self.store_patch.start()
        self.runtime_patch.start()
        self.host = Application()
        self.host.bridge._set_mode("admin")
        self.app.processEvents()
        self.cleaned = False

    def tearDown(self):
        if not self.cleaned:
            self.host.shutting_down = True
            with patch.object(self.host, "stop_console"):
                self.host._cleanup()
        self.runtime_patch.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    def test_x_opens_confirmation_and_cancel_keeps_window(self):
        self.assertFalse(self.host.tray.isVisible())
        self.host.window.close()
        self.app.processEvents()
        self.assertTrue(self.host.window.isVisible())
        confirmation = self.host.window.property("confirmation")
        self.assertEqual(confirmation.property("label").toString(),
                         "Exit Application")
        self.assertIn("Existing sessions on other User PCs will continue",
                      confirmation.property("message").toString())
        self.assertTrue(QMetaObject.invokeMethod(self.host.window,
                                                 "cancelConfirmation"))
        self.app.processEvents()
        self.assertFalse(self.host.window.property("confirmation").property("title").isString())
        self.assertTrue(self.host.window.isVisible())
        self.assertFalse(self.host.shutting_down)

    def test_confirm_runs_full_cleanup_without_tray_interception(self):
        self.host.window.close()
        self.app.processEvents()
        self.assertTrue(QMetaObject.invokeMethod(self.host.window,
                                                 "acceptConfirmation"))
        self.app.processEvents()
        self.assertTrue(self.host.shutting_down)
        self.assertFalse(self.host.tray.isVisible())
        with patch.object(self.host, "stop_console") as stop_console:
            self.host._cleanup()
        self.cleaned = True
        stop_console.assert_called_once()
        self.runtime.release_admin.assert_called_once()
        self.runtime.stop.assert_called_once()
        self.runtime.flush_owner_record.assert_called_once()
        self.assertFalse(self.host.tray.isVisible())
        self.assertFalse(self.host.bridge.tick_timer.isActive())
        self.assertFalse(self.host.bridge.refresh_timer.isActive())
        with self.assertRaises(sqlite3.ProgrammingError):
            self.store.db.execute("SELECT 1")

    def test_confirm_exits_the_qt_event_loop(self):
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(self.app.quit)
        watchdog.start(1500)
        QTimer.singleShot(0, self.host.window.close)
        QTimer.singleShot(20, lambda: QMetaObject.invokeMethod(
            self.host.window, "acceptConfirmation"))
        with patch.object(self.host.bridge, "begin"):
            self.host.run()
        watchdog.stop()
        self.cleaned = True
        self.assertTrue(self.host.shutting_down)
        self.runtime.release_admin.assert_called_once()
        self.runtime.stop.assert_called_once()

    def test_admin_minimize_does_not_activate_tray_or_shutdown(self):
        self.host.window.showMinimized()
        self.app.processEvents()
        self.assertTrue(self.host.window.windowState() & Qt.WindowMinimized)
        self.assertFalse(self.host.tray.isVisible())
        self.assertFalse(self.host.shutting_down)
        self.assertFalse(self.host.window.property("confirmation").property("title").isString())


class RuntimeShutdownTest(unittest.TestCase):
    def test_stop_waits_for_background_worker(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder) / "cafe.sqlite3")
            try:
                runtime = Runtime(store)
                runtime.network.stop = Mock()
                worker = threading.Thread(target=lambda: runtime.stop_event.wait(5))
                runtime.worker = worker
                worker.start()
                runtime.stop()
                self.assertFalse(worker.is_alive())
                runtime.network.stop.assert_called_once()
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
