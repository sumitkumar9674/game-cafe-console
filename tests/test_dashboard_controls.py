"""UI presentation rules without creating windows or switching desktops."""

import time
import unittest

from game_cafe import sessions
from game_cafe.presentation import (pc_row_data, validate_start,
                                    add_time_preview, history_rows)

ADMIN = "a" * 32
USER = "b" * 32


class DashboardControlsTest(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.snap = {
            "members": {ADMIN: {"name": "PC-01"}, USER: {"name": "PC-02"}},
            "active_admin": {"pc_id": ADMIN},
            "settings": {"grace_minutes": 10},
            "sessions": {}, "unlock_requests": {}, "history": {USER: []},
        }

    def row(self, pc_id=USER, reported=None, now=None):
        return pc_row_data(self.snap, pc_id,
                           {"desktop": "CafeConsole"} if reported is None else reported,
                           self.now if now is None else now)

    def test_locked_online_pc_has_start_action(self):
        row = self.row()
        self.assertTrue(row["start"])
        self.assertFalse(row["add"] or row["end"])
        self.assertEqual(row["access"], "LOCKED")

    def test_timed_buffer_paid_and_grace_controls(self):
        session = sessions.start_session("timed", 60, 5, now=self.now)
        self.snap["sessions"][USER] = session
        buffer = self.row(now=self.now + 60)
        self.assertEqual(buffer["phase"], "BUFFER")
        self.assertTrue(buffer["add"] and buffer["end"])
        sessions.add_paid_time(session, 15, now=self.now + 60)
        self.assertEqual(session["paid_minutes"], 75)
        paid = self.row(now=self.now + 360)
        self.assertEqual(paid["phase"], "TIMED")
        grace = self.row(now=session["paid_end"] + 60)
        self.assertEqual(grace["phase"], "GRACE")
        self.assertTrue(grace["add"] and grace["end"])

    def test_open_session_and_offline_controls(self):
        self.snap["sessions"][USER] = sessions.start_session("open", 0, 0, now=self.now)
        self.assertFalse(self.row(now=self.now + 125)["add"])
        self.assertTrue(self.row(now=self.now + 125)["end"])
        offline = pc_row_data(self.snap, USER, None, self.now)
        self.assertFalse(offline["online"] or offline["end"])
        admin = self.row(ADMIN)
        self.assertFalse(admin["start"] or admin["add"] or admin["end"])

    def test_unlock_request_and_remote_state(self):
        self.snap["unlock_requests"][USER] = self.now
        self.assertTrue(self.row()["unlockRequested"])
        self.assertEqual(self.row(reported={"desktop": "Default"})["access"],
                         "UNLOCKED")

    def test_validation_and_add_time_preview(self):
        self.assertEqual(validate_start("timed", "60", "5"), (60, 5))
        self.assertEqual(validate_start("timed", "37", "0"), (37, 0))
        self.assertEqual(validate_start("open", "0", "5"), (0, 5))
        with self.assertRaises(ValueError):
            validate_start("timed", "0", "5")
        for invalid in ("", "-1", "1.5", "1e2", "1441"):
            with self.assertRaises(ValueError):
                validate_start("timed", invalid, "0")
        self.snap["sessions"][USER] = sessions.start_session(
            "timed", 30, 0, now=self.now)
        self.assertIn("45 minutes", add_time_preview(self.snap, USER, 15,
                                                     now=self.now))

    def test_add_preview_keeps_buffer_and_grace_on_same_session(self):
        session = sessions.start_session("timed", 30, 5, now=self.now)
        self.snap["sessions"][USER] = session
        self.assertIn("Buffer continues unchanged", add_time_preview(
            self.snap, USER, 2, now=self.now + 30))
        self.assertIn("same session resumes", add_time_preview(
            self.snap, USER, 5, now=session["paid_end"] + 30))

    def test_history_filters_by_selected_pc(self):
        first = sessions.start_session("timed", 30, 0, now=self.now - 100)
        second = sessions.start_session("open", 0, 0, now=self.now - 80)
        self.snap["history"] = {
            USER: [sessions.finish_session(first, USER, "PC-02", "admin", self.now)],
            ADMIN: [sessions.finish_session(second, ADMIN, "PC-01", "admin", self.now)],
        }
        self.assertEqual(len(history_rows(self.snap, USER)), 1)
        self.assertEqual(len(history_rows(self.snap, None)), 2)


if __name__ == "__main__":
    unittest.main()
