# -*- coding: utf-8 -*-
"""Migration 018 - Doubt Management, per-doubt AI history, student profile picture, teacher notifications.

Additive only: new tables, new nullable columns, new indexes. Nothing is dropped, rewritten or deleted.
Safe to run twice (every statement is IF NOT EXISTS / checked with PRAGMA first).
"""


def _has_col(conn, table, col):
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _add_col(conn, table, col, ddl):
    if not _has_col(conn, table, col):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def up(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doubts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            chapter TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            question TEXT NOT NULL,
            image_url TEXT,
            image_public_id TEXT,
            image_meta TEXT,
            client_token TEXT,
            teacher_answer TEXT,
            teacher_answered_at TEXT,
            teacher_updated_at TEXT,
            teacher_seen INTEGER NOT NULL DEFAULT 0,
            ai_summary TEXT,
            ai_answered_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubts_student ON doubts(student_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubts_status ON doubts(teacher_answered_at, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubts_subject ON doubts(subject COLLATE NOCASE)")
    # one submission per (student, token): a double tap / retry can never create two doubts
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_doubts_token ON doubts(student_id, client_token) "
                 "WHERE client_token IS NOT NULL AND client_token <> ''")

    # every doubt owns exactly ONE ai_conversations row -> its own persistent history in ai_messages
    if _has_col(conn, "ai_conversations", "id"):
        _add_col(conn, "ai_conversations", "doubt_id", "INTEGER REFERENCES doubts(id) ON DELETE CASCADE")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_conv_doubt ON ai_conversations(doubt_id) "
                     "WHERE doubt_id IS NOT NULL")

    _add_col(conn, "students", "profile_image_url", "TEXT")
    _add_col(conn, "students", "profile_image_public_id", "TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS teacher_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            notif_type TEXT NOT NULL DEFAULT 'doubt',
            link TEXT NOT NULL DEFAULT '',
            ref_key TEXT,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_teacher_notif_ref ON teacher_notifications(ref_key) "
                 "WHERE ref_key IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_notif_read ON teacher_notifications(is_read, id)")
    return "doubts, teacher_notifications, ai_conversations.doubt_id, students.profile_image_*"


def down(conn):
    # Only removes what this migration created (profile columns / doubt_id are left in place: SQLite
    # cannot drop them safely and they hold no data this migration did not add).
    conn.execute("DROP TABLE IF EXISTS teacher_notifications")
    conn.execute("DROP INDEX IF EXISTS uq_ai_conv_doubt")
    return "teacher_notifications dropped (doubts kept; use backup to remove)"
