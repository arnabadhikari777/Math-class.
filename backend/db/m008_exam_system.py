# -*- coding: utf-8 -*-
"""Migration 008 - exam snapshots, versions, attempts, and history-safe foreign keys.

What it does (all inside ONE transaction, run with foreign_keys=OFF by the
runner and verified with PRAGMA foreign_key_check before COMMIT):

  1. students: is_archived / archived_at / archive_reason  (archive != block)
  2. tests: max_attempts, result_policy, late_policy, grace_seconds,
            negative_marks, is_archived, archived_at
  3. NEW exam_versions, exam_version_questions, exam_attempts, attempt_questions
  4. REBUILT results / answers / attendance / login_history / ai_conversations
     so that deleting a student or a test (or editing / deleting a question)
     can never silently cascade into academic history (ON DELETE RESTRICT).
     answers now stores a full question snapshot of its own.
  5. Backfill: every old result becomes a submitted attempt (attempt_no 1)
     with a version-1 snapshot; every old opened-but-unsubmitted test_attempts
     row becomes an in_progress attempt.  Nothing is deleted.
  6. attendance duplicates (if any) are COPIED to attendance_duplicates_archive
     before the unique rebuild - nothing is lost.

Honest limitation: results created BEFORE this migration had no snapshot, so
their snapshot is taken from the questions as they exist at migration time.
That is the best data that exists; from now on every attempt is frozen.
"""
import hashlib
import json
from datetime import datetime, timedelta

MIGRATION_ID = "008_exam_system"


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _add(conn, table, col, ddl):
    if _exists(conn, table) and col not in _cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def question_hash(rows):
    """Stable hash of a question set (used to decide whether a new exam version is needed)."""
    payload = [[r["question_text"], r["image_url"], r["option_a"], r["option_b"], r["option_c"],
                r["option_d"], r["correct_option"], r["marks"]] for r in rows]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


