"""Bounded and shareable production diagnostic log behavior."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from game_cafe import diagnostics


class DiagnosticLoggingTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        diagnostics.close_diagnostics()
        self.temp.cleanup()

    def test_events_are_human_readable_and_sensitive_values_are_redacted(self):
        path = diagnostics.configure_diagnostics(self.root)
        diagnostics.info(
            "session", "command_sent", pc="abcd1234", password="never-log",
            reason=("token=remote-secret private_key=private-secret id="
                    + "a" * 64),
        )
        try:
            raise RuntimeError("signature=remote-value")
        except RuntimeError as caught:
            diagnostics.unexpected("runtime", "failed", caught)
        diagnostics.close_diagnostics()

        text = path.read_text(encoding="utf-8")
        self.assertIn("Z | INFO | SESSION | COMMAND_SENT", text)
        self.assertIn("PC=abcd1234", text)
        self.assertIn("PASSWORD=[REDACTED]", text)
        self.assertIn("token=[REDACTED]", text)
        self.assertIn("private_key=[REDACTED]", text)
        self.assertIn("signature=[REDACTED]", text)
        self.assertIn("STACK=", text)
        for secret in ("never-log", "remote-secret", "private-secret",
                       "remote-value", "a" * 64):
            self.assertNotIn(secret, text)

    def test_rotation_retains_only_the_configured_archives(self):
        with (patch.object(diagnostics, "MAX_LOG_BYTES", 300),
              patch.object(diagnostics, "LOG_BACKUP_COUNT", 2)):
            path = diagnostics.configure_diagnostics(self.root)
            for number in range(80):
                diagnostics.info(
                    "network", "connection_lost", pc=f"pc-{number:03}",
                    reason="bounded diagnostic line " + "x" * 80)
            diagnostics.close_diagnostics()
        self.assertTrue(path.is_file())
        self.assertTrue(Path(str(path) + ".1").is_file())
        self.assertTrue(Path(str(path) + ".2").is_file())
        self.assertFalse(Path(str(path) + ".3").exists())

    def test_logging_setup_failure_never_crashes_the_application(self):
        with patch("pathlib.Path.mkdir", side_effect=OSError("read only")):
            path = diagnostics.configure_diagnostics(self.root)
        diagnostics.info("application", "still_running")
        diagnostics.close_diagnostics()
        self.assertEqual(path, self.root / "logs" / "gamegrid.log")


if __name__ == "__main__":
    unittest.main()
