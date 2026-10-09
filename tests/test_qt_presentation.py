"""Non-destructive Qt/QML integration checks; no Win32 desktop switching."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import base64
from pathlib import Path
import queue
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QUrl
from PySide6.QtGui import QIcon, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from game_cafe import sessions
from game_cafe.branding import APP_ICON_PATH, APP_LOGO_PATH, app_logo_source, valid_image
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
        self.host.console_process = None
        self.runtime = Mock()
        self.runtime.events = queue.Queue()
        self.runtime.confirmed_remote_admin_online.return_value = False
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

    def test_dashboard_history_waits_for_an_immutable_pc_selection(self):
        snapshot, secret = new_pool(
            "Cafe", self.store.pc_id, "PC-01", "Admin",
            "long test password", self.store.node_public_key())
        pc_id = "b" * 32
        snapshot["members"][pc_id] = {
            "name": "PC-02", "public_key": "b" * 64}
        session = sessions.start_session("timed", 30, 0, now=time.time() - 60)
        snapshot["history"][pc_id] = [sessions.finish_session(
            session, pc_id, "PC-02", "admin", time.time())]
        self.store.save_pool(snapshot, secret, join=True)
        self.runtime.status.return_value = {"desktop": "Default"}
        self.runtime.peer_status.return_value = None
        self.runtime.is_admin.return_value = True
        initial = self.bridge._collect_state("", True)
        self.assertEqual(initial["dashboardHistory"], [])
        self.assertEqual(len(initial["history"]), 1)
        selected = self.bridge._collect_state(pc_id, False)
        self.assertEqual(len(selected["dashboardHistory"]), 1)
        self.assertEqual(selected["dashboardHistory"][0]["pcName"], "PC-02")

    def test_sorting_preserves_selection_expansion_and_command_target(self):
        rows = [
            {"pcId": "pc-10", "name": "PC-10", "unlockRequested": False,
             "start": True},
            {"pcId": "pc-2", "name": "PC-02", "unlockRequested": False,
             "start": True},
        ]
        self.bridge.pc_model.update_rows(rows)
        with patch.object(self.bridge, "refresh"):
            self.bridge.selectPc("pc-10")
            self.bridge.pc_model.update_rows(list(reversed(rows)))
            self.bridge.setComputerSort("name")
            self.wait_for_idle()
            self.assertEqual(self.bridge.selectedPcId, "pc-10")
            self.assertEqual(self.bridge.expandedPcId, "pc-10")
            self.runtime.admin_action.assert_called_once_with(
                "computer_sort", option="name")
            self.runtime.admin_action.reset_mock()
            self.bridge.startSession("pc-10", "timed", "60", "0")
            self.wait_for_idle()
        self.runtime.admin_action.assert_called_once_with(
            "start", "pc-10", kind="timed", paid_minutes=60,
            buffer_minutes=0)

    def test_admin_custom_start_and_add_target_immutable_pc_id(self):
        self.bridge.pc_model.update_rows([{
            "pcId": "target-id", "name": "PC-02", "start": True, "add": True,
        }])
        with patch.object(self.bridge, "refresh"):
            self.bridge.startSession("target-id", "timed", "37", "3")
            self.wait_for_idle()
            self.runtime.admin_action.assert_called_with(
                "start", "target-id", kind="timed", paid_minutes=37,
                buffer_minutes=3)
            self.runtime.admin_action.reset_mock()
            self.bridge.addTime("target-id", 23)
            self.wait_for_idle()
        self.runtime.admin_action.assert_called_once_with(
            "add", "target-id", minutes=23)

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
        for mode in ("onboarding", "joining", "candidate", "admin", "widget",
                     "console"):
            self.bridge._set_mode(mode)
            self.app.processEvents()
            self.assertEqual(self.bridge.mode, mode)
            self.assertIsNotNone(loader.property("item"), mode)
        password = root.findChild(QObject, "staffPasswordField")
        self.assertIsNotNone(password)
        password.setProperty("text", "temporary test password")
        self.app.processEvents()
        self.assertNotEqual(password.property("displayText"), "temporary test password")
        password.setProperty("passwordVisible", True)
        self.app.processEvents()
        self.assertEqual(password.property("displayText"), "temporary test password")
        password.setProperty("passwordVisible", False)
        self.app.processEvents()
        self.assertNotEqual(password.property("displayText"), "temporary test password")

    def test_gamegrid_artwork_loads_in_existing_logo_container(self):
        self.assertTrue(valid_image(APP_LOGO_PATH))
        icon = QIcon(str(APP_ICON_PATH))
        self.assertFalse(icon.isNull())
        self.assertIn(16, [size.width() for size in icon.availableSizes()])
        self.assertIn(256, [size.width() for size in icon.availableSizes()])
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        logo = root.findChild(QObject, "gamegridLogoImage")
        self.assertIsNotNone(logo)
        self.assertEqual(logo.property("source").toString(), app_logo_source())
        deadline = time.monotonic() + 2
        while not logo.property("visible") and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(logo.property("visible"))
        self.assertEqual(root.property("title"), "GameGrid")

    def test_gamegrid_branding_fits_splash_and_about_at_desktop_sizes(self):
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        for width, height, minimum in ((1920, 1080, 300), (2560, 1440, 400)):
            root.setWidth(width)
            root.setHeight(height)
            self.app.processEvents()
            logo = root.findChild(QObject, "gamegridLogoImage")
            self.assertGreaterEqual(logo.width(), minimum)
            self.assertLessEqual(logo.width(), 420)
            self.assertEqual(logo.width(), logo.height())
        self.bridge._set_mode("admin")
        self.app.processEvents()
        admin = root.findChild(QObject, "adminPage")
        admin.setProperty("section", "About")
        self.app.processEvents()
        logos = admin.findChildren(QObject, "gamegridLogoImage")
        self.assertEqual([round(logo.width()) for logo in logos], [180])

    def test_admin_sidebar_uses_cafe_avatar_and_name(self):
        self.bridge._set_view(cafeName="Sumit Cafe", hasAvatar=False,
                              avatarSource="")
        self.bridge._set_mode("admin")
        engine = QQmlApplicationEngine()
        engine.addImageProvider("cafe", AvatarProvider(self.store))
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        admin = root.findChild(QObject, "adminPage")
        avatar = admin.findChild(QObject, "adminCafeAvatar")
        name = admin.findChild(QObject, "adminCafeName")
        self.assertEqual(avatar.property("diameter"), 78)
        self.assertEqual(name.property("text"), "Sumit Cafe")
        self.assertEqual(admin.findChildren(QObject, "gamegridLogoImage"), [])
        image = next(child for child in avatar.findChildren(QObject)
                     if child.property("source") is not None)
        initials = next(child for child in avatar.findChildren(QObject)
                        if child.property("text") == "SU")
        self.assertIsNotNone(initials)
        self.assertFalse(image.property("visible"))

        snapshot, secret = new_pool("Sumit Cafe", self.store.pc_id, "PC-01",
                                    "Admin", "long test password",
                                    self.store.node_public_key())
        previous_source = ""
        for color in ("#D85288", "#64BCC1"):
            picture = QImage(8, 8, QImage.Format_ARGB32)
            picture.fill(color)
            payload = QByteArray()
            buffer = QBuffer(payload)
            buffer.open(QIODevice.WriteOnly)
            self.assertTrue(picture.save(buffer, "PNG"))
            buffer.close()
            snapshot["logo"] = base64.b64encode(bytes(payload)).decode("ascii")
            self.store.save_pool(snapshot, secret, join=True)
            source = self.bridge._branding(self.store.snapshot())["avatarSource"]
            self.assertTrue(source.startswith("image://cafe/avatar/"))
            self.assertNotEqual(source, previous_source)
            self.bridge._set_view(hasAvatar=True, avatarSource=source)
            deadline = time.monotonic() + 2
            while (not image.property("visible") or
                   image.property("source").toString() != source) and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertEqual(image.property("source").toString(), source)
            self.assertTrue(image.property("visible"))
            self.assertEqual(name.property("text"), "Sumit Cafe")
            snapshot["revision"] += 1
            previous_source = source
        self.bridge._set_view(cafeName="New Cafe", hasAvatar=False,
                              avatarSource="")
        self.app.processEvents()
        self.assertEqual(name.property("text"), "New Cafe")
        self.assertEqual(initials.property("text"), "NE")
        self.assertFalse(image.property("visible"))

    def test_gamegrid_timers_follow_existing_phase_in_every_surface(self):
        self.bridge._set_view(hasSession=True, phase="BUFFER", timeText="00:04:59")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        colors = {
            "BUFFER": "#FACC15", "TIMED": "#64BCC1",
            "OPEN": "#64BCC1", "PAUSED": "#B9A4D7", "GRACE": "#F2A65A",
        }
        # ListView creates Admin card delegates only when exposed on screen.
        admin_qml = qml.with_name("Admin.qml").read_text(encoding="utf-8")
        self.assertIn('objectName: "adminSessionTimer"', admin_qml)
        self.assertIn('card.rowData.phase === "BUFFER" ? theme.buffer', admin_qml)
        for mode, name in (("console", "consoleSessionTimer"),
                           ("widget", "widgetSessionTimer")):
            self.bridge._set_mode(mode)
            self.app.processEvents()
            timer = root.findChild(QObject, name)
            self.assertIsNotNone(timer, mode)
            for phase, expected in colors.items():
                with self.subTest(mode=mode, phase=phase):
                    self.bridge._set_view(phase=phase)
                    self.app.processEvents()
                    self.assertEqual(timer.property("color").name().upper(), expected)

    def test_settings_history_clear_requires_two_confirmations(self):
        owner = self.store.pc_id
        self.bridge._set_view(historyTargets=[
            {"pcId": owner, "name": "PC-01", "count": 3, "online": True},
            {"pcId": "offline", "name": "PC-02", "count": 2, "online": False},
        ])
        self.bridge._set_mode("admin")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        page = root.findChild(QObject, "adminPage")
        page.setProperty("section", "Settings")
        self.app.processEvents()
        selector = root.findChild(QObject, "historyPcSelector")
        clear = root.findChild(QObject, "clearSelectedHistory")
        self.assertIsNotNone(selector)
        self.assertIsNotNone(clear)
        popup = root.findChild(QObject, "historyPcSelectorPopup")
        popup.metaObject().invokeMethod(popup, "open")
        self.app.processEvents()
        self.assertEqual(selector.property("count"), 2)
        self.assertEqual(root.findChild(QObject, "historyPcSelectorPopupList")
                         .property("count"), 2)
        popup.metaObject().invokeMethod(popup, "close")
        page.setProperty("historyTargetId", "offline")
        self.app.processEvents()
        self.assertFalse(clear.property("enabled"))
        page.setProperty("historyTargetId", owner)
        self.app.processEvents()
        self.assertTrue(clear.property("enabled"))
        with patch.object(self.bridge, "clearHistory") as submit:
            clear.clicked.emit()
            self.app.processEvents()
            submit.assert_not_called()
            root.metaObject().invokeMethod(root, "cancelConfirmation")
            self.app.processEvents()
            submit.assert_not_called()
            clear.clicked.emit()
            page.setProperty("historyTargetId", "offline")
            root.metaObject().invokeMethod(root, "acceptConfirmation")
            self.app.processEvents()
            submit.assert_not_called()
            page.setProperty("historyTargetId", owner)
            clear.clicked.emit()
            root.metaObject().invokeMethod(root, "acceptConfirmation")
            self.app.processEvents()
            submit.assert_not_called()
            root.metaObject().invokeMethod(root, "acceptConfirmation")
            self.app.processEvents()
            submit.assert_called_once_with(owner)

    def test_admin_password_is_hidden_and_can_be_revealed(self):
        self.bridge._set_mode("candidate")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        password = root.findChild(QObject, "adminPasswordField")
        self.assertIsNotNone(password)
        password.setProperty("text", "temporary test password")
        self.app.processEvents()
        self.assertNotEqual(password.property("displayText"), "temporary test password")
        password.setProperty("passwordVisible", True)
        self.app.processEvents()
        self.assertEqual(password.property("displayText"), "temporary test password")
        password.setProperty("passwordVisible", False)
        self.assertEqual(password.property("text"), "temporary test password")

    def test_admin_login_uses_responsive_splash_and_failure_retry(self):
        gate = threading.Event()
        event_loop_ran = threading.Event()

        def fail_login(_password):
            gate.wait(2)
            raise PermissionError("Incorrect Admin password.")

        self.runtime.take_admin.side_effect = fail_login
        self.bridge._set_mode("candidate")
        self.bridge.claimAdmin("wrong password")
        self.assertEqual(self.bridge.mode, "splash")
        self.assertTrue(self.bridge.busy)
        self.assertIn("synchronizing", self.bridge.statusText)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, event_loop_ran.set)
        self.app.processEvents()
        self.assertTrue(event_loop_ran.is_set())
        gate.set()
        self.wait_for_idle()
        self.assertEqual(self.bridge.mode, "candidate")
        self.assertTrue(self.bridge.view["adminLoginFailed"])
        self.assertEqual(self.bridge.notice["title"], "Admin login failed")

    def test_admin_login_success_prepares_dashboard_off_thread(self):
        self.bridge._set_mode("candidate")
        self.runtime.take_admin.return_value = None
        self.bridge.claimAdmin("correct password")
        self.wait_for_idle()
        self.assertEqual(self.bridge.mode, "admin")
        self.runtime.take_admin.assert_called_once_with("correct password")
        self.host.stop_console.assert_called_once()

    def test_dashboard_history_width_and_avatar_preview_are_bounded(self):
        self.bridge._set_view(settings={"cafeName": "Cafe", "adminName": "Admin",
                                      "graceMinutes": 10, "signoutMinutes": 0})
        self.bridge._set_mode("admin")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        history = computers = None
        for width in (1100, 1600):
            root.setProperty("width", width)
            self.app.processEvents()
            history = root.findChild(QObject, "recentSessionsPanel")
            computers = root.findChild(QObject, "computerManagementPanel")
            self.assertIsNotNone(history)
            self.assertIsNotNone(computers)
            self.assertGreaterEqual(history.property("width"), 180)
            self.assertLessEqual(history.property("width"), 235)
            self.assertGreater(computers.property("width"), history.property("width"))
        sort_box = root.findChild(QObject, "computerSortBox")
        self.assertIsNotNone(sort_box)
        self.assertEqual(sort_box.property("count"), 2)
        empty_history = root.findChild(QObject, "recentHistoryEmptyState")
        self.assertIsNotNone(empty_history)
        self.assertEqual(empty_history.property("text"),
                         "Select a computer to view recent sessions.")
        self.bridge.pc_model.update_rows([{"pcId": "selected-pc",
                                           "unlockRequested": False}])
        with patch.object(self.bridge, "refresh"):
            self.bridge.selectPc("selected-pc")
        self.app.processEvents()
        self.assertEqual(empty_history.property("text"),
                         "No sessions recorded for this PC.")
        admin = root.findChild(QObject, "mainLoader").property("item")
        admin.setProperty("section", "History")
        self.app.processEvents()
        self.assertIsNotNone(root.findChild(QObject, "allHistoryButton"))
        admin.setProperty("section", "Settings")
        self.app.processEvents()
        preview = root.findChild(QObject, "avatarPreview")
        self.assertIsNotNone(preview)
        self.assertEqual(preview.property("width"), 184)
        self.assertEqual(preview.property("height"), 184)
        self.assertEqual(self.bridge.avatarPreviewSource(""), "")
        self.assertEqual(self.bridge.avatarPreviewSource("missing.png"), "")
        valid = Path(self.temp.name) / "transparent.png"
        image = QImage(8, 8, QImage.Format_ARGB32)
        image.fill(0)
        self.assertTrue(image.save(str(valid)))
        self.assertTrue(self.bridge.avatarPreviewSource(str(valid)).startswith("file:"))

    def test_optional_branding_and_build_fallback(self):
        missing = Path(self.temp.name) / "missing.png"
        self.assertFalse(valid_image(missing))
        with patch("game_cafe.branding.APP_LOGO_PATH", missing):
            self.assertEqual(app_logo_source(), "")
        valid = Path(self.temp.name) / "logo.png"
        image = QImage(10, 6, QImage.Format_ARGB32)
        image.fill("#35c7c7")
        self.assertTrue(image.save(str(valid)))
        with patch("game_cafe.branding.APP_LOGO_PATH", valid):
            self.assertTrue(app_logo_source().startswith("file:"))
        build = (Path(__file__).resolve().parents[1] /
                 "build_game_cafe.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Test-Path -LiteralPath $iconPath', build)
        self.assertIn('@("--icon", $iconPath)', build)
        self.assertIn('game_cafe\\assets;game_cafe\\assets', build)

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
        uploaded = base64.b64decode(kwargs["logo"])
        self.assertTrue(uploaded.startswith(b"\x89PNG"))
        self.assertTrue(QImage.fromData(uploaded).hasAlphaChannel())

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
        self.assertFalse(self.bridge.view["activeAdminDetected"])
        self.assertEqual(self.bridge.view["cafeName"], "Cafe")

    def test_saved_pool_with_live_admin_stays_on_login_as_user_choice(self):
        snapshot, secret = new_pool("Cafe", self.store.pc_id, "PC-01", "Admin",
                                    "long test password", self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)
        self.runtime.verified_admin_online.return_value = True
        self.runtime.confirmed_remote_admin_online.return_value = True
        self.bridge.begin()
        self.wait_for_idle()
        self.assertEqual(self.bridge.mode, "candidate")
        self.assertTrue(self.bridge.view["activeAdminDetected"])
        self.host.start_console.assert_not_called()
        self.host.stop_console.assert_not_called()

    def test_login_selects_user_and_disables_admin_when_admin_appears(self):
        self.bridge._set_mode("candidate")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        admin_option = root.findChild(QObject, "adminRoleOption")
        continue_button = root.findChild(QObject, "continueAsUserButton")
        heading = root.findChild(QObject, "loginRoleHeading")
        message = root.findChild(QObject, "activeAdminMessage")
        self.assertTrue(admin_option.property("enabled"))
        self.assertFalse(continue_button.property("visible"))

        self.bridge._set_view(activeAdminDetected=True)
        self.app.processEvents()
        self.assertFalse(admin_option.property("enabled"))
        self.assertTrue(continue_button.property("visible"))
        self.assertEqual(heading.property("text"), "Continue as User")
        self.assertTrue(message.property("visible"))
        self.host.start_console.assert_not_called()

        self.bridge._set_view(activeAdminDetected=False)
        self.app.processEvents()
        self.assertTrue(admin_option.property("enabled"))
        self.assertTrue(continue_button.property("visible"))
        self.assertEqual(heading.property("text"), "Continue as User")
        self.host.start_console.assert_not_called()

    def test_login_admin_check_reuses_refresh_and_stops_outside_login(self):
        snapshot, secret = new_pool("Cafe", self.store.pc_id, "PC-01", "Admin",
                                    "long test password", self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)
        self.runtime.status.return_value = {"desktop": "Default"}
        self.runtime.is_admin.return_value = False
        self.runtime.confirmed_remote_admin_online.return_value = True
        self.bridge._set_mode("candidate")
        self.assertTrue(self.bridge._collect_state("", True)["activeAdminDetected"])
        self.runtime.confirmed_remote_admin_online.assert_called_once_with()

        for mode in ("widget", "admin"):
            with self.subTest(mode=mode):
                self.runtime.confirmed_remote_admin_online.reset_mock()
                self.bridge._set_mode(mode)
                self.assertFalse(
                    self.bridge._collect_state("", True)["activeAdminDetected"])
                self.runtime.confirmed_remote_admin_online.assert_not_called()

    def test_detected_admin_still_requires_continue_as_user_confirmation(self):
        snapshot, secret = new_pool("Cafe", self.store.pc_id, "PC-01", "Admin",
                                    "long test password", self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)
        self.bridge.refresh_timer.stop()
        self.bridge._set_view(activeAdminDetected=True)
        self.bridge._set_mode("candidate")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parents[1] / "game_cafe" / "qml" / "App.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        continue_button = root.findChild(QObject, "continueAsUserButton")
        self.assertTrue(continue_button.property("visible"))
        self.host.start_console.assert_not_called()
        continue_button.clicked.emit()
        self.wait_for_idle()
        self.assertEqual(self.bridge.mode, "widget")
        self.host.start_console.assert_called_once_with()

    def test_invalid_session_action_never_reaches_runtime(self):
        self.bridge.pc_model.update_rows([{"pcId": "admin", "start": False,
                                          "add": False, "end": False, "lock": False}])
        self.bridge.startSession("admin", "timed", "60", "0")
        self.assertTrue(self.bridge.notice["error"])
        self.runtime.admin_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
