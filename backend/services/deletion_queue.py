# -*- coding: utf-8 -*-
"""Retry + reconciliation for deletes that span two systems (database <-> file system / Cloudinary).

Pattern: the database row is the source of truth. Its row is removed (or marked) first, in a committed
transaction; the physical delete happens afterwards. If the physical delete fails, a row is left in
`deletion_queue` and `run_pending()` (CLI: python -m backend.services.deletion_queue) retries it.
Nothing is ever left half-deleted without a record."""
import logging
from datetime import datetime

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 8


def enqueue(conn, kind, ref, reason=""):
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO deletion_queue (kind, ref, reason, status, attempts, created_at, updated_at)
           VALUES (?,?,?,'pending',0,?,?)
           ON CONFLICT(kind, ref) DO UPDATE SET status='pending', updated_at=excluded.updated_at,
                                                reason=excluded.reason""",
        (kind, ref, reason, now, now))


def run_pending(conn, handlers, limit=100):
    """handlers: {'file': fn(ref)->None, 'cloudinary': fn(ref)->None}; a handler raises on failure.
    Returns {'done': n, 'failed': n, 'pending': n}."""
    stats = {"done": 0, "failed": 0, "pending": 0}
    rows = conn.execute("SELECT * FROM deletion_queue WHERE status='pending' ORDER BY id LIMIT ?", (limit,)).fetchall()
    for r in rows:
        now = datetime.now().isoformat()
        try:
            handlers[r["kind"]](r["ref"])
            conn.execute("UPDATE deletion_queue SET status='done', attempts=attempts+1, last_error=NULL, "
                         "updated_at=? WHERE id=?", (now, r["id"]))
            stats["done"] += 1
        except Exception as exc:  # noqa: BLE001 - recorded, retried later
            attempts = r["attempts"] + 1
            status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
            conn.execute("UPDATE deletion_queue SET status=?, attempts=?, last_error=?, updated_at=? WHERE id=?",
                         (status, attempts, str(exc)[:300], now, r["id"]))
            stats["failed" if status == "failed" else "pending"] += 1
            log.warning("deletion_queue %s %s failed (%s): %s", r["kind"], r["ref"], attempts, exc)
    conn.commit()
    return stats
