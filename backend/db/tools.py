# -*- coding: utf-8 -*-
"""Database health tools: integrity, foreign keys, duplicates, backup verification, restore test.

    python -m backend.db.migrations check   [--db FILE]
    python -m backend.db.migrations verify-backup FILE
    python -m backend.db.migrations restore-test FILE
    python -m backend.db.migrations duplicates [--db FILE]
Nothing here modifies the database it inspects (restore-test works on a temporary copy)."""
import os
import shutil
import sqlite3
import tempfile

CORE_TABLES = ("students", "tests", "questions", "results", "answers", "schema_migrations")


def _open(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def duplicates(conn):
    """Rows that break (or would break) a uniqueness rule. Empty lists = healthy."""
    q = lambda sql: [tuple(r) for r in conn.execute(sql).fetchall()]
    return {
        "attendance (student_id, date)": q("SELECT student_id, date, COUNT(*) FROM attendance GROUP BY 1,2 HAVING COUNT(*)>1"),
        "results (test_id, student_id)": q("SELECT test_id, student_id, COUNT(*) FROM results GROUP BY 1,2 HAVING COUNT(*)>1"),
        "students (email)": q("SELECT lower(email), COUNT(*) FROM students GROUP BY 1 HAVING COUNT(*)>1"),
        "push_subscriptions (endpoint)": q("SELECT endpoint, COUNT(*) FROM push_subscriptions GROUP BY 1 HAVING COUNT(*)>1"),
        "notes (filename shared by several rows)": q("SELECT filename, COUNT(*) FROM notes GROUP BY 1 HAVING COUNT(*)>1"),
    }


def check(path):
    """-> dict(ok, integrity, foreign_key_violations, duplicates, tables, applied_migrations)."""
    conn = _open(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
        tables = {r[0]: conn.execute(f"SELECT COUNT(*) FROM [{r[0]}]").fetchone()[0]
                  for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        try:
            applied = [r[0] for r in conn.execute("SELECT id FROM schema_migrations ORDER BY id")]
        except sqlite3.Error:
            applied = []
        dups = {k: v for k, v in duplicates(conn).items() if v and not k.startswith("notes")}
        return {"ok": integrity == "ok" and not fk, "integrity": integrity, "foreign_key_violations": fk,
                "duplicates": dups, "tables": tables, "applied_migrations": applied}
    finally:
        conn.close()


def verify_backup(path):
    """A backup is only good if it opens, passes integrity_check and has the core tables."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return {"ok": False, "error": "file missing or empty"}
    try:
        rep = check(path)
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"cannot be opened as SQLite: {exc}"}
    missing = [t for t in CORE_TABLES if t not in rep["tables"]]
    rep["missing_core_tables"] = missing
    rep["ok"] = rep["ok"] and not missing
    return rep


def restore_test(path):
    """Restore the backup into a temporary file, open it, and compare row counts. Proves it is restorable."""
    rep = verify_backup(path)
    if not rep["ok"]:
        return {"ok": False, "verify": rep}
    tmpdir = tempfile.mkdtemp(prefix="restore-test-")
    try:
        target = os.path.join(tmpdir, "restored.db")
        shutil.copy2(path, target)
        after = check(target)
        same = after["tables"] == rep["tables"]
        return {"ok": bool(after["ok"] and same), "row_counts_match": same, "tables": after["tables"]}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