_NEW_TABLES = [
    """CREATE TABLE IF NOT EXISTS exam_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id INTEGER NOT NULL,
        version_no INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        negative_marks NUMERIC NOT NULL DEFAULT 0,
        is_frozen INTEGER NOT NULL DEFAULT 0,
        frozen_at TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(test_id, version_no),
        FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE IF NOT EXISTS exam_version_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        original_question_id INTEGER,
        question_text TEXT NOT NULL,
        image_url TEXT,
        bank_image_id INTEGER,
        image_info TEXT,
        option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL,
        correct_option TEXT NOT NULL CHECK (correct_option IN ('A','B','C','D')),
        marks NUMERIC NOT NULL DEFAULT 1,
        negative_marks NUMERIC NOT NULL DEFAULT 0,
        UNIQUE(version_id, position),
        FOREIGN KEY (version_id) REFERENCES exam_versions(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE IF NOT EXISTS exam_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        version_id INTEGER NOT NULL,
        attempt_no INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('in_progress','submitted','auto_submitted','rejected')),
        started_at TEXT NOT NULL,
        deadline_at TEXT NOT NULL,
        duration_seconds INTEGER NOT NULL,
        grace_seconds INTEGER NOT NULL DEFAULT 0,
        submitted_at TEXT,
        submit_source TEXT,
        score NUMERIC,
        total_marks NUMERIC,
        is_counted INTEGER NOT NULL DEFAULT 0,
        draft_json TEXT,
        draft_saved_at TEXT,
        UNIQUE(test_id, student_id, attempt_no),
        FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE RESTRICT,
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE RESTRICT,
        FOREIGN KEY (version_id) REFERENCES exam_versions(id) ON DELETE RESTRICT
    )""",
    # one running attempt per student per test, enforced by the database
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_attempt_running ON exam_attempts(test_id, student_id) WHERE status='in_progress'",
    "CREATE INDEX IF NOT EXISTS idx_attempts_student ON exam_attempts(student_id, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_attempts_test ON exam_attempts(test_id, status)",
    """CREATE TABLE IF NOT EXISTS attempt_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attempt_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        version_id INTEGER NOT NULL,
        original_question_id INTEGER,
        question_text TEXT NOT NULL,
        image_url TEXT,
        bank_image_id INTEGER,
        image_info TEXT,
        option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL,
        correct_option TEXT NOT NULL CHECK (correct_option IN ('A','B','C','D')),
        marks NUMERIC NOT NULL DEFAULT 1,
        negative_marks NUMERIC NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(attempt_id, position),
        FOREIGN KEY (attempt_id) REFERENCES exam_attempts(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_attempt_q_image ON attempt_questions(bank_image_id)",
    "CREATE INDEX IF NOT EXISTS idx_attempt_q_imgurl ON attempt_questions(image_url)",
    "CREATE INDEX IF NOT EXISTS idx_versionq_image ON exam_version_questions(bank_image_id)",
    """CREATE TABLE IF NOT EXISTS attendance_duplicates_archive (
        archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_id INTEGER, student_id INTEGER, date TEXT, status TEXT, note TEXT, marked_at TEXT,
        archived_at TEXT NOT NULL, reason TEXT
    )""",
]

_REBUILD = {
    "results": ("""CREATE TABLE results__new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        score NUMERIC NOT NULL,
        total_marks NUMERIC NOT NULL,
        submitted_at TEXT NOT NULL,
        attempt_id INTEGER,
        UNIQUE(test_id, student_id),
        FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE RESTRICT,
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE RESTRICT
    )""", "id, test_id, student_id, score, total_marks, submitted_at"),
    "attendance": ("""CREATE TABLE attendance__new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        status TEXT DEFAULT 'present',
        note TEXT,
        marked_at TEXT NOT NULL,
        UNIQUE(student_id, date),
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE RESTRICT
    )""", "id, student_id, date, status, note, marked_at"),
    "login_history": ("""CREATE TABLE login_history__new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        login_time TEXT NOT NULL,
        ip_address TEXT,
        via_teacher INTEGER DEFAULT 0,
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE RESTRICT
    )""", "id, student_id, login_time, ip_address, via_teacher"),
    "ai_conversations": ("""CREATE TABLE ai_conversations__new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        test_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE RESTRICT,
        FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE SET NULL
    )""", "id, student_id, title, test_id, created_at, updated_at"),
}

_ANSWERS_DDL = """CREATE TABLE answers__new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER,
    attempt_id INTEGER,
    question_id INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    question_text TEXT NOT NULL DEFAULT '',
    image_url TEXT,
    bank_image_id INTEGER,
    option_a TEXT, option_b TEXT, option_c TEXT, option_d TEXT,
    correct_option TEXT,
    marks NUMERIC NOT NULL DEFAULT 1,
    negative_marks NUMERIC NOT NULL DEFAULT 0,
    selected_option TEXT,
    is_correct INTEGER DEFAULT 0,
    marks_awarded NUMERIC NOT NULL DEFAULT 0,
    FOREIGN KEY (result_id) REFERENCES results(id) ON DELETE SET NULL,
    FOREIGN KEY (attempt_id) REFERENCES exam_attempts(id) ON DELETE RESTRICT
)"""


def _rebuild_plain(conn, table):
    ddl, copy_cols = _REBUILD[table]
    if not _exists(conn, table):
        return
    conn.execute(f"DROP TABLE IF EXISTS {table}__new")
    conn.execute(ddl)
    conn.execute(f"INSERT INTO {table}__new ({copy_cols}) SELECT {copy_cols} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}__new RENAME TO {table}")


def _archive_attendance_duplicates(conn):
    if not _exists(conn, "attendance"):
        return 0
    dups = conn.execute(
        """SELECT a.* FROM attendance a WHERE EXISTS (
               SELECT 1 FROM attendance b WHERE b.student_id=a.student_id AND b.date=a.date
                 AND (b.marked_at > a.marked_at OR (b.marked_at = a.marked_at AND b.id > a.id)))""").fetchall()
    now = datetime.now().isoformat()
    for r in dups:
        conn.execute(
            """INSERT INTO attendance_duplicates_archive
               (original_id, student_id, date, status, note, marked_at, archived_at, reason)
               VALUES (?,?,?,?,?,?,?,?)""",
            (r["id"], r["student_id"], r["date"], r["status"], r["note"], r["marked_at"], now,
             "duplicate (student,date) removed by migration 008; newest row kept"))
        conn.execute("DELETE FROM attendance WHERE id=?", (r["id"],))
    return len(dups)


def _snapshot_version(conn, test, frozen):
    """Create a version row + question rows from the CURRENT questions of a test."""
    qs = conn.execute("SELECT * FROM questions WHERE test_id=? ORDER BY id", (test["id"],)).fetchall()
    nxt = (conn.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM exam_versions WHERE test_id=?",
                        (test["id"],)).fetchone()[0])
    now = datetime.now().isoformat()
    neg = float(test["negative_marks"] or 0) if "negative_marks" in test.keys() else 0.0
    cur = conn.execute(
        """INSERT INTO exam_versions (test_id, version_no, content_hash, negative_marks, is_frozen, frozen_at, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (test["id"], nxt, question_hash(qs), neg, 1 if frozen else 0, now if frozen else None, now))
    vid = cur.lastrowid
    for pos, q in enumerate(qs, 1):
        conn.execute(
            """INSERT INTO exam_version_questions
               (version_id, position, original_question_id, question_text, image_url, bank_image_id,
                option_a, option_b, option_c, option_d, correct_option, marks, negative_marks)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid, pos, q["id"], q["question_text"], q["image_url"], q["bank_image_id"],
             q["option_a"], q["option_b"], q["option_c"], q["option_d"], q["correct_option"],
             q["marks"] or 1, neg))
    return vid


def _copy_to_attempt(conn, attempt_id, version_id):
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO attempt_questions
           (attempt_id, position, version_id, original_question_id, question_text, image_url, bank_image_id,
            image_info, option_a, option_b, option_c, option_d, correct_option, marks, negative_marks, created_at)
           SELECT ?, position, version_id, original_question_id, question_text, image_url, bank_image_id,
                  image_info, option_a, option_b, option_c, option_d, correct_option, marks, negative_marks, ?
           FROM exam_version_questions WHERE version_id=? ORDER BY position""",
        (attempt_id, now, version_id))


