"""Bounded, credential-safe diagnostic logs for installed GameGrid processes."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
import traceback


MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 4  # Current file plus four archives per process = about 50 MB.
MAX_FIELD_LENGTH = 240
_BLOCKED_FIELD_PARTS = (
    "password", "secret", "private", "verifier", "ciphertext", "signature",
    "authorization_id", "sealed", "token",
)
_SAFE_TEXT = re.compile(r"[^\x20-\x7e]+")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|secret|token|signature|verifier|private(?:_key)?|"
    r"authorization_id)\s*[:=]\s*[^\s,;|]+")
_LONG_HEX = re.compile(r"(?i)\b[0-9a-f]{32,}\b")
_logger = logging.getLogger("gamegrid")
_logger.addHandler(logging.NullHandler())
_lock = threading.RLock()


class _UtcFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        stamp = datetime.fromtimestamp(record.created, timezone.utc)
        return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fingerprint(value: object) -> str:
    """Return a short non-secret correlation label for an immutable identifier."""
    text = str(value or "")
    return text[:8] if text else "-"


def _safe_value(key: str, value: object) -> str:
    lowered = key.lower()
    if any(part in lowered for part in _BLOCKED_FIELD_PARTS):
        return "[REDACTED]"
    if isinstance(value, BaseException):
        value = f"{type(value).__name__}: {value}"
    text = _SAFE_TEXT.sub("?", str(value)).replace("|", "/")
    text = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _LONG_HEX.sub("[REDACTED-ID]", text)
    return text[:MAX_FIELD_LENGTH]


def configure_diagnostics(data_dir: Path, child_mode: bool = False) -> Path:
    """Configure one rotating file owned by this process; logging must be optional."""
    name = "console-child.log" if child_mode else "gamegrid.log"
    path = data_dir / "logs" / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8", delay=True,
        )
        handler.setFormatter(_UtcFormatter(
            "%(asctime)s | %(levelname)s | %(message)s"))
        with _lock:
            for previous in list(_logger.handlers):
                _logger.removeHandler(previous)
                try:
                    previous.close()
                except Exception:
                    pass
            _logger.addHandler(handler)
            _logger.setLevel(logging.INFO)
            _logger.propagate = False
    except Exception:
        # Diagnostics can never prevent the cafe controller from starting.
        pass
    return path


def close_diagnostics() -> None:
    with _lock:
        for handler in list(_logger.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
            _logger.removeHandler(handler)
        _logger.addHandler(logging.NullHandler())


def diagnostic_event(level: int, component: str, event: str, **fields) -> None:
    """Write a single bounded line without allowing external payloads into logs."""
    try:
        parts = [component.upper()[:24], event.upper()[:48]]
        parts.extend(
            f"{str(key).upper()[:32]}={_safe_value(str(key), value)}"
            for key, value in fields.items() if value is not None
        )
        _logger.log(level, " | ".join(parts))
    except Exception:
        pass


def info(component: str, event: str, **fields) -> None:
    diagnostic_event(logging.INFO, component, event, **fields)


def warning(component: str, event: str, **fields) -> None:
    diagnostic_event(logging.WARNING, component, event, **fields)


def error(component: str, event: str, **fields) -> None:
    diagnostic_event(logging.ERROR, component, event, **fields)


def unexpected(component: str, event: str, caught: BaseException,
               **fields) -> None:
    """Record a bounded stack location without logging source or payload data."""
    frames = traceback.extract_tb(caught.__traceback__)[-8:]
    stack = " > ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in frames)
    diagnostic_event(
        logging.ERROR, component, event, error_type=type(caught).__name__,
        reason=caught, stack=stack, **fields)
