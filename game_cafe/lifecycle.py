"""Small, credential-free lifecycle diagnostics for Windows shutdowns."""

from __future__ import annotations

from datetime import datetime
import os
import threading
import time


PROCESS_RUN_ID = f"{os.getpid()}-{time.time_ns():x}"
_enabled = False


def enable_lifecycle_logging() -> None:
    global _enabled
    _enabled = True


def disable_lifecycle_logging() -> None:
    global _enabled
    _enabled = False


def lifecycle_event(stage: str, **details) -> None:
    """Write one line to the existing per-process log without risking shutdown."""
    if not _enabled:
        return
    fields = " ".join(f"{key}={value}" for key, value in details.items())
    line = (
        f"{datetime.now().astimezone().isoformat(timespec='milliseconds')} "
        f"lifecycle run={PROCESS_RUN_ID} pid={os.getpid()} "
        f"thread={threading.get_ident()} stage={stage}"
    )
    if fields:
        line += " " + fields
    try:
        print(line, flush=True)
    except Exception:
        pass
