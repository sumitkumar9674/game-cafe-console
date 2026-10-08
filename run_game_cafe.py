"""Launch the installed Game Cafe Console application."""

from __future__ import annotations

import sys


def main() -> int:
    if sys.platform != "win32":
        print("Game Cafe Console runs on Windows only.", file=sys.stderr)
        return 1
    try:
        from game_cafe.storage import Store, default_data_path
        child_mode = len(sys.argv) == 5 and sys.argv[1] == "--console-child"
        log_name = "console-child.log" if child_mode else "controller.log"
        log_file = open(default_data_path().parent / log_name,
                        "a", encoding="utf-8", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file
        from game_cafe.ui import Application, ConsoleChildScreen

        if child_mode:
            ConsoleChildScreen(Store(), sys.argv[2], sys.argv[3],
                               int(sys.argv[4])).run()
        elif len(sys.argv) == 1:
            Application().run()
        else:
            print("Unsupported application arguments.", file=sys.stderr)
            return 2
    except Exception as error:
        print(f"Game Cafe Console could not start: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
