"""Authoritative computer removal without real sockets or desktop switching."""

from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import time
import unittest
from unittest.mock import MagicMock, patch

from game_cafe import sessions
from game_cafe.network import PairingRevokedError, signed, verify_signed
from game_cafe.runtime import Runtime
from game_cafe.storage import Store, new_pool


PASSWORD = "long test password"


class ComputerRemovalTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.admin_store = Store(root / "admin.sqlite3")
        self.user_store = Store(root / "user.sqlite3")
        snapshot, secret = new_pool(
            "Test Cafe", self.admin_store.pc_id, "PC-01", "Owner",
            PASSWORD, self.admin_store.node_public_key())
        snapshot["members"][self.user_store.pc_id] = {
            "name": "PC-02",
            "public_key": self.user_store.node_public_key(),
            "last_connected_at": time.time(),
        }
        completed = sessions.start_session("open", 0, 0)
        snapshot["history"][self.user_store.pc_id] = [
            sessions.finish_session(
                completed, self.user_store.pc_id, "PC-02", "admin")]
        self.admin_store.save_pool(snapshot, secret, join=True)
        # Read the migrated authorization fields so both stores save the exact
        # same membership generation.
        snapshot = self.admin_store.snapshot()
        self.user_store.save_pool(snapshot, secret, join=True)
        self.admin = Runtime(self.admin_store)
        self.user = Runtime(self.user_store)
        self.admin_store.remember_address(self.user_store.pc_id, "user")
        self.user_store.remember_address(self.admin_store.pc_id, "admin")
        with (patch.object(self.admin, "_discover_peers_once", return_value=[]),
              patch.object(self.admin, "_replicate_once")):
            self.admin.login_admin(PASSWORD)

    def tearDown(self):
        self.admin_store.close()
        self.user_store.close()
        self.temp.cleanup()

    def _online(self):
        self.admin.peer_statuses[self.user_store.pc_id] = (
            time.time(), {"pc_id": self.user_store.pc_id, "admin": False})

        def member_call(_ip, operation, data):
            return self.user.handle_member(
                operation, data, self.admin_store.pc_id, "admin")

        def deliver(_ip, _snapshot, _secret, notice):
            reply = self.user.handle_member(
                "pairing_revoked", {"revocation": notice},
                self.admin_store.pc_id, "admin")
            self.user.receive_pairing_revocation(notice)
            return reply

        return (patch.object(self.admin.network, "call", side_effect=member_call),
                patch.object(self.admin, "_send_pairing_revocation",
                             side_effect=deliver),
                patch.object(self.admin, "_replicate_once"))

    def test_online_idle_removal_unpairs_immediately_and_preserves_identity_history(self):
        pc_id = self.user_store.pc_id
        public_key = self.user_store.node_public_key()
        customer = self.admin_store.create_customer(
            "Balance Owner", "Owner", "create-balance-owner")
        customer = self.admin_store.adjust_customer_balance(
            customer["customer_id"], "add", 3600, customer["revision"],
            customer["balance_seconds"], "Owner", "add-balance")
        history = copy.deepcopy(
            self.admin_store.snapshot()["history"][pc_id])
        first, second, third = self._online()
        with first, second, third:
            result = self.admin.remove_computer(pc_id)

        self.assertTrue(result["removed"])
        self.assertNotIn(pc_id, self.admin_store.snapshot()["members"])
        self.assertEqual(self.admin_store.snapshot()["history"][pc_id], history)
        self.assertEqual(self.admin_store.customer_account(
            customer["customer_id"])["balance_seconds"], 3600)
        self.assertEqual(len(self.admin_store.customer_adjustments(
            customer["customer_id"])), 1)
        self.assertIsNone(self.user_store.current_pool_id)
        self.assertEqual(self.user_store.pc_id, pc_id)
        self.assertEqual(self.user_store.node_public_key(), public_key)

    def test_active_guest_registered_or_pending_transition_blocks_removal(self):
        pc_id = self.user_store.pc_id
        self.admin_store.update(lambda state: state["sessions"].update(
            {pc_id: sessions.start_session("open", 0, 0)}))
        with self.assertRaisesRegex(RuntimeError, "active gaming session"):
            self.admin.remove_computer(pc_id)
        self.admin_store.update(lambda state: state["sessions"].pop(pc_id))

        customer = self.admin_store.create_customer(
            "Registered Player", "Owner", "create-player")
        login = self.admin_store.authenticate_customer(
            customer["customer_id"], "12345", pc_id, "login-player")
        with self.assertRaisesRegex(RuntimeError, "registered customer"):
            self.admin.remove_computer(pc_id)
        self.admin_store.release_customer_login(
            customer["customer_id"], pc_id,
            login["ownership"]["login_id"], force=True)

        key = (f"pending_session_command:{self.admin_store.current_pool_id}:"
               f"{pc_id}")
        self.admin_store.set_local(key, "{}")
        with self.assertRaisesRegex(RuntimeError, "session change"):
            self.admin.remove_computer(pc_id)

    def test_active_admin_cannot_be_removed(self):
        with self.assertRaisesRegex(ValueError, "active Admin"):
            self.admin.remove_computer(self.admin_store.pc_id)

    def test_offline_revocation_is_authenticated_idempotent_and_survives_restart(self):
        pc_id = self.user_store.pc_id
        old_authorization = self.user_store.snapshot()["members"][pc_id][
            "authorization_id"]
        with patch.object(self.admin, "_replicate_once"):
            result = self.admin.remove_computer(pc_id)
            duplicate = self.admin.remove_computer(pc_id)
        self.assertTrue(duplicate["already_removed"])
        self.assertIsNotNone(self.user_store.current_pool_id)

        notice = self.admin_store.snapshot()["revocations"][pc_id]
        forged = copy.deepcopy(notice)
        forged["certificate"]["pc_name"] = "Forged"
        with self.assertRaises(PermissionError):
            self.user.receive_pairing_revocation(forged)
        self.assertIsNotNone(self.user_store.current_pool_id)

        connection = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = connection
        with (patch("game_cafe.network.socket.create_connection",
                    return_value=context),
              patch("game_cafe.network.send_message"),
              patch("game_cafe.network.read_message",
                    return_value={"revocation": notice})):
            with self.assertRaises(PairingRevokedError):
                self.user.network.call("admin", "status", {})
        self.assertIsNone(self.user_store.current_pool_id)
        self.assertFalse(self.user.receive_pairing_revocation(notice))
        restarted = Store(Path(self.temp.name) / "user.sqlite3")
        try:
            self.assertIsNone(restarted.current_pool_id)
            self.assertEqual(restarted.pc_id, pc_id)
            self.assertNotEqual(old_authorization, "")
        finally:
            restarted.close()
        self.assertTrue(result["removed"])

    def test_old_authorization_is_rejected_and_fresh_pairing_gets_a_new_one(self):
        pc_id = self.user_store.pc_id
        stale_snapshot = self.user_store.snapshot()
        old_authorization = stale_snapshot["members"][pc_id][
            "authorization_id"]
        old_request = signed(
            self.user_store.secret(), pc_id, stale_snapshot["pool_id"],
            "status", {}, self.user_store.node_private_key(),
            old_authorization)
        with patch.object(self.admin, "_replicate_once"):
            self.admin.remove_computer(pc_id)
        with self.assertRaises(PermissionError):
            verify_signed(old_request, self.admin_store.secret(),
                          self.admin_store.snapshot()["members"])

        notice = self.admin_store.snapshot()["revocations"][pc_id]
        self.user.receive_pairing_revocation(notice)

        def route(_ip, operation, data):
            return self.admin.handle_plain(operation, data, "user")

        with (patch("game_cafe.runtime.plain_call", side_effect=route),
              patch("game_cafe.runtime.authenticated_call",
                    return_value={"pc_id": self.admin_store.pc_id}),
              patch.object(self.user.network, "call", return_value={}),
              patch.object(self.admin, "_replicate_once")):
            request = self.user.request_join(
                "admin", stale_snapshot["pool_id"], "PC-02")
            self.admin.approve_join(
                request["request_id"], pc_id, request["code"], "PC-02")
            state, _ = self.user.finish_join(
                "admin", request["request_id"], request["private"])
        self.assertEqual(state, "accepted")
        self.assertEqual(self.user_store.pc_id, pc_id)
        new_authorization = self.user_store.snapshot()["members"][pc_id][
            "authorization_id"]
        self.assertNotEqual(new_authorization, old_authorization)
        with self.assertRaisesRegex(PermissionError, "older authorization"):
            self.user.receive_pairing_revocation(notice)

    def test_temporary_network_failure_never_clears_pairing(self):
        pool_id = self.user_store.current_pool_id
        with patch.object(self.user.network, "call",
                          side_effect=OSError("network unavailable")):
            self.user._poll_peer_statuses()
        self.assertEqual(self.user_store.current_pool_id, pool_id)

    def test_offline_local_session_is_not_interrupted_by_revocation(self):
        pc_id = self.user_store.pc_id
        with patch.object(self.admin, "_replicate_once"):
            self.admin.remove_computer(pc_id)
        notice = self.admin_store.snapshot()["revocations"][pc_id]
        self.user_store.update(lambda state: state["sessions"].update(
            {pc_id: sessions.start_session("open", 0, 0)}))

        self.assertFalse(self.user.receive_pairing_revocation(notice))
        self.assertIsNotNone(self.user_store.current_pool_id)
        self.assertIn(pc_id, self.user_store.snapshot()["sessions"])

        self.user_store.update(lambda state: state["sessions"].pop(pc_id))
        self.assertTrue(self.user._apply_pending_pairing_revocation())
        self.assertIsNone(self.user_store.current_pool_id)


if __name__ == "__main__":
    unittest.main()
