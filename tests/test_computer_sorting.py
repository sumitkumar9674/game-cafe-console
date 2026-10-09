"""Persistent Admin computer sorting without real networking or desktops."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from game_cafe.presentation import sort_pc_rows
from game_cafe.runtime import Runtime
from game_cafe.storage import Store, new_pool


PASSWORD = "long test password"


class ComputerSortingTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "cafe.sqlite3"
        self.store = Store(self.path)
        snapshot, secret = new_pool(
            "Test Cafe", self.store.pc_id, "PC-10", "Owner", PASSWORD,
            self.store.node_public_key())
        snapshot["members"].update({
            "b" * 32: {"name": "pc-2", "public_key": "b" * 64,
                       "last_connected_at": 200},
            "c" * 32: {"name": "PC-01", "public_key": "c" * 64,
                       "last_connected_at": 300},
            "d" * 32: {"name": "PC-2", "public_key": "d" * 64,
                       "last_connected_at": 100},
            "e" * 32: {"name": "Lobby", "public_key": "e" * 64},
        })
        self.secret = secret
        self.store.save_pool(snapshot, secret, join=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def rows(self):
        snapshot = self.store.snapshot()
        return [{"pcId": pc_id, "name": member["name"],
                 "online": pc_id != "e" * 32}
                for pc_id, member in snapshot["members"].items()]

    def test_recent_order_and_stable_legacy_fallback(self):
        snapshot = self.store.snapshot()
        snapshot["members"]["d" * 32].pop("last_connected_at")
        rows = [{"pcId": pc_id, "name": member["name"], "online": True}
                for pc_id, member in snapshot["members"].items()]
        ordered = sort_pc_rows(rows, snapshot, "recent")
        self.assertEqual([row["pcId"] for row in ordered[:3]],
                         [self.store.pc_id, "c" * 32, "b" * 32])
        # Missing legacy timestamps remain deterministic rather than using refresh time.
        zero_time_ids = [row["pcId"] for row in ordered
                         if not snapshot["members"][row["pcId"]].get(
                             "last_connected_at")]
        self.assertEqual(zero_time_ids, sorted(zero_time_ids))

    def test_name_order_is_case_insensitive_natural_and_id_tied(self):
        ordered = sort_pc_rows(self.rows(), self.store.snapshot(), "name")
        names = [row["name"] for row in ordered]
        self.assertEqual(names, ["Lobby", "PC-01", "pc-2", "PC-2", "PC-10"])
        self.assertLess(ordered[2]["pcId"], ordered[3]["pcId"])

    def test_rename_changes_name_order_and_keeps_every_pc(self):
        snapshot = self.store.snapshot()
        snapshot["members"]["e" * 32]["name"] = "PC-03"
        rows = [{"pcId": pc_id, "name": member["name"], "online": False}
                for pc_id, member in snapshot["members"].items()]
        ordered = sort_pc_rows(rows, snapshot, "name")
        self.assertEqual([row["name"] for row in ordered],
                         ["PC-01", "pc-2", "PC-2", "PC-03", "PC-10"])
        self.assertEqual({row["pcId"] for row in ordered},
                         set(snapshot["members"]))
        self.assertIn(self.store.pc_id, {row["pcId"] for row in ordered})

    def authenticated_runtime(self):
        runtime = Runtime(self.store)
        with patch.object(runtime, "_find_live_admin", return_value=None):
            runtime.login_admin(PASSWORD)
        return runtime

    def test_reconnect_updates_once_but_heartbeats_do_not(self):
        runtime = self.authenticated_runtime()
        pc_id = "b" * 32
        self.store.note_peer(pc_id, "peer")
        runtime.connection_states[pc_id] = False

        def call(_ip, operation, _data):
            if operation == "status":
                return {"pc_id": pc_id, "desktop": "CafeConsole"}
            return {"records": []}

        with patch.object(runtime.network, "call", side_effect=call):
            runtime._poll_peer_statuses()
            connected = self.store.snapshot()["members"][pc_id]["last_connected_at"]
            self.assertGreater(connected, 200)
            ordered = sort_pc_rows(self.rows(), self.store.snapshot(), "recent")
            self.assertEqual(ordered[0]["pcId"], pc_id)
            runtime._poll_peer_statuses()
        self.assertEqual(self.store.snapshot()["members"][pc_id]["last_connected_at"],
                         connected)

    def test_sort_preference_survives_restart_and_is_in_shared_snapshot(self):
        runtime = self.authenticated_runtime()
        runtime.admin_action("computer_sort", option="name")
        shared = self.store.snapshot()
        self.assertEqual(shared["settings"]["computer_sort"], "name")
        reopened = Store(self.path)
        try:
            self.assertEqual(reopened.snapshot()["settings"]["computer_sort"],
                             "name")
        finally:
            reopened.close()
        takeover = Store(Path(self.temp.name) / "takeover.sqlite3")
        try:
            takeover.set_local("pc_id", "b" * 32)
            takeover.save_pool(shared, self.secret, join=True)
            self.assertEqual(takeover.snapshot()["settings"]["computer_sort"],
                             "name")
        finally:
            takeover.close()


if __name__ == "__main__":
    unittest.main()
