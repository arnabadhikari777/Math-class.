# -*- coding: utf-8 -*-
"""Doubts schema compatibility: a database holding the OLD/legacy `doubts` layout (question_text/status/updated_at,
doubt_images) must be repaired by a safe forward-only migration with its rows preserved. Temp DBs only."""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import shutil, sqlite3, tempfile, unittest  # noqa: E402,E401

from legacy import FIXTURE  # noqa: E402
from backend.db import migrations  # noqa: E402

LEGACY_DDL = """
CREATE TABLE doubts (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL, subject TEXT NOT NULL,
  chapter TEXT, topic TEXT, question_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', teacher_answer TEXT,
  teacher_answered_at TEXT, teacher_answered_by TEXT, ai_has_answer INTEGER NOT NULL DEFAULT 0, ai_summary TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE);
CREATE INDEX idx_doubts_student ON doubts(student_id, created_at DESC);
CREATE TABLE doubt_images (id INTEGER PRIMARY KEY AUTOINCREMENT, doubt_id INTEGER NOT NULL, url TEXT NOT NULL,
  public_id TEXT, original_name TEXT, width INTEGER, height INTEGER, bytes INTEGER, format TEXT, created_at TEXT NOT NULL,
  FOREIGN KEY (doubt_id) REFERENCES doubts(id) ON DELETE CASCADE);
"""


class DoubtMigrationCompat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mc-mig-")
        self.db = _os.path.join(self.tmp, "database.db")
        shutil.copy(FIXTURE, self.db)
        migrations.run_pending(self.db, backup=False)          # current schema, everything applied
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,"
                  "is_blocked,session_version,username) VALUES ('Sam','TEN','1','s@t.l','1','x',1,'2026-01-01',0,0,'sam_x')")
        self.sid = c.execute("SELECT id FROM students").fetchone()[0]
        c.commit(); c.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_legacy(self, applied_018):
        c = sqlite3.connect(self.db)
        c.execute("DROP INDEX IF EXISTS uq_ai_conv_doubt")
        for t in ("doubt_images", "doubts"):
            c.execute("DROP TABLE IF EXISTS " + t)
        c.executescript(LEGACY_DDL)
        c.execute("INSERT INTO doubts (student_id,subject,chapter,topic,question_text,status,teacher_answer,"
                  "teacher_answered_at,ai_has_answer,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (self.sid, "Maths", None, None, "Legacy question?", "answered", "Do this", None, 1,
                   "2026-02-01T10:00:00", "2026-02-02T11:00:00"))
        c.execute("INSERT INTO doubt_images (doubt_id,url,public_id,created_at) VALUES (1,'https://x/y.png','pid','2026-02-01')")
        c.execute("DELETE FROM schema_migrations WHERE id='019_doubts_schema_check'")
        if not applied_018:
            c.execute("DELETE FROM schema_migrations WHERE id='018_doubts'")
        c.commit(); c.close()

    def check_repaired(self):
        c = sqlite3.connect(self.db); c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM doubts WHERE id=1").fetchone()
        self.assertEqual(row["question"], "Legacy question?")                    # question_text mapped
        self.assertEqual(row["chapter"], ""); self.assertEqual(row["topic"], "")  # NULL -> ''
        self.assertEqual(row["teacher_answer"], "Do this")
        self.assertEqual(row["teacher_answered_at"], "2026-02-02T11:00:00")       # status=answered mapped
        self.assertEqual(row["ai_answered_at"], "2026-02-02T11:00:00")            # ai_has_answer mapped
        self.assertEqual(row["image_url"], "https://x/y.png")                     # doubt_images mapped
        self.assertEqual(row["teacher_seen"], 0)
        from backend.services import doubt_service
        c.execute("PRAGMA foreign_keys=ON")
        cards, total = doubt_service.list_for_student(c, self.sid, status="answered")
        self.assertEqual(total, 1)
        self.assertEqual(doubt_service.list_for_student(c, self.sid, q="legacy")[1], 1)
        self.assertEqual(doubt_service.teacher_counts(c)["answered"], 1)
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()

    def test_legacy_table_and_018_pending(self):
        self.make_legacy(applied_018=False)
        rep = migrations.run_pending(self.db, backup=False)
        self.assertIn(("018_doubts", "applied"), rep); self.assertIn(("019_doubts_schema_check", "applied"), rep)
        self.check_repaired()

    def test_legacy_table_and_018_already_recorded(self):
        self.make_legacy(applied_018=True)
        rep = migrations.run_pending(self.db, backup=False)
        self.assertEqual([r for r in rep if r[0] == "019_doubts_schema_check"], [("019_doubts_schema_check", "applied")])
        self.check_repaired()

    def test_correct_schema_is_untouched_and_retry_safe(self):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO doubts (student_id,subject,question,created_at) VALUES (?,?,?,?)", (self.sid, "Maths", "Q?", "2026-03-01"))
        before = c.execute("PRAGMA table_info(doubts)").fetchall()
        c.execute("DELETE FROM schema_migrations WHERE id='019_doubts_schema_check'"); c.commit(); c.close()
        migrations.run_pending(self.db, backup=False); migrations.run_pending(self.db, backup=False)
        c = sqlite3.connect(self.db)
        self.assertEqual(before, c.execute("PRAGMA table_info(doubts)").fetchall())
        self.assertEqual(c.execute("SELECT question FROM doubts").fetchone()[0], "Q?")
        self.assertEqual(c.execute("SELECT note FROM schema_migrations WHERE id='019_doubts_schema_check'").fetchone()[0],
                         "doubts schema verified (no change needed)")
        c.close()

    def test_failed_migration_rolls_back_completely(self):
        self.make_legacy(applied_018=True)
        m018_doubts = migrations.m019_doubts_schema_check.m018_doubts      # the very module object 019 calls
        orig = m018_doubts.up
        def boom(conn):
            conn.execute("ALTER TABLE doubts ADD COLUMN zz_probe TEXT"); raise RuntimeError("boom")
        m018_doubts.up = boom
        try:
            rep = migrations.run_pending(self.db, backup=False)
        finally:
            m018_doubts.up = orig
        self.assertTrue(any(r[0] == "019_doubts_schema_check" and r[1].startswith("FAILED") for r in rep))
        c = sqlite3.connect(self.db)
        cols = {r[1] for r in c.execute("PRAGMA table_info(doubts)")}
        self.assertNotIn("question", cols); self.assertNotIn("zz_probe", cols)       # nothing half-applied
        self.assertEqual(c.execute("SELECT question_text FROM doubts").fetchone()[0], "Legacy question?")
        c.close()


if __name__ == "__main__":
    unittest.main()
