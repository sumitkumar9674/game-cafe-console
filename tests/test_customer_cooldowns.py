"""Timestamp-based customer cooldown rules; no desktop switching."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from game_cafe.storage import Store, new_pool


PASSWORD = "long test password"


class CustomerCooldownTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "cooldowns.sqlite3"
        self.store = Store(self.path)
        snapshot, secret = new_pool(
            "Cafe", self.store.pc_id, "PC-01", "Sahil", PASSWORD,
            self.store.node_public_key())
        snapshot["settings"]["retainer_restart_cooldown_minutes"] = 2
        snapshot["settings"]["customer_login_cooldown_minutes"] = 1
        self.store.save_pool(snapshot, secret, join=True)
        self.customer = self.store.create_customer(
            "Sumit", "Sahil", "create-sumit", now=10)
        self.other = self.store.create_customer(
            "Sahil", "Sahil", "create-sahil", now=10)
        self.customer = self.store.adjust_customer_balance(
            self.customer["customer_id"], "add", 600,
            self.customer["revision"], 0, "Sahil", "add", now=20)
        self.other = self.store.adjust_customer_balance(
            self.other["customer_id"], "add", 60,
            self.other["revision"], 0, "Sahil", "add-other", now=20)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _login(self, customer, pc_id, operation, now):
        return self.store.authenticate_customer(
            customer["customer_id"], "12345", pc_id, operation, now=now)

    def test_logout_cooldown_uses_exact_boundary_across_pcs(self):
        state = self._login(self.customer, "pc-a", "login-a", 100)
        self.store.release_customer_login(
            self.customer["customer_id"], "pc-a",
            state["ownership"]["login_id"], record_logout=True, now=200)
        self.store.release_customer_login(
            self.customer["customer_id"], "pc-a",
            state["ownership"]["login_id"], record_logout=True, now=220)

        cooldown = self.store.customer_cooldown_state(
            self.customer["customer_id"], now=200)
        self.assertEqual(cooldown["login_expires_at"], 260)
        self.assertEqual(cooldown["login_remaining"], 60)
        with self.assertRaisesRegex(RuntimeError, "60 more seconds"):
            self._login(self.customer, "pc-b", "login-too-soon", 200)
        self.assertIsNotNone(
            self._login(self.customer, "pc-b", "login-boundary", 260))

    def test_retainer_stop_cooldown_is_customer_specific_and_idempotent(self):
        login = self._login(self.customer, "pc-a", "login-a", 100)
        login_id = login["ownership"]["login_id"]
        prepared = self.store.prepare_retainer(
            self.customer["customer_id"], "pc-a", login_id,
            "prepare-a", now=100)
        self.store.activate_retainer(
            prepared["session_id"], self.customer["customer_id"],
            "pc-a", login_id, now=100)
        result = self.store.consume_retainer(
            prepared["session_id"], 0, finalize=True,
            reason="customer", now=300)
        balance = result["account"]["balance_seconds"]
        self.assertEqual(balance, 600)
        self.assertEqual(
            result["account"]["last_retainer_stopped_at"], 300)
        with self.assertRaisesRegex(RuntimeError, "120 more seconds"):
            self.store.prepare_retainer(
                self.customer["customer_id"], "pc-a", login_id,
                "prepare-too-soon", now=300)

        other_login = self._login(self.other, "pc-b", "other-login", 300)
        self.assertIsNotNone(self.store.prepare_retainer(
            self.other["customer_id"], "pc-b",
            other_login["ownership"]["login_id"], "other", now=300))
        self.assertIsNotNone(self.store.prepare_retainer(
            self.customer["customer_id"], "pc-a", login_id,
            "prepare-boundary", now=420))
        with self.assertRaisesRegex(RuntimeError, "not active"):
            self.store.consume_retainer(
                prepared["session_id"], 0, finalize=True,
                reason="customer", now=500)
        saved = self.store.customer_account(self.customer["customer_id"])
        self.assertEqual(saved["last_retainer_stopped_at"], 300)
        self.assertEqual(saved["balance_seconds"], balance)

    def test_overrides_global_changes_clear_and_restart_persist(self):
        account = self.store.set_customer_cooldown_overrides(
            self.customer["customer_id"], 0, 3,
            self.customer["revision"], "Sahil", now=30)
        self.store.db.execute(
            "UPDATE customer_accounts SET last_retainer_stopped_at=100,"
            "last_logged_out_at=100 WHERE pool_id=? AND customer_id=?",
            (self.store.current_pool_id, self.customer["customer_id"]))
        self.store.db.commit()
        cooldown = self.store.customer_cooldown_state(
            self.customer["customer_id"], now=150)
        self.assertEqual(cooldown["retainer_remaining"], 0)
        self.assertEqual(cooldown["login_remaining"], 130)

        snapshot = self.store.snapshot()
        snapshot["settings"]["customer_login_cooldown_minutes"] = 10
        snapshot["revision"] += 1
        self.store.save_pool(snapshot, self.store.secret())
        # The per-customer value remains authoritative after a global change.
        self.assertEqual(self.store.customer_cooldown_state(
            self.customer["customer_id"], now=150)["login_remaining"], 130)

        cleared = self.store.clear_customer_cooldown(
            self.customer["customer_id"], "login", "Sahil", now=160)
        self.assertIsNone(cleared["last_logged_out_at"])
        self.assertEqual(self.store.customer_cooldown_state(
            cleared, now=160)["login_remaining"], 0)
        self.store.close()
        self.store = Store(self.path)
        reopened = self.store.customer_account(self.customer["customer_id"])
        self.assertEqual(reopened["retainer_cooldown_minutes"], 0)
        self.assertEqual(reopened["login_cooldown_minutes"], 3)
        self.assertIsNone(reopened["last_logged_out_at"])


if __name__ == "__main__":
    unittest.main()
