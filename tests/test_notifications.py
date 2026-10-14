"""Admin lifecycle notification coverage without real LAN or desktop changes."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from game_cafe.qt_bridge import CafeBridge
from game_cafe.runtime import PEER_ONLINE_SECONDS, Runtime
from game_cafe.storage import NOTIFICATION_LIMIT, Store, new_pool


PASSWORD = "long test password"
USER_ID = "b" * 32


class NotificationStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "cafe.sqlite3"
        self.store = Store(self.path)
        snapshot, secret = new_pool(
            "Test Cafe", self.store.pc_id, "STARPIRATE", "Owner", PASSWORD,
            self.store.node_public_key())
        snapshot["members"][USER_ID] = {
            "name": "GameGrid-Test-PC", "public_key": "b" * 64,
            "last_connected_at": 1,
        }
        self.store.save_pool(snapshot, secret, join=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_latest_100_persist_and_acknowledgment_survives_restart(self):
        for number in range(NOTIFICATION_LIMIT + 5):
            self.store.add_notification(
                USER_ID, f"PC-{number:03}", "offline", created_at=number)
        rows = self.store.notifications()
        self.assertEqual(len(rows), NOTIFICATION_LIMIT)
        self.assertEqual(rows[0]["pc_name"], "PC-104")
        self.assertEqual(rows[-1]["pc_name"], "PC-005")
        self.assertTrue(all(not row["acknowledged"] for row in rows))
        self.store.acknowledge_notifications()
        self.store.close()
        self.store = Store(self.path)
        self.assertTrue(all(row["acknowledged"]
                            for row in self.store.notifications()))


class NotificationLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "cafe.sqlite3")
        snapshot, secret = new_pool(
            "Test Cafe", self.store.pc_id, "STARPIRATE", "Owner", PASSWORD,
            self.store.node_public_key())
        snapshot["members"][USER_ID] = {
            "name": "GameGrid-Test-PC", "public_key": "b" * 64,
            "last_connected_at": 1,
        }
        self.store.save_pool(snapshot, secret, join=True)
        self.store.note_peer(USER_ID, "test-pc")
        self.runtime = Runtime(self.store)
        self.admin = patch.object(self.runtime, "is_admin", return_value=True)
        self.admin.start()

    def tearDown(self):
        self.admin.stop()
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def successful_call(_ip, operation, _data):
        if operation == "status":
            return {"pc_id": USER_ID, "desktop": "CafeConsole"}
        if operation == "session_records":
            return {"records": []}
        raise AssertionError(operation)

    def poll_online(self):
        with patch.object(self.runtime.network, "call",
                          side_effect=self.successful_call):
            self.runtime._poll_peer_statuses()

    def poll_offline(self):
        self.runtime.peer_statuses[USER_ID] = (
            time.time() - PEER_ONLINE_SECONDS - 1, {"pc_id": USER_ID})
        with patch.object(self.runtime.network, "call",
                          side_effect=OSError("offline")):
            self.runtime._poll_peer_statuses()

    def test_online_offline_and_reconnect_are_single_transitions(self):
        self.poll_online()
        self.assertEqual(self.store.notifications(), [])
        self.poll_offline()
        self.poll_offline()
        self.assertEqual([row["event_type"] for row in self.store.notifications()],
                         ["offline"])
        self.poll_online()
        self.poll_online()
        self.assertEqual([row["event_type"] for row in self.store.notifications()],
                         ["online", "offline"])

    def test_existing_offline_peer_is_silent_at_startup(self):
        with patch.object(self.runtime.network, "call",
                          side_effect=OSError("offline")):
            self.runtime._poll_peer_statuses()
            self.runtime._poll_peer_statuses()
        self.assertEqual(self.store.notifications(), [])

    def test_graceful_close_suppresses_following_offline_and_is_idempotent(self):
        self.poll_online()
        self.runtime.admin_ui_open = False
        self.runtime.handle_member("graceful_shutdown", {}, USER_ID, "test-pc")
        self.runtime.handle_member("graceful_shutdown", {}, USER_ID, "test-pc")
        self.poll_offline()
        self.poll_offline()
        self.assertEqual([row["event_type"] for row in self.store.notifications()],
                         ["closed"])

    def test_pc_id_and_event_time_name_are_preserved_across_rename(self):
        self.poll_online()
        self.poll_offline()
        self.poll_offline()
        snapshot = self.store.snapshot()
        snapshot["members"][USER_ID]["name"] = "Renamed PC"
        snapshot["revision"] += 1
        self.store.save_pool(snapshot, self.store.secret() or "")
        self.poll_online()
        rows = self.store.notifications()
        self.assertEqual({row["pc_id"] for row in rows}, {USER_ID})
        self.assertEqual([row["pc_name"] for row in rows],
                         ["Renamed PC", "GameGrid-Test-PC"])


class NotificationBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "cafe.sqlite3")
        snapshot, secret = new_pool(
            "Test Cafe", self.store.pc_id, "STARPIRATE", "Owner", PASSWORD,
            self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)
        self.runtime = Mock()
        self.runtime.is_admin.return_value = True
        self.host = Mock()
        self.bridge = CafeBridge(self.store, self.host, self.runtime)

    def tearDown(self):
        self.bridge.shutdown()
        self.store.close()
        self.temp.cleanup()

    def test_relative_time_boundaries_and_future_clock(self):
        now = 100_000
        self.assertEqual(self.bridge.notification_time_text(now + 10, now), "Now")
        self.assertEqual(self.bridge.notification_time_text(now - 59, now), "Now")
        self.assertEqual(self.bridge.notification_time_text(now - 60, now), "1m")
        self.assertEqual(self.bridge.notification_time_text(now - 120, now), "2m")
        self.assertEqual(self.bridge.notification_time_text(now - 3600, now), "1h")
        self.assertEqual(self.bridge.notification_time_text(now - 86400, now), "1d")

    def test_acknowledgment_clears_indicator_without_deleting_history(self):
        self.store.add_notification(USER_ID, "PC-02", "offline")
        state = self.bridge._collect_state("", True)
        self.bridge.notification_model.update_rows(state["notifications"])
        self.bridge._set_view(
            notificationUnreadCount=state["notificationUnreadCount"])
        self.assertEqual(self.bridge.view["notificationUnreadCount"], 1)
        self.bridge.acknowledgeNotifications()
        self.assertEqual(self.bridge.view["notificationUnreadCount"], 0)
        self.assertEqual(len(self.store.notifications()), 1)
        self.assertTrue(self.store.notifications()[0]["acknowledged"])
        self.store.add_notification(USER_ID, "PC-02", "online")
        state = self.bridge._collect_state("", True)
        self.assertEqual(state["notificationUnreadCount"], 1)


if __name__ == "__main__":
    unittest.main()
