"""Owner-local writes and reconnection without sockets or desktop switches."""

from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import json
import unittest
from unittest.mock import MagicMock, patch

from game_cafe.runtime import Runtime
from game_cafe.network import signed
from game_cafe.security import sign_admin_proof, sign_node
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

    def test_owner_fifo_clear_and_stale_replica_cannot_resurrect(self):
        owner = self.user_store.pc_id
        records = []
        grants = []
        for number in range(151):
            session_id = f"{number + 1:032x}"
            records.append({"id": session_id, "pc_id": owner,
                            "paid_minutes": 0, "ended_at": float(number)})
            command = {"id": f"{number + 1000:032x}",
                       "pool_id": self.admin_store.current_pool_id,
                       "admin_id": self.admin_store.pc_id, "target": owner,
                       "term": self.admin.snapshot()["active_admin"]["term"],
                       "action": "start", "session_id": session_id,
                       "details": {"paid_minutes": 0}}
            grants.append({"command": command,
                           "signature": sign_node(self.admin_store.node_private_key(), command),
                           "admin_proof": sign_admin_proof(self.admin.admin_signing_key,
                                                            command)})
        first = self.user_store.save_owner_record(None, records[:100], grants[:100],
                                                  self.user.run_id, 1.0)
        self.assertTrue(self.admin_store.merge_owner_record(first))
        stale = self.admin_store.snapshot()
        second = self.user_store.save_owner_record(None, records[:101], grants[:101],
                                                   self.user.run_id, 2.0)
        self.assertTrue(self.admin_store.merge_owner_record(second))
        self.assertEqual([r["id"] for r in self.admin_store.snapshot()["history"][owner]],
                         [r["id"] for r in records[1:101]])
        latest = self.user_store.save_owner_record(None, records, grants,
                                                   self.user.run_id, 3.0)
        self.assertTrue(self.admin_store.merge_owner_record(latest))
        self.assertEqual([r["id"] for r in self.admin_store.snapshot()["history"][owner]],
                         [r["id"] for r in records[-100:]])
        self.admin_store.save_pool(stale, self.admin_store.secret())
        self.assertEqual(len(self.admin_store.snapshot()["history"][owner]), 100)
        self.assertFalse(self.admin_store.merge_owner_record(first))

        self.admin.clear_history(owner)
        self.assertEqual(self.admin_store.snapshot()["history"][owner], [])
        self.assertEqual(self.user_store.snapshot()["history"][owner], [])
        raw = json.loads(self.admin_store.db.execute(
            "SELECT snapshot FROM pools WHERE pool_id=?",
            (self.admin_store.current_pool_id,)).fetchone()[0])
        self.assertEqual(raw["history"][owner], [])
        self.assertFalse(self.admin_store.merge_owner_record(latest))
        self.admin_store.save_pool(stale, self.admin_store.secret())
        self.assertEqual(self.admin_store.snapshot()["history"][owner], [])
        self.admin.admin_action("start", owner, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        new_session_id = self.user_store.snapshot()["sessions"][owner]["id"]
        self.user.submit_customer_action("end")
        self.admin._poll_peer_statuses()
        self.assertEqual([r["id"] for r in self.admin_store.snapshot()["history"][owner]],
                         [new_session_id])

    def test_history_clear_timeout_retries_same_command_without_second_deletion(self):
        owner = self.user_store.pc_id
        self.admin.admin_action("start", owner, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        self.user.submit_customer_action("end")
        self.admin._poll_peer_statuses()
        original_call = self.admin.network.call

        def lose_reply(ip, operation, data):
            reply = original_call(ip, operation, data)
            if operation == "session_command" and data["command"]["action"] == "clear_history":
                raise OSError("reply lost")
            return reply

        with patch.object(self.admin.network, "call", side_effect=lose_reply):
            with self.assertRaises(OSError):
                self.admin.clear_history(owner)
        self.assertEqual(self.user_store.snapshot()["history"][owner], [])
        first_epoch = next(item["payload"]["history_epoch"]
                           for item in self.user_store.owner_records()
                           if item["payload"]["pc_id"] == owner)
        self.admin.clear_history(owner)
        second_epoch = next(item["payload"]["history_epoch"]
                            for item in self.user_store.owner_records()
                            if item["payload"]["pc_id"] == owner)
        self.assertEqual(first_epoch, second_epoch)
        self.assertEqual(self.admin_store.snapshot()["history"][owner], [])

    def test_clear_keeps_active_session_and_checkpoint(self):
        owner = self.user_store.pc_id
        self.admin.admin_action("start", owner, kind="timed",
                                paid_minutes=30, buffer_minutes=5)
        before = self.user_store.snapshot()["sessions"][owner]
        self.admin.clear_history(owner)
        after = self.user_store.snapshot()["sessions"][owner]
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(self.user_store.checkpoint()["session_id"], before["id"])

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
        self.assertTrue(self.user.local_shutdown_pending)
        self.admin.peer_statuses.clear()  # Simulate disconnect after the accepted exit.
        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.admin.remote_exit(pc_id)

    def test_remote_exit_notifies_only_after_signed_reply_is_sent(self):
        pc_id = self.user_store.pc_id
        self.admin.admin_action("start", pc_id, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        self.user.events.queue.clear()
        snapshot = self.user_store.snapshot()
        request = signed(self.user_store.secret(), self.admin_store.pc_id,
                         snapshot["pool_id"], "remote_exit",
                         {"term": snapshot["active_admin"]["term"]},
                         self.admin_store.node_private_key())
        replies = []

        def capture_reply(connection, response):
            self.assertFalse(any(kind == "remote_exit" for kind, _ in
                                 list(self.user.events.queue)))
            replies.append(response)

        with patch("game_cafe.network.read_message", return_value=request), \
             patch("game_cafe.network.send_message", side_effect=capture_reply):
            self.user.network._serve_one(MagicMock(), "admin")
        self.assertTrue(replies[0]["data"]["ok"])
        self.assertEqual(sum(kind == "remote_exit" for kind, _ in
                             list(self.user.events.queue)), 1)

        self.user.events.queue.clear()
        request = signed(self.user_store.secret(), self.admin_store.pc_id,
                         snapshot["pool_id"], "remote_exit",
                         {"term": snapshot["active_admin"]["term"]},
                         self.admin_store.node_private_key())
        with patch("game_cafe.network.read_message", return_value=request), \
             patch("game_cafe.network.send_message", side_effect=OSError("disconnected")):
            self.user.network._serve_one(MagicMock(), "admin")
        self.assertFalse(any(kind == "remote_exit" for kind, _ in
                             list(self.user.events.queue)))

    def test_remote_exit_leaves_another_user_pc_session_untouched(self):
        third_store = Store(Path(self.temp.name) / "third.sqlite3")
        try:
            member = {"name": "PC-03",
                      "public_key": third_store.node_public_key()}
            for store in (self.admin_store, self.user_store):
                store.update(lambda state: state["members"].update(
                    {third_store.pc_id: member}))
            third_store.save_pool(self.admin_store.snapshot(),
                                  self.admin_store.secret(), join=True)
            third = Runtime(third_store)
            third.local_staff_session_action(
                "start", PASSWORD, "a" * 32, kind="open",
                paid_minutes=0, buffer_minutes=0)
            other_session = copy.deepcopy(
                third_store.snapshot()["sessions"][third_store.pc_id])
            owner = self.user_store.pc_id
            self.admin.admin_action("start", owner, kind="timed",
                                    paid_minutes=30, buffer_minutes=0)
            self.admin._poll_peer_statuses()
            self.assertTrue(self.admin.remote_exit(owner)["accepted"])
            self.assertEqual(third_store.snapshot()["sessions"][third_store.pc_id],
                             other_session)
            self.assertEqual(third_store.snapshot()["history"].get(
                third_store.pc_id, []), [])
            self.assertFalse(third.local_shutdown_pending)
        finally:
            third_store.close()

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

    def test_admin_shutdown_does_not_finalize_user_session(self):
        owner = self.user_store.pc_id
        self.admin.admin_action("start", owner, kind="timed",
                                paid_minutes=30, buffer_minutes=0)
        before = copy.deepcopy(self.user_store.snapshot()["sessions"][owner])
        self.assertFalse(self.admin.prepare_local_shutdown())
        self.admin.release_admin()
        self.assertEqual(self.user_store.snapshot()["sessions"][owner], before)
        self.assertEqual(self.user_store.snapshot()["history"].get(owner, []), [])
        self.assertTrue(self.user_store.checkpoint())

    def test_user_shutdown_offline_persists_once_and_syncs_after_restart(self):
        owner = self.user_store.pc_id
        self.admin.admin_action("start", owner, kind="timed",
                                paid_minutes=30, buffer_minutes=5)
        self.user.submit_customer_action("rename", name="Sahil")
        with patch.object(self.user.network, "call", side_effect=OSError("offline")):
            self.assertTrue(self.user.prepare_local_shutdown())
            self.assertFalse(self.user.prepare_local_shutdown())
        snap = self.user_store.snapshot()
        self.assertNotIn(owner, snap["sessions"])
        self.assertIsNone(self.user_store.checkpoint())
        self.assertEqual(len(snap["history"][owner]), 1)
        self.assertEqual(snap["history"][owner][0]["player"], "Sahil")
        self.assertEqual(snap["history"][owner][0]["reason"], "close_software")
        restarted = Runtime(self.user_store)
        with patch.object(restarted, "_reconcile_local_access"):
            self.assertFalse(restarted.recover_interrupted_session())
        self.admin._poll_peer_statuses()
        self.assertEqual(len(self.admin_store.snapshot()["history"][owner]), 1)
        self.admin._poll_peer_statuses()
        self.assertEqual(len(self.admin_store.snapshot()["history"][owner]), 1)

    def test_failed_shutdown_persistence_keeps_session_recoverable(self):
        owner = self.user_store.pc_id
        self.admin.admin_action("start", owner, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        session_id = self.user_store.snapshot()["sessions"][owner]["id"]
        with patch.object(self.user_store, "save_owner_record",
                          side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.user.prepare_local_shutdown()
        self.assertFalse(self.user.local_shutdown_pending)
        self.assertEqual(self.user_store.snapshot()["sessions"][owner]["id"],
                         session_id)
        self.assertEqual(self.user_store.snapshot()["history"].get(owner, []), [])
        self.assertTrue(self.user.prepare_local_shutdown())
        self.assertEqual(len(self.user_store.snapshot()["history"][owner]), 1)

    def test_already_completed_session_is_not_finalized_again_on_exit(self):
        owner = self.user_store.pc_id
        self.admin.admin_action("start", owner, kind="open",
                                paid_minutes=0, buffer_minutes=0)
        self.user.finish_local_session("customer")
        previous = self.user_store.snapshot()["history"][owner]
        self.assertFalse(self.user.prepare_local_shutdown())
        self.assertEqual(self.user_store.snapshot()["history"][owner], previous)

    def test_shutdown_finalizes_timed_open_buffer_paused_and_grace_phases(self):
        cases = (
            ("timed", "timed", 30, 0, 120, 120, 0, 0),
            ("buffer", "timed", 30, 5, 60, 0, 60, 0),
            ("open", "open", 0, 0, 120, 120, 0, 0),
            ("paused", "timed", 30, 0, 180, 60, 0, 120),
            ("grace", "timed", 1, 0, 180, 60, 0, 0),
        )
        for name, kind, paid, buffer, elapsed, counted, buffer_used, paused in cases:
            with self.subTest(name=name), TemporaryDirectory() as folder:
                store = Store(Path(folder) / "phase.sqlite3")
                snap, secret = new_pool("Cafe", store.pc_id, "PC-02", "Owner",
                                        PASSWORD, store.node_public_key())
                store.save_pool(snap, secret, join=True)
                runtime = Runtime(store)
                runtime.local_staff_session_action(
                    "start", PASSWORD, "b" * 32, kind=kind,
                    paid_minutes=paid, buffer_minutes=buffer)
                session, history, grants = runtime._owner_payload()
                started = session["started_at"]
                session["player"] = "Sahil"
                if name == "paused":
                    sessions.pause_session(session, now=started + 60)
                if name == "grace":
                    sessions.enter_grace_if_due(session, started + 120, 10)
                runtime._save_owner_state(session, history, grants)
                with patch("game_cafe.runtime.time.time",
                           return_value=started + elapsed):
                    self.assertTrue(runtime.prepare_local_shutdown())
                record = store.snapshot()["history"][store.pc_id][0]
                self.assertEqual(record["id"], session["id"])
                self.assertEqual(record["player"], "Sahil")
                self.assertAlmostEqual(record["counted_seconds"], counted, delta=1)
                self.assertAlmostEqual(record["buffer_used_seconds"], buffer_used,
                                       delta=1)
                self.assertAlmostEqual(record["paused_seconds"], paused, delta=1)
                if name == "grace":
                    self.assertAlmostEqual(record["grace_used_seconds"], 120, delta=1)
                store.close()

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
