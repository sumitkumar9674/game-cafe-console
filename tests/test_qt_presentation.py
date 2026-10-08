"""Non-destructive Qt/QML integration checks; no Win32 desktop switching."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QUrl
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from game_cafe.qt_bridge import CafeBridge, RecordListModel
from game_cafe.qt_app import AvatarProvider
from game_cafe.storage import Store, new_pool


class QtPresentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "cafe.sqlite3")
        self.host = Mock()
        self.host.console_transitioning = False
        self.runtime = Mock()
        self.bridge = CafeBridge(self.store, self.host, self.runtime)

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

    def test_model_refresh_preserves_row_identity_when_ids_do_not_change(self):
        model = RecordListModel("pcId")
        model.update_rows([{"pcId": "a", "name": "Old"}])
        changed = Mock()
        reset = Mock()
        model.dataChanged.connect(changed)
        model.modelReset.connect(reset)
        model.update_rows([{"pcId": "a", "name": "New"}])
        changed.assert_called_once()
        reset.assert_not_called()
        self.assertEqual(model.data(model.index(0, 0), model.RowRole)["name"], "New")

    def test_card_selection_and_expansion(self):
        self.bridge.pc_model.update_rows([
            {"pcId": "a", "unlockRequested": True},
            {"pcId": "b", "unlockRequested": False},
        ])
        self.bridge.selectFirstUnlock()
        self.assertEqual(self.bridge.selectedPcId, "a")
        self.assertEqual(self.bridge.expandedPcId, "a")
        self.bridge.selectPc("a")
        self.assertEqual(self.bridge.expandedPcId, "")
        self.bridge.selectPc("b")
        self.assertEqual(self.bridge.expandedPcId, "b")
        self.bridge.showAllHistory()
        self.assertTrue(self.bridge.historyAll)

    def test_all_qml_surfaces_load(self):
        self.bridge.pc_model.update_rows([{
            "pcId": "b", "name": "PC-02", "role": "USER", "online": True,
            "access": "LOCKED", "phase": "NONE", "player": "Guest",
            "kind": "none", "detail": "No active session", "timeText": "00:00:00",
            "startedText": "", "unlockRequested": True, "start": True,
            "add": False, "end": False, "lock": True,
        }])
        self.bridge._set_view(cafeName="Example Cafe", onlineCount=1, totalCount=2,
                              unlockRequests=1, settings={"cafeName": "Example Cafe",
                              "adminName": "Admin", "graceMinutes": 10,
                              "signoutMinutes": 30})
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        self.assertEqual(len(engine.rootObjects()), 1)
        root = engine.rootObjects()[0]
        loader = root.findChild(QObject, "mainLoader")
        self.assertIsNotNone(loader)
        # Loading every route catches missing QML imports without creating a real desktop.
        for mode in ("onboarding", "joining", "candidate", "admin", "widget", "console"):
            self.bridge._set_mode(mode)
            self.app.processEvents()
            self.assertEqual(self.bridge.mode, mode)
            self.assertIsNotNone(loader.property("item"), mode)
        password = root.findChild(QObject, "staffPasswordField")
        visibility = root.findChild(QObject, "showStaffPassword")
        self.assertIsNotNone(password)
        self.assertIsNotNone(visibility)
        password.setProperty("text", "temporary test password")
        self.app.processEvents()
        self.assertNotEqual(password.property("displayText"), "temporary test password")
        visibility.setProperty("checked", True)
        self.app.processEvents()
        self.assertEqual(password.property("displayText"), "temporary test password")
        visibility.setProperty("checked", False)
        self.app.processEvents()
        self.assertNotEqual(password.property("displayText"), "temporary test password")

    def test_saved_pool_identity_is_unchanged_by_qt_bridge(self):
        pc_id = self.store.pc_id
        snapshot, secret = new_pool("Cafe", pc_id, "PC-01", "Admin",
                                    "long test password", self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)
        self.bridge._collect_state("", True)
        self.assertEqual(self.store.pc_id, pc_id)
        self.assertEqual(self.store.snapshot()["pool_id"], snapshot["pool_id"])

    def test_customer_unlock_is_queued_once_during_cooldown(self):
        child = CafeBridge(self.store, self.host, child=True)
        try:
            child.requestUnlock()
            child.requestUnlock()
            commands = self.store.take_local_commands()
            self.assertEqual([action for action, _ in commands], ["request_unlock"])
            self.assertIn("Sending", self.store.local("customer_feedback"))
            child.renamePlayer("Alice")
            self.assertEqual(self.store.take_local_commands()[0],
                             ("rename", {"name": "Alice"}))
        finally:
            child.shutdown()

    def test_avatar_provider_fallback_and_shared_image(self):
        provider = AvatarProvider(self.store)
        self.assertTrue(provider.requestImage("avatar", None, QImage().size()).isNull())
        image = QImage(2, 2, QImage.Format_ARGB32)
        image.fill("#35c7c7")
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        snapshot, secret = new_pool("Cafe", self.store.pc_id, "PC-01", "Admin",
                                    "long test password", self.store.node_public_key())
        snapshot["logo"] = base64.b64encode(bytes(payload)).decode("ascii")
        self.store.save_pool(snapshot, secret, join=True)
        self.assertFalse(provider.requestImage("avatar", None, image.size()).isNull())

    def test_avatar_browse_returns_native_selection_without_saving(self):
        with patch("game_cafe.qt_bridge.QFileDialog.getOpenFileName",
                   return_value=("C:/Cafe/logo.jpeg", "Images")):
            self.assertEqual(self.bridge.browseAvatar(), "C:/Cafe/logo.jpeg")
        self.runtime.admin_action.assert_not_called()

    def test_avatar_upload_uses_shared_snapshot_data_not_local_path(self):
        source = Path(self.temp.name) / "avatar.png"
        image = QImage(8, 8, QImage.Format_ARGB32)
        image.fill("#35c7c7")
        self.assertTrue(image.save(str(source)))
        self.bridge.setAvatar(str(source))
        self.wait_for_idle()
        args, kwargs = self.runtime.admin_action.call_args
        self.assertEqual(args, ("logo",))
        self.assertTrue(base64.b64decode(kwargs["logo"]).startswith(b"\x89PNG"))

    def test_unregistered_startup_opens_onboarding(self):
        with patch("game_cafe.qt_bridge.discover", return_value=[]):
            self.bridge.begin()
            self.wait_for_idle()
        self.runtime.start.assert_called_once()
        self.assertEqual(self.bridge.mode, "onboarding")

    def test_saved_pool_without_live_admin_offers_claim_or_user(self):
        snapshot, secret = new_pool("Cafe", self.store.pc_id, "PC-01", "Admin",
                                    "long test password", self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)
        self.runtime.verified_admin_online.return_value = False
        self.bridge.begin()
        self.wait_for_idle()
        self.assertEqual(self.bridge.mode, "candidate")
        self.assertEqual(self.bridge.view["cafeName"], "Cafe")

    def test_invalid_session_action_never_reaches_runtime(self):
        self.bridge.pc_model.update_rows([{"pcId": "admin", "start": False,
                                          "add": False, "end": False, "lock": False}])
        self.bridge.startSession("admin", "timed", "60", "0")
        self.assertTrue(self.bridge.notice["error"])
        self.runtime.admin_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
