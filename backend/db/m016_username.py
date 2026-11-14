# -*- coding: utf-8 -*-
"""Migration 016 - unique student usernames.

* adds students.username (nullable, so inserts that do not set it keep working)
* gives every existing student a username built from the name, oldest student first, so the
  first "Rahul Das" gets "rahuldas" and later ones get "rahuldas2", "rahuldas3" ...
* creates a unique case-insensitive index
Safe to run twice: only rows with username IS NULL are touched, nothing else is modified.
"""
from backend.utils.usernames import candidates, FALLBACK


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    cols = _cols(conn, "students")
    if not cols:
        return "SKIP: students table missing (created by init_db; will retry on next start)"
    if "username" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN username TEXT")
    taken = {(r[0] or "").lower() for r in conn.execute("SELECT username FROM students WHERE username IS NOT NULL")}
    todo = conn.execute("SELECT id, name FROM students WHERE username IS NULL ORDER BY id").fetchall()
    for sid, name in ((r[0], r[1]) for r in todo):
        pick = None
        for cand in candidates(name):
            if cand.lower() not in taken:
                pick = cand
                break
        if pick is None:
            pick = "%s%d" % (FALLBACK, sid)
        taken.add(pick.lower())
        conn.execute("UPDATE students SET username=? WHERE id=? AND username IS NULL", (pick, sid))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_students_username ON students(username COLLATE NOCASE)")
    return "usernames assigned to %d existing student(s)" % len(todo)


def down(conn):
    # Only the index is removed. The column and the generated names are harmless and are kept.
    conn.execute("DROP INDEX IF EXISTS uq_students_username")
    return "unique index dropped (students.username column kept)"
