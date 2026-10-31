# -*- coding: utf-8 -*-
"""Database schema: creates every table and adds missing columns (init_db)."""

from backend.db.connection import get_db


def _ensure_column(conn, table, column, col_def):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                class_name TEXT NOT NULL,
                roll TEXT,
                email TEXT UNIQUE NOT NULL,
                mobile TEXT,
                password_hash TEXT NOT NULL,
                is_verified INTEGER DEFAULT 0,
                registered_at TEXT NOT NULL,
                admitted_by_teacher INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                notes_internal TEXT
            )
        """)
        _ensure_column(conn, "students", "mobile", "TEXT")
        _ensure_column(conn, "students", "admitted_by_teacher", "INTEGER DEFAULT 0")
        _ensure_column(conn, "students", "is_blocked", "INTEGER DEFAULT 0")
        _ensure_column(conn, "students", "notes_internal", "TEXT")

        c.execute("""
            CREATE TABLE IF NOT EXISTS otp_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                otp_code TEXT NOT NULL,
                purpose TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_used INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS login_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                login_time TEXT NOT NULL,
                ip_address TEXT,
                via_teacher INTEGER DEFAULT 0,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)
        _ensure_column(conn, "login_history", "via_teacher", "INTEGER DEFAULT 0")

        c.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                subject TEXT,
                class_name TEXT,
                filename TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                subject TEXT,
                class_name TEXT,
                duration_minutes INTEGER NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                option_a TEXT NOT NULL,
                option_b TEXT NOT NULL,
                option_c TEXT NOT NULL,
                option_d TEXT NOT NULL,
                correct_option TEXT NOT NULL,
                marks INTEGER DEFAULT 1,
                FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                score INTEGER NOT NULL,
                total_marks INTEGER NOT NULL,
                submitted_at TEXT NOT NULL,
                FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                selected_option TEXT,
                is_correct INTEGER DEFAULT 0,
                FOREIGN KEY (result_id) REFERENCES results(id) ON DELETE CASCADE,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                class_name TEXT,
                created_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS contact_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                mobile TEXT,
                subject TEXT,
                message TEXT NOT NULL,
                student_id INTEGER,
                status TEXT DEFAULT 'new',
                created_at TEXT NOT NULL,
                teacher_reply TEXT,
                replied_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS site_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT DEFAULT 'present',
                note TEXT,
                marked_at TEXT NOT NULL,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                notif_type TEXT DEFAULT 'info',
                class_name TEXT DEFAULT '',
                link TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS notification_reads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                read_at TEXT NOT NULL,
                UNIQUE(notification_id, student_id),
                FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)
        # Real push subscriptions: one row per browser/device that tapped
        # "Allow" on notifications. A student can have several (phone +
        # laptop, etc). class_name is copied in at subscribe time so we can
        # target a push by class without a join.
        c.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                class_name TEXT DEFAULT '',
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket TEXT NOT NULL,
                ts REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_rate_bucket ON rate_limit_hits(bucket, ts)")

        # Password-reset support:
        #  - session_version: bumping it logs the student out of every device
        #  - attempts: counts wrong guesses on an OTP (brute-force protection)
        _ensure_column(conn, "students", "session_version", "INTEGER DEFAULT 0")
        _ensure_column(conn, "otp_codes", "attempts", "INTEGER DEFAULT 0")

        conn.commit()
