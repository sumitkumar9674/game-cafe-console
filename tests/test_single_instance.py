"""Windows main-controller instance ownership without starting Qt or desktops."""

import io
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import uuid
from unittest.mock import Mock, patch

import run_game_cafe
from game_cafe.single_instance import (MAIN_INSTANCE_MUTEX,
                                       WindowsSingleInstance)


class SingleInstanceTest(unittest.TestCase):
    def mutex_name(self) -> str:
        return rf"Global\StickForYou.GameCafeConsole.Test.{uuid.uuid4().hex}"

    def test_first_controller_acquires_second_is_rejected_and_close_releases(self):
        first = WindowsSingleInstance(self.mutex_name())
        second = WindowsSingleInstance(first.name)
        replacement = WindowsSingleInstance(first.name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.close()
            self.assertTrue(replacement.acquire())
        finally:
            first.close()
            second.close()
            replacement.close()

    def test_simultaneous_launches_cannot_both_acquire(self):
        name = self.mutex_name()
        start = threading.Barrier(3)
        acquired = threading.Barrier(3)
        results = []

        def attempt():
            instance = WindowsSingleInstance(name)
            start.wait()
            result = instance.acquire()
            results.append(result)
            acquired.wait()
            instance.close()

        workers = [threading.Thread(target=attempt) for _ in range(2)]
        for worker in workers:
            worker.start()
        start.wait()
        acquired.wait()
        for worker in workers:
            worker.join()
        self.assertEqual(sorted(results), [False, True])
        after = WindowsSingleInstance(name)
        try:
            self.assertTrue(after.acquire())
        finally:
            after.close()

    def test_identity_is_stable_for_source_and_packaged_launches(self):
        self.assertEqual(
            MAIN_INSTANCE_MUTEX,
            r"Global\StickForYou.GameCafeConsole.MainController.v1")
        self.assertNotIn("python", MAIN_INSTANCE_MUTEX.lower())
        self.assertNotIn(".exe", MAIN_INSTANCE_MUTEX.lower())

    def test_operating_system_releases_mutex_after_owner_crash(self):
        name = self.mutex_name()
        script = (
            "import os,sys; "
            "from game_cafe.single_instance import WindowsSingleInstance; "
            "guard=WindowsSingleInstance(sys.argv[1]); "
            "assert guard.acquire(); os._exit(17)"
        )
        result = subprocess.run([sys.executable, "-c", script, name],
                                cwd=str(Path(run_game_cafe.__file__).parent),
                                check=False)
        self.assertEqual(result.returncode, 17)
        replacement = WindowsSingleInstance(name)
        try:
            self.assertTrue(replacement.acquire())
        finally:
            replacement.close()

    def test_duplicate_main_launch_exits_before_log_or_application_initialization(self):
        guard = Mock()
        guard.acquire.return_value = False
        with patch.object(sys, "argv", ["run_game_cafe.py"]), \
             patch("run_game_cafe.WindowsSingleInstance", return_value=guard), \
             patch("builtins.open") as open_file:
            self.assertEqual(run_game_cafe.main(), 0)
        open_file.assert_not_called()
        guard.close.assert_called_once()

    def test_console_child_bypasses_main_instance_guard(self):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        log = io.StringIO()
        child = Mock()
        try:
            with patch.object(sys, "argv", ["run_game_cafe.py", "--console-child",
                                            "ready", "stop", "123"]), \
                 patch("run_game_cafe.WindowsSingleInstance") as guard_type, \
                 patch("game_cafe.storage.Store", return_value=Mock()), \
                 patch("game_cafe.storage.default_data_path"), \
                 patch("game_cafe.ui.ConsoleChildScreen", return_value=child), \
                 patch("builtins.open", return_value=log):
                self.assertEqual(run_game_cafe.main(), 0)
            guard_type.assert_not_called()
            child.run.assert_called_once()
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def test_guard_is_held_until_main_application_returns(self):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        log = io.StringIO()
        guard = Mock()
        guard.acquire.return_value = True
        application = Mock()

        def running():
            guard.close.assert_not_called()

        application.run.side_effect = running
        try:
            with patch.object(sys, "argv", ["run_game_cafe.py"]), \
                 patch("run_game_cafe.WindowsSingleInstance", return_value=guard), \
                 patch("game_cafe.storage.default_data_path"), \
                 patch("game_cafe.ui.Application", return_value=application), \
                 patch("builtins.open", return_value=log):
                self.assertEqual(run_game_cafe.main(), 0)
            application.run.assert_called_once()
            guard.close.assert_called_once()
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr


if __name__ == "__main__":
    unittest.main()