def up(conn):
    # 1-2. new columns -------------------------------------------------------
    _add(conn, "students", "is_archived", "INTEGER NOT NULL DEFAULT 0")
    _add(conn, "students", "archived_at", "TEXT")
    _add(conn, "students", "archive_reason", "TEXT")
    _add(conn, "tests", "max_attempts", "INTEGER NOT NULL DEFAULT 1")       # 0 = unlimited
    _add(conn, "tests", "result_policy", "TEXT NOT NULL DEFAULT 'best'")     # best | latest
    _add(conn, "tests", "late_policy", "TEXT NOT NULL DEFAULT 'auto_submit'")  # auto_submit | reject
    _add(conn, "tests", "grace_seconds", "INTEGER NOT NULL DEFAULT 30")
    _add(conn, "tests", "negative_marks", "NUMERIC NOT NULL DEFAULT 0")
    _add(conn, "tests", "is_archived", "INTEGER NOT NULL DEFAULT 0")
    _add(conn, "tests", "archived_at", "TEXT")
    # 3. new tables -----------------------------------------------------------
    for ddl in _NEW_TABLES:
        conn.execute(ddl)
    # 4a. attendance duplicates are archived (never silently lost) ----------------
    archived = _archive_attendance_duplicates(conn)
    # 4b. rebuild tables so academic history cannot be cascaded away -------------
    old_answers = []
    if _exists(conn, "answers") and "attempt_id" not in _cols(conn, "answers"):
        old_answers = conn.execute(
            """SELECT a.id, a.result_id, a.question_id, a.selected_option, a.is_correct,
                      q.question_text, q.image_url, q.bank_image_id, q.option_a, q.option_b, q.option_c,
                      q.option_d, q.correct_option, q.marks
               FROM answers a LEFT JOIN questions q ON q.id=a.question_id ORDER BY a.id""").fetchall()
    if "attempt_id" not in _cols(conn, "results"):
        _rebuild_plain(conn, "results")
    for t in ("attendance", "login_history", "ai_conversations"):
        fks = conn.execute(f"PRAGMA foreign_key_list({t})").fetchall()
        if any(str(fk["on_delete"]).upper() == "CASCADE" for fk in fks if fk["table"] == "students"):
            _rebuild_plain(conn, t)
    if old_answers or "attempt_id" not in _cols(conn, "answers"):
        conn.execute("DROP TABLE IF EXISTS answers__new")
        conn.execute(_ANSWERS_DDL)
        conn.execute("DROP TABLE IF EXISTS answers")
        conn.execute("ALTER TABLE answers__new RENAME TO answers")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_answers_result ON answers(result_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_answers_attempt ON answers(attempt_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_results_test_student ON results(test_id, student_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_student ON results(student_id, submitted_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_test ON results(test_id, score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_login_history_student ON login_history(student_id, login_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_conv_student ON ai_conversations(student_id, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_students_archived ON students(is_archived)")
    # 5. backfill ---------------------------------------------------------------
    tests = {t["id"]: t for t in conn.execute("SELECT * FROM tests")}
    versions = {}
    old_attempts = {}
    if _exists(conn, "test_attempts"):
        old_attempts = {(r["test_id"], r["student_id"]): r["started_at"]
                        for r in conn.execute("SELECT * FROM test_attempts")}
    result_attempt = {}
    for r in conn.execute("SELECT * FROM results WHERE attempt_id IS NULL ORDER BY id").fetchall():
        t = tests.get(r["test_id"])
        if t is None:
            continue
        if t["id"] not in versions:
            versions[t["id"]] = _snapshot_version(conn, t, frozen=True)
        started = old_attempts.get((r["test_id"], r["student_id"])) or r["submitted_at"]
        dur = int(t["duration_minutes"] or 0) * 60
        try:
            deadline = (datetime.fromisoformat(started) + timedelta(seconds=dur)).isoformat()
        except (TypeError, ValueError):
            deadline = r["submitted_at"]
        cur = conn.execute(
            """INSERT INTO exam_attempts (test_id, student_id, version_id, attempt_no, status, started_at,
                   deadline_at, duration_seconds, grace_seconds, submitted_at, submit_source, score,
                   total_marks, is_counted)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (r["test_id"], r["student_id"], versions[t["id"]], 1, "submitted", started, deadline, dur,
             int(t["grace_seconds"] or 0), r["submitted_at"], "legacy", r["score"], r["total_marks"]))
        aid = cur.lastrowid
        _copy_to_attempt(conn, aid, versions[t["id"]])
        conn.execute("UPDATE results SET attempt_id=? WHERE id=?", (aid, r["id"]))
        result_attempt[r["id"]] = aid
    if old_answers:
        pos = {}
        for a in old_answers:
            aid = result_attempt.get(a["result_id"])
            if aid is None:
                continue
            pos[aid] = pos.get(aid, 0) + 1
            marks = float(a["marks"] or 1)
            conn.execute(
                """INSERT INTO answers (result_id, attempt_id, question_id, position, question_text, image_url,
                       bank_image_id, option_a, option_b, option_c, option_d, correct_option, marks,
                       selected_option, is_correct, marks_awarded)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (a["result_id"], aid, a["question_id"], pos[aid], a["question_text"] or "", a["image_url"],
                 a["bank_image_id"], a["option_a"], a["option_b"], a["option_c"], a["option_d"],
                 a["correct_option"], marks, a["selected_option"], a["is_correct"] or 0,
                 marks if a["is_correct"] else 0))
    # opened-but-unsubmitted old attempts -> in_progress
    for (test_id, student_id), started in old_attempts.items():
        t = tests.get(test_id)
        if t is None or conn.execute("SELECT 1 FROM results WHERE test_id=? AND student_id=?",
                                     (test_id, student_id)).fetchone():
            continue
        if test_id not in versions:
            versions[test_id] = _snapshot_version(conn, t, frozen=True)
        dur = int(t["duration_minutes"] or 0) * 60
        try:
            deadline = (datetime.fromisoformat(started) + timedelta(seconds=dur)).isoformat()
        except (TypeError, ValueError):
            continue
        cur = conn.execute(
            """INSERT INTO exam_attempts (test_id, student_id, version_id, attempt_no, status, started_at,
                   deadline_at, duration_seconds, grace_seconds)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (test_id, student_id, versions[test_id], 1, "in_progress", started, deadline, dur,
             int(t["grace_seconds"] or 0)))
        _copy_to_attempt(conn, cur.lastrowid, versions[test_id])
    return f"exam system installed; backfilled {len(result_attempt)} result(s); archived {archived} duplicate attendance row(s)"


def down(conn):
    raise RuntimeError(
        "008_exam_system cannot be rolled back automatically: it rebuilt results/answers and created "
        "exam history that has no old-schema equivalent. Restore the pre-migration backup instead "
        "(see BACKUP_RESTORE_GUIDE.md).")
