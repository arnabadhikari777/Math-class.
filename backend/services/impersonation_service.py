# -*- coding: utf-8 -*-
"""Teacher 'view as student': audit trail + hard limits.

* While impersonating, the session is a STUDENT session (is_teacher is False), so no teacher page or
  teacher API accepts it; the only way back is the Exit button, which closes the audit row and
  rebuilds a clean teacher session.
* Every state-changing request is refused while impersonating (see middleware/auth.py), except Exit.
* Start and end times are stored in impersonation_log; a view session expires after MAX_MINUTES.
"""
import logging
from datetime import datetime

from backend.db.connection import get_db

log = logging.getLogger(__name__)
MAX_MINUTES = 60


def start(student_id, ip):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO impersonation_log (student_id, teacher_label, started_at, ip_address) VALUES (?,?,?,?)",
            (student_id, "teacher", datetime.now().isoformat(), ip))
        conn.commit()
        return cur.lastrowid


def end(log_id, reason):
    if not log_id:
        return
    try:
        with get_db() as conn:
            conn.execute("UPDATE impersonation_log SET ended_at=?, end_reason=? WHERE id=? AND ended_at IS NULL",
                         (datetime.now().isoformat(), reason, log_id))
            conn.commit()
    except Exception:
        log.exception("could not close impersonation_log %s", log_id)


def expired(started_iso):
    try:
        return (datetime.now() - datetime.fromisoformat(started_iso)).total_seconds() > MAX_MINUTES * 60
    except (TypeError, ValueError):
        return True
