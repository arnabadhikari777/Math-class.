# -*- coding: utf-8 -*-
"""Safe, additive, reversible SQLite migrations.

Rules this module follows (see MIGRATION_NOTES.md):
  * Only CREATE TABLE/INDEX IF NOT EXISTS. Never DROP/DELETE/UPDATE existing data.
  * Each migration has a ``down`` that removes ONLY what that migration created.
  * A timestamped backup of the database is taken (SQLite online-backup API)
    before the first pending migration is applied.
  * A unique index is created only when current data has no duplicates;
    otherwise it is skipped (and retried on next start) - data is never edited.

CLI (run from the project folder):
    python -m backend.migrations status
    python -m backend.migrations backup
    python -m backend.migrations migrate
    python -m backend.migrations rollback 002_perf_indexes
"""
import logging
import os
import sqlite3
import sys
from datetime import datetime

log = logging.getLogger(__name__)
KEEP_BACKUPS = 10


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


# ---------------------------------------------------------------- migrations
def _up_test_attempts(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS test_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            UNIQUE(test_id, student_id),
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
        )""")


def _down_test_attempts(conn):
    conn.execute("DROP TABLE IF EXISTS test_attempts")


_PERF_INDEXES = [
    ("idx_results_student", "results", "(student_id, submitted_at)"),
    ("idx_results_test", "results", "(test_id, score)"),
    ("idx_answers_result", "answers", "(result_id)"),
    ("idx_questions_test", "questions", "(test_id)"),
    ("idx_notes_class", "notes", "(class_name, uploaded_at)"),
    ("idx_login_history_student", "login_history", "(student_id, login_time)"),
    ("idx_notifications_class", "notifications", "(class_name, created_at)"),
    ("idx_notification_reads_student", "notification_reads", "(student_id)"),
    ("idx_students_class", "students", "(class_name)"),
]


def _up_perf_indexes(conn):
    for name, table, cols in _PERF_INDEXES:
        if _table_exists(conn, table):
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")


def _down_perf_indexes(conn):
    for name, _t, _c in _PERF_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")


def _make_unique(index, table, cols, group_sql):
    def up(conn):
        if not _table_exists(conn, table):
            return "SKIP: table %s missing" % table
        dup = conn.execute(group_sql).fetchone()
        if dup:
            return "SKIP: duplicate rows already exist in %s - not touching data" % table
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table} {cols}")

    def down(conn):
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    return up, down


_up_uq_results, _down_uq_results = _make_unique(
    "uq_results_test_student", "results", "(test_id, student_id)",
    "SELECT 1 FROM results GROUP BY test_id, student_id HAVING COUNT(*) > 1 LIMIT 1")
_up_uq_attendance, _down_uq_attendance = _make_unique(
    "uq_attendance_student_date", "attendance", "(student_id, date)",
    "SELECT 1 FROM attendance GROUP BY student_id, date HAVING COUNT(*) > 1 LIMIT 1")


# Normalize legacy class labels (Six, Nine, SSC Batch, …) to canonical values.
# Safe, deterministic mapping only — unknown values are left unchanged.
_CLASS_NORMALIZE_MAP = {
    "Six": "SIX", "six": "SIX",
    "Seven": "SEVEN", "seven": "SEVEN",
    "Eight": "EIGHT", "eight": "EIGHT",
    "Nine": "NINE", "nine": "NINE",
    "Ten": "TEN", "ten": "TEN",
    "SSC Batch": "TEN", "ssc batch": "TEN", "SSC": "TEN",
    "HSC Batch": "12TH", "hsc batch": "12TH", "HSC": "12TH",
    "11th": "11TH", "11": "11TH",
    "12th": "12TH", "12": "12TH",
    "Other": "OTHER", "other": "OTHER",
}

_CLASS_TABLES = (
    ("students", "class_name"),
    ("tests", "class_name"),
    ("notes", "class_name"),
    ("announcements", "class_name"),
    ("notifications", "class_name"),
    ("push_subscriptions", "class_name"),
)


def _up_normalize_classes(conn):
    updated = 0
    for table, col in _CLASS_TABLES:
        if not _table_exists(conn, table):
            continue
        for old, new in _CLASS_NORMALIZE_MAP.items():
            cur = conn.execute(
                f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old)
            )
            updated += cur.rowcount or 0
    return f"normalized {updated} class_name cells"


def _down_normalize_classes(conn):
    reverse = {
        "SIX": "Six", "SEVEN": "Seven", "EIGHT": "Eight", "NINE": "Nine",
        "TEN": "Ten", "11TH": "11th", "12TH": "12th", "OTHER": "Other",
    }
    for table, col in _CLASS_TABLES:
        if not _table_exists(conn, table):
            continue
        for new, old in reverse.items():
            conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (old, new))


def _up_ai_chat(conn):
    """AI assistant chats: one conversation belongs to exactly one student."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            test_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE SET NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user','assistant')),
            content TEXT NOT NULL,
            provider TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES ai_conversations(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_conv_student ON ai_conversations(student_id, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_msg_conv ON ai_messages(conversation_id, id)")


def _down_ai_chat(conn):
    conn.execute("DROP TABLE IF EXISTS ai_messages")
    conn.execute("DROP TABLE IF EXISTS ai_conversations")


def _up_question_bank(conn):
    """Question Bank + Cloudinary image library. NEW tables, plus two nullable
    columns on questions so a bank question copied into a test can carry its image."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at TEXT NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            public_id TEXT NOT NULL UNIQUE,
            original_name TEXT,
            width INTEGER,
            height INTEGER,
            bytes INTEGER,
            format TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (category_id) REFERENCES bank_categories(id) ON DELETE RESTRICT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_text TEXT NOT NULL DEFAULT '',
            image_id INTEGER,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_option TEXT NOT NULL CHECK (correct_option IN ('A','B','C','D')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (image_id) REFERENCES bank_images(id) ON DELETE RESTRICT
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bank_images_cat ON bank_images(category_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bank_questions_img ON bank_questions(image_id)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    if not cols:
        return "SKIP: questions table missing (it is created by app.init_db(); will retry on next start)"
    if "image_url" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN image_url TEXT")
    if "bank_image_id" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN bank_image_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_bank_image ON questions(bank_image_id)")


def _down_question_bank(conn):
    # Removes only what 007 created. Needs SQLite >= 3.35 for DROP COLUMN.
    conn.execute("DROP INDEX IF EXISTS idx_questions_bank_image")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    for col in ("image_url", "bank_image_id"):
        if col in cols:
            conn.execute(f"ALTER TABLE questions DROP COLUMN {col}")
    conn.execute("DROP TABLE IF EXISTS bank_questions")
    conn.execute("DROP TABLE IF EXISTS bank_images")
    conn.execute("DROP TABLE IF EXISTS bank_categories")


MIGRATIONS = [
    ("001_test_attempts", "Record when a student opens a test (for timer checks)",
     _up_test_attempts, _down_test_attempts),
    ("002_perf_indexes", "Add read-performance indexes (no data change)",
     _up_perf_indexes, _down_perf_indexes),
    ("003_unique_results", "One result per (test, student) enforced by the database",
     _up_uq_results, _down_uq_results),
    ("004_unique_attendance", "One attendance row per (student, date)",
     _up_uq_attendance, _down_uq_attendance),
    ("005_normalize_class_names", "Map legacy class labels to canonical values (SIX…OTHER)",
     _up_normalize_classes, _down_normalize_classes),
    ("006_ai_chat", "AI assistant: ai_conversations + ai_messages (new tables only)",
     _up_ai_chat, _down_ai_chat),
    ("007_question_bank", "Question Bank + image library tables; questions.image_url / bank_image_id (nullable)",
     _up_question_bank, _down_question_bank),
]


# ------------------------------------------------------------------- runner
def _open(db_file):
    conn = sqlite3.connect(db_file, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        id TEXT PRIMARY KEY, applied_at TEXT NOT NULL, note TEXT)""")
    return conn


def applied_ids(db_file):
    conn = _open(db_file)
    try:
        return {r["id"] for r in conn.execute("SELECT id FROM schema_migrations")}
    finally:
        conn.close()


def backup_database(db_file, directory=None):
    """Consistent copy of the live DB (safe even while the app is running)."""
    directory = directory or os.path.join(os.path.dirname(os.path.abspath(db_file)), "backups")
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(directory, f"database-{stamp}.db")
    src = sqlite3.connect(db_file)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    old = sorted(f for f in os.listdir(directory) if f.startswith("database-") and f.endswith(".db"))
    for name in old[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass
    return target


def run_pending(db_file, backup=True):
    """Apply pending migrations. Returns a list of (id, status) tuples."""
    done = applied_ids(db_file)
    pending = [m for m in MIGRATIONS if m[0] not in done]
    report = []
    if not pending:
        return report
    if backup and os.path.isfile(db_file):
        path = backup_database(db_file)
        log.info("Database backup created: %s", path)
        report.append(("backup", path))
    conn = _open(db_file)
    try:
        for mid, _desc, up, _down in pending:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute("SELECT 1 FROM schema_migrations WHERE id=?", (mid,)).fetchone():
                    conn.execute("COMMIT")
                    continue  # another worker already applied it
                note = up(conn)
                if isinstance(note, str) and note.startswith("SKIP"):
                    conn.execute("ROLLBACK")
                    log.warning("Migration %s skipped: %s", mid, note)
                    report.append((mid, note))
                    continue
                conn.execute("INSERT INTO schema_migrations (id, applied_at, note) VALUES (?,?,?)",
                             (mid, datetime.now().isoformat(), note))
                conn.execute("COMMIT")
                report.append((mid, "applied"))
            except Exception as exc:  # leave DB exactly as before this migration
                conn.execute("ROLLBACK")
                log.error("Migration %s failed and was rolled back: %s", mid, exc)
                report.append((mid, f"FAILED: {exc}"))
    finally:
        conn.close()
    return report


def rollback(db_file, migration_id, backup=True):
    entry = next((m for m in MIGRATIONS if m[0] == migration_id), None)
    if entry is None:
        raise ValueError(f"Unknown migration: {migration_id}")
    if backup:
        backup_database(db_file)
    conn = _open(db_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        entry[3](conn)
        conn.execute("DELETE FROM schema_migrations WHERE id=?", (migration_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _cli(argv):
    db_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.db")
    if "--db" in argv:
        i = argv.index("--db")
        db_file = argv[i + 1]
        del argv[i:i + 2]
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        done = applied_ids(db_file)
        for mid, desc, _u, _d in MIGRATIONS:
            print(("[applied] " if mid in done else "[pending] ") + mid + " - " + desc)
    elif cmd == "backup":
        print("Backup written to", backup_database(db_file))
    elif cmd == "migrate":
        for row in run_pending(db_file):
            print(*row)
    elif cmd == "rollback" and len(argv) > 1:
        rollback(db_file, argv[1])
        print("Rolled back", argv[1])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
