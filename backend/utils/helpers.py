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


# ---- safe redirects / CSV -------------------------------------------------
def safe_back(default_endpoint="index"):
    """Where to send the user 'back'. Uses the Referer ONLY when it points to this same site,
    never to a foreign host (open-redirect protection)."""
    from urllib.parse import urlsplit
    from flask import request, url_for
    ref = request.referrer or ""
    if ref:
        parts = urlsplit(ref)
        if parts.scheme in ("http", "https") and parts.netloc == request.host:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            return path
    return url_for(default_endpoint)


def safe_next(target, default_endpoint="index"):
    """Validate a user-supplied 'next' value: only same-site relative paths pass."""
    from urllib.parse import urlsplit
    from flask import url_for
    if isinstance(target, str) and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc:
            return target
    return url_for(default_endpoint)


_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Neutralise spreadsheet formula injection in an EXPORT cell only (the database value is untouched).
    A leading apostrophe makes Excel / LibreOffice / Sheets treat the cell as text."""
    if value is None:
        return ""
    text = str(value)
    if text and text.lstrip()[:1] in ("=", "+", "-", "@") or text[:1] in ("\t", "\r"):
        return "'" + text
    return value
