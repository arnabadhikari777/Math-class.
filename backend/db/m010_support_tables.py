# -*- coding: utf-8 -*-
"""Migration 010 - support tables for the security / storage fixes (additive only).

  impersonation_log      who-viewed-which-student audit (start, end, reason, ip)
  deletion_queue         retry + reconciliation queue for files / Cloudinary images that could not be
                         removed at delete time (DB and storage are separate systems)
  notes                  + original_filename, file_sha256, file_size, is_deleted, deleted_at
  bank_images            + status ('active' | 'pending_delete'), status_changed_at
  push_subscriptions     + host, expires_at, fail_count, last_success_at, last_failure_at
  attendance             unique (student_id, date) already enforced by 008's rebuild
"""


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add(conn, table, col, ddl):
    if _cols(conn, table) and col not in _cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def up(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS impersonation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        teacher_label TEXT NOT NULL DEFAULT 'teacher',
        started_at TEXT NOT NULL,
        ended_at TEXT,
        end_reason TEXT,
        ip_address TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imp_student ON impersonation_log(student_id, started_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS deletion_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK (kind IN ('file','cloudinary')),
        ref TEXT NOT NULL,
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','failed')),
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(kind, ref)
    )""")
    for col, ddl in (("original_filename", "TEXT"), ("file_sha256", "TEXT"), ("file_size", "INTEGER"),
                     ("is_deleted", "INTEGER NOT NULL DEFAULT 0"), ("deleted_at", "TEXT")):
        _add(conn, "notes", col, ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_filename ON notes(filename)")
    for col, ddl in (("status", "TEXT NOT NULL DEFAULT 'active'"), ("status_changed_at", "TEXT")):
        _add(conn, "bank_images", col, ddl)
    for col, ddl in (("host", "TEXT"), ("expires_at", "TEXT"), ("fail_count", "INTEGER NOT NULL DEFAULT 0"),
                     ("last_success_at", "TEXT"), ("last_failure_at", "TEXT")):
        _add(conn, "push_subscriptions", col, ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_push_student ON push_subscriptions(student_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_image_url ON questions(image_url)")
    return "support tables created"


def down(conn):
    raise RuntimeError("010_support_tables holds audit / queue data; restore a backup instead of rolling back.")
