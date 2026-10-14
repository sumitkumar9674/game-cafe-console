"""Launch the installed Game Cafe Console application."""

from __future__ import annotations

import sys

from game_cafe.single_instance import WindowsSingleInstance
from game_cafe.lifecycle import (disable_lifecycle_logging,
                                 enable_lifecycle_logging, lifecycle_event)
from game_cafe.diagnostics import (close_diagnostics, configure_diagnostics,
                                   info as diagnostic_info,
                                   unexpected as diagnostic_unexpected)


BUILD_IDENTIFIER = "1.1.2"


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
        configure_diagnostics(default_data_path().parent, child_mode)
        enable_lifecycle_logging()
        logging_started = True
        diagnostic_info(
            "APPLICATION", "STARTUP", build=BUILD_IDENTIFIER,
            role="console-child" if child_mode else "controller",
            packaged=bool(getattr(sys, "frozen", False)),
        )
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
        diagnostic_unexpected("APPLICATION", "UNEXPECTED_PROCESS_ERROR", error)
        report(f"Game Cafe Console could not start: {error}")
        return 1
    finally:
        if logging_started:
            lifecycle_event("main_process_returning")
            disable_lifecycle_logging()
            close_diagnostics()
        if instance:
            instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
