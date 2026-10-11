"""Role assignment and Admin handoff without Tk windows or real sockets."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from game_cafe.runtime import PEER_ONLINE_SECONDS, Runtime, customer_controls
from game_cafe.security import (create_local_command_key, open_local_password,
                                seal_local_password)
from game_cafe.storage import (Store, hash_customer_password, new_pool,
                               verify_password)
from game_cafe.presentation import pc_row_data
from game_cafe.qt_bridge import CafeBridge


PASSWORD = "long test password"


class StartupRoleTest(unittest.TestCase):
    def test_discovery_deduplicates_cafes(self):
        found = [{"pool_id": "one", "pc_id": "a", "active_admin": "b",
                  "cafe_name": "Cafe", "ip": "10.0.0.2"},
                 {"pool_id": "one", "pc_id": "b", "active_admin": "b",
                  "cafe_name": "Cafe", "ip": "10.0.0.1"}]
        self.assertEqual(CafeBridge._distinct_pools(found)[0]["ip"], "10.0.0.1")

    def test_admin_password_command_is_sealed_before_persistence(self):
        private, public = create_local_command_key()
        package = seal_local_password(public, PASSWORD)
        self.assertNotIn(PASSWORD, str(package))
        self.assertEqual(open_local_password(private, package), PASSWORD)
        changed = dict(package)
        ciphertext = bytearray(bytes.fromhex(changed["sealed"]))
        ciphertext[0] ^= 1
        changed["sealed"] = ciphertext.hex()
        with self.assertRaises(Exception):
            open_local_password(private, changed)


class AdminHandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        base = Path(self.temp.name)
        self.first = Store(base / "first.sqlite3")
        self.second = Store(base / "second.sqlite3")
        snap, secret = new_pool("Test Café", self.first.pc_id, "PC-01",
                                "Owner", PASSWORD, self.first.node_public_key())
        snap["members"][self.second.pc_id] = {
            "name": "PC-02", "public_key": self.second.node_public_key()
        }
        self.offline_ids = [f"{number:032x}" for number in (3, 4, 5)]
        for number, pc_id in enumerate(self.offline_ids, start=3):
            snap["members"][pc_id] = {
                "name": f"PC-{number:02d}", "public_key": "c" * 64
            }
        self.first.save_pool(snap, secret, join=True)
        self.second.save_pool(snap, secret, join=True)
        self.admin = Runtime(self.first)
        self.user = Runtime(self.second)
        self.first.note_peer(self.second.pc_id, "second")
        self.second.note_peer(self.first.pc_id, "first")

        def route(sender, ip, operation, data):
            destination = self.admin if ip == "first" else self.user
            return destination.handle_member(operation, data, sender.pc_id,
                                             "first" if sender is self.first else "second")

        self.patches = [
            patch.object(self.admin.network, "call",
                         side_effect=lambda ip, op, data: route(self.first, ip, op, data)),
            patch.object(self.user.network, "call",
                         side_effect=lambda ip, op, data: route(self.second, ip, op, data)),
            patch.object(self.admin, "_discover_peers_once", return_value=[]),
            patch.object(self.user, "_discover_peers_once", return_value=[]),
            patch.object(self.admin, "_reconcile_local_access"),
            patch.object(self.user, "_reconcile_local_access"),
            patch.object(self.user, "_local_access_ready", return_value=True),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.first.close()
        self.second.close()
        self.temp.cleanup()

    def test_transfer_revokes_old_admin_and_preserves_shared_state(self):
        self.admin.login_admin(PASSWORD)
        self.admin.admin_action("settings", cafe_name="Updated Café",
                                admin_name="Owner", grace_minutes=12,
                                auto_signout_minutes=30)
        self.admin.admin_action("start", self.second.pc_id,
                                kind="open", paid_minutes=0, buffer_minutes=0)
        self.admin.admin_action("end", self.second.pc_id, reason="admin")
        self.assertFalse(any(customer_controls(
            self.first.snapshot(), self.first.pc_id).values()))
        with self.assertRaises(ValueError):
            self.admin.admin_action("start", self.first.pc_id,
                                    kind="open", paid_minutes=0,
                                    buffer_minutes=0)

        self.user.take_admin(PASSWORD)
        self.assertFalse(self.admin.is_admin())
        self.assertTrue(self.user.is_admin())
        self.assertEqual(self.first.snapshot()["active_admin"]["pc_id"],
                         self.second.pc_id)
        self.assertEqual(self.first.snapshot()["cafe_name"], "Updated Café")
        self.assertEqual(self.first.snapshot()["settings"]["grace_minutes"], 12)
        self.assertEqual(len(self.first.snapshot()["history"][self.second.pc_id]), 1)
        self.assertEqual(len(self.second.snapshot()["history"][self.second.pc_id]), 1)
        self.assertEqual(self.first.pc_id, self.first.local("pc_id"))
        self.assertEqual(self.second.pc_id, self.second.local("pc_id"))
        self.assertFalse(any(customer_controls(
            self.second.snapshot(), self.second.pc_id).values()))

    def test_transfer_preserves_removed_membership_revocation(self):
        self.admin.login_admin(PASSWORD)
        removed_id = self.offline_ids[0]
        result = self.admin.remove_computer(removed_id)
        notice = self.first.snapshot()["revocations"][removed_id]
        self.assertTrue(result["removed"])
        self.assertNotIn(removed_id, self.first.snapshot()["members"])

        self.user.take_admin(PASSWORD)

        transferred = self.second.snapshot()
        self.assertTrue(self.user.is_admin())
        self.assertNotIn(removed_id, transferred["members"])
        self.assertEqual(transferred["revocations"][removed_id], notice)

    def test_customer_authority_survives_transfer_both_directions(self):
        customer = self.first.create_customer(
            "Transfer Customer", "Owner", "create-customer")
        customer = self.first.adjust_customer_balance(
            customer["customer_id"], "add", 3600, customer["revision"],
            customer["balance_seconds"], "Owner", "add-hour")
        customer = self.first.adjust_customer_balance(
            customer["customer_id"], "reduce", 900, customer["revision"],
            customer["balance_seconds"], "Owner", "reduce-quarter")
        login = self.first.authenticate_customer(
            customer["customer_id"], "12345", self.first.pc_id,
            "password-login")
        customer = self.first.change_customer_password(
            customer["customer_id"], self.first.pc_id,
            login["ownership"]["login_id"], "12345", "password-under-a")
        self.first.release_customer_login(
            customer["customer_id"], self.first.pc_id,
            login["ownership"]["login_id"], force=True)
        customer = self.first.set_customer_cooldown_overrides(
            customer["customer_id"], 7, 11, customer["revision"], "Owner")
        self.first.db.execute(
            "UPDATE customer_accounts SET last_retainer_stopped_at=1000,"
            "last_logged_out_at=2000 WHERE customer_id=?",
            (customer["customer_id"],))
        deleted = self.first.create_customer(
            "Deleted Customer", "Owner", "create-deleted")
        self.first.delete_customer(
            deleted["customer_id"], deleted["name"], deleted["revision"],
            "delete-customer")

        # Ordinary UI replication still omits credentials; a stale local value
        # must be replaced only by the privileged handoff.
        replica = self.first.customer_replica_payload()
        self.second.apply_customer_replica(
            replica["accounts"], replica["tombstones"])
        self.assertIsNone(self.second.customer_password_verifier(
            customer["customer_id"]))
        stale = json.dumps(hash_customer_password("stale-local-password"),
                           separators=(",", ":"))
        self.second.db.execute(
            "UPDATE customer_accounts SET password_verifier=? WHERE customer_id=?",
            (stale, customer["customer_id"]))

        self.admin.login_admin(PASSWORD)
        self.user.take_admin(PASSWORD)
        verifier = self.second.customer_password_verifier(customer["customer_id"])
        self.assertTrue(verify_password("password-under-a", verifier))
        self.assertFalse(verify_password("12345", verifier))
        self.assertFalse(verify_password("stale-local-password", verifier))
        self.assertEqual(len(self.second.customer_adjustments(
            customer["customer_id"])), 2)
        transferred = self.second.customer_account(customer["customer_id"])
        self.assertEqual(transferred["balance_seconds"], 2700)
        self.assertEqual(transferred["retainer_cooldown_minutes"], 7)
        self.assertEqual(transferred["login_cooldown_minutes"], 11)
        self.assertEqual(transferred["last_retainer_stopped_at"], 1000)
        self.assertEqual(transferred["last_logged_out_at"], 2000)
        self.assertIsNone(self.second.customer_account(deleted["customer_id"]))
        self.assertEqual(self.second.db.execute(
            "SELECT revision FROM customer_tombstones WHERE customer_id=?",
            (deleted["customer_id"],)).fetchone()[0], 2)

        # Retrying a transferred operation is idempotent and creates no audit row.
        before_history = self.second.customer_adjustments(customer["customer_id"])
        repeated = self.second.adjust_customer_balance(
            customer["customer_id"], "add", 3600, 1, 0,
            "Owner", "add-hour")
        self.assertEqual(repeated["balance_seconds"], 2700)
        self.assertEqual(self.second.customer_adjustments(
            customer["customer_id"]), before_history)

        login = self.second.authenticate_customer(
            customer["customer_id"], "password-under-a", self.second.pc_id,
            "second-password-login")
        self.second.change_customer_password(
            customer["customer_id"], self.second.pc_id,
            login["ownership"]["login_id"], "password-under-a",
            "password-under-b")
        self.second.release_customer_login(
            customer["customer_id"], self.second.pc_id,
            login["ownership"]["login_id"], force=True)
        created_under_b = self.second.create_customer(
            "Created Under B", "Owner", "create-under-b")

        self.admin.handoff_until = 0
        self.admin.take_admin(PASSWORD)
        latest = self.first.customer_password_verifier(customer["customer_id"])
        self.assertTrue(verify_password("password-under-b", latest))
        self.assertFalse(verify_password("password-under-a", latest))
        self.assertIsNotNone(self.first.customer_account(
            created_under_b["customer_id"]))
        self.assertTrue(verify_password(
            "12345", self.first.customer_password_verifier(
                created_under_b["customer_id"])))
        self.assertEqual(len(self.first.customer_adjustments(
            customer["customer_id"])), 2)

    def test_lost_commit_response_is_resolved_without_split_brain(self):
        self.admin.login_admin(PASSWORD)
        route = self.user.network.call.side_effect

        def lose_commit_response(ip, operation, data):
            result = route(ip, operation, data)
            if operation == "commit_admin_transfer":
                raise OSError("response lost")
            return result

        with patch.object(self.user.network, "call",
                          side_effect=lose_commit_response):
            self.user.take_admin(PASSWORD)
        self.assertFalse(self.admin.is_admin())
        self.assertTrue(self.user.is_admin())

    def test_unresolved_commit_fences_both_sides_until_retry_confirms_it(self):
        self.admin.login_admin(PASSWORD)
        route = self.user.network.call.side_effect
        committed = False

        def lose_commit_and_snapshot(ip, operation, data):
            nonlocal committed
            if operation == "snapshot" and committed:
                raise OSError("snapshot response lost")
            result = route(ip, operation, data)
            if operation == "commit_admin_transfer":
                committed = True
                raise OSError("commit response lost")
            return result

        with patch.object(self.user.network, "call",
                          side_effect=lose_commit_and_snapshot):
            with self.assertRaisesRegex(
                    RuntimeError, "outcome could not be confirmed"):
                self.user.take_admin(PASSWORD)
        self.assertFalse(self.admin.is_admin())
        self.assertFalse(self.user.is_admin())
        self.assertIsNotNone(self.second.local(
            self.user._pending_handoff_key()))

        self.user.take_admin(PASSWORD)
        self.assertFalse(self.admin.is_admin())
        self.assertTrue(self.user.is_admin())
        self.assertIsNone(self.second.local(
            self.user._pending_handoff_key()))

    def test_active_customer_session_blocks_transfer(self):
        self.admin.login_admin(PASSWORD)
        self.admin.admin_action("start", self.second.pc_id,
                                kind="open", paid_minutes=0, buffer_minutes=0)
        with self.assertRaisesRegex(RuntimeError, "End this PC's customer session"):
            self.user.take_admin(PASSWORD)
        self.assertTrue(self.admin.is_admin())
        self.assertFalse(self.user.is_admin())

    def test_failed_handoff_preparation_keeps_original_admin(self):
        self.admin.login_admin(PASSWORD)
        with patch.object(
                self.second, "apply_authoritative_handoff_bundle",
                side_effect=RuntimeError("Simulated durable apply failure")):
            with self.assertRaisesRegex(RuntimeError, "Simulated durable apply failure"):
                self.user.take_admin(PASSWORD)
        self.assertTrue(self.admin.is_admin())
        self.assertFalse(self.user.is_admin())

    def test_handoff_barrier_rejects_customer_mutations(self):
        customer = self.first.create_customer(
            "Barrier Customer", "Owner", "create-barrier-customer")
        self.admin.login_admin(PASSWORD)
        self.admin.handoff_transfer = {
            "transfer_id": uuid.uuid4().hex,
            "candidate": self.second.pc_id,
            "expires_at": time.time() + 60,
        }

        with self.assertRaisesRegex(RuntimeError, "transfer is in progress"):
            self.admin.handle_member("customer_access", {
                "action": "login", "customer_id": customer["customer_id"],
                "password": "12345", "operation_id": uuid.uuid4().hex,
            }, self.second.pc_id, "second")
        self.assertIsNone(self.first.customer_ownership(
            customer_id=customer["customer_id"]))

    def test_single_available_pc_can_administer_with_offline_members(self):
        with patch.object(self.admin.network, "call", side_effect=OSError("offline")):
            self.admin.login_admin(PASSWORD)
        self.assertTrue(self.admin.is_admin())
        self.assertEqual(len(self.first.snapshot()["members"]), 5)
        for pc_id in self.offline_ids:
            self.assertIsNone(self.admin.peer_status(pc_id))
        self.admin.admin_action("settings", cafe_name="Open Café",
                                admin_name="Owner", grace_minutes=12,
                                auto_signout_minutes=30)
        self.assertEqual(self.first.snapshot()["cafe_name"], "Open Café")

    def test_admin_node_can_host_local_guest_session_without_losing_authority(self):
        self.admin.login_admin(PASSWORD)
        self.admin.admin_ui_open = False
        self.admin.admin_signing_key = None
        self.admin.local_staff_session_action(
            "start", PASSWORD, uuid.uuid4().hex,
            kind="timed", paid_minutes=60, buffer_minutes=0)
        self.assertTrue(self.admin.is_admin())
        self.assertFalse(self.admin.admin_ui_open)
        self.assertIsNotNone(self.first.snapshot()["sessions"][self.first.pc_id])

    def test_later_online_pc_follows_existing_admin_without_password(self):
        with patch.object(self.admin.network, "call", side_effect=OSError("offline")):
            self.admin.login_admin(PASSWORD)
        self.assertTrue(self.user.verified_admin_online())
        self.assertTrue(self.admin.is_admin())
        self.assertFalse(self.user.is_admin())
        self.assertEqual(self.second.snapshot()["active_admin"]["pc_id"],
                         self.first.pc_id)

    def test_confirmed_admin_status_uses_existing_peer_freshness_window(self):
        with patch.object(self.admin.network, "call", side_effect=OSError("offline")):
            self.admin.login_admin(PASSWORD)
        self.assertTrue(self.user.verified_admin_online())
        snapshot = self.user.snapshot()
        observed_at = time.time()
        self.user.peer_statuses[self.first.pc_id] = (observed_at, {
            "pc_id": self.first.pc_id,
            "admin": True,
            "admin_term": snapshot["active_admin"]["term"],
        })
        with patch("game_cafe.runtime.time.time",
                   return_value=observed_at + PEER_ONLINE_SECONDS - 0.1):
            self.assertTrue(self.user.confirmed_remote_admin_online())
        with patch("game_cafe.runtime.time.time",
                   return_value=observed_at + PEER_ONLINE_SECONDS + 0.1):
            self.assertFalse(self.user.confirmed_remote_admin_online())

    def test_unverified_or_mismatched_admin_status_is_not_accepted(self):
        with patch.object(self.admin.network, "call", side_effect=OSError("offline")):
            self.admin.login_admin(PASSWORD)
        self.assertTrue(self.user.verified_admin_online())
        snapshot = self.user.snapshot()
        self.user.peer_statuses[self.first.pc_id] = (time.time(), {
            "pc_id": self.first.pc_id,
            "admin": True,
            "admin_term": snapshot["active_admin"]["term"] + 1,
        })
        self.assertFalse(self.user.confirmed_remote_admin_online())

    def test_dashboard_keeps_offline_registered_pcs_visible(self):
        self.admin.login_admin(PASSWORD)
        snap = self.first.snapshot()
        rows = {pc_id: pc_row_data(snap, pc_id, self.admin.peer_status(pc_id))
                for pc_id in snap["members"]}
        self.assertEqual(set(rows), set(snap["members"]))
        for pc_id in self.offline_ids:
            self.assertFalse(rows[pc_id]["online"])
            self.assertFalse(rows[pc_id]["start"])

    def test_offline_member_cannot_claim_designated_admin_role(self):
        with patch.object(self.user.network, "call", side_effect=OSError("partition")):
            with self.assertRaisesRegex(RuntimeError, "designated Admin Node is offline"):
                self.user.login_admin(PASSWORD)
        self.assertFalse(self.user.is_admin())
        self.assertEqual(self.second.snapshot()["active_admin"]["pc_id"],
                         self.first.pc_id)

    def test_registered_user_cannot_push_a_forged_admin_claim(self):
        self.admin.login_admin(PASSWORD)
        forged = self.second.snapshot()
        forged["active_admin"] = {
            "pc_id": self.second.pc_id,
            "term": forged["active_admin"]["term"] + 1,
            "expires_at": time.time() + 15,
            "proof": "00",
        }
        forged["revision"] += 1
        with self.assertRaises(Exception):
            self.admin.handle_member("push_snapshot", {"snapshot": forged},
                                     self.second.pc_id, "second")
        self.assertTrue(self.admin.is_admin())

    def test_wrong_password_cannot_claim_admin(self):
        with self.assertRaises(PermissionError):
            self.admin.login_admin("incorrect password")
        self.assertFalse(self.admin.is_admin())

    def test_returning_old_admin_follows_current_admin(self):
        self.admin.login_admin(PASSWORD)
        self.user.take_admin(PASSWORD)
        restarted = Runtime(self.first)
        with (patch.object(restarted.network, "call",
                           side_effect=lambda ip, op, data:
                           self.user.handle_member(op, data, self.first.pc_id, "first")),
              patch.object(restarted, "_discover_peers_once", return_value=[])):
            self.assertTrue(restarted.verified_admin_online())
        self.assertFalse(restarted.is_admin())
        self.assertEqual(restarted.snapshot()["active_admin"]["pc_id"],
                         self.second.pc_id)

    def test_restart_restores_signed_admin_node_without_staff_login(self):
        self.admin.login_admin(PASSWORD)
        term = self.first.snapshot()["active_admin"]["term"]
        reopened_store = Store(self.first.path)
        restarted = Runtime(reopened_store)
        self.assertFalse(restarted.is_admin())
        try:
            with (patch.object(restarted.network, "start") as network_start,
                  patch.object(restarted.network, "stop"),
                  patch.object(restarted, "_background_loop")):
                restarted.start()
                network_start.assert_called_once()
                self.assertTrue(restarted.is_admin())
                self.assertFalse(restarted.admin_ui_open)
                self.assertIsNone(restarted.admin_signing_key)
                self.assertEqual(restarted.snapshot()["active_admin"]["term"], term)
                restarted.stop()
        finally:
            reopened_store.close()

    def test_restart_after_handoff_cannot_restore_old_claim(self):
        self.admin.login_admin(PASSWORD)
        self.user.take_admin(PASSWORD)
        restarted = Runtime(self.first)
        with (patch.object(restarted.network, "start"),
              patch.object(restarted.network, "stop"),
              patch.object(restarted, "_background_loop")):
            restarted.start()
            self.assertFalse(restarted.is_admin())
            restarted.stop()

    def test_restart_after_password_change_preserves_admin_node(self):
        self.admin.login_admin(PASSWORD)
        self.admin.admin_action("password", new_password="new long test password")
        restarted = Runtime(self.first)
        with (patch.object(restarted.network, "start"),
              patch.object(restarted.network, "stop"),
              patch.object(restarted, "_background_loop")):
            restarted.start()
            self.assertTrue(restarted.is_admin())
            self.assertFalse(restarted.admin_ui_open)
            restarted._renew_admin_lease()
            self.assertTrue(restarted.is_admin())
            restarted.stop()


if __name__ == "__main__":
    unittest.main()
