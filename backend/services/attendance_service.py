# -*- coding: utf-8 -*-
"""Attendance business logic. The ``attendance`` table already existed in the
database (created by app.init_db) but nothing used it; this is its first user.
Schema is untouched: id, student_id, date, status, note, marked_at.
"""
import re
from datetime import date, datetime

STATUSES = ("present", "absent", "late", "excused")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class ValidationError(ValueError):
    pass


def parse_date(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValidationError("date must be YYYY-MM-DD")
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError("date is not a real calendar date")
    if d > date.today():
        raise ValidationError("attendance cannot be marked for a future date")
    return d.isoformat()


def roster(conn, day, class_name=""):
    """Verified, unblocked students with their status on ``day`` (None = not marked)."""
    sql = """SELECT s.id AS student_id, s.name, s.class_name, s.roll,
                    a.status, a.note
             FROM students s
             LEFT JOIN attendance a ON a.student_id=s.id AND a.date=?
             WHERE s.is_verified=1 AND s.is_blocked=0"""
    params = [day]
    if class_name:
        sql += " AND s.class_name=?"
        params.append(class_name)
    sql += " ORDER BY s.class_name, s.name"
    return [dict(r) for r in conn.execute(sql, params)]


def mark(conn, day, records):
    """Upsert attendance for many students in ONE transaction. Returns count.
    Raises ValidationError before writing anything if any record is invalid."""
    if not isinstance(records, list) or not records:
        raise ValidationError("records must be a non-empty list")
    if len(records) > 500:
        raise ValidationError("at most 500 records per request")
    clean = []
    for rec in records:
        if not isinstance(rec, dict):
            raise ValidationError("each record must be an object")
        sid, status = rec.get("student_id"), str(rec.get("status", "present")).lower()
        note = (rec.get("note") or "").strip()[:200]
        if not isinstance(sid, int) or isinstance(sid, bool):
            raise ValidationError("student_id must be an integer")
        if status not in STATUSES:
            raise ValidationError("status must be one of: " + ", ".join(STATUSES))
        clean.append((sid, status, note))
    ids = {c[0] for c in clean}
    marks = ",".join("?" * len(ids))
    found = {r[0] for r in conn.execute(
        f"SELECT id FROM students WHERE id IN ({marks}) AND is_verified=1", list(ids))}
    missing = sorted(ids - found)
    if missing:
        raise ValidationError(f"unknown or unverified student id(s): {missing[:10]}")
    now = datetime.now().isoformat()
    for sid, status, note in clean:
        cur = conn.execute(
            "UPDATE attendance SET status=?, note=?, marked_at=? WHERE student_id=? AND date=?",
            (status, note, now, sid, day))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO attendance (student_id, date, status, note, marked_at) VALUES (?,?,?,?,?)",
                (sid, day, status, note, now))
    return len(clean)


def student_history(conn, sid, month=None):
    sql, params = "SELECT date, status, note FROM attendance WHERE student_id=?", [sid]
    if month:
        if not MONTH_RE.match(month):
            raise ValidationError("month must be YYYY-MM")
        sql += " AND date LIKE ?"
        params.append(month + "-%")
    sql += " ORDER BY date DESC LIMIT 400"
    rows = [dict(r) for r in conn.execute(sql, params)]
    counts = {s: 0 for s in STATUSES}
    for r in rows:
        if r["status"] in counts:
            counts[r["status"]] += 1
    return rows, counts
