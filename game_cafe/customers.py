"""Customer account validation shared by storage, runtime, and the Qt bridge."""

from __future__ import annotations

import re


DEFAULT_CUSTOMER_PASSWORD = "12345"
MAX_CUSTOMER_NAME_LENGTH = 60
MAX_CUSTOMER_PASSWORD_LENGTH = 128
MIN_CUSTOMER_PASSWORD_LENGTH = 5
MAX_RETAINER_SECONDS = 9_999 * 3600 + 59 * 60
MAX_COOLDOWN_MINUTES = 525_600


def normalize_customer_name(name: str) -> tuple[str, str]:
    """Return the saved display name and its case-insensitive unique key."""
    if not isinstance(name, str):
        raise ValueError("Customer name is required.")
    display = " ".join(name.split())
    if not display:
        raise ValueError("Customer name is required.")
    if len(display) > MAX_CUSTOMER_NAME_LENGTH:
        raise ValueError(
            f"Customer name must be {MAX_CUSTOMER_NAME_LENGTH} characters or less."
        )
    return display, display.casefold()


def retainer_seconds(hours: str | int, minutes: str | int) -> int:
    """Validate separate whole-hour/minute fields and return exact seconds."""
    hour_text, minute_text = str(hours).strip(), str(minutes).strip()
    if not re.fullmatch(r"[0-9]+", hour_text) or not re.fullmatch(
            r"[0-9]+", minute_text):
        raise ValueError("Enter whole, nonnegative hours and minutes.")
    hour_value, minute_value = int(hour_text), int(minute_text)
    if minute_value > 59:
        raise ValueError("Minutes must be between 0 and 59.")
    total = hour_value * 3600 + minute_value * 60
    if total <= 0:
        raise ValueError("Retainer adjustment must be greater than zero.")
    if total > MAX_RETAINER_SECONDS:
        raise ValueError("Retainer adjustment is too large.")
    return total


def retainer_text(seconds: int) -> str:
    value = max(0, int(seconds))
    return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"


def validate_cooldown_minutes(value: int | str) -> int:
    """Accept practical whole-minute cooldowns, including zero to disable."""
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError("Cooldown minutes must be a nonnegative whole number.")
    minutes = int(text)
    if minutes > MAX_COOLDOWN_MINUTES:
        raise ValueError("Cooldown cannot exceed 525600 minutes.")
    return minutes


def validate_customer_password(password: str) -> str:
    """Keep customer passwords practical while rejecting empty/oversized values."""
    if not isinstance(password, str):
        raise ValueError("Password is required.")
    if not MIN_CUSTOMER_PASSWORD_LENGTH <= len(password) <= MAX_CUSTOMER_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be {MIN_CUSTOMER_PASSWORD_LENGTH} to "
            f"{MAX_CUSTOMER_PASSWORD_LENGTH} characters."
        )
    return password
