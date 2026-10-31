# -*- coding: utf-8 -*-
"""SQLite-backed sliding-window rate limiting (works across workers)."""

import time
from functools import wraps

from flask import flash, redirect, request, url_for

from backend.db.connection import get_db


def _bucket_ok(bucket, limit, window_seconds):
    """SQLite-backed sliding window for an arbitrary bucket key
    (works across multiple workers). Records a hit when allowed."""
    now = time.time()
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM rate_limit_hits WHERE ts < ?", (now - 86400,))
            used = conn.execute(
                "SELECT COUNT(*) FROM rate_limit_hits WHERE bucket=? AND ts>?",
                (bucket, now - window_seconds)
            ).fetchone()[0]
            if used >= limit:
                conn.commit()
                return False
            conn.execute("INSERT INTO rate_limit_hits (bucket, ts) VALUES (?,?)", (bucket, now))
            conn.commit()
    except Exception:
        return True  # never lock everyone out because of a DB hiccup
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
