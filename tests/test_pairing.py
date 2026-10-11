"""Six-digit approval layered over encrypted, identity-bound pairing."""

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from game_cafe.runtime import Runtime
from game_cafe.storage import Store, new_pool


class PairingTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.admin_store = Store(root / "admin.sqlite3")
        self.joiner_store = Store(root / "joiner.sqlite3")
        snap, secret = new_pool(
            "Test Café", self.admin_store.pc_id, "PC-01", "Owner",
            "long test password", self.admin_store.node_public_key())
        self.admin_store.save_pool(snap, secret, join=True)
        self.admin = Runtime(self.admin_store)
        self.joiner = Runtime(self.joiner_store)
        with (patch.object(self.admin, "_discover_peers_once", return_value=[]),
              patch.object(self.admin, "_replicate_once")):
            self.admin.login_admin("long test password")

    def tearDown(self):
        self.admin_store.close()
        self.joiner_store.close()
        self.temp.cleanup()

    def prepare(self, name="PC-02"):
        return self.joiner.prepare_join_request(
            self.admin_store.current_pool_id, name)

    def submit(self, request, ip="127.0.0.2"):
        reply = self.admin.handle_plain("join_request", request["payload"], ip)
        request["admin_public"] = reply["pairing_admin_public"]
        return reply

    def test_code_is_immediate_six_digits_and_preserves_leading_zeroes(self):
        with patch("game_cafe.runtime.secrets.randbelow", return_value=42):
            request = self.prepare()
        self.assertEqual(request["code"], "000042")
        self.assertRegex(request["code"], r"^\d{6}$")
        self.assertEqual(request["payload"]["verification_code"], "000042")

    def test_correct_code_approves_identity_without_exposing_code_in_pending_ui_data(self):
        request = self.prepare()
        self.admin.admin_ui_open = False
        self.submit(request)
        pending = self.admin_store.pending_joins()[0]
        self.assertNotIn("verification_code", pending)
        self.assertNotIn(request["code"], str(pending))

        self.admin.approve_join(request["request_id"], self.joiner_store.pc_id,
                                request["code"], "PC-02")
        self.assertIn(self.joiner_store.pc_id,
                      self.admin_store.snapshot()["members"])
        self.assertFalse(self.admin.admin_ui_open)
        with self.assertRaisesRegex(ValueError, "no longer pending"):
            self.admin.approve_join(request["request_id"], self.joiner_store.pc_id,
                                    request["code"], "PC-02")

    def test_wrong_code_is_bound_to_request_and_limited_to_five_attempts(self):
        request = self.prepare()
        self.submit(request)
        wrong = "999999" if request["code"] != "999999" else "888888"
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.admin.approve_join(request["request_id"],
                                        self.joiner_store.pc_id, wrong, "PC-02")
        decision = self.admin.handle_plain(
            "join_poll", {"request_id": request["request_id"]}, "127.0.0.2")
        self.assertEqual(decision["state"], "rejected")
        self.assertNotIn(self.joiner_store.pc_id,
                         self.admin_store.snapshot()["members"])

    def test_code_cannot_approve_another_device_request(self):
        request = self.prepare()
        self.submit(request)
        other_store = Store(Path(self.temp.name) / "other.sqlite3")
        try:
            other = Runtime(other_store)
            other_request = other.prepare_join_request(
                self.admin_store.current_pool_id, "PC-03")
            self.submit(other_request, "127.0.0.3")
            with self.assertRaises(PermissionError):
                self.admin.approve_join(other_request["request_id"],
                                        other_store.pc_id, request["code"], "PC-03")
            self.assertNotIn(other_store.pc_id,
                             self.admin_store.snapshot()["members"])
        finally:
            other_store.close()

    def test_expired_and_cancelled_requests_cannot_be_approved(self):
        expired = self.prepare()
        self.submit(expired)
        with self.admin_store._lock, self.admin_store.db:
            self.admin_store.db.execute(
                "UPDATE pending_joins SET expires_at=? WHERE request_id=?",
                (time.time() - 1, expired["request_id"]))
        with self.assertRaisesRegex(ValueError, "no longer pending"):
            self.admin.approve_join(expired["request_id"],
                                    self.joiner_store.pc_id,
                                    expired["code"], "PC-02")

        cancelled = self.prepare()
        self.submit(cancelled)
        reply = self.admin.handle_plain("join_cancel", {
            "request_id": cancelled["request_id"],
            "pc_id": self.joiner_store.pc_id,
            "cancel_token": cancelled["cancel_token"],
        }, "127.0.0.2")
        self.assertEqual(reply["state"], "cancelled")
        with self.assertRaisesRegex(ValueError, "no longer pending"):
            self.admin.approve_join(cancelled["request_id"],
                                    self.joiner_store.pc_id,
                                    cancelled["code"], "PC-02")

    def test_wrong_token_cannot_cancel_request(self):
        request = self.prepare()
        self.submit(request)
        with self.assertRaises(PermissionError):
            self.admin.handle_plain("join_cancel", {
                "request_id": request["request_id"],
                "pc_id": self.joiner_store.pc_id,
                "cancel_token": "0" * 64,
            }, "127.0.0.3")
        self.assertEqual(len(self.admin_store.pending_joins()), 1)

    def test_joiner_saves_encrypted_authenticated_membership(self):
        def route(ip, operation, data):
            return self.admin.handle_plain(operation, data, "127.0.0.2")

        with (patch("game_cafe.runtime.plain_call", side_effect=route),
              patch("game_cafe.runtime.authenticated_call",
                    return_value={"pc_id": self.admin_store.pc_id}),
              patch.object(self.joiner.network, "call", return_value={})):
            request = self.joiner.request_join(
                "127.0.0.1", self.admin_store.current_pool_id, "PC-02")
            self.assertEqual(self.joiner.finish_join(
                "127.0.0.1", request["request_id"], request["private"]),
                ("pending", None))
            self.admin.approve_join(request["request_id"], self.joiner_store.pc_id,
                                    request["code"], "PC-02")
            self.assertEqual(self.joiner.finish_join(
                "127.0.0.1", request["request_id"], request["private"]),
                ("accepted", None))
        self.assertEqual(self.joiner_store.current_pool_id,
                         self.admin_store.current_pool_id)
        self.assertEqual(self.joiner_store.secret(), self.admin_store.secret())


if __name__ == "__main__":
    unittest.main()
