"""Registered-customer and retainer rules without real desktop switching."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import ANY, patch

from game_cafe.presentation import pc_row_data
from game_cafe.runtime import Runtime
from game_cafe.storage import Store, new_pool, verify_password


PASSWORD = "long test password"


class CustomerGameplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.admin_store = Store(root / "admin.sqlite3")
        self.user_store = Store(root / "user.sqlite3")
        snapshot, secret = new_pool(
            "Cafe", self.admin_store.pc_id, "PC-01", "Sahil", PASSWORD,
            self.admin_store.node_public_key())
        snapshot["members"][self.user_store.pc_id] = {
            "name": "PC-02", "public_key": self.user_store.node_public_key(),
            "last_connected_at": time.time(),
        }
        for store in (self.admin_store, self.user_store):
            store.save_pool(snapshot, secret, join=True)
        self.admin = Runtime(self.admin_store)
        self.user = Runtime(self.user_store)
        self.user.default_handle = 1
        self.user.console_handle = 2
        self.user.console_available = True
        self.admin_store.note_peer(self.user_store.pc_id, "user")
        self.user_store.note_peer(self.admin_store.pc_id, "admin")

        def route(source, ip, operation, data):
            target = self.user if ip == "user" else self.admin
            return target.handle_member(operation, data, source.store.pc_id, ip)

        self.patches = [
            patch.object(self.admin.network, "call",
                         side_effect=lambda ip, op, data: route(
                             self.admin, ip, op, data)),
            patch.object(self.user.network, "call",
                         side_effect=lambda ip, op, data: route(
                             self.user, ip, op, data)),
            patch.object(self.admin, "_reconcile_local_access"),
            patch.object(self.user, "_reconcile_local_access"),
            patch.object(self.user, "_local_access_ready", return_value=True),
        ]
        for item in self.patches:
            item.start()
        self.admin.login_admin(PASSWORD)
        self.admin.admin_ui_open = True
        now = time.time()
        self.admin.peer_statuses[self.user_store.pc_id] = (
            now, {"pc_id": self.user_store.pc_id, "desktop": "CafeConsole"})
        self.user.peer_statuses[self.admin_store.pc_id] = (
            now, {"pc_id": self.admin_store.pc_id, "admin": True,
                  "admin_term": self.admin.snapshot()["active_admin"]["term"]})
        self.customer = self.admin.create_customer("Sumit", "create")
        preview = self.admin.preview_customer_adjustment(
            self.customer["customer_id"], "add", "0", "2")
        self.customer = self.admin.adjust_customer(preview)
        self.admin._replicate_once()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.admin_store.close()
        self.user_store.close()
        self.temp.cleanup()

    def login(self):
        return self.user.login_customer(
            self.customer["customer_id"], "12345", "login-operation")

    def start_retainer(self, operation_id="retainer-operation"):
        with patch("game_cafe.runtime.switch"), \
             patch("game_cafe.runtime.active_desktop_name",
                   return_value="Default"):
            return self.user.start_local_retainer(operation_id)

    def confirm_user_offline(self, establish: bool = True):
        pc_id = self.user_store.pc_id
        if establish:
            self.admin.connection_states[pc_id] = True
        self.admin.peer_statuses[pc_id] = (
            time.time() - 20, {"pc_id": pc_id})
        with patch.object(self.admin.network, "call",
                          side_effect=OSError("offline")):
            self.admin._poll_peer_statuses()

    def test_login_is_authoritative_single_pc_and_logout_returns_guest(self):
        self.assertIsNone(self.user_store.local_customer_session())
        with self.assertRaisesRegex(PermissionError, "incorrect"):
            self.user.login_customer(
                self.customer["customer_id"], "wrong", "wrong-login")
        local = self.login()
        self.assertEqual(local["name"], "Sumit")
        self.assertFalse(local["retainer_active"])
        self.assertEqual(
            self.admin_store.customer_ownership(
                customer_id=self.customer["customer_id"])["pc_id"],
            self.user_store.pc_id)
        with self.assertRaisesRegex(RuntimeError, "already logged in"):
            self.admin_store.authenticate_customer(
                self.customer["customer_id"], "12345", "another-pc",
                "other-login")
        self.user.logout_customer()
        self.assertIsNone(self.user_store.local_customer_session())
        self.assertIsNone(self.admin_store.customer_ownership(
            customer_id=self.customer["customer_id"]))

    def test_authentication_is_throttled_and_deleted_accounts_cannot_login(self):
        for index in range(5):
            with self.assertRaises(PermissionError):
                self.user.login_customer(
                    self.customer["customer_id"], "wrong", f"wrong-{index}")
        with self.assertRaisesRegex(PermissionError, "Too many attempts"):
            self.user.login_customer(
                self.customer["customer_id"], "wrong", "wrong-blocked")
        self.admin.customer_auth_failures.clear()
        current = self.admin_store.customer_account(self.customer["customer_id"])
        self.admin.delete_customer(
            self.customer["customer_id"], "Sumit", current["revision"],
            "delete")
        with self.assertRaises(PermissionError):
            self.user.login_customer(
                self.customer["customer_id"], "12345", "deleted-login")

    def test_password_change_validates_and_keeps_login(self):
        self.login()
        with self.assertRaisesRegex(PermissionError, "Current password"):
            self.user.change_logged_in_customer_password(
                "wrong", "new-password", "new-password")
        with self.assertRaisesRegex(ValueError, "do not match"):
            self.user.change_logged_in_customer_password(
                "12345", "new-password", "different")
        updated = self.user.change_logged_in_customer_password(
            "12345", "new-password", "new-password")
        self.assertEqual(updated["name"], "Sumit")
        self.assertIsNotNone(self.user_store.local_customer_session())
        verifier = self.admin_store.customer_password_verifier(
            self.customer["customer_id"])
        self.assertTrue(verify_password("new-password", verifier))
        self.assertFalse(verify_password("12345", verifier))
        self.admin._replicate_once()
        self.assertIsNone(self.user_store.customer_password_verifier(
            self.customer["customer_id"]))

    def test_start_checkpoint_rejects_adjustments_and_stop_is_authoritative(self):
        self.login()
        pending_preview = self.admin.preview_customer_adjustment(
            self.customer["customer_id"], "add", "0", "1")
        local = self.start_retainer()
        self.assertTrue(local["retainer_active"])
        session_id = local["retainer_session_id"]
        self.assertEqual(len(self.admin_store.active_retainers()), 1)
        # A repeated click cannot create a second session.
        self.assertEqual(
            self.user.start_local_retainer("another-operation")
            ["retainer_session_id"], session_id)
        original_clock = self.admin.retainer_clocks[session_id]
        local = self.user_store.local_customer_session()
        self.admin._handle_customer_access({
            "action": "retainer_activate",
            "customer_id": self.customer["customer_id"],
            "login_id": local["login_id"],
            "session_id": session_id,
        }, self.user_store.pc_id)
        self.assertEqual(self.admin.retainer_clocks[session_id], original_clock)
        self.admin.retainer_clocks[session_id] = (100.0, 1000.0)
        with patch("game_cafe.runtime.time.monotonic", return_value=111.0):
            self.admin._checkpoint_active_retainers()
            first = self.admin_store.customer_account(self.customer["customer_id"])
            self.admin._checkpoint_active_retainers()
            second = self.admin_store.customer_account(self.customer["customer_id"])
        self.assertEqual(first["balance_seconds"], 109)
        self.assertEqual(second["balance_seconds"], 109)
        self.admin.retainer_clocks[session_id] = (
            time.monotonic(), time.time())

        before = self.admin_store.customer_account(self.customer["customer_id"])
        history_before = self.admin_store.customer_adjustments(
            self.customer["customer_id"])
        with self.assertRaisesRegex(RuntimeError, "unavailable during an active"):
            self.admin.adjust_customer(pending_preview)
        for action in ("add", "reduce"):
            with self.subTest(action=action), self.assertRaisesRegex(
                    RuntimeError, "unavailable during an active"):
                self.admin.preview_customer_adjustment(
                    self.customer["customer_id"], action, "0", "1")
        after = self.admin_store.customer_account(self.customer["customer_id"])
        self.assertEqual(after["balance_seconds"], before["balance_seconds"])
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(self.admin_store.customer_adjustments(
            self.customer["customer_id"]), history_before)
        with patch("game_cafe.runtime.switch"):
            stopped = self.user.stop_local_retainer()
        self.assertFalse(stopped["retainer_active"])
        self.assertFalse(stopped["retainer_finalizing"])
        self.assertEqual(
            stopped["balance_seconds"],
            self.admin_store.customer_account(
                self.customer["customer_id"])["balance_seconds"])
        self.assertEqual(self.user_store.take_locked_notice(), {
            "id": ANY,
            "title": "Retainer Stopped",
            "message": "Your remaining time has been saved.",
            "created_at": ANY,
        })
        self.assertIsNone(self.user_store.take_locked_notice())
        self.assertEqual(self.admin_store.active_retainers(), [])
        self.assertIsNotNone(self.admin_store.customer_ownership(
            customer_id=self.customer["customer_id"]))
        preview = self.admin.preview_customer_adjustment(
            self.customer["customer_id"], "add", "0", "1")
        adjusted = self.admin.adjust_customer(preview)
        self.assertEqual(adjusted["balance_seconds"],
                         stopped["balance_seconds"] + 60)

    def test_customer_login_and_cooldown_do_not_block_adjustments(self):
        self.login()
        account = self.admin_store.customer_account(self.customer["customer_id"])
        self.admin_store.db.execute(
            "UPDATE customer_accounts SET last_retainer_stopped_at=? "
            "WHERE customer_id=?", (int(time.time()), account["customer_id"]))
        snapshot = self.admin_store.snapshot()
        snapshot["settings"]["retainer_restart_cooldown_minutes"] = 10
        self.admin_store.save_pool(snapshot, self.admin_store.secret())
        preview = self.admin.preview_customer_adjustment(
            account["customer_id"], "add", "0", "1")
        adjusted = self.admin.adjust_customer(preview)
        self.assertEqual(adjusted["balance_seconds"],
                         account["balance_seconds"] + 60)

    def test_prepared_retainer_blocks_adjustment_before_activation(self):
        local = self.login()
        prepared = self.admin._handle_customer_access({
            "action": "retainer_prepare",
            "customer_id": self.customer["customer_id"],
            "login_id": local["login_id"],
            "operation_id": "prepared-only",
        }, self.user_store.pc_id)["retainer"]
        account = self.admin_store.customer_account(self.customer["customer_id"])
        history_before = self.admin_store.customer_adjustments(
            account["customer_id"])
        with self.assertRaisesRegex(RuntimeError, "unavailable during an active"):
            self.admin_store.adjust_customer_balance(
                account["customer_id"], "add", 60, account["revision"],
                account["balance_seconds"], "Sahil", "blocked-prepared")
        self.assertEqual(self.admin_store.customer_adjustments(
            account["customer_id"]), history_before)
        self.admin_store.abort_retainer(
            prepared["session_id"], self.user_store.pc_id,
            local["login_id"])
        adjusted = self.admin_store.adjust_customer_balance(
            account["customer_id"], "add", 60, account["revision"],
            account["balance_seconds"], "Sahil", "allowed-after-abort")
        self.assertEqual(adjusted["balance_seconds"],
                         account["balance_seconds"] + 60)

    def test_failed_unlock_does_not_charge_or_leave_active_retainer(self):
        self.login()
        before = self.admin_store.customer_account(
            self.customer["customer_id"])["balance_seconds"]
        with patch("game_cafe.runtime.switch"), \
             patch("game_cafe.runtime.active_desktop_name",
                   return_value="CafeConsole"):
            with self.assertRaisesRegex(RuntimeError, "did not unlock"):
                self.user.start_local_retainer("failed-start")
        self.assertEqual(self.admin_store.customer_account(
            self.customer["customer_id"])["balance_seconds"], before)
        self.assertEqual(self.admin_store.active_retainers(), [])

    def test_exhaustion_locks_without_buffer_and_keeps_customer_logged_in(self):
        current = self.admin_store.customer_account(self.customer["customer_id"])
        preview = self.admin.preview_customer_adjustment(
            self.customer["customer_id"], "reduce", "0", "1")
        self.admin.adjust_customer(preview)
        # Leave exactly five seconds for a deterministic exhaustion check.
        current = self.admin_store.customer_account(self.customer["customer_id"])
        self.admin_store.adjust_customer_balance(
            self.customer["customer_id"], "reduce",
            current["balance_seconds"] - 5, current["revision"],
            current["balance_seconds"], "Sahil", "reduce-to-five")
        self.login()
        local = self.start_retainer()
        session_id = local["retainer_session_id"]
        self.admin.retainer_clocks[session_id] = (100.0, 1000.0)
        with patch("game_cafe.runtime.time.monotonic", return_value=110.0):
            self.admin._checkpoint_active_retainers()
        local = self.user_store.local_customer_session()
        self.assertFalse(local["retainer_active"])
        self.assertEqual(local["balance_seconds"], 0)
        self.assertEqual(self.admin_store.customer_account(
            self.customer["customer_id"])["balance_seconds"], 0)
        self.assertIn("exhausted", self.user_store.local(
            "customer_feedback").lower())
        notice = self.user_store.take_locked_notice()
        self.assertEqual(notice["title"], "Retainer Time Exhausted")
        self.assertIn("reached zero", notice["message"])
        with self.assertRaisesRegex(ValueError, "no retainer time"):
            self.start_retainer("start-with-zero")

    def test_admin_loss_forces_registered_logout_but_guest_session_is_independent(self):
        self.login()
        local = self.user_store.local_customer_session()
        local["last_admin_seen"] = 0
        self.user_store.save_local_customer_session(local)
        self.user.peer_statuses.clear()
        self.user._reconcile_registered_customer()
        self.assertIsNone(self.user_store.local_customer_session())

        self.admin.admin_action(
            "start", self.user_store.pc_id, kind="timed",
            paid_minutes=30, buffer_minutes=0)
        self.assertIn(self.user_store.pc_id,
                      self.user_store.snapshot()["sessions"])
        self.user._reconcile_registered_customer()
        self.assertIn(self.user_store.pc_id,
                      self.user_store.snapshot()["sessions"])

    def test_admin_loss_ends_registered_ordinary_session_before_logout(self):
        self.login()
        self.admin.admin_action(
            "start", self.user_store.pc_id, kind="timed",
            paid_minutes=30, buffer_minutes=0)
        self.assertEqual(
            self.user_store.snapshot()["sessions"][self.user_store.pc_id]
            ["player"], "Sumit")
        local = self.user_store.local_customer_session()
        local["last_admin_seen"] = 0
        self.user_store.save_local_customer_session(local)
        self.user.peer_statuses.clear()

        self.user._reconcile_registered_customer()

        snapshot = self.user_store.snapshot()
        self.assertIsNone(self.user_store.local_customer_session())
        self.assertNotIn(self.user_store.pc_id, snapshot["sessions"])
        self.assertEqual(
            snapshot["history"][self.user_store.pc_id][-1]["reason"],
            "interrupted")

    def test_single_missed_heartbeat_does_not_release_customer(self):
        self.login()
        pc_id = self.user_store.pc_id
        self.admin.connection_states[pc_id] = True
        self.admin.peer_statuses[pc_id] = (
            time.time(), {"pc_id": pc_id})
        with patch.object(self.admin.network, "call",
                          side_effect=OSError("one missed heartbeat")):
            self.admin._poll_peer_statuses()
        self.assertIsNotNone(self.admin_store.customer_ownership(pc_id=pc_id))

    def test_confirmed_offline_release_is_idempotent_and_has_no_login_cooldown(self):
        self.login()
        self.confirm_user_offline()
        self.assertIsNone(self.admin_store.customer_ownership(
            customer_id=self.customer["customer_id"]))
        account = self.admin_store.customer_account(self.customer["customer_id"])
        self.assertIsNone(account["last_logged_out_at"])
        self.confirm_user_offline(establish=False)
        self.assertIsNone(self.admin_store.customer_ownership(
            customer_id=self.customer["customer_id"]))
        self.assertIsNone(self.admin_store.customer_account(
            self.customer["customer_id"])["last_logged_out_at"])

    def test_offline_retainer_keeps_checkpoint_and_old_login_cannot_resume(self):
        self.login()
        local = self.start_retainer()
        old_login_id = local["login_id"]
        session_id = local["retainer_session_id"]
        self.admin.retainer_clocks[session_id] = (100.0, 1000.0)
        with patch("game_cafe.runtime.time.monotonic", return_value=110.0):
            self.admin._checkpoint_active_retainers(force=True)
        saved_balance = self.admin_store.customer_account(
            self.customer["customer_id"])["balance_seconds"]

        self.confirm_user_offline()

        account = self.admin_store.customer_account(self.customer["customer_id"])
        stopped = self.admin_store.retainer_session(session_id)
        self.assertEqual(account["balance_seconds"], saved_balance)
        self.assertIsNone(account["last_retainer_stopped_at"])
        self.assertEqual(stopped["state"], "stopped")
        self.assertEqual(stopped["stop_reason"], "pc_offline")
        self.assertNotIn(session_id, self.admin.retainer_clocks)
        with self.assertRaisesRegex(PermissionError, "no longer active"):
            self.admin._handle_customer_access({
                "action": "retainer_prepare",
                "customer_id": self.customer["customer_id"],
                "login_id": old_login_id,
                "operation_id": "stale-retainer",
            }, self.user_store.pc_id)

    def test_released_customer_can_move_and_old_pc_returns_to_guest(self):
        self.login()
        stale_login = self.user_store.local_customer_session()["login_id"]
        self.confirm_user_offline()
        replacement_pc = "c" * 32
        self.admin.change(lambda state: state["members"].update({
            replacement_pc: {"name": "PC-03", "public_key": "c" * 64,
                             "last_connected_at": time.time()}
        }))
        replacement = self.admin._handle_customer_access({
            "action": "login", "customer_id": self.customer["customer_id"],
            "password": "12345", "operation_id": "replacement-login",
        }, replacement_pc)
        self.assertNotEqual(replacement["ownership"]["login_id"], stale_login)

        self.user._reconcile_registered_customer()

        self.assertIsNone(self.user_store.local_customer_session())
        ownership = self.admin_store.customer_ownership(
            customer_id=self.customer["customer_id"])
        self.assertEqual(ownership["pc_id"], replacement_pc)
        self.assertEqual(ownership["login_id"],
                         replacement["ownership"]["login_id"])

    def test_authenticated_graceful_close_releases_without_duplicate_cooldown(self):
        self.login()
        self.admin.handle_member(
            "graceful_shutdown", {}, self.user_store.pc_id, "user")
        self.admin.handle_member(
            "graceful_shutdown", {}, self.user_store.pc_id, "user")
        self.assertIsNone(self.admin_store.customer_ownership(
            customer_id=self.customer["customer_id"]))
        self.assertIsNone(self.admin_store.customer_account(
            self.customer["customer_id"])["last_logged_out_at"])

    def test_admin_dashboard_retainer_state_disables_ordinary_controls(self):
        self.login()
        self.start_retainer()
        authoritative = self.admin.customer_pc_states()[self.user_store.pc_id]
        row = pc_row_data(
            self.admin.snapshot(), self.user_store.pc_id,
            {
                "retainer_active": authoritative["retainerActive"],
                "retainer_remaining": authoritative[
                    "retainerBalanceSeconds"],
                "registered_customer_name": authoritative["customerName"],
            }, time.time())
        self.assertEqual(row["phase"], "RETAINER")
        self.assertEqual(row["player"], "Sumit")
        self.assertTrue(row["retainerActive"])
        self.assertFalse(row["start"])
        self.assertFalse(row["add"])

    def test_registered_login_and_retainer_require_live_admin(self):
        self.user.peer_statuses.clear()
        with self.assertRaisesRegex(RuntimeError, "Admin is offline"):
            self.login()
        self.user.peer_statuses[self.admin_store.pc_id] = (
            time.time(), {"pc_id": self.admin_store.pc_id, "admin": True,
                          "admin_term": self.admin.snapshot()[
                              "active_admin"]["term"]})
        self.login()
        self.user.peer_statuses.clear()
        with self.assertRaisesRegex(RuntimeError, "Admin is offline"):
            self.start_retainer()

    def test_admin_signout_does_not_interrupt_but_shutdown_does(self):
        self.login()
        self.start_retainer()
        self.admin.admin_ui_open = False
        self.user._reconcile_registered_customer()
        self.assertTrue(self.user_store.local_customer_session()[
            "retainer_active"])
        self.admin.prepare_admin_shutdown()
        self.assertIsNone(self.user_store.local_customer_session())
        self.assertEqual(self.admin_store.active_retainers(), [])
        self.assertIsNone(self.admin_store.customer_ownership(
            customer_id=self.customer["customer_id"]))

    def test_restart_recovery_charges_no_offline_time_and_requires_login(self):
        self.login()
        local = self.start_retainer()
        session_id = local["retainer_session_id"]
        self.admin.retainer_clocks[session_id] = (100.0, 1000.0)
        with patch("game_cafe.runtime.time.monotonic", return_value=110.0):
            self.admin._checkpoint_active_retainers(force=True)
        saved = self.admin_store.customer_account(
            self.customer["customer_id"])["balance_seconds"]
        recovered = self.admin_store.recover_interrupted_retainers(now=99999)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(self.admin_store.customer_account(
            self.customer["customer_id"])["balance_seconds"], saved)
        self.assertIsNone(self.admin_store.customer_ownership(
            customer_id=self.customer["customer_id"]))

    def test_active_login_blocks_delete_and_admin_can_release_it(self):
        self.login()
        current = self.admin_store.customer_account(self.customer["customer_id"])
        with self.assertRaisesRegex(RuntimeError, "Log out"):
            self.admin.delete_customer(
                self.customer["customer_id"], "Sumit", current["revision"],
                "delete-blocked")
        self.admin.release_customer_ownership(self.customer["customer_id"])
        self.assertIsNone(self.user_store.local_customer_session())
        current = self.admin_store.customer_account(self.customer["customer_id"])
        self.admin.delete_customer(
            self.customer["customer_id"], "Sumit", current["revision"],
            "delete-after-release")
        self.assertIsNone(self.admin_store.customer_account(
            self.customer["customer_id"]))

    def test_registered_login_cannot_take_admin_or_be_handed_off(self):
        self.login()
        self.assertFalse(self.user.can_become_admin())
        snapshot = self.admin.snapshot()
        with self.assertRaisesRegex(PermissionError, "not eligible"):
            self.admin.handle_member(
                "prepare_admin_transfer",
                {"proof": {
                    "candidate": self.user_store.pc_id,
                    "pool_id": snapshot["pool_id"],
                    "term": snapshot["active_admin"]["term"] + 1,
                }, "signature": ""},
                self.user_store.pc_id, "user")


if __name__ == "__main__":
    unittest.main()
