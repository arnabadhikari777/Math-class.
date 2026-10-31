# -*- coding: utf-8 -*-
"""JEE Main countdown helpers used by the home page / dashboards."""

from datetime import datetime

from backend.config import JEE_SESSIONS


# ───────────────────────── Smart insights ─────────────────────────
def jee_sessions_state():
    """JEE sessions with server-side days_left/state (used for no-JS fallback + insights)."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    out = []
    for s in JEE_SESSIONS:
        start = datetime.fromisoformat(s["start"])
        secs = (start - now).total_seconds()
        if secs > 0:
            state = "before"
        elif -secs <= s["window_days"] * 86400:
            state = "live"
        else:
            state = "done"
        out.append({
            **s,
            "iso": s["start"],
            "days_left": int(secs // 86400) if secs > 0 else 0,
            "state": state,
            "date_text": start.strftime("%d %b %Y, %I:%M %p").lstrip("0") + " IST",
        })
    return out


def _next_jee(sessions=None):
    for s in (sessions or jee_sessions_state()):
        if s["state"] == "before":
            return s
    return None
