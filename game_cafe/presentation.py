"""UI-facing data derived from authoritative snapshots; no GUI dependency."""

from __future__ import annotations

from datetime import datetime
import re
import time

from . import sessions


RETAINER_DISPLAY_TOLERANCE_SECONDS = 30


class RetainerDisplayClock:
    """Smooth a live retainer display without becoming an accounting clock."""

    def __init__(self) -> None:
        self._key: tuple[str, str] | None = None
        self._base_seconds = 0
        self._base_monotonic = 0.0
        self._last_sample_at = 0.0

    def _predicted(self, now: float) -> int:
        return max(0, self._base_seconds - int(max(0, now - self._base_monotonic)))

    def remaining(self, customer: dict,
                  monotonic_now: float | None = None) -> int:
        """Return display-only time, hard-resetting whenever gameplay is inactive."""
        monotonic_now = time.monotonic() if monotonic_now is None else monotonic_now
        authoritative = max(0, int(customer.get(
            "retainer_display_seconds", customer.get("balance_seconds", 0))))
        if not customer.get("retainer_active"):
            self._key = None
            self._base_seconds = authoritative
            self._base_monotonic = monotonic_now
            self._last_sample_at = 0.0
            return authoritative

        key = (str(customer.get("customer_id", "")),
               str(customer.get("retainer_session_id", "")))
        sample_at = float(customer.get("retainer_display_at", 0) or 0)
        received_at = float(customer.get(
            "retainer_display_received_monotonic", monotonic_now))
        message_age = max(0, monotonic_now - received_at)
        reported_now = max(0, authoritative - int(message_age))
        if key != self._key:
            self._key = key
            self._base_seconds = reported_now
            self._base_monotonic = monotonic_now
            self._last_sample_at = sample_at
            return reported_now

        predicted = self._predicted(monotonic_now)
        if sample_at <= self._last_sample_at:
            return predicted
        self._last_sample_at = sample_at
        if (reported_now == 0 or
                abs(reported_now - predicted) >= RETAINER_DISPLAY_TOLERANCE_SECONDS):
            self._base_seconds = reported_now
            self._base_monotonic = monotonic_now
            return reported_now
        return predicted


def natural_name_key(name: str) -> tuple:
    """Case-insensitive key that keeps PC-2 before PC-10."""
    return tuple((1, int(part)) if part.isdigit() else (0, part)
                 for part in re.split(r"(\d+)", name.casefold()) if part)


def sort_pc_rows(rows: list[dict], snapshot: dict,
                 option: str) -> list[dict]:
    """Order PC rows without changing their immutable ID-based actions."""
    if option == "name":
        return sorted(rows, key=lambda row: (natural_name_key(row["name"]),
                                             row["pcId"]))
    members = snapshot.get("members", {})
    return sorted(rows, key=lambda row: (
        -float(members.get(row["pcId"], {}).get("last_connected_at", 0)),
        row["pcId"],
    ))


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
    report = reported if isinstance(reported, dict) else {}
    admin = pc_id == snapshot["active_admin"].get("pc_id")
    desktop = report.get("desktop")
    access = ("ADMIN" if admin else "LOCKED" if desktop == "CafeConsole"
              else "UNLOCKED" if desktop == "Default" else "UNKNOWN")
    remaining = sessions.remaining_seconds(session, now, grace)
    registered_name = report.get("registered_customer_name", "")
    retainer_active = bool(report.get("retainer_active"))
    retainer_remaining = max(0, int(
        report.get("retainer_remaining", 0)))
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
    if retainer_active:
        phase = "retainer"
        detail = f"Retainer active · {duration(retainer_remaining)} remaining"
        remaining = retainer_remaining
    can_start = not admin and online and session is None and not retainer_active
    can_add = not admin and online and bool(session and
              session["kind"] == "timed" and phase in (
                  "buffer", "timed", "grace", "paused")) and not retainer_active
    can_end = not admin and online and session is not None
    can_replace = can_end and not retainer_active
    return {
        "pcId": pc_id, "name": snapshot["members"][pc_id]["name"],
        "role": "ADMIN" if admin else "USER", "online": online,
        "access": access, "phase": phase.upper(),
        "player": (registered_name if retainer_active else
                   session["player"] if session else registered_name or "—"),
        "kind": ("retainer" if retainer_active else
                 session["kind"] if session else "none"),
        "detail": detail, "timeText": duration(remaining),
        "remainingSeconds": remaining,
        "paidMinutes": session["paid_minutes"] if session else 0,
        "bufferMinutes": session["buffer_minutes"] if session else 0,
        "startedText": date_text(session["started_at"]) if session else "",
        "unlockRequested": pc_id in snapshot["unlock_requests"],
        "start": can_start, "add": can_add, "end": can_end,
        "replace": can_replace,
        "pause": not admin and online and bool(session) and phase in (
            "buffer", "timed", "open") and not retainer_active,
        "resume": not admin and online and phase == "paused",
        "exitSoftware": not admin and online,
        "lock": not admin and online and session is None and not retainer_active,
        "registeredCustomerId": report.get(
            "registered_customer_id", ""),
        "registeredCustomerName": registered_name,
        "retainerActive": retainer_active,
        "retainerBalanceSeconds": retainer_remaining,
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
    } for record in sorted(records, key=lambda item: item["ended_at"], reverse=True)]


def validate_start(kind: str, paid: str, buffer: str) -> tuple[int, int]:
    if kind not in ("timed", "open"):
        raise ValueError("Choose a timed or no-timer session.")
    if (kind == "timed" and not re.fullmatch(r"[0-9]+", paid.strip())) or \
            not re.fullmatch(r"[0-9]+", buffer.strip()):
        raise ValueError("Enter whole minutes only.")
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
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
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
