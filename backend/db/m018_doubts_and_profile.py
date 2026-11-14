# -*- coding: utf-8 -*-
"""Migration 018 - Doubt Management System + Student profile pictures.

Additive only:
  * students.profile_image_url, students.profile_public_id
  * doubts table (student academic questions)
  * doubt_images metadata (Cloudinary URLs only)
  * doubt_ai_conversations + doubt_ai_messages (per-doubt AI history)
  * indexes for common filters

Safe to run twice. Does not touch existing student/exam/contact data.
"""


def up(conn):
    # --- student profile picture (Cloudinary) ---
    cols = {r[1] for r in conn.execute("PRAGMA table_info(students)")}
    if "profile_image_url" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN profile_image_url TEXT")
    if "profile_public_id" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN profile_public_id TEXT")

    # --- doubts ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doubts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            chapter TEXT,
            topic TEXT,
            question_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            teacher_answer TEXT,
            teacher_answered_at TEXT,
            teacher_answered_by TEXT,
            ai_has_answer INTEGER NOT NULL DEFAULT 0,
            ai_summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubts_student ON doubts(student_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubts_status ON doubts(status, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubts_subject ON doubts(subject)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubts_class ON doubts(student_id)")  # join via students

    # optional image attached to a doubt (Cloudinary only; no BLOB in SQLite)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doubt_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doubt_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            public_id TEXT,
            original_name TEXT,
            width INTEGER,
            height INTEGER,
            bytes INTEGER,
            format TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (doubt_id) REFERENCES doubts(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubt_images_doubt ON doubt_images(doubt_id)")

    # per-doubt AI conversation (isolated history)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doubt_ai_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doubt_id INTEGER NOT NULL UNIQUE,
            student_id INTEGER NOT NULL,
            summary TEXT,
            message_count INTEGER NOT NULL DEFAULT 0,
            last_message_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (doubt_id) REFERENCES doubts(id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubt_ai_conv_student ON doubt_ai_conversations(student_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS doubt_ai_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES doubt_ai_conversations(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doubt_ai_msg_conv ON doubt_ai_messages(conversation_id, id)")

    return "doubts + profile_image columns + per-doubt AI history"


def down(conn):
    conn.execute("DROP TABLE IF EXISTS doubt_ai_messages")
    conn.execute("DROP TABLE IF EXISTS doubt_ai_conversations")
    conn.execute("DROP TABLE IF EXISTS doubt_images")
    conn.execute("DROP TABLE IF EXISTS doubts")
    # columns on students are left in place (SQLite DROP COLUMN needs newer version;
    # leaving them is safe and non-destructive)
    return "doubt tables dropped (profile columns kept)"
