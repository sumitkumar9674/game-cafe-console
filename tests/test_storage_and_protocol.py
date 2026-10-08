"""Check durable identity and rejection of altered or replayed LAN messages."""

from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import secrets
import sqlite3
import unittest
from unittest.mock import patch

from game_cafe.network import (PAIRING_ALPHABET, create_pairing_key,
                               open_welcome, pairing_code, pairing_secret,
                               seal_welcome, signed, verify_signed)
from game_cafe.storage import Store, new_pool, verify_password
from game_cafe.security import (create_admin_signing_record,
                                create_node_keypair, sign_admin_proof,
                                unlock_admin_signing_key, verify_admin_proof)


class StorageAndProtocolTest(unittest.TestCase):
    def test_existing_database_gets_pairing_columns_without_data_reset(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "existing.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE pending_joins (request_id TEXT PRIMARY KEY, "
                       "pc_id TEXT NOT NULL, proposed_name TEXT NOT NULL, "
                       "ip TEXT NOT NULL, public_key TEXT NOT NULL, "
                       "created_at REAL NOT NULL, decision TEXT NOT NULL DEFAULT 'pending', "
                       "sealed_welcome TEXT)")
            db.execute("INSERT INTO pending_joins "
                       "(request_id,pc_id,proposed_name,ip,public_key,created_at) "
                       "VALUES('old','pc','Old PC','127.0.0.1','key',0)")
            db.commit()
            db.close()
            store = Store(path)
            try:
                columns = {row[1] for row in store.db.execute(
                    "PRAGMA table_info(pending_joins)"
                )}
                self.assertTrue({"pairing_public", "pairing_secret", "expires_at",
                                 "attempts"} <= columns)
                self.assertEqual(store.db.execute(
                    "SELECT proposed_name FROM pending_joins WHERE request_id='old'"
                ).fetchone()[0], "Old PC")
            finally:
                store.close()

    def test_identity_snapshot_and_password_survive_reopen(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            store = Store(path)
            pc_id = store.pc_id
            with patch("game_cafe.storage.create_admin_signing_record",
                       return_value={"public": "test-verifier"}):
                snapshot, secret = new_pool("Example Café", pc_id, "PC-01",
                                            "Owner", "long test password", "test-public")
            store.save_pool(snapshot, secret, join=True)
            self.assertTrue(verify_password("long test password",
                                            snapshot["admin_password"]))
            self.assertFalse(verify_password("wrong", snapshot["admin_password"]))
            store.update(lambda state: state.update(cafe_name="Renamed Café"))
            store.close()

            reopened = Store(path)
            self.assertEqual(reopened.pc_id, pc_id)
            self.assertEqual(reopened.snapshot()["cafe_name"], "Renamed Café")
            self.assertEqual(reopened.snapshot()["revision"], 2)
            self.assertEqual(reopened.secret(), secret)
            reopened.close()

    def test_signed_message_rejects_tampering_and_replay(self):
        secret = "a5" * 32
        message = signed(secret, "pc-1", "pool-1", "status", {})
        seen = {}
        verify_signed(message, secret, {"pc-1": {}}, seen)
        with self.assertRaises(PermissionError):
            verify_signed(message, secret, {"pc-1": {}}, seen)
        tampered = dict(message)
        tampered["operation"] = "push_snapshot"
        with self.assertRaises(PermissionError):
            verify_signed(tampered, secret, {"pc-1": {}})

    @unittest.skipUnless(importlib.util.find_spec("cryptography"),
                         "cryptography is not installed in this environment")
    def test_member_signature_prevents_impersonation(self):
        private, public = create_node_keypair()
        other_private, _ = create_node_keypair()
        secret = "a5" * 32
        members = {"pc-1": {"public_key": public}}
        valid = signed(secret, "pc-1", "pool-1", "status", {}, private)
        verify_signed(valid, secret, members)
        forged = signed(secret, "pc-1", "pool-1", "status", {}, other_private)
        with self.assertRaises(PermissionError):
            verify_signed(forged, secret, members)

    @unittest.skipUnless(importlib.util.find_spec("cryptography"),
                         "cryptography is not installed in this environment")
    def test_pairing_and_admin_proof_require_their_secrets(self):
        applicant_private, applicant_public = create_pairing_key()
        admin_private, admin_public = create_pairing_key()
        shared = pairing_secret(applicant_private, admin_public)
        self.assertEqual(shared, pairing_secret(admin_private, applicant_public))
        request_id = secrets.token_hex(16)
        code = pairing_code(shared, request_id)
        self.assertEqual(len(code), 7)
        self.assertTrue(set(code) <= set(PAIRING_ALPHABET))
        self.assertFalse(set(code) & set("O0I1"))
        welcome = {"pool_id": "pool-1", "secret": secrets.token_hex(32)}
        sealed = seal_welcome(shared, request_id, welcome)
        self.assertEqual(open_welcome(shared, request_id, sealed), welcome)
        with self.assertRaises(Exception):
            open_welcome(secrets.token_bytes(32), request_id, sealed)
        with self.assertRaises(Exception):
            open_welcome(shared, secrets.token_hex(16), sealed)

        admin_record = create_admin_signing_record("long test password")
        admin_key = unlock_admin_signing_key("long test password", admin_record)
        with self.assertRaises(Exception):
            unlock_admin_signing_key("wrong password", admin_record)
        proof = {"candidate": "pc-1", "term": 3, "challenge": secrets.token_hex(16)}
        signature = sign_admin_proof(admin_key, proof)
        verify_admin_proof(admin_record["public"], proof, signature)
        with self.assertRaises(Exception):
            verify_admin_proof(admin_record["public"],
                               {**proof, "term": 4}, signature)


if __name__ == "__main__":
    unittest.main()
