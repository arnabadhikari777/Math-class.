# -*- coding: utf-8 -*-
"""Read-side business logic for teacher/admin dashboards (counts, student search,
per-test results). Write operations for students/tests/notes remain in backend/routes/teacher/."""
from backend.db import escape_like

STUDENT_FIELDS = ("id", "name", "class_name", "roll", "email", "mobile", "is_verified",
                  "is_blocked", "admitted_by_teacher", "registered_at")


def overview(conn):
    def one(sql):
        return conn.execute(sql).fetchone()[0]
    return {
        "students_verified": one("SELECT COUNT(*) FROM students WHERE is_verified=1"),
        "students_pending": one("SELECT COUNT(*) FROM students WHERE is_verified=0"),
        "students_blocked": one("SELECT COUNT(*) FROM students WHERE is_blocked=1"),
        "notes": one("SELECT COUNT(*) FROM notes"),
        "tests": one("SELECT COUNT(*) FROM tests"),
        "tests_active": one("SELECT COUNT(*) FROM tests WHERE is_active=1"),
        "results": one("SELECT COUNT(*) FROM results"),
        "contact_requests_new": one("SELECT COUNT(*) FROM contact_requests WHERE status='new'"),
    }


def search_students(conn, q, class_name, status, per_page, offset):
    where, params = ["1=1"], []
    if q:
        like = f"%{escape_like(q)}%"
        where.append("(name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' "
                     "OR mobile LIKE ? ESCAPE '\\' OR roll LIKE ? ESCAPE '\\')")
        params += [like] * 4
    if class_name:
        where.append("class_name=?")
        params.append(class_name)
    if status == "verified":
        where.append("is_verified=1 AND is_blocked=0")
    elif status == "pending":
        where.append("is_verified=0")
    elif status == "blocked":
        where.append("is_blocked=1")
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM students WHERE {clause}", params).fetchone()[0]
    cols = ", ".join(STUDENT_FIELDS)  # never password_hash / session_version
    rows = conn.execute(
        f"SELECT {cols} FROM students WHERE {clause} ORDER BY class_name, name LIMIT ? OFFSET ?",
        params + [per_page, offset]).fetchall()
    return [dict(r) for r in rows], total


def test_results(conn, test_id, per_page, offset):
    test = conn.execute("SELECT id, title, subject, class_name, duration_minutes, is_active "
                        "FROM tests WHERE id=?", (test_id,)).fetchone()
    if test is None:
        return None, [], 0
    total = conn.execute("SELECT COUNT(*) FROM results WHERE test_id=?", (test_id,)).fetchone()[0]
    rows = conn.execute(
        """SELECT r.id AS result_id, r.score, r.total_marks, r.submitted_at,
                  s.id AS student_id, s.name, s.class_name, s.roll
           FROM results r JOIN students s ON s.id=r.student_id
           WHERE r.test_id=? ORDER BY r.score DESC, r.submitted_at ASC LIMIT ? OFFSET ?""",
        (test_id, per_page, offset)).fetchall()
    return dict(test), [dict(r) for r in rows], total
