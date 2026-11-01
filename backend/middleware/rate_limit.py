# -*- coding: utf-8 -*-
"""SQLite-backed sliding-window rate limiting (works across workers)."""

import logging
import time
from functools import wraps

from flask import flash, redirect, request, url_for

from backend.db.connection import get_db


_log = logging.getLogger(__name__)
_CLEANUP_EVERY = 600          # seconds: delete expired hits at most once per 10 minutes per worker
_KEEP_SECONDS = 86400 + 3600  # the longest window used anywhere is one day
_last_cleanup = 0.0


def _cleanup(conn, now):
    """Throttled: a DELETE on every request made each limited call a database write."""
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_EVERY:
        return
    _last_cleanup = now
    conn.execute("DELETE FROM rate_limit_hits WHERE ts < ?", (now - _KEEP_SECONDS,))


def _bucket_ok(bucket, limit, window_seconds, fail_open=False):
    """The ONE sliding-window limiter (SQLite, works across workers). Records a hit when allowed.

    If the database fails the request is REFUSED and the failure is logged - a security check that
    cannot run must not silently allow everything. Pass fail_open=True only for low-risk limits.
    """
    now = time.time()
    try:
        with get_db() as conn:
            _cleanup(conn, now)
            used = conn.execute(
                "SELECT COUNT(*) FROM rate_limit_hits WHERE bucket=? AND ts>?",
                (bucket, now - window_seconds)
            ).fetchone()[0]
            if used >= limit:
                conn.commit()
                return False
            conn.execute("INSERT INTO rate_limit_hits (bucket, ts) VALUES (?,?)", (bucket, now))
            conn.commit()
    except Exception as exc:
        _log.error("rate limiter database error for bucket %r: %s", bucket.split(":")[0], exc)
        return bool(fail_open)
    return True


def _rate_ok(name, limit, window_seconds):
    """Per-IP rate limit."""
    return _bucket_ok(f"{name}:{request.remote_addr or 'unknown'}", limit, window_seconds)


def rate_limit(name, limit, window_seconds, methods=("POST",)):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if request.method in methods and not _rate_ok(name, limit, window_seconds):
                minutes = max(1, window_seconds // 60)
                flash(f"Too many attempts. Please wait about {minutes} minute(s) and try again.", "error")
                return redirect(url_for("index") if request.method != "POST" else request.path)
            return f(*args, **kwargs)
        return wrapper
    return deco
