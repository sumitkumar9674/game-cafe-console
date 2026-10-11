"""Offscreen registered-customer Console UI checks; no desktop switching."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QMetaObject, QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from game_cafe.qt_bridge import CafeBridge
from game_cafe.security import create_local_command_key
from game_cafe.storage import Store, new_pool


class CustomerConsoleUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "console.sqlite3")
        snapshot, secret = new_pool(
            "Cafe", self.store.pc_id, "PC-02", "Sahil",
            "long test password", self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)
        self.customer = self.store.create_customer(
            "Sumit", "Sahil", "create")
        private, public = create_local_command_key()
        self.store.set_local("controller_command_public", public)
        self.store.set_local("customer_admin_online", "1")
        self.host = Mock()
        self.host.command_private_key = private
        self.bridge = CafeBridge(self.store, self.host, child=True)
        self.engine = QQmlApplicationEngine()
        self.engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        self.engine.load(QUrl.fromLocalFile(str(qml)))
        self.root = self.engine.rootObjects()[0]
        self.console = self.root.findChild(QObject, "gamingControlsCard")
        self._refresh_view()

    def tearDown(self):
        self.bridge.shutdown()
        self.store.close()
        self.temp.cleanup()

    def _refresh_view(self):
        state = self.bridge._collect_state("", True)
        self.bridge.customer_model.update_rows(state["customers"])
        for key in ("snapshot", "rows", "history", "dashboardHistory",
                    "joins", "members", "historyTargets", "customers",
                    "customerAdjustments", "session"):
            state.pop(key, None)
        self.bridge._set_view(**state)
        self.app.processEvents()

    def _set_registered(self, active=False, cooldown_expires_at=0):
        self.store.save_local_customer_session({
            "customer_id": self.customer["customer_id"], "name": "Sumit",
            "login_id": "login", "balance_seconds": 120, "revision": 1,
            "retainer_session_id": "retainer" if active else "",
            "retainer_active": active,
            "retainer_state": "active" if active else "",
            "displayed_at": 10_000_000_000.0, "last_admin_seen": 10_000_000_000.0,
            "retainer_cooldown_expires_at": cooldown_expires_at,
            "login_cooldown_expires_at": 0,
            "feedback": "",
        })
        self._refresh_view()

    def test_guest_default_search_and_login_password_is_sealed(self):
        self.assertTrue(self.bridge.view["guestMode"])
        self.assertIsNone(self.root.findChild(QObject, "widgetPlayerName"))
        select = self.root.findChild(QObject, "selectCustomerAccountButton")
        select.clicked.emit()
        self.app.processEvents()
        popup = self.root.findChild(QObject, "customerAccountPopup")
        self.assertTrue(popup.property("visible"))
        search = self.root.findChild(QObject, "customerAccountSearch")
        search.setProperty("text", "sum")
        self.bridge.setCustomerSearch("sum")
        self._refresh_view()
        self.assertEqual(self.bridge.customer_model.rowCount(), 1)
        self.bridge.setCustomerSearch("missing")
        self._refresh_view()
        self.assertEqual(self.bridge.customer_model.rowCount(), 0)

        self.bridge.loginCustomer(self.customer["customer_id"], "12345")
        action, payload = self.store.take_local_commands()[0]
        self.assertEqual(action, "customer_login")
        self.assertEqual(payload["customer_id"], self.customer["customer_id"])
        self.assertNotIn("password", payload)
        self.assertNotIn("12345", str(payload))
        self.assertIn("password_sealed", payload)

    def test_retainer_start_uses_two_confirmations_and_stop_uses_none(self):
        self._set_registered()
        play = self.root.findChild(QObject, "playUsingRetainerButton")
        play.clicked.emit()
        self.app.processEvents()
        confirmation = self.root.property("confirmation")
        self.assertEqual(confirmation.property("title").toString(),
                         "Start Retainer Session?")
        QMetaObject.invokeMethod(self.root, "cancelConfirmation")
        self.assertEqual(self.store.take_local_commands(), [])

        play.clicked.emit()
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.assertEqual(
            self.root.property("confirmation").property("title").toString(),
            "Confirm Retainer Play")
        QMetaObject.invokeMethod(self.root, "cancelConfirmation")
        self.assertEqual(
            self.root.property("confirmation").property("title").toString(),
            "Start Retainer Session?")
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        action, _payload = self.store.take_local_commands()[0]
        self.assertEqual(action, "retainer_start")

        self._set_registered(active=True)
        stop = self.root.findChild(QObject, "stopRetainerConsoleButton")
        self.assertTrue(stop.property("visible"))
        stop.clicked.emit()
        self.app.processEvents()
        self.assertTrue(
            self.root.property("confirmation").property("title").isUndefined())
        action, _payload = self.store.take_local_commands()[0]
        self.assertEqual(action, "retainer_stop")

    def test_password_fields_are_sealed_and_guest_name_edit_is_absent(self):
        self._set_registered()
        self.assertIsNone(self.root.findChild(QObject, "widgetUpdateButton"))
        self.bridge.changeCustomerPassword(
            "12345", "new-password", "new-password")
        action, payload = self.store.take_local_commands()[0]
        self.assertEqual(action, "customer_password")
        self.assertNotIn("12345", str(payload))
        self.assertNotIn("new-password", str(payload))
        self.assertIn("current_password_sealed", payload)
        self.assertIn("new_password_sealed", payload)
        self.assertIn("confirmation_sealed", payload)

    def test_live_cooldowns_disable_login_and_retainer_start(self):
        expires = int(time.time()) + 90
        self._set_registered(cooldown_expires_at=expires)
        play = self.root.findChild(QObject, "playUsingRetainerButton")
        message = self.root.findChild(QObject,
                                      "retainerRestartCooldownMessage")
        self.assertFalse(play.property("enabled"))
        self.assertTrue(message.property("visible"))
        self.assertIn("Cooldown Active", message.property("text"))

        self.store.clear_local_customer_session()
        self._refresh_view()
        select = self.root.findChild(QObject, "selectCustomerAccountButton")
        select.clicked.emit()
        self.app.processEvents()
        popup = self.root.findChild(QObject, "customerAccountPopup")
        console_page = popup.parent()
        console_page.setProperty("selectedAccountId", self.customer["customer_id"])
        console_page.setProperty("selectedAccountName", "Sumit")
        console_page.setProperty("selectedLoginCooldownExpiresAt", expires)
        console_page.setProperty("accountPanel", "password")
        self.app.processEvents()
        password = self.root.findChild(QObject, "customerLoginPassword")
        login = self.root.findChild(QObject, "customerLoginButton")
        login_message = self.root.findChild(
            QObject, "customerLoginCooldownMessage")
        self.assertFalse(password.property("enabled"))
        self.assertFalse(login.property("enabled"))
        self.assertTrue(login_message.property("visible"))

    def test_locked_notice_waits_for_cafe_console_and_is_shown_once(self):
        self.store.post_locked_notice("Retainer Stopped", "Saved.")
        with patch("game_cafe.qt_bridge.desktops.active_desktop_name",
                   return_value="Default"):
            self.bridge.tick()
        self.assertEqual(self.bridge.notice, {})
        self.assertIsNotNone(self.store.local("locked_console_notice"))

        with patch("game_cafe.qt_bridge.desktops.active_desktop_name",
                   return_value="CafeConsole"):
            self.bridge.tick()
        self.assertEqual(self.bridge.notice["title"], "Retainer Stopped")
        self.assertEqual(self.bridge.notice["message"], "Saved.")
        self.assertIsNone(self.store.local("locked_console_notice"))
        with patch("game_cafe.qt_bridge.desktops.active_desktop_name",
                   return_value="CafeConsole"):
            self.bridge.tick()
        self.assertEqual(self.bridge.notice["title"], "Retainer Stopped")

    def test_three_sections_fit_supported_resolutions(self):
        account = self.root.findChild(QObject, "customerAccountBar")
        staff = self.root.findChild(QObject, "staffAccessPanel")
        for width, height in ((1366, 768), (1920, 1080)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            self.app.processEvents()
            for control in (account, self.console, staff):
                self.assertLessEqual(
                    control.property("y") + control.property("height"), height)


if __name__ == "__main__":
    unittest.main()
