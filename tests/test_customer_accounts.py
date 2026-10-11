"""Stage 2 customer storage and LAN replication without desktop switching."""

from __future__ import annotations

import copy
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from game_cafe.customers import retainer_seconds
from game_cafe.runtime import Runtime
from game_cafe.storage import Store, new_pool, verify_password


PASSWORD = "long test password"


class CustomerStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "cafe.sqlite3"
        self.store = Store(self.path)
        snapshot, secret = new_pool(
            "Cafe", self.store.pc_id, "PC-01", "Sahil", PASSWORD,
            self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_additive_schema_migration_preserves_pool_and_restart_data(self):
        pool_id = self.store.current_pool_id
        customer = self.store.create_customer(
            "  Sumit   Kumar  ", "Sahil", "create-1", now=10.0)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.current_pool_id, pool_id)
        saved = self.store.customer_account(customer["customer_id"])
        self.assertEqual(saved["name"], "Sumit Kumar")
        tables = {row[0] for row in self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"customer_accounts", "customer_adjustments",
                         "customer_tombstones", "customer_sync_acks"}
                        .issubset(tables))

    def test_additive_schema_is_created_for_an_existing_database(self):
        pool_id = self.store.current_pool_id
        self.store.close()
        with closing(sqlite3.connect(self.path)) as legacy, legacy:
            for table in (
                    "customer_accounts", "customer_adjustments",
                    "customer_tombstones", "customer_operations",
                    "customer_sync_acks", "customer_sync_state",
                    "customer_ownership", "retainer_sessions"):
                legacy.execute(f"DROP TABLE {table}")
        self.store = Store(self.path)
        self.assertEqual(self.store.current_pool_id, pool_id)
        self.assertEqual(self.store.customer_accounts(), [])
        tables = {row[0] for row in self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"customer_accounts", "customer_adjustments",
                         "customer_tombstones", "customer_operations",
                         "customer_sync_acks", "customer_sync_state",
                         "customer_ownership", "retainer_sessions"}
                        .issubset(tables))

    def test_create_is_unique_secure_zero_balance_and_idempotent(self):
        customer = self.store.create_customer(
            " Sumit ", "Sahil", "create-1", now=10.0)
        repeated = self.store.create_customer(
            "Sumit", "Sahil", "create-1", now=11.0)
        self.assertEqual(repeated["customer_id"], customer["customer_id"])
        self.assertEqual(customer["balance_seconds"], 0)
        self.assertEqual(customer["revision"], 1)
        self.assertTrue(verify_password(
            "12345", self.store.customer_password_verifier(
                customer["customer_id"])))
        raw = self.store.db.execute(
            "SELECT password_verifier FROM customer_accounts"
        ).fetchone()[0]
        self.assertNotIn("12345", raw)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.create_customer("sUMIT", "Sahil", "create-2")
        with self.assertRaisesRegex(ValueError, "Operation ID"):
            self.store.create_customer("Another Name", "Sahil", "create-1")
        self.assertEqual(len(self.store.customer_accounts()), 1)

    def test_balance_adjustment_is_atomic_audited_and_idempotent(self):
        customer = self.store.create_customer("Sumit", "Sahil", "create")
        added = self.store.adjust_customer_balance(
            customer["customer_id"], "add", 5400, 1, 0,
            "Sahil", "add-1", now=20.0)
        repeated = self.store.adjust_customer_balance(
            customer["customer_id"], "add", 5400, 1, 0,
            "Sahil", "add-1", now=21.0)
        self.assertEqual(added["balance_seconds"], 5400)
        self.assertEqual(repeated["balance_seconds"], 5400)
        with self.assertRaisesRegex(ValueError, "Operation ID"):
            self.store.adjust_customer_balance(
                customer["customer_id"], "add", 60, 1, 0,
                "Sahil", "add-1")
        history = self.store.customer_adjustments(customer["customer_id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["previous_balance"], 0)
        self.assertEqual(history[0]["resulting_balance"], 5400)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.store.adjust_customer_balance(
                customer["customer_id"], "add", 60, 1, 0,
                "Sahil", "stale")
        with self.assertRaisesRegex(ValueError, "below zero"):
            self.store.adjust_customer_balance(
                customer["customer_id"], "reduce", 7200, 2, 5400,
                "Sahil", "too-much")
        reduced = self.store.adjust_customer_balance(
            customer["customer_id"], "reduce", 1800, 2, 5400,
            "Sahil", "reduce-1", now=30.0)
        self.assertEqual(reduced["balance_seconds"], 3600)
        self.assertEqual(len(self.store.customer_adjustments(
            customer["customer_id"])), 2)

    def test_duration_validation(self):
        self.assertEqual(retainer_seconds("1", "30"), 5400)
        for hours, minutes in (("-1", "0"), ("0", "60"),
                               ("0", "0"), ("lots", "1"),
                               ("10000", "0")):
            with self.subTest(hours=hours, minutes=minutes):
                with self.assertRaises(ValueError):
                    retainer_seconds(hours, minutes)

    def test_delete_removes_account_history_and_leaves_minimal_tombstone(self):
        customer = self.store.create_customer("Sumit", "Sahil", "create")
        updated = self.store.adjust_customer_balance(
            customer["customer_id"], "add", 3600, 1, 0,
            "Sahil", "add")
        with self.assertRaises(ValueError):
            self.store.delete_customer(customer["customer_id"], "sumit",
                                       updated["revision"], "delete-wrong")
        revision = self.store.delete_customer(
            customer["customer_id"], "Sumit", updated["revision"], "delete")
        self.assertEqual(revision, 3)
        self.assertIsNone(self.store.customer_account(customer["customer_id"]))
        self.assertEqual(self.store.customer_adjustments(customer["customer_id"]), [])
        row = self.store.db.execute(
            "SELECT customer_id,revision FROM customer_tombstones"
        ).fetchone()
        self.assertEqual(row, (customer["customer_id"], 3))

    def test_handoff_rejects_missing_verifier_without_partial_apply(self):
        candidate = Store(Path(self.temp.name) / "candidate.sqlite3")
        try:
            snapshot = self.store.snapshot()
            snapshot["members"][candidate.pc_id] = {
                "name": "PC-02", "public_key": candidate.node_public_key()}
            self.store.save_pool(snapshot, self.store.secret(), join=True)
            candidate.save_pool(snapshot, self.store.secret(), join=True)

            self.store.create_customer("Source Customer", "Sahil", "source")
            marker = candidate.create_customer(
                "Candidate Marker", "Sahil", "marker")
            transfer_id = "transfer-with-missing-verifier"
            bundle = self.store.authoritative_handoff_bundle(
                self.store.pc_id, candidate.pc_id, 2, transfer_id)
            corrupt = copy.deepcopy(bundle)
            corrupt["state"]["accounts"][0]["password_verifier"] = None
            corrupt["state_digest"] = Store._handoff_digest(corrupt["state"])

            with self.assertRaisesRegex(ValueError, "invalid customer account"):
                candidate.apply_authoritative_handoff_bundle(
                    corrupt, self.store.pc_id, candidate.pc_id, 2,
                    transfer_id)
            self.assertEqual(
                candidate.customer_account(marker["customer_id"])["name"],
                "Candidate Marker")
        finally:
            candidate.close()

    def test_replica_rejects_older_updates_and_tombstone_blocks_resurrection(self):
        customer = self.store.create_customer("Sumit", "Sahil", "create")
        initial = self.store.customer_replica_payload()["accounts"][0]
        updated = self.store.adjust_customer_balance(
            customer["customer_id"], "add", 3600, 1, 0,
            "Sahil", "add")
        latest = self.store.customer_replica_payload()["accounts"][0]

        replica = Store(Path(self.temp.name) / "replica.sqlite3")
        try:
            snapshot = self.store.snapshot()
            snapshot["members"][replica.pc_id] = {
                "name": "PC-02", "public_key": replica.node_public_key()}
            replica.save_pool(snapshot, self.store.secret(), join=True)
            acknowledgments = replica.apply_customer_replica([latest], [])
            self.assertEqual(acknowledgments[customer["customer_id"]], 2)
            duplicate = replica.apply_customer_replica([latest], [])
            self.assertEqual(duplicate[customer["customer_id"]], 2)
            replica.apply_customer_replica([initial], [])
            self.assertEqual(replica.customer_account(
                customer["customer_id"])["balance_seconds"], 3600)
            self.assertIsNone(replica.customer_password_verifier(
                customer["customer_id"]))

            self.store.delete_customer(
                customer["customer_id"], "Sumit", updated["revision"], "delete")
            tombstone = self.store.customer_replica_payload()["tombstones"][0]
            replica.apply_customer_replica([], [tombstone])
            self.assertIsNone(replica.customer_account(customer["customer_id"]))
            replica.apply_customer_replica([latest], [])
            self.assertIsNone(replica.customer_account(customer["customer_id"]))
        finally:
            replica.close()


class CustomerReplicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.admin_store = Store(root / "admin.sqlite3")
        self.user_store = Store(root / "user.sqlite3")
        snapshot, secret = new_pool(
            "Cafe", self.admin_store.pc_id, "PC-01", "Sahil", PASSWORD,
            self.admin_store.node_public_key())
        snapshot["members"][self.user_store.pc_id] = {
            "name": "PC-02", "public_key": self.user_store.node_public_key()}
        for store in (self.admin_store, self.user_store):
            store.save_pool(snapshot, secret, join=True)
        self.admin = Runtime(self.admin_store)
        self.user = Runtime(self.user_store)
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
        ]
        for item in self.patches:
            item.start()
        self.admin.login_admin(PASSWORD)
        self.admin.admin_ui_open = True
        self.admin.peer_statuses[self.user_store.pc_id] = (
            time.time(), {"pc_id": self.user_store.pc_id})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.admin_store.close()
        self.user_store.close()
        self.temp.cleanup()

    def test_create_adjust_delete_replicates_with_durable_ack(self):
        customer = self.admin.create_customer("Sumit", "create")
        self.admin._replicate_once()
        replica = self.user_store.customer_account(customer["customer_id"])
        self.assertEqual(replica["balance_seconds"], 0)
        self.assertIsNone(self.user_store.customer_password_verifier(
            customer["customer_id"]))
        self.assertEqual(self.user_store.customer_adjustments(
            customer["customer_id"]), [])
        state = self.admin.customer_admin_state(selected_id=customer["customer_id"])
        self.assertEqual(state["selected"]["syncStatus"], "Synced")

        preview = self.admin.preview_customer_adjustment(
            customer["customer_id"], "add", "2", "15")
        self.admin.adjust_customer(preview)
        self.admin._replicate_once()
        self.assertEqual(self.user_store.customer_account(
            customer["customer_id"])["balance_seconds"], 8100)

        current = self.admin_store.customer_account(customer["customer_id"])
        self.admin.delete_customer(customer["customer_id"], "Sumit",
                                   current["revision"], "delete")
        self.admin._replicate_once()
        self.assertIsNone(self.user_store.customer_account(customer["customer_id"]))
        tombstone = self.user_store.db.execute(
            "SELECT revision FROM customer_tombstones WHERE customer_id=?",
            (customer["customer_id"],)).fetchone()
        self.assertEqual(tombstone[0], current["revision"] + 1)

    def test_offline_change_catches_up_and_user_cannot_publish_mutations(self):
        self.admin.peer_statuses.pop(self.user_store.pc_id, None)
        customer = self.admin.create_customer("Offline User", "create")
        self.assertIsNone(self.user_store.customer_account(customer["customer_id"]))
        state = self.admin.customer_admin_state(selected_id=customer["customer_id"])
        self.assertEqual(state["selected"]["syncStatus"], "Pending / Offline")
        self.admin.peer_statuses[self.user_store.pc_id] = (
            time.time(), {"pc_id": self.user_store.pc_id})
        self.admin._replicate_once()
        self.assertIsNotNone(self.user_store.customer_account(customer["customer_id"]))
        with self.assertRaises(PermissionError):
            self.admin.handle_member(
                "customer_sync", self.user_store.customer_replica_payload(),
                self.user_store.pc_id, "user")

    def test_missing_durable_ack_is_reported_and_staff_auth_is_enforced(self):
        customer = self.admin.create_customer("Sumit", "create")
        state = self.admin.customer_admin_state(selected_id=customer["customer_id"])
        self.assertEqual(state["selected"]["syncStatus"], "Synchronizing")

        def missing_ack(ip, operation, data):
            if operation == "customer_sync":
                return {"acknowledgments": {}}
            return self.user.handle_member(
                operation, data, self.admin_store.pc_id, ip)

        with patch.object(self.admin.network, "call", side_effect=missing_ack):
            self.admin._replicate_once()
        state = self.admin.customer_admin_state(selected_id=customer["customer_id"])
        self.assertEqual(state["selected"]["syncStatus"], "Error")

        self.admin.admin_ui_open = False
        with self.assertRaisesRegex(PermissionError, "staff"):
            self.admin.create_customer("Blocked", "blocked")


if __name__ == "__main__":
    unittest.main()
