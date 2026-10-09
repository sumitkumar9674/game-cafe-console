"""Launch the installed Game Cafe Console application."""

from __future__ import annotations

import sys

from game_cafe.single_instance import WindowsSingleInstance
from game_cafe.lifecycle import (disable_lifecycle_logging,
                                 enable_lifecycle_logging, lifecycle_event)


def report(message: str) -> None:
    """Windowed PyInstaller launches may not provide a stderr stream."""
    if sys.stderr is not None:
        print(message, file=sys.stderr)


def main() -> int:
    if sys.platform != "win32":
        report("Game Cafe Console runs on Windows only.")
        return 1
    child_mode = len(sys.argv) == 5 and sys.argv[1] == "--console-child"
    if not child_mode and len(sys.argv) != 1:
        report("Unsupported application arguments.")
        return 2
    instance = None
    logging_started = False
    try:
        if not child_mode:
            instance = WindowsSingleInstance()
            if not instance.acquire():
                report("Game Cafe Console is already running.")
                return 0
        from game_cafe.storage import Store, default_data_path
        log_name = "console-child.log" if child_mode else "controller.log"
        log_file = open(default_data_path().parent / log_name,
                        "a", encoding="utf-8", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file
        enable_lifecycle_logging()
        logging_started = True
        lifecycle_event("process_started",
                        mode="console_child" if child_mode else "controller")
        from game_cafe.ui import Application, ConsoleChildScreen

        if child_mode:
            ConsoleChildScreen(Store(), sys.argv[2], sys.argv[3],
                               int(sys.argv[4])).run()
        else:
            Application().run()
        lifecycle_event("application_run_returned")
    except Exception as error:
        report(f"Game Cafe Console could not start: {error}")
        return 1
    finally:
        if logging_started:
            lifecycle_event("main_process_returning")
            disable_lifecycle_logging()
        if instance:
            instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
