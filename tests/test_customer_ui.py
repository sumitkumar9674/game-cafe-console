"""Offscreen Stage 2 Admin Users UI checks; no desktop switching."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import queue
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QMetaObject, QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from game_cafe.qt_bridge import CafeBridge
from game_cafe.storage import Store, new_pool


class CustomerUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "ui.sqlite3")
        snapshot, secret = new_pool(
            "Cafe", self.store.pc_id, "PC-01", "Sahil",
            "long test password", self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)
        self.host = Mock()
        self.host.console_transitioning = False
        self.runtime = Mock()
        self.runtime.events = queue.Queue()
        self.runtime.admin_ui_open = True
        self.runtime.is_admin.return_value = True
        self.runtime.status.return_value = {"desktop": "Default"}
        self.runtime.peer_status.return_value = None
        self.account = {
            "customer_id": "c" * 32, "name": "Sumit",
            "balance_seconds": 3600, "created_at": 10.0,
            "modified_at": 20.0, "modified_by": "Sahil", "revision": 2,
            "status": "Not logged in", "syncStatus": "Synced",
            "syncPeers": [],
        }
        self.runtime.customer_admin_state.return_value = {
            "accounts": [self.account], "selected": None, "adjustments": []}
        self.runtime.create_customer.return_value = self.account
        self.runtime.preview_customer_adjustment.return_value = {
            "customerId": self.account["customer_id"], "name": "Sumit",
            "action": "add", "seconds": 5400, "previousBalance": 3600,
            "resultingBalance": 9000, "revision": 2,
            "operationId": "operation-1",
        }
        self.runtime.adjust_customer.return_value = {
            **self.account, "balance_seconds": 9000, "revision": 3}
        self.runtime.delete_customer.return_value = 3
        self.bridge = CafeBridge(self.store, self.host, self.runtime)
        self.bridge._set_mode("admin")
        self.engine = QQmlApplicationEngine()
        self.engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        self.engine.load(QUrl.fromLocalFile(str(qml)))
        self.root = self.engine.rootObjects()[0]
        self.root.setWidth(1366)
        self.root.setHeight(768)
        self.admin = self.root.findChild(QObject, "adminPage")
        self.admin.setProperty("section", "Users")
        self.app.processEvents()

    def tearDown(self):
        self.bridge.shutdown()
        self.store.close()
        self.temp.cleanup()

    def wait_for_idle(self):
        deadline = time.monotonic() + 5
        while self.bridge.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(self.bridge.busy)

    def _confirmation(self):
        return self.root.property("confirmation")

    def _show_selected_customer(self):
        row = self.bridge._customer_row(self.account)
        self.bridge._selected_customer = self.account["customer_id"]
        self.bridge.customer_model.update_rows([row])
        self.bridge._set_view(
            selectedCustomer=row, customerCount=1,
            customerAdjustmentCount=0)
        self.bridge.selectionChanged.emit()
        self.app.processEvents()

    def test_users_page_create_confirmation_and_responsive_layout(self):
        self.assertIsNotNone(self.root.findChild(QObject, "customerListPanel"))
        details = self.root.findChild(QObject, "customerDetailsPanel")
        self.assertIsNotNone(details)
        self.assertLessEqual(
            details.property("y") + details.property("height"),
            self.admin.property("height"))
        name = self.root.findChild(QObject, "newCustomerNameField")
        create = self.root.findChild(QObject, "createCustomerButton")
        name.setProperty("text", "  Sumit   Kumar  ")
        create.clicked.emit()
        self.app.processEvents()
        confirmation = self._confirmation()
        self.assertEqual(confirmation.property("title").toString(),
                         "Create Customer")
        message = confirmation.property("message").toString()
        self.assertIn("Name: Sumit Kumar", message)
        self.assertIn("Default Password: 12345", message)
        self.assertIn("Retainer Balance: 00:00:00", message)
        QMetaObject.invokeMethod(self.root, "cancelConfirmation")
        self.runtime.create_customer.assert_not_called()

        name.setProperty("text", "Sumit Kumar")
        create.clicked.emit()
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.wait_for_idle()
        self.runtime.create_customer.assert_called_once()
        self.assertEqual(self.runtime.create_customer.call_args.args[0],
                         "Sumit Kumar")

    def test_active_retainer_disables_balance_adjustments(self):
        self.account["retainerInProgress"] = True
        self.account["retainerActive"] = True
        self._show_selected_customer()
        add = self.root.findChild(QObject, "addRetainerButton")
        reduce = self.root.findChild(QObject, "reduceRetainerButton")
        message = self.root.findChild(
            QObject, "activeRetainerAdjustmentMessage")
        self.assertFalse(add.property("enabled"))
        self.assertFalse(reduce.property("enabled"))
        self.assertTrue(message.property("visible"))
        self.assertIn("End the session", message.property("text"))

    def test_add_time_uses_two_confirmations_and_commits_once(self):
        self._show_selected_customer()
        hours = self.root.findChild(QObject, "retainerAddHours")
        minutes = self.root.findChild(QObject, "retainerAddMinutes")
        add = self.root.findChild(QObject, "addRetainerButton")
        hours.setProperty("text", "1")
        minutes.setProperty("text", "30")
        add.clicked.emit()
        self.app.processEvents()
        self.assertEqual(self._confirmation().property("title").toString(),
                         "Add Retainer Time")
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.assertEqual(self._confirmation().property("title").toString(),
                         "Confirm Add Time")
        QMetaObject.invokeMethod(self.root, "cancelConfirmation")
        self.assertEqual(self._confirmation().property("title").toString(),
                         "Add Retainer Time")
        self.runtime.adjust_customer.assert_not_called()
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.wait_for_idle()
        self.runtime.adjust_customer.assert_called_once()

    def test_reduce_time_uses_the_same_two_confirmation_flow(self):
        self._show_selected_customer()
        self.runtime.preview_customer_adjustment.return_value = {
            "customerId": self.account["customer_id"], "name": "Sumit",
            "action": "reduce", "seconds": 1800, "previousBalance": 3600,
            "resultingBalance": 1800, "revision": 2,
            "operationId": "operation-reduce",
        }
        hours = self.root.findChild(QObject, "retainerReduceHours")
        minutes = self.root.findChild(QObject, "retainerReduceMinutes")
        reduce_button = self.root.findChild(QObject, "reduceRetainerButton")
        hours.setProperty("text", "0")
        minutes.setProperty("text", "30")
        reduce_button.clicked.emit()
        self.app.processEvents()
        self.assertEqual(self._confirmation().property("title").toString(),
                         "Reduce Retainer Time")
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.assertEqual(self._confirmation().property("title").toString(),
                         "Confirm Reduce Time")
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.wait_for_idle()
        self.runtime.adjust_customer.assert_called_once()
        self.assertEqual(
            self.runtime.preview_customer_adjustment.call_args.args[1:],
            ("reduce", "0", "30"))

    def test_delete_requires_two_steps_and_exact_display_name(self):
        self._show_selected_customer()
        delete = self.root.findChild(QObject, "deleteCustomerButton")
        delete.clicked.emit()
        self.app.processEvents()
        self.assertEqual(self._confirmation().property("title").toString(),
                         "Delete User")
        QMetaObject.invokeMethod(self.root, "cancelConfirmation")
        self.runtime.delete_customer.assert_not_called()

        delete.clicked.emit()
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.app.processEvents()
        self.assertEqual(self._confirmation().property("title").toString(),
                         "Confirm Permanent Deletion")
        QMetaObject.invokeMethod(self.root, "cancelConfirmation")
        self.assertEqual(self._confirmation().property("title").toString(),
                         "Delete User")
        self.runtime.delete_customer.assert_not_called()
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        field = self.root.findChild(QObject, "confirmationExactNameInput")
        accept = self.root.findChild(QObject, "confirmationAcceptButton")
        field.setProperty("text", "sumit")
        self.app.processEvents()
        self.assertFalse(accept.property("enabled"))
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.runtime.delete_customer.assert_not_called()
        field.setProperty("text", "Sumit")
        self.app.processEvents()
        self.assertTrue(accept.property("enabled"))
        QMetaObject.invokeMethod(self.root, "acceptConfirmation")
        self.wait_for_idle()
        self.runtime.delete_customer.assert_called_once()
        self.assertEqual(self.runtime.delete_customer.call_args.args[:3],
                         (self.account["customer_id"], "Sumit", 2))

    def test_customer_cooldown_controls_route_by_immutable_customer_id(self):
        account = {
            **self.account,
            "retainer_cooldown_minutes": None,
            "login_cooldown_minutes": None,
            "retainerCooldownMinutes": 10,
            "loginCooldownMinutes": 5,
            "retainerCooldownExpiresAt": int(time.time()) + 60,
            "loginCooldownExpiresAt": int(time.time()) + 60,
        }
        self.account = account
        self.runtime.customer_admin_state.return_value = {
            "accounts": [account], "selected": account, "adjustments": []}
        self._show_selected_customer()
        retainer = self.root.findChild(
            QObject, "customerRetainerCooldownOverride")
        login = self.root.findChild(QObject, "customerLoginCooldownOverride")
        save = self.root.findChild(
            QObject, "saveCustomerCooldownOverridesButton")
        self.assertIsNotNone(retainer)
        self.assertIsNotNone(login)
        retainer.setProperty("text", "0")
        login.setProperty("text", "12")
        save.clicked.emit()
        self.wait_for_idle()
        self.runtime.set_customer_cooldown_overrides.assert_called_once_with(
            self.account["customer_id"], 0, 12, self.account["revision"])

        clear = self.root.findChild(QObject, "clearLoginCooldownButton")
        self.assertTrue(clear.property("visible"))
        clear.clicked.emit()
        self.wait_for_idle()
        self.runtime.clear_customer_cooldown.assert_called_once_with(
            self.account["customer_id"], "login")

    def test_settings_expose_global_cooldowns_and_wallpaper_controls(self):
        self.admin.setProperty("section", "Settings")
        self.app.processEvents()
        retainer = self.root.findChild(
            QObject, "retainerRestartCooldownMinutes")
        login = self.root.findChild(QObject, "customerLoginCooldownMinutes")
        save = self.root.findChild(QObject, "saveCustomerAccessSettingsButton")
        self.assertIsNotNone(retainer)
        self.assertIsNotNone(login)
        self.assertIsNotNone(self.root.findChild(QObject, "wallpaperPreview"))
        self.assertIsNotNone(self.root.findChild(QObject, "saveWallpaperButton"))
        self.assertIsNotNone(self.root.findChild(QObject, "resetWallpaperButton"))
        retainer.setProperty("text", "15")
        login.setProperty("text", "5")
        save.clicked.emit()
        self.wait_for_idle()
        self.runtime.admin_action.assert_called_with(
            "customer_access_settings", retainer_minutes=15,
            login_minutes=5)


if __name__ == "__main__":
    unittest.main()
