"""Check the session transitions that affect access and final history."""

import unittest

from game_cafe import sessions


class SessionRulesTest(unittest.TestCase):
    def test_timed_pause_freezes_remaining_and_resumes_without_double_counting(self):
        start = 1_000_000.0
        session = sessions.start_session("timed", 60, 0, start)
        self.assertTrue(sessions.pause_session(session, start + 18 * 60))
        frozen = sessions.remaining_seconds(session, start + 30 * 60)
        self.assertEqual(frozen, 42 * 60)
        self.assertEqual(sessions.phase(session, start + 30 * 60), "paused")
        self.assertFalse(sessions.pause_session(session, start + 31 * 60))
        self.assertTrue(sessions.resume_session(session, start + 38 * 60))
        self.assertFalse(sessions.resume_session(session, start + 39 * 60))
        self.assertEqual(sessions.remaining_seconds(session, start + 38 * 60),
                         42 * 60)
        finished = sessions.finish_session(session, "pc", "PC", "customer",
                                           start + 48 * 60)
        self.assertEqual(finished["counted_seconds"], 28 * 60)
        self.assertEqual(finished["paused_seconds"], 20 * 60)

    def test_buffer_and_open_pause_exclude_frozen_time(self):
        start = 2_000_000.0
        timed = sessions.start_session("timed", 30, 5, start)
        sessions.pause_session(timed, start + 2 * 60)
        self.assertEqual(sessions.remaining_seconds(timed, start + 50 * 60), 3 * 60)
        sessions.resume_session(timed, start + 12 * 60)
        self.assertEqual(sessions.phase(timed, start + 14 * 60), "buffer")
        self.assertEqual(sessions.phase(timed, start + 15 * 60), "timed")
        open_session = sessions.start_session("open", 0, 0, start)
        sessions.pause_session(open_session, start + 10 * 60)
        self.assertEqual(sessions.remaining_seconds(open_session, start + 30 * 60),
                         10 * 60)
        sessions.resume_session(open_session, start + 30 * 60)
        history = sessions.finish_session(open_session, "pc", "PC", "customer",
                                          start + 35 * 60)
        self.assertEqual(history["counted_seconds"], 15 * 60)

    def test_paid_time_can_be_added_while_paused(self):
        start = 3_000_000.0
        session = sessions.start_session("timed", 30, 0, start)
        sessions.pause_session(session, start + 10 * 60)
        sessions.add_paid_time(session, 15, start + 20 * 60)
        self.assertEqual(sessions.remaining_seconds(session, start + 25 * 60),
                         35 * 60)
        sessions.resume_session(session, start + 30 * 60)
        self.assertEqual(sessions.remaining_seconds(session, start + 30 * 60),
                         35 * 60)

    def test_grace_cannot_be_paused(self):
        start = 4_000_000.0
        session = sessions.start_session("timed", 1, 0, start)
        with self.assertRaisesRegex(ValueError, "Only buffer"):
            sessions.pause_session(session, start + 61)

    def test_buffer_paid_grace_renewal_is_one_session(self):
        start = 1_000_000.0
        session = sessions.start_session("timed", 60, 5, start)
        session["player"] = "Sumit"
        self.assertEqual(sessions.phase(session, start + 4 * 60), "buffer")
        sessions.add_paid_time(session, 30, start + 2 * 60)
        self.assertEqual(session["paid_minutes"], 90)
        self.assertEqual(sessions.phase(session, start + 5 * 60), "timed")

        paid_expiry = start + 95 * 60
        self.assertEqual(sessions.enter_grace_if_due(session, paid_expiry, 10),
                         "grace")
        self.assertEqual(session["grace_started"], paid_expiry)
        sessions.add_paid_time(session, 30, paid_expiry + 5 * 60, 10)
        self.assertEqual(session["id"], session["id"])
        self.assertEqual(session["player"], "Sumit")
        self.assertEqual(session["paid_minutes"], 120)
        self.assertEqual(session["grace_used_seconds"], 5 * 60)
        self.assertEqual(sessions.phase(session, paid_expiry + 30 * 60), "timed")

        history = sessions.finish_session(
            session, "pc-id", "PC-01", "customer", paid_expiry + 35 * 60
        )
        self.assertEqual(history["player"], "Sumit")
        self.assertEqual(history["paid_minutes"], 120)
        self.assertEqual(history["grace_used_seconds"], 5 * 60)
        self.assertEqual(len(history["additions"]), 2)
        self.assertEqual(history["counted_seconds"], 120 * 60)

    def test_grace_expiry_and_open_session(self):
        start = 2_000_000.0
        timed = sessions.start_session("timed", 1, 0, start)
        self.assertEqual(sessions.phase(timed, start + 61, 10), "grace")
        self.assertEqual(sessions.phase(timed, start + 11 * 60, 10), "expired")
        with self.assertRaises(ValueError):
            sessions.add_paid_time(timed, 1, start + 11 * 60, 10)

        open_session = sessions.start_session("open", 0, 5, start)
        self.assertEqual(sessions.phase(open_session, start + 4 * 60), "buffer")
        self.assertEqual(sessions.phase(open_session, start + 6 * 60), "open")
        self.assertEqual(sessions.remaining_seconds(open_session, start + 6 * 60), 60)
        with self.assertRaises(ValueError):
            sessions.add_paid_time(open_session, 15, start + 6 * 60)


if __name__ == "__main__":
    unittest.main()
