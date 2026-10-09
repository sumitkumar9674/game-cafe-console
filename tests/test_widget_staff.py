"""Normal widget and local Staff authorization without desktop switching."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import queue
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QMetaObject, QObject
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from game_cafe.qt_app import Application
from game_cafe.qt_bridge import CafeBridge
from game_cafe import sessions
from game_cafe.runtime import Runtime
from game_cafe.security import create_local_command_key, open_local_password
from game_cafe.storage import Store, hash_password, new_pool


PASSWORD = "long test password"


class WidgetLifecycleTest(unittest.TestCase):
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
        self.runtime.events = queue.Queue()
        self.store_patch = patch("game_cafe.qt_app.Store", return_value=self.store)
        self.runtime_patch = patch("game_cafe.qt_app.Runtime", return_value=self.runtime)
        self.store_patch.start()
        self.runtime_patch.start()
        self.host = Application()
        self.host.bridge._set_view(accessAllowed=True, hasSession=True,
                                   phase="TIMED", timeText="00:42:00")
        self.host.bridge._set_mode("widget")
        self.app.processEvents()

    def tearDown(self):
        self.host.shutting_down = True
        with patch.object(self.host, "stop_console"):
            self.host._cleanup()
        self.runtime_patch.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    def test_hide_reopen_and_refresh_do_not_change_widget_mode(self):
        self.assertEqual(self.host.bridge.mode, "widget")
        with patch.object(self.host, "_show_window", wraps=self.host._show_window) as show:
            self.host.update_widget_access(True)
            self.host.update_widget_access(True)
            show.assert_not_called()
        if self.host.tray.isVisible():
            self.host.hide_widget()
            self.assertFalse(self.host.window.isVisible())
            self.host.update_widget_access(True)
            self.assertFalse(self.host.window.isVisible())
            self.host._show_window()
            self.assertTrue(self.host.window.isVisible())
        self.host.update_widget_access(False)
        self.app.processEvents()
        self.assertEqual(self.host.bridge.mode, "widget")
        self.assertFalse(self.host.window.isVisible())

    def test_widget_has_content_based_height_and_scroll_fallback(self):
        loader = self.host.window.findChild(QObject, "mainLoader")
        content = loader.property("item")
        self.assertGreaterEqual(self.host.window.minimumHeight(),
                                min(int(content.property("implicitHeight")),
                                    self.host.app.primaryScreen().availableGeometry().height() - 40))


class StaffAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "staff.sqlite3")
        snapshot, secret = new_pool(
            "Cafe", self.store.pc_id, "PC-02", "Owner", PASSWORD,
            self.store.node_public_key())
        admin_id = "a" * 32
        snapshot["members"][admin_id] = {
            "name": "PC-01", "public_key": "a" * 64}
        snapshot["active_admin"]["pc_id"] = admin_id
        self.store.save_pool(snapshot, secret, join=True)
        self.private, public = create_local_command_key()
        self.store.set_local("controller_command_public", public)
        self.host = Mock()
        self.host.default_handle = 1
        self.desktop_patch = patch(
            "game_cafe.qt_bridge.desktops.active_desktop_name",
            return_value="CafeConsole")
        self.desktop_patch.start()
        self.bridge = CafeBridge(self.store, self.host, child=True)

    def tearDown(self):
        self.bridge.shutdown()
        self.desktop_patch.stop()
        self.store.close()
        self.temp.cleanup()

    def wait_for_idle(self):
        deadline = time.monotonic() + 5
        while self.bridge.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(self.bridge.busy)

    def authorize(self):
        self.bridge.authenticateStaff(PASSWORD)
        self.wait_for_idle()
        self.assertTrue(self.bridge.staffAuthorized)

    def test_wrong_password_denied_and_direct_actions_fail_closed(self):
        self.bridge.staffStartSession("timed", "60", "0")
        self.assertFalse(self.store.take_local_commands())
        self.bridge.authenticateStaff("incorrect password")
        self.wait_for_idle()
        self.assertFalse(self.bridge.staffAuthorized)
        self.assertEqual(self.bridge.notice["title"],
                         "Staff authentication failed")

    def test_repeated_failures_temporarily_block_staff_login(self):
        self.bridge._staff_failed_attempts = 4
        self.bridge.authenticateStaff("incorrect password")
        self.wait_for_idle()
        self.assertGreater(self.bridge._staff_blocked_until, time.monotonic())
        self.bridge.authenticateStaff(PASSWORD)
        self.assertFalse(self.bridge.busy)
        self.assertFalse(self.bridge.staffAuthorized)
        self.assertEqual(self.bridge.notice["title"],
                         "Staff access temporarily locked")

    def test_authorization_locks_closes_times_out_and_reauthenticates(self):
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        console = root.findChild(QObject, "mainLoader").property("item")
        console.setProperty("staffPanel", "staff")
        self.app.processEvents()
        controls = root.findChild(QObject, "staffControls")
        self.assertIsNotNone(controls)
        self.assertFalse(controls.property("visible"))
        self.authorize()
        self.app.processEvents()
        self.assertTrue(controls.property("visible"))
        self.bridge.lockStaffAccess()
        self.assertFalse(self.bridge.staffAuthorized)
        self.app.processEvents()
        self.assertFalse(controls.property("visible"))
        self.authorize()
        self.bridge._staff_authorized_until = time.monotonic() - 1
        self.bridge.tick()
        self.assertFalse(self.bridge.staffAuthorized)
        self.authorize()
        with patch("game_cafe.qt_bridge.desktops.switch") as switch:
            self.bridge._set_view(accessAllowed=True)
            self.bridge.goDesktop()
        switch.assert_called_once_with(1)
        self.assertFalse(self.bridge.staffAuthorized)

    def test_staff_dropdowns_have_opaque_scrollable_popups(self):
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        console = root.findChild(QObject, "mainLoader").property("item")
        console.setProperty("staffPanel", "staff")
        self.authorize()
        self.app.processEvents()
        for name, count in (("staffSessionKind", 2),
                            ("staffSessionDuration", 5),
                            ("staffAddDuration", 7)):
            combo = root.findChild(QObject, name)
            self.assertIsNotNone(combo)
            self.assertEqual(combo.property("count"), count)
            background = root.findChild(QObject, name + "PopupBackground")
            self.assertIsNotNone(background)
            self.assertEqual(background.property("color").alpha(), 255)
            popup_list = root.findChild(QObject, name + "PopupList")
            self.assertIsNotNone(popup_list)
            self.assertTrue(popup_list.property("clip"))
            popup = root.findChild(QObject, name + "Popup")
            self.assertIsNotNone(popup)
            QMetaObject.invokeMethod(popup, "open")
            self.app.processEvents()
            self.assertTrue(popup.property("visible"))
            self.assertEqual(popup_list.property("count"), count)
            QMetaObject.invokeMethod(popup, "close")

    def test_password_change_invalidates_authorization(self):
        self.authorize()
        self.store.update(lambda state: state.update(
            admin_password=hash_password("different long password")))
        self.assertFalse(self.bridge.staffAuthorized)

    def test_authorized_command_contains_revalidated_encrypted_credential(self):
        self.authorize()
        self.bridge.staffStartSession("timed", "60", "5")
        commands = self.store.take_local_commands()
        self.assertEqual(len(commands), 1)
        action, payload = commands[0]
        self.assertEqual(action, "staff_start")
        self.assertNotIn(PASSWORD, str(payload))
        self.assertEqual(open_local_password(
            self.private, payload["password_sealed"]), PASSWORD)
        self.assertFalse(self.bridge.staffAuthorized)

    def test_active_close_warns_and_cancel_leaves_session_untouched(self):
        session = sessions.start_session("timed", 30, 0)
        self.store.update(lambda state: state["sessions"].update(
            {self.store.pc_id: session}))
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        root.findChild(QObject, "mainLoader").property("item").setProperty(
            "staffPanel", "staff")
        self.authorize()
        self.app.processEvents()
        close = root.findChild(QObject, "staffCloseSoftware")
        close.clicked.emit()
        self.app.processEvents()
        confirmation = root.property("confirmation")
        self.assertEqual(confirmation.property("title").toString(),
                         "Active Session Detected")
        self.assertEqual(confirmation.property("label").toString(),
                         "End Session & Exit")
        self.assertFalse(self.store.take_local_commands())
        QMetaObject.invokeMethod(root, "cancelConfirmation")
        self.assertEqual(self.store.snapshot()["sessions"][self.store.pc_id]["id"],
                         session["id"])
        self.assertFalse(self.store.take_local_commands())
        close.clicked.emit()
        QMetaObject.invokeMethod(root, "acceptConfirmation")
        commands = self.store.take_local_commands()
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], "close_software")
        self.assertFalse(self.bridge.staffAuthorized)

    def test_idle_close_keeps_normal_confirmation_and_requires_staff_auth(self):
        self.bridge.closeSoftware()
        self.assertFalse(self.store.take_local_commands())
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        root.findChild(QObject, "mainLoader").property("item").setProperty(
            "staffPanel", "staff")
        self.authorize()
        self.app.processEvents()
        root.findChild(QObject, "staffCloseSoftware").clicked.emit()
        self.assertEqual(root.property("confirmation").property("title").toString(),
                         "Close software")
        self.assertFalse(self.store.take_local_commands())
        QMetaObject.invokeMethod(root, "acceptConfirmation")
        self.assertEqual(self.store.take_local_commands()[0][0], "close_software")


class LocalStaffSessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.user_store = Store(Path(self.temp.name) / "user.sqlite3")
        self.admin_store = Store(Path(self.temp.name) / "admin.sqlite3")
        snapshot, secret = new_pool(
            "Cafe", self.user_store.pc_id, "PC-02", "Owner", PASSWORD,
            self.user_store.node_public_key())
        snapshot["members"][self.admin_store.pc_id] = {
            "name": "PC-01", "public_key": self.admin_store.node_public_key()}
        snapshot["active_admin"]["pc_id"] = self.admin_store.pc_id
        self.user_store.save_pool(snapshot, secret, join=True)
        self.admin_store.save_pool(snapshot, secret, join=True)
        self.runtime = Runtime(self.user_store)
        self.reconcile = patch.object(self.runtime, "_reconcile_local_access")
        self.reconcile.start()

    def tearDown(self):
        self.reconcile.stop()
        self.user_store.close()
        self.admin_store.close()
        self.temp.cleanup()

    def test_local_timed_start_add_is_idempotent_and_syncable_offline(self):
        start_id = "1" * 32
        self.runtime.local_staff_session_action(
            "start", PASSWORD, start_id, kind="timed",
            paid_minutes=60, buffer_minutes=5)
        session = self.user_store.snapshot()["sessions"][self.user_store.pc_id]
        session_id = session["id"]
        self.runtime.local_staff_session_action(
            "start", PASSWORD, start_id, kind="timed",
            paid_minutes=60, buffer_minutes=5)
        self.assertEqual(self.user_store.snapshot()["sessions"]
                         [self.user_store.pc_id]["id"], session_id)
        self.runtime.local_staff_session_action(
            "add", PASSWORD, "2" * 32, minutes=15)
        updated = self.user_store.snapshot()["sessions"][self.user_store.pc_id]
        self.assertEqual(updated["id"], session_id)
        self.assertEqual(updated["paid_minutes"], 75)
        record = next(item for item in self.user_store.owner_records()
                      if item["payload"]["pc_id"] == self.user_store.pc_id)
        self.assertTrue(self.admin_store.merge_owner_record(record))
        self.assertEqual(self.admin_store.snapshot()["sessions"]
                         [self.user_store.pc_id]["paid_minutes"], 75)

    def test_custom_local_minutes_use_same_session_and_add_once(self):
        self.runtime.local_staff_session_action(
            "start", PASSWORD, "6" * 32, kind="timed",
            paid_minutes=37, buffer_minutes=3)
        before = self.user_store.snapshot()["sessions"][self.user_store.pc_id]
        self.runtime.local_staff_session_action(
            "add", PASSWORD, "7" * 32, minutes=23)
        self.runtime.local_staff_session_action(
            "add", PASSWORD, "7" * 32, minutes=23)
        after = self.user_store.snapshot()["sessions"][self.user_store.pc_id]
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["paid_minutes"], 60)
        self.assertEqual(after["buffer_minutes"], 3)

    def test_local_no_timer_and_invalid_operations(self):
        self.runtime.local_staff_session_action(
            "start", PASSWORD, "3" * 32, kind="open",
            paid_minutes=0, buffer_minutes=0)
        session = self.user_store.snapshot()["sessions"][self.user_store.pc_id]
        self.assertEqual(session["kind"], "open")
        with self.assertRaises(ValueError):
            self.runtime.local_staff_session_action(
                "add", PASSWORD, "4" * 32, minutes=15)
        with self.assertRaises(PermissionError):
            self.runtime.local_staff_session_action(
                "add", "incorrect password", "5" * 32, minutes=15)


if __name__ == "__main__":
    unittest.main()
