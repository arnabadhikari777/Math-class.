# -*- coding: utf-8 -*-
"""One timezone strategy for the whole site: Asia/Kolkata (IST).

* NEW timestamps are written as timezone-aware ISO strings ("2026-11-14T10:30:00+05:30").
* OLD rows were written with the server's local clock and no zone. Their real zone cannot be
  established, so they are never converted or rewritten - they are shown exactly as stored.
* ``fmt_ist`` converts aware values to IST for display and leaves naive (old) values untouched.
"""
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - tzdata missing: fixed offset is identical for India
    IST = timezone(timedelta(hours=5, minutes=30), "IST")


def now_ist():
    return datetime.now(IST)


def now_ist_iso():
    return now_ist().isoformat(timespec="seconds")


def parse(value):
    try:
        return datetime.fromisoformat(str(value).strip().replace(" ", "T", 1)) if value else None
    except (TypeError, ValueError):
        return None


def to_ist(value):
    """Aware -> IST datetime. Naive (legacy) -> returned as-is (zone unknown)."""
    dt = parse(value)
    if dt is None:
        return None
    return dt.astimezone(IST) if dt.tzinfo else dt


def fmt_ist(value, with_time=True):
    dt = to_ist(value)
    if dt is None:
        return ""
    return dt.strftime("%d %b %Y, %I:%M %p") if with_time else dt.strftime("%d %b %Y")


def split_date_time(value):
    """(date, time) strings for the teacher login table."""
    dt = to_ist(value)
    if dt is None:
        return "", ""
    return dt.strftime("%d %b %Y"), dt.strftime("%I:%M %p")
