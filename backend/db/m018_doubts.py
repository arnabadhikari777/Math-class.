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


# Columns the application code (doubt_service / doubts routes) reads or writes. ADD COLUMN cannot add a NOT NULL
# column without a default, so those carry one. Order matters only for readability.
DOUBT_COLUMNS = (
    ("chapter", "TEXT NOT NULL DEFAULT ''"),
    ("topic", "TEXT NOT NULL DEFAULT ''"),
    ("question", "TEXT NOT NULL DEFAULT ''"),
    ("image_url", "TEXT"),
    ("image_public_id", "TEXT"),
    ("image_meta", "TEXT"),
    ("client_token", "TEXT"),
    ("teacher_answer", "TEXT"),
    ("teacher_answered_at", "TEXT"),
    ("teacher_updated_at", "TEXT"),
    ("teacher_seen", "INTEGER NOT NULL DEFAULT 0"),
    ("ai_summary", "TEXT"),
    ("ai_answered_at", "TEXT"),
    ("created_at", "TEXT NOT NULL DEFAULT ''"),
)


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def repair_existing_doubts_table(conn):
    """If a ``doubts`` table already exists with a different (legacy / unregistered m018_doubts_and_profile) layout,
    add every missing column and copy legacy values across. Additive only: nothing is dropped or overwritten.

    CREATE TABLE IF NOT EXISTS silently keeps an old table as it is, so without this the queries fail with
    ``no such column: question`` (-> "Your doubts could not be loaded right now"). Returns the list of columns added."""
    if not _table_exists(conn, "doubts"):
        return []
    have = {r[1] for r in conn.execute("PRAGMA table_info(doubts)")}
    added = []
    for col, ddl in DOUBT_COLUMNS:
        if col not in have:
            conn.execute("ALTER TABLE doubts ADD COLUMN %s %s" % (col, ddl))
            have.add(col)
            added.append(col)
    stamp = "COALESCE(NULLIF(updated_at,''), created_at)" if "updated_at" in have else "created_at"
    # --- legacy field mapping (only fills values that are still empty) ---
    if "question_text" in have:
        conn.execute("UPDATE doubts SET question=question_text WHERE (question IS NULL OR question='') "
                     "AND question_text IS NOT NULL")
    conn.execute("UPDATE doubts SET chapter='' WHERE chapter IS NULL")
    conn.execute("UPDATE doubts SET topic='' WHERE topic IS NULL")
    if "status" in have:   # legacy status 'answered' + a stored answer == teacher answered
        conn.execute("UPDATE doubts SET teacher_answered_at=%s WHERE teacher_answered_at IS NULL "
                     "AND status='answered' AND teacher_answer IS NOT NULL AND teacher_answer<>''" % stamp)
    if "ai_has_answer" in have:
        conn.execute("UPDATE doubts SET ai_answered_at=%s WHERE ai_answered_at IS NULL AND ai_has_answer=1" % stamp)
    if "created_at" in have:
        conn.execute("UPDATE doubts SET created_at=? WHERE created_at IS NULL OR created_at=''",
                     (__import__("datetime").datetime.now().isoformat(),))
    if _table_exists(conn, "doubt_images"):   # first legacy image row becomes the doubt's picture
        conn.execute("""UPDATE doubts SET image_url=(SELECT url FROM doubt_images i WHERE i.doubt_id=doubts.id
                                                      ORDER BY i.id LIMIT 1),
                                          image_public_id=(SELECT public_id FROM doubt_images i WHERE i.doubt_id=doubts.id
                                                      ORDER BY i.id LIMIT 1)
                        WHERE (image_url IS NULL OR image_url='')
                          AND EXISTS (SELECT 1 FROM doubt_images i WHERE i.doubt_id=doubts.id)""")
    return added


def up(conn):
    repair_existing_doubts_table(conn)   # a pre-existing legacy table must be fixed BEFORE the indexes below
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
