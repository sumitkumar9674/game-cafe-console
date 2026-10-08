"""UI-facing data derived from authoritative snapshots; no GUI dependency."""

from __future__ import annotations

from datetime import datetime
import time

from . import sessions


def duration(seconds: float) -> str:
    value = max(0, int(seconds))
    return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"


def readable_duration(seconds: float) -> str:
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return (f"{hours}h {minutes}m {secs}s" if hours else
            f"{minutes}m {secs}s")


def date_text(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%d %b %Y, %I:%M %p")


def pc_row_data(snapshot: dict, pc_id: str, reported: dict | None,
                now: float | None = None) -> dict:
    now = time.time() if now is None else now
    session = snapshot["sessions"].get(pc_id)
    grace = snapshot["settings"]["grace_minutes"]
    phase = sessions.phase(session, now, grace)
    online = reported is not None
    admin = pc_id == snapshot["active_admin"].get("pc_id")
    desktop = reported.get("desktop") if reported else None
    access = ("ADMIN" if admin else "LOCKED" if desktop == "CafeConsole"
              else "UNLOCKED" if desktop == "Default" else "UNKNOWN")
    remaining = sessions.remaining_seconds(session, now, grace)
    if admin:
        detail = "Admin workstation · Customer controls unavailable"
    elif not session:
        detail = "No active session"
    elif phase == "buffer":
        detail = f"Buffer {duration(remaining)} left"
        detail += (f" · {session['paid_minutes']} paid min waiting"
                   if session["kind"] == "timed" else " · Counted use starts after buffer")
    elif phase == "timed":
        detail = (f"Paid time {duration(remaining)} left"
                  f" · {session['paid_minutes']} min assigned")
    elif phase == "grace":
        detail = f"Grace {duration(remaining)} left · Access locked"
    elif phase == "open":
        detail = f"Counted use {duration(remaining)}"
    elif phase == "paused":
        detail = f"Paused · {session['paused_phase'].upper()} · {duration(remaining)} frozen"
    else:
        detail = "Grace expired · Awaiting finalization"
    can_start = not admin and online and session is None
    can_add = not admin and online and bool(session and
              session["kind"] == "timed" and phase in ("buffer", "timed", "grace", "paused"))
    can_end = not admin and online and session is not None
    return {
        "pcId": pc_id, "name": snapshot["members"][pc_id]["name"],
        "role": "ADMIN" if admin else "USER", "online": online,
        "access": access, "phase": phase.upper(),
        "player": session["player"] if session else "—",
        "kind": session["kind"] if session else "none",
        "detail": detail, "timeText": duration(remaining),
        "remainingSeconds": remaining,
        "paidMinutes": session["paid_minutes"] if session else 0,
        "bufferMinutes": session["buffer_minutes"] if session else 0,
        "startedText": date_text(session["started_at"]) if session else "",
        "unlockRequested": pc_id in snapshot["unlock_requests"],
        "start": can_start, "add": can_add, "end": can_end,
        "pause": not admin and online and bool(session) and phase in ("buffer", "timed", "open"),
        "resume": not admin and online and phase == "paused",
        "exitSoftware": not admin and online,
        "lock": not admin and online and session is None,
    }


def history_rows(snapshot: dict, pc_id: str | None) -> list[dict]:
    records = ([record for group in snapshot["history"].values() for record in group]
               if pc_id is None else snapshot["history"].get(pc_id, []))
    return [{
        "id": record["id"], "pcName": record["pc_name"],
        "player": record["player"], "kind": record["kind"],
        "started": date_text(record["started_at"]),
        "ended": date_text(record["ended_at"]),
        "duration": readable_duration(record["elapsed_seconds"]),
        "counted": duration(record["counted_seconds"]),
        "paidMinutes": record["paid_minutes"],
        "reason": record["reason"].replace("_", " ").title(),
    } for record in sorted(records, key=lambda item: item["ended_at"], reverse=True)[:40]]


def validate_start(kind: str, paid: str, buffer: str) -> tuple[int, int]:
    if kind not in ("timed", "open"):
        raise ValueError("Choose a timed or no-timer session.")
    try:
        paid_minutes = int(paid.strip()) if kind == "timed" else 0
        buffer_minutes = int(buffer.strip())
    except (ValueError, AttributeError) as error:
        raise ValueError("Enter whole minutes only.") from error
    if kind == "timed" and not 1 <= paid_minutes <= 1440:
        raise ValueError("Paid minutes must be 1 to 1440.")
    if not 0 <= buffer_minutes <= 1440:
        raise ValueError("Buffer minutes must be 0 to 1440.")
    return paid_minutes, buffer_minutes


def validate_add(minutes: int) -> int:
    if not 1 <= minutes <= 1440:
        raise ValueError("Added time must be 1 to 1440 minutes.")
    return minutes


def add_time_preview(snapshot: dict, pc_id: str, minutes: int,
                     now: float | None = None) -> str:
    validate_add(minutes)
    session = snapshot["sessions"].get(pc_id)
    if not session or session["kind"] != "timed":
        raise ValueError("This PC has no timed session to extend.")
    phase = sessions.phase(session, now, snapshot["settings"]["grace_minutes"])
    if phase == "paused":
        phase = session["paused_phase"]
    if phase == "expired":
        raise ValueError("Grace has expired; this session cannot be renewed.")
    if phase == "buffer":
        return (f"Buffer continues unchanged. Upcoming paid allotment becomes "
                f"{session['paid_minutes'] + minutes} minutes.")
    if phase == "grace":
        return (f"The same session resumes now with {minutes} paid minutes. "
                f"Total assigned becomes {session['paid_minutes'] + minutes} minutes.")
    remaining = sessions.remaining_seconds(
        session, now, snapshot["settings"]["grace_minutes"]
    )
    return (f"Remaining paid time becomes about {duration(remaining + minutes * 60)}. "
            f"Total assigned becomes {session['paid_minutes'] + minutes} minutes.")
