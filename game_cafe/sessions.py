"""Pure session timing rules shared by the controller and Admin UI."""

from __future__ import annotations

import time
import uuid


def start_session(kind: str, paid_minutes: int, buffer_minutes: int,
                  now: float | None = None) -> dict:
    """Create a timed or open session; every new player starts as Guest."""
    if kind not in ("timed", "open"):
        raise ValueError("Session type must be timed or open.")
    if kind == "timed" and paid_minutes <= 0:
        raise ValueError("A timed session needs positive paid minutes.")
    if kind == "open" and paid_minutes != 0:
        raise ValueError("An open session cannot have paid minutes.")
    if buffer_minutes < 0:
        raise ValueError("Buffer minutes cannot be negative.")
    now = time.time() if now is None else now
    buffer_end = now + buffer_minutes * 60
    return {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "player": "Guest",
        "started_at": now,
        "buffer_minutes": buffer_minutes,
        "buffer_end": buffer_end,
        "paid_minutes": paid_minutes,
        "paid_end": buffer_end + paid_minutes * 60 if kind == "timed" else None,
        "grace_started": None,
        "grace_used_seconds": 0.0,
        "paused_at": None,
        "paused_phase": None,
        "paused_seconds": 0.0,
        "additions": [],
    }


def phase(session: dict | None, now: float | None = None,
          grace_minutes: int = 10) -> str:
    """Return buffer, timed, open, grace, expired, or none."""
    if session is None:
        return "none"
    now = time.time() if now is None else now
    if session.get("paused_at") is not None:
        return "paused"
    if now < session["buffer_end"]:
        return "buffer"
    if session["kind"] == "open":
        return "open"
    if session["grace_started"] is not None:
        if now >= session["grace_started"] + grace_minutes * 60:
            return "expired"
        return "grace"
    if now < session["paid_end"]:
        return "timed"
    if now >= session["paid_end"] + grace_minutes * 60:
        return "expired"
    return "grace"


def enter_grace_if_due(session: dict, now: float, grace_minutes: int) -> str:
    """Pin grace to paid expiry, including after a temporarily stopped process."""
    current = phase(session, now, grace_minutes)
    if current in ("grace", "expired") and session["grace_started"] is None:
        session["grace_started"] = session["paid_end"]
    return current


def add_paid_time(session: dict, minutes: int, now: float | None = None,
                  grace_minutes: int = 10) -> None:
    """Extend the same timed session, restarting from now during grace."""
    if session["kind"] != "timed":
        raise ValueError("Open sessions do not accept added time.")
    if minutes <= 0:
        raise ValueError("Added time must be positive.")
    now = time.time() if now is None else now
    current = enter_grace_if_due(session, now, grace_minutes)
    if current == "paused":
        current = session["paused_phase"]
    if current == "expired":
        raise ValueError("The grace period has expired.")
    if current == "grace":
        session["grace_used_seconds"] += max(0, now - session["grace_started"])
        session["grace_started"] = None
        session["paid_end"] = now + minutes * 60
    else:
        session["paid_end"] += minutes * 60
    session["paid_minutes"] += minutes
    session["additions"].append({"at": now, "minutes": minutes})


def pause_session(session: dict, now: float | None = None,
                  grace_minutes: int = 10) -> bool:
    now = time.time() if now is None else now
    current = phase(session, now, grace_minutes)
    if current == "paused":
        return False
    if current not in ("buffer", "timed", "open"):
        raise ValueError("Only buffer, paid, or no-timer sessions can be paused.")
    session["paused_at"] = now
    session["paused_phase"] = current
    return True


def resume_session(session: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    paused_at = session.get("paused_at")
    if paused_at is None:
        return False
    paused_for = max(0, now - paused_at)
    session["paused_seconds"] = session.get("paused_seconds", 0.0) + paused_for
    session["buffer_end"] += paused_for
    if session["paid_end"] is not None:
        session["paid_end"] += paused_for
    session["paused_at"] = None
    session["paused_phase"] = None
    return True


def finish_session(session: dict, pc_id: str, pc_name: str, reason: str,
                   now: float | None = None) -> dict:
    """Create one final history entry; callers then clear the active session."""
    if reason not in ("customer", "admin", "grace_expired", "close_software", "interrupted"):
        raise ValueError("Invalid session end reason.")
    now = time.time() if now is None else now
    paused_used = session.get("paused_seconds", 0.0)
    if session.get("paused_at") is not None:
        paused_used += max(0, now - session["paused_at"])
    grace_used = session["grace_used_seconds"]
    if session["grace_started"] is not None:
        grace_used += max(0, now - session["grace_started"])
    buffer_used = min(max(0, now - session["started_at"] - paused_used),
                      session["buffer_minutes"] * 60)
    counted = max(0, now - session["buffer_end"] - grace_used -
                  (max(0, now - session["paused_at"]) if session.get("paused_at") is not None else 0))
    if session["kind"] == "timed":
        counted = min(counted, session["paid_minutes"] * 60)
    return {
        "id": session["id"], "pc_id": pc_id, "pc_name": pc_name,
        "player": session["player"], "kind": session["kind"],
        "started_at": session["started_at"], "ended_at": now,
        "elapsed_seconds": max(0, now - session["started_at"]),
        "counted_seconds": counted,
        "paid_minutes": session["paid_minutes"],
        "buffer_minutes": session["buffer_minutes"],
        "buffer_used_seconds": buffer_used,
        "grace_used_seconds": grace_used,
        "paused_seconds": paused_used,
        "reason": reason, "additions": list(session["additions"]),
    }


def remaining_seconds(session: dict | None, now: float | None = None,
                      grace_minutes: int = 10) -> int:
    if session is None:
        return 0
    now = time.time() if now is None else now
    current = phase(session, now, grace_minutes)
    if current == "paused":
        now = session["paused_at"]
        current = session["paused_phase"]
    if current == "buffer":
        return max(0, int(session["buffer_end"] - now))
    if current == "timed":
        return max(0, int(session["paid_end"] - now))
    if current == "grace":
        started = session["grace_started"] or session["paid_end"]
        return max(0, int(started + grace_minutes * 60 - now))
    if current == "open":
        return max(0, int(now - session["buffer_end"]))
    return 0
