from __future__ import annotations

"""Shared helpers for scheduled social publishing.

Both Facebook and YouTube let callers hand over a future publish time at
upload time; the platform servers then make the content public at exactly
that moment. This module normalises the different payload spellings into
one ISO-8601 (UTC) value and validates the platform windows.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

SCHEDULE_KEYS = ("scheduledPublishAt", "publishAt", "scheduled_publish_at")


def parse_scheduled_publish_at(payload: dict) -> str | None:
    """Return a normalised ISO-8601 UTC string when the payload schedules a post.

    Accepts an ISO-8601 string (with or without an offset) or a UNIX
    timestamp (number, or numeric string). Returns None when the payload
    does not carry any scheduling key or the value is empty.
    """
    if not isinstance(payload, dict):
        return None
    for key in SCHEDULE_KEYS:
        raw = payload.get(key)
        if raw is not None and str(raw).strip():
            return normalize_iso_datetime(raw)
    return None


def normalize_iso_datetime(raw: Any) -> str:
    """Normalise a datetime string or UNIX timestamp to 'YYYY-MM-DDTHH:MM:SSZ'."""
    value = str(raw).strip()
    if not value:
        raise ValueError("Scheduled publish time is empty.")
    try:
        timestamp = float(value)
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except ValueError:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def scheduled_unix_timestamp(raw: str) -> int:
    """Convert a normalised ISO-8601 value to a UNIX timestamp (Facebook Graph API)."""
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return int(dt.timestamp())


def parse_iso_datetime(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def validate_schedule_window(
    iso: str,
    min_ahead: timedelta,
    max_ahead: timedelta | None = None,
    platform: str = "",
) -> None:
    """Reject schedule times outside the platform window.

    min_ahead is required (the platform needs time to process the upload);
    max_ahead is optional (Facebook caps scheduling at 75 days).
    """
    dt = parse_iso_datetime(iso)
    now = datetime.now(timezone.utc)
    label = f" cho {platform}" if platform else ""
    if dt < now + min_ahead:
        raise ValueError(
            f"Thời gian hẹn đăng{label} phải cách hiện tại ít nhất {int(min_ahead.total_seconds() // 60)} phút."
        )
    if max_ahead is not None and dt > now + max_ahead:
        raise ValueError(
            f"Thời gian hẹn đăng{label} vượt quá giới hạn {int(max_ahead.days)} ngày của nền tảng."
        )
