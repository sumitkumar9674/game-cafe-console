"""Integrated User shutdown persistence without Win32 desktop switching."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import queue
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from game_cafe.qt_app import Application
from game_cafe.runtime import Runtime
from game_cafe.storage import Store, new_pool


PASSWORD = "long test password"


class UserApplicationShutdownTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "user.sqlite3"
        self.store = Store(self.path)
        snapshot, secret = new_pool(
            "Cafe", self.store.pc_id, "PC-02", "Owner", PASSWORD,
            self.store.node_public_key())
        admin_id = "a" * 32
        snapshot["members"][admin_id] = {
            "name": "PC-01", "public_key": "a" * 64}
        snapshot["active_admin"] = {
            "pc_id": admin_id, "term": 2,
            "expires_at": time.time() + 60, "proof": None}
        self.store.save_pool(snapshot, secret, join=True)
        self.runtime = Runtime(self.store)
        self.runtime.events = queue.Queue()
        self.store_patch = patch("game_cafe.qt_app.Store", return_value=self.store)
        self.runtime_patch = patch("game_cafe.qt_app.Runtime",
                                   return_value=self.runtime)
        self.store_patch.start()
        self.runtime_patch.start()
        self.host = Application()
        self.host.bridge._set_view(accessAllowed=True, hasSession=False)
        self.host.bridge._set_mode("widget")
        self.cleaned = False

    def tearDown(self):
        if not self.cleaned:
            self.host.shutting_down = True
            with patch.object(self.host, "stop_console"):
                self.host._cleanup()
        self.runtime_patch.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    def start_session(self, kind: str) -> str:
        self.runtime.local_staff_session_action(
            "start", PASSWORD, "b" * 32, kind=kind,
            paid_minutes=30 if kind == "timed" else 0,
            buffer_minutes=0)
        return self.store.snapshot()["sessions"][self.store.pc_id]["id"]

    def close_and_reopen(self) -> Store:
        with patch.object(self.host, "stop_console") as stop_console:
            self.host.shutdown(source="test")
            deadline = time.monotonic() + 3
            while not self.host.shutting_down and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertTrue(self.host.shutting_down)
            stop_console.assert_called_once()
        self.host._cleanup()
        self.cleaned = True
        return Store(self.path)

    def test_no_active_session_closes_without_history(self):
        reopened = self.close_and_reopen()
        try:
            snapshot = reopened.snapshot()
            self.assertNotIn(reopened.pc_id, snapshot["sessions"])
            self.assertEqual(snapshot["history"].get(reopened.pc_id, []), [])
        finally:
            reopened.close()

    def assert_ended_session_is_not_finalized_twice(self, kind: str) -> None:
        session_id = self.start_session(kind)
        self.runtime.finish_local_session("customer")
        reopened = self.close_and_reopen()
        try:
            records = reopened.snapshot()["history"][reopened.pc_id]
            self.assertEqual([record["id"] for record in records], [session_id])
            self.assertEqual(records[0]["reason"], "customer")
        finally:
            reopened.close()

    def test_ended_open_session_is_not_finalized_twice(self):
        self.assert_ended_session_is_not_finalized_twice("open")

    def test_ended_timed_session_is_not_finalized_twice(self):
        self.assert_ended_session_is_not_finalized_twice("timed")

    def test_active_open_session_is_persisted_before_exit(self):
        session_id = self.start_session("open")
        reopened = self.close_and_reopen()
        try:
            records = reopened.snapshot()["history"][reopened.pc_id]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["id"], session_id)
            self.assertEqual(records[0]["reason"], "close_software")
            self.assertNotIn(reopened.pc_id, reopened.snapshot()["sessions"])
            self.assertIsNone(reopened.checkpoint())
        finally:
            reopened.close()

    def test_active_timed_session_and_repeated_close_finalize_once(self):
        session_id = self.start_session("timed")
        with patch.object(self.runtime, "prepare_local_shutdown",
                          wraps=self.runtime.prepare_local_shutdown) as prepare, \
             patch.object(self.host, "stop_console"):
            self.host.shutdown(source="test")
            self.host.shutdown(source="duplicate")
            deadline = time.monotonic() + 3
            while not self.host.shutting_down and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertTrue(self.host.shutting_down)
            prepare.assert_called_once()
        self.host._cleanup()
        self.cleaned = True
        reopened = Store(self.path)
        try:
            records = reopened.snapshot()["history"][reopened.pc_id]
            self.assertEqual([record["id"] for record in records], [session_id])
        finally:
            reopened.close()

    def test_runtime_background_worker_has_stopped_before_database_close(self):
        worker = threading.Thread(
            target=lambda: self.runtime.stop_event.wait(5),
            name="test-cafe-sync")
        self.runtime.worker = worker
        worker.start()
        reopened = self.close_and_reopen()
        try:
            self.assertFalse(worker.is_alive())
            self.assertIsNone(self.runtime.worker)
            self.assertIsNotNone(reopened.snapshot())
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
