# -*- coding: utf-8 -*-
"""Small shared helpers (percentage, date parsing, streak counting)."""

from datetime import datetime, timedelta


def percentage(score, total):
    if total <= 0:
        return 0
    return round((score / total) * 100, 1)


def _parse_dt(value):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _streak_days(result_rows):
    """Consecutive calendar days (ending today or yesterday) with >=1 submitted test."""
    days = set()
    for r in result_rows:
        d = _parse_dt(r["submitted_at"])
        if d:
            days.add(d.date())
    if not days:
        return 0
    cur = datetime.now().date()
    if cur not in days:
        cur -= timedelta(days=1)
    streak = 0
    while cur in days:
        streak += 1
        cur -= timedelta(days=1)
    return streak
