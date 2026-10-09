"""CafeConsole child shutdown regression coverage without desktop switching."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


def run_child_shutdown_probe() -> int:
    """Run the real child QML/event loop with all Win32 calls isolated."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtCore import QTimer

    from game_cafe.qt_app import ConsoleChildScreen
    from game_cafe.storage import Store

    timed_out = False
    child_close_was_rejected = False

    with TemporaryDirectory() as folder:
        store = Store(Path(folder) / "child.sqlite3")
        with patch("game_cafe.qt_app.desktops.open_default", return_value=1), \
             patch("game_cafe.qt_app.desktops.open_parent_process", return_value=2), \
             patch("game_cafe.qt_app.desktops.open_event", side_effect=[3, 4]), \
             patch("game_cafe.qt_app.desktops.signal"), \
             patch("game_cafe.qt_app.desktops.signaled", return_value=True), \
             patch("game_cafe.qt_app.desktops.exited", return_value=False), \
             patch("game_cafe.qt_app.desktops.active_desktop_name",
                   return_value="Default"), \
             patch("game_cafe.qt_app.desktops.close_handle"), \
             patch("game_cafe.qt_app.desktops.close_desktop"):
            screen = ConsoleChildScreen(store, "ready", "stop", 1234)
            stages: list[str] = []
            screen.trace_shutdown = lambda stage, **details: stages.append(stage)

            def verify_child_close_rejection() -> None:
                nonlocal child_close_was_rejected
                child_close_was_rejected = screen.window.isVisible()

            def watchdog() -> None:
                nonlocal timed_out
                timed_out = True
                screen.app.exit(1)

            # CafeConsole must still reject ordinary window-close requests.
            QTimer.singleShot(50, screen.window.close)
            QTimer.singleShot(100, verify_child_close_rejection)
            QTimer.singleShot(1500, watchdog)
            screen.run()

    expected = [
        "child_stop_signal_observed", "child_qt_exit_requested",
        "child_qt_exit_call_returned", "child_qt_event_loop_exited",
        "child_ui_jobs_stopped", "child_cleanup_completed",
    ]
    if timed_out:
        return 1
    if not child_close_was_rejected:
        return 2
    if any(stage not in stages for stage in expected):
        return 3
    if [stages.index(stage) for stage in expected] != sorted(
            stages.index(stage) for stage in expected):
        return 4
    return 0


class ChildShutdownTest(unittest.TestCase):
    def test_stop_signal_exits_real_child_qt_event_loop(self):
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [sys.executable, "-m", "tests.test_child_shutdown", "--child-probe"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Child event loop did not exit after its stop signal.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__" and "--child-probe" in sys.argv:
    raise SystemExit(run_child_shutdown_probe())
elif __name__ == "__main__":
    unittest.main()
