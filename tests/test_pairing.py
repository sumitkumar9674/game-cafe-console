"""Short human verification with full-strength encrypted join credentials."""

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
import uuid
from unittest.mock import patch

from game_cafe.network import (PAIRING_ALPHABET, create_pairing_key,
                               open_welcome, pairing_code, pairing_secret)
from game_cafe.runtime import Runtime
from game_cafe.storage import Store, new_pool


class PairingTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.admin_store = Store(root / "admin.sqlite3")
        self.joiner_store = Store(root / "joiner.sqlite3")
        snap, secret = new_pool("Test Café", self.admin_store.pc_id, "PC-01",
                                "Owner", "long test password",
                                self.admin_store.node_public_key())
        self.admin_store.save_pool(snap, secret, join=True)
        self.admin = Runtime(self.admin_store)
        with (patch.object(self.admin, "_discover_peers_once", return_value=[]),
              patch.object(self.admin, "_replicate_once")):
            self.admin.login_admin("long test password")

    def tearDown(self):
        self.admin_store.close()
        self.joiner_store.close()
        self.temp.cleanup()

    def request(self):
        private, public = create_pairing_key()
        request_id = uuid.uuid4().hex
        self.admin.handle_plain("join_request", {
            "pool_id": self.admin_store.current_pool_id,
            "pc_id": self.joiner_store.pc_id,
            "request_id": request_id, "name": "PC-02",
            "public_key": self.joiner_store.node_public_key(),
            "pairing_public": public,
        }, "127.0.0.2")
        return request_id, private

    def test_approved_join_keeps_full_identity_and_secret(self):
        request_id, private = self.request()
        self.admin.prepare_join_pairing(request_id)
        reply = self.admin.handle_plain("join_poll", {"request_id": request_id},
                                        "127.0.0.2")
        shared = pairing_secret(private, reply["pairing_admin_public"])
        code = pairing_code(shared, request_id)
        self.assertEqual(len(code), 7)
        self.assertTrue(set(code) <= set(PAIRING_ALPHABET))
        self.admin.approve_join(request_id, self.joiner_store.pc_id, code, "PC-02")
        accepted = self.admin.handle_plain("join_poll", {"request_id": request_id},
                                           "127.0.0.2")
        self.assertEqual(accepted["state"], "accepted")
        welcome = open_welcome(shared, request_id, accepted["sealed_welcome"])
        self.assertEqual(welcome["secret"], self.admin_store.secret())
        self.assertEqual(welcome["pc_id"], self.joiner_store.pc_id)
        self.assertEqual(welcome["snapshot"]["members"][self.joiner_store.pc_id]
                         ["public_key"], self.joiner_store.node_public_key())
        self.joiner_store.save_pool(welcome["snapshot"], welcome["secret"], join=True)
        self.assertEqual(self.joiner_store.pc_id, welcome["pc_id"])
        with self.assertRaisesRegex(ValueError, "no longer pending"):
            self.admin.approve_join(request_id, self.joiner_store.pc_id, code, "PC-02")

    def test_wrong_code_limit_and_expiry(self):
        request_id, private = self.request()
        self.admin.prepare_join_pairing(request_id)
        expected = pairing_code(pairing_secret(
            private, self.admin.handle_plain("join_poll", {"request_id": request_id},
                                             "127.0.0.2")["pairing_admin_public"]
        ), request_id)
        wrong = "AAAAAAA" if expected != "AAAAAAA" else "BBBBBBB"
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.admin.approve_join(request_id, self.joiner_store.pc_id,
                                        wrong, "PC-02")
        self.assertEqual(self.admin.handle_plain(
            "join_poll", {"request_id": request_id}, "127.0.0.2"
        )["state"], "rejected")
        self.assertNotIn(self.joiner_store.pc_id,
                         self.admin_store.snapshot()["members"])

        renewed_id, _ = self.request()
        self.assertNotEqual(renewed_id, request_id)
        with self.admin_store._lock, self.admin_store.db:
            self.admin_store.db.execute(
                "UPDATE pending_joins SET expires_at=? WHERE request_id=?",
                (time.time() - 1, renewed_id)
            )
        self.assertEqual(self.admin.handle_plain(
            "join_poll", {"request_id": renewed_id}, "127.0.0.2"
        )["state"], "expired")
        with self.assertRaisesRegex(ValueError, "expired"):
            self.admin.prepare_join_pairing(renewed_id)

    def test_joiner_sees_code_then_saves_authenticated_membership(self):
        joiner = Runtime(self.joiner_store)
        def route(ip, operation, data):
            return self.admin.handle_plain(operation, data, "127.0.0.2")

        with (patch("game_cafe.runtime.plain_call", side_effect=route),
              patch("game_cafe.runtime.authenticated_call",
                    return_value={"pc_id": self.admin_store.pc_id}),
              patch.object(joiner.network, "call", return_value={})):
            request_id, private = joiner.request_join(
                "127.0.0.1", self.admin_store.current_pool_id, "PC-02"
            )
            self.assertEqual(joiner.finish_join("127.0.0.1", request_id, private),
                             ("pending", None))
            self.admin.prepare_join_pairing(request_id)
            state, code = joiner.finish_join("127.0.0.1", request_id, private)
            self.assertEqual(state, "verify")
            self.assertEqual(len(code), 7)
            self.admin.approve_join(request_id, self.joiner_store.pc_id, code, "PC-02")
            self.assertEqual(joiner.finish_join("127.0.0.1", request_id, private),
                             ("accepted", None))
        self.assertEqual(self.joiner_store.current_pool_id,
                         self.admin_store.current_pool_id)
        self.assertEqual(self.joiner_store.secret(), self.admin_store.secret())


if __name__ == "__main__":
    unittest.main()
