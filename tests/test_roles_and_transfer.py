"""Role assignment and Admin handoff without Tk windows or real sockets."""

from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from unittest.mock import Mock, patch

from game_cafe.runtime import Runtime, customer_controls
from game_cafe.security import (create_local_command_key, open_local_password,
                                seal_local_password)
from game_cafe.storage import Store, new_pool
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

    def test_active_customer_session_blocks_transfer(self):
        self.admin.login_admin(PASSWORD)
        self.admin.admin_action("start", self.second.pc_id,
                                kind="open", paid_minutes=0, buffer_minutes=0)
        with self.assertRaisesRegex(RuntimeError, "End this PC's customer session"):
            self.user.take_admin(PASSWORD)
        self.assertTrue(self.admin.is_admin())
        self.assertFalse(self.user.is_admin())

    def test_failed_handoff_leaves_no_active_admin(self):
        self.admin.login_admin(PASSWORD)
        with patch.object(self.user, "_activate_admin",
                          side_effect=RuntimeError("Simulated startup failure")):
            with self.assertRaisesRegex(RuntimeError, "Simulated startup failure"):
                self.user.take_admin(PASSWORD)
        self.assertFalse(self.admin.is_admin())
        self.assertFalse(self.user.is_admin())

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

    def test_later_online_pc_follows_existing_admin_without_password(self):
        with patch.object(self.admin.network, "call", side_effect=OSError("offline")):
            self.admin.login_admin(PASSWORD)
        self.assertTrue(self.user.verified_admin_online())
        self.assertTrue(self.admin.is_admin())
        self.assertFalse(self.user.is_admin())
        self.assertEqual(self.second.snapshot()["active_admin"]["pc_id"],
                         self.first.pc_id)

    def test_dashboard_keeps_offline_registered_pcs_visible(self):
        self.admin.login_admin(PASSWORD)
        snap = self.first.snapshot()
        rows = {pc_id: pc_row_data(snap, pc_id, self.admin.peer_status(pc_id))
                for pc_id in snap["members"]}
        self.assertEqual(set(rows), set(snap["members"]))
        for pc_id in self.offline_ids:
            self.assertFalse(rows[pc_id]["online"])
            self.assertFalse(rows[pc_id]["start"])

    def test_competing_claims_converge_after_contact(self):
        self.first.update(lambda state: state["active_admin"].update(expires_at=0))
        self.second.apply_snapshot(self.first.snapshot())
        # A partition can briefly create two claims; no quorum guarantee is claimed.
        with (patch.object(self.admin.network, "call", side_effect=OSError("partition")),
              patch.object(self.user.network, "call", side_effect=OSError("partition"))):
            with ThreadPoolExecutor(max_workers=2) as executor:
                attempts = [executor.submit(runtime.login_admin, PASSWORD)
                            for runtime in (self.admin, self.user)]
                for attempt in attempts:
                    attempt.result()
        self.assertTrue(self.admin.is_admin() and self.user.is_admin())
        for runtime in (self.admin, self.user):
            runtime._poll_peer_statuses()
            runtime._settle_admin_ownership()
        winner = max(self.first.pc_id, self.second.pc_id)
        self.assertEqual(self.first.pc_id if self.admin.is_admin()
                         else self.second.pc_id, winner)
        self.assertFalse(self.admin.is_admin() and self.user.is_admin())

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

    def test_restart_does_not_inherit_admin_claim(self):
        self.admin.login_admin(PASSWORD)
        restarted = Runtime(self.first)
        self.assertFalse(restarted.is_admin())


if __name__ == "__main__":
    unittest.main()
