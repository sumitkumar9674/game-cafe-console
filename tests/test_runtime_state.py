"""Owner-local writes and reconnection without sockets or desktop switches."""

from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import unittest
from unittest.mock import patch

from game_cafe.runtime import Runtime
from game_cafe.security import sign_node
from game_cafe import sessions
from game_cafe.storage import Store, new_pool


PASSWORD = "long test password"


class RuntimeStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.admin_store = Store(root / "admin.sqlite3")
        self.user_store = Store(root / "user.sqlite3")
        snap, secret = new_pool("Test Cafe", self.admin_store.pc_id,
                                "PC-01", "Owner", PASSWORD,
                                self.admin_store.node_public_key())
        snap["members"][self.user_store.pc_id] = {
            "name": "PC-02", "public_key": self.user_store.node_public_key()}
        for store in (self.admin_store, self.user_store):
            store.save_pool(snap, secret, join=True)
        self.admin = Runtime(self.admin_store)
        self.user = Runtime(self.user_store)
        self.admin_store.note_peer(self.user_store.pc_id, "user")
        self.user_store.note_peer(self.admin_store.pc_id, "admin")

        def route(source, ip, operation, data):
            target = self.user if ip == "user" else self.admin
            return target.handle_member(operation, data, source.store.pc_id, ip)

        self.patches = [
            patch.object(self.admin.network, "call",
                         side_effect=lambda ip, op, data: route(self.admin, ip, op, data)),
            patch.object(self.user.network, "call",
                         side_effect=lambda ip, op, data: route(self.user, ip, op, data)),
            patch.object(self.admin, "_discover_peers_once", return_value=[]),
            patch.object(self.user, "_discover_peers_once", return_value=[]),
            patch.object(self.admin, "_reconcile_local_access"),
            patch.object(self.user, "_reconcile_local_access"),
            patch.object(self.user, "_local_access_ready", return_value=True),
        ]
        for item in self.patches:
            item.start()
        self.admin.login_admin(PASSWORD)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.admin_store.close()
        self.user_store.close()
        self.temp.cleanup()

    def test_owner_start_add_end_and_stale_snapshot(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="timed",
                                paid_minutes=60, buffer_minutes=5)
        old = self.admin_store.snapshot()
        first_id = old["sessions"][pc_id]["id"]
        self.admin.admin_action("add", pc_id, minutes=30)
        self.assertEqual(self.user_store.snapshot()["sessions"][pc_id]["id"], first_id)
        self.user.submit_customer_action("end")
        self.assertNotIn(pc_id, self.user_store.snapshot()["sessions"])
        self.admin._poll_peer_statuses()
        self.assertNotIn(pc_id, self.admin_store.snapshot()["sessions"])
        self.assertEqual(self.admin_store.snapshot()["history"][pc_id][0]["paid_minutes"], 90)
        self.admin_store.apply_snapshot(old)
        self.assertNotIn(pc_id, self.admin_store.snapshot()["sessions"])

    def test_offline_admin_does_not_block_local_completion(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        with patch.object(self.user.network, "call", side_effect=OSError("offline")):
            self.user.submit_customer_action("rename", name="Rahul")
            self.user.submit_customer_action("end")
        self.assertEqual(self.user_store.snapshot()["history"][pc_id][0]["player"], "Rahul")
        self.admin._poll_peer_statuses()
        self.assertEqual(len(self.admin_store.snapshot()["history"][pc_id]), 1)

    def test_existing_session_continues_during_admin_disconnect(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="timed",
                                paid_minutes=60, buffer_minutes=0)
        started = self.user_store.snapshot()["sessions"][pc_id]["started_at"]
        with patch.object(self.user.network, "call", side_effect=OSError("offline")):
            with patch("game_cafe.runtime.time.time", return_value=started + 5 * 60):
                self.user._tick_sessions()
                self.user.checkpoint_local_session()
        self.assertIn(pc_id, self.user_store.snapshot()["sessions"])
        self.assertEqual(self.user_store.checkpoint()["activity_at"],
                         started + 5 * 60)

    def test_failed_remote_delivery_never_creates_a_local_grant(self):
        pc_id = self.user_store.pc_id
        with patch.object(self.admin.network, "call", side_effect=OSError("no ack")):
            with self.assertRaises(OSError):
                self.admin.admin_action("start", pc_id, kind="timed",
                                        paid_minutes=30, buffer_minutes=0)
        self.assertNotIn(pc_id, self.admin_store.snapshot()["sessions"])
        self.assertNotIn(pc_id, self.user_store.snapshot()["sessions"])

    def test_locking_an_idle_pc_is_a_confirmed_noop(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("end", pc_id, reason="admin")
        self.assertNotIn(pc_id, self.user_store.snapshot()["sessions"])
        self.assertEqual(self.user_store.snapshot()["history"].get(pc_id, []), [])

    def test_pause_resume_and_duplicate_command_are_idempotent(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="timed",
                                paid_minutes=30, buffer_minutes=0)
        captured = []
        original = self.user._apply_session_command
        def capture(data, sender):
            captured.append(data)
            return original(data, sender)
        with patch.object(self.user, "_apply_session_command", side_effect=capture):
            self.admin.admin_action("pause", pc_id)
        paused = self.user_store.snapshot()["sessions"][pc_id]
        self.assertIsNotNone(paused["paused_at"])
        live_user = Runtime(self.user_store)
        live_user.console_available = True
        live_user.console_handle = 123
        live_user.last_access_allowed = True
        self.assertTrue(live_user._local_access_ready())
        version = next(item["payload"]["version"] for item in
                       self.user_store.owner_records() if item["payload"]["pc_id"] == pc_id)
        self.user.handle_member("session_command", captured[0],
                                self.admin_store.pc_id, "admin")
        again = next(item["payload"]["version"] for item in
                     self.user_store.owner_records() if item["payload"]["pc_id"] == pc_id)
        self.assertEqual(version, again)
        self.admin.admin_action("resume", pc_id)
        self.assertIsNone(self.user_store.snapshot()["sessions"][pc_id]["paused_at"])

    def test_checkpoint_recovery_uses_last_saved_activity_once(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        session = self.user_store.snapshot()["sessions"][pc_id]
        saved_at = session["started_at"] + 63
        self.user.checkpoint_local_session(saved_at)
        self.assertEqual(self.user_store.checkpoint()["activity_at"], saved_at)
        restarted = Runtime(self.user_store)
        with patch.object(restarted, "_reconcile_local_access"):
            self.assertTrue(restarted.recover_interrupted_session())
            self.assertFalse(restarted.recover_interrupted_session())
        snap = self.user_store.snapshot()
        self.assertNotIn(pc_id, snap["sessions"])
        self.assertEqual(len(snap["history"][pc_id]), 1)
        self.assertEqual(snap["history"][pc_id][0]["reason"], "interrupted")
        self.assertEqual(snap["history"][pc_id][0]["ended_at"], saved_at)
        self.assertAlmostEqual(snap["history"][pc_id][0]["counted_seconds"], 63,
                               delta=0.001)
        self.assertIsNone(self.user_store.checkpoint())

    def test_remote_exit_acceptance_finalizes_and_rejects_wrong_sender(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        with self.assertRaises(PermissionError):
            self.user.handle_member("remote_exit", {"term": 1}, pc_id, "user")
        self.admin._poll_peer_statuses()
        response = self.admin.remote_exit(pc_id)
        self.assertTrue(response["session_finalized"])
        self.assertNotIn(pc_id, self.user_store.snapshot()["sessions"])
        self.assertEqual(self.user_store.snapshot()["history"][pc_id][0]["reason"],
                         "close_software")
        self.assertIn("remote_exit", [item[0] for item in list(self.user.events.queue)])
        self.admin.peer_statuses.clear()  # Simulate disconnect after the accepted exit.
        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.admin.remote_exit(pc_id)

    def test_owner_signature_cannot_invent_paid_minutes(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="timed",
                                paid_minutes=10, buffer_minutes=0)
        record = next(item for item in self.user_store.owner_records()
                      if item["payload"]["pc_id"] == pc_id)
        forged = copy.deepcopy(record)
        forged["payload"]["version"] += 1
        forged["payload"]["session"]["paid_minutes"] = 600
        forged["signature"] = sign_node(self.user_store.node_private_key(),
                                        forged["payload"])
        with self.assertRaisesRegex(PermissionError, "grants"):
            self.admin_store.merge_owner_record(forged)

    def test_user_cannot_issue_privileged_session_command(self):
        pc_id = self.user_store.pc_id
        command = {"id": "x" * 32, "pool_id": self.user_store.current_pool_id,
                   "admin_id": pc_id, "target": pc_id, "term": 1,
                   "action": "start", "session_id": "y" * 32,
                   "details": {"kind": "timed", "paid_minutes": 999,
                               "buffer_minutes": 0}}
        with self.assertRaises(PermissionError):
            self.user.handle_member("session_command", {"command": command,
                "signature": sign_node(self.user_store.node_private_key(), command),
                "admin_proof": ""}, pc_id, "user")
        self.assertNotIn(pc_id, self.user_store.snapshot()["sessions"])

    def test_remote_exit_timeout_is_not_reported_as_success(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        self.admin._poll_peer_statuses()
        with patch.object(self.admin.network, "call", side_effect=TimeoutError("no reply")):
            with self.assertRaisesRegex(RuntimeError, "Could not confirm remote exit"):
                self.admin.remote_exit(pc_id)
        self.assertIn(pc_id, self.user_store.snapshot()["sessions"])
        self.assertFalse(any(kind == "remote_exit" for kind, _ in
                             list(self.user.events.queue)))

    def test_password_rotation_keeps_prior_session_grants_verifiable(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="timed",
                                paid_minutes=15, buffer_minutes=0)
        self.admin.admin_action("password", new_password="new long password")
        self.user.submit_customer_action("end")
        self.admin._poll_peer_statuses()
        self.assertEqual(len(self.admin_store.snapshot()["history"][pc_id]), 1)

    def test_existing_snapshot_session_migrates_without_database_reset(self):
        pc_id = self.user_store.pc_id
        legacy = sessions.start_session("open", 0, 0)
        self.admin_store.update(lambda snap: snap["sessions"].update({pc_id: legacy}))
        self.user_store.apply_snapshot(self.admin_store.snapshot())
        self.user._migrate_legacy_owner_session()
        self.assertEqual(self.user_store.snapshot()["sessions"][pc_id]["id"],
                         legacy["id"])
        self.assertIsNotNone(self.user_store.checkpoint())
        self.admin._poll_peer_statuses()
        self.user.finish_local_session("customer")
        self.admin._poll_peer_statuses()
        self.assertEqual(self.admin_store.snapshot()["history"][pc_id][0]["id"],
                         legacy["id"])


if __name__ == "__main__":
    unittest.main()
