# -*- coding: utf-8 -*-
"""Tests for the exam-snapshot / security / storage upgrade. Runs on a TEMPORARY copy of the project;
the real database.db is never opened. Run: python -m unittest discover -s tests"""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import csv, importlib, io, json, os, shutil, sqlite3, sys, tempfile, threading, unittest  # noqa: E402,E401
from datetime import datetime, timedelta  # noqa: E402
from unittest import mock  # noqa: E402

from werkzeug.security import generate_password_hash  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRF = "csrf-test-token"
PW = "StudentPass123"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class H(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mathclass-hard-")
        cls.proj = os.path.join(cls.tmp, "proj")
        shutil.copytree(ROOT, cls.proj, ignore=shutil.ignore_patterns("backups", ".secret_key", "__pycache__", ".env", "uploads"))
        cls.db = os.path.join(cls.proj, "database.db")
        if os.path.exists(cls.db):
            os.remove(cls.db)
        __import__("legacy").ensure_legacy_db(cls.db)
        cls.env_backup = dict(os.environ)
        os.environ.update({"FORCE_HTTPS": "0", "SECRET_KEY": "hard-secret", "TEACHER_USERNAME": "sir",
                           "TEACHER_PASSWORD": "TeacherPass123", "EMAIL_ADDRESS": "", "EMAIL_APP_PASSWORD": ""})
        os.environ.pop("ENFORCE_TEST_TIMER", None)
        sys.path.insert(0, cls.proj)
        for n in [m for m in sys.modules if m == "app" or m == "backend" or m.startswith("backend.")]:
            del sys.modules[n]
        cls.appmod = importlib.import_module("app")
        cls.app = cls.appmod.app
        cls.app.config["TESTING"] = True
        from backend.services import exam_service
        cls.ex = exam_service
        cls._seed()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.proj)
        os.environ.clear(); os.environ.update(cls.env_backup)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _seed(cls):
        c = sqlite3.connect(cls.db); c.execute("PRAGMA foreign_keys=ON")
        now = datetime.now().isoformat(); ph = generate_password_hash(PW)
        def student(name, email, **kw):
            return c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,"
                             "is_blocked,session_version) VALUES (?,?,?,?,?,?,1,?,?,0)",
                             (name, "10", "1", email, "=cmd|' /C calc'!A0", ph, now, kw.get("blocked", 0))).lastrowid
        cls.sa = student("=Alice", "a@t.local"); cls.sb = student("Bob", "b@t.local"); cls.sc = student("Cara", "c@t.local")
        cls.sd = student("Dan", "d@t.local"); cls.se = student("Eve", "e@t.local")
        cls.sblock = student("Blocked", "blk@t.local", blocked=1)
        def test(title, **kw):
            tid = c.execute("INSERT INTO tests (title,subject,class_name,duration_minutes,is_active,created_at,max_attempts,"
                            "result_policy,late_policy,grace_seconds,negative_marks) VALUES (?,?,?,?,1,?,?,?,?,?,?)",
                            (title, "Math", "10", kw.get("dur", 30), now, kw.get("max", 1), kw.get("pol", "best"),
                             kw.get("late", "auto_submit"), kw.get("grace", 0), kw.get("neg", 0))).lastrowid
            for i in range(2):
                c.execute("INSERT INTO questions (test_id,question_text,option_a,option_b,option_c,option_d,correct_option,marks) "
                          "VALUES (?,?,?,?,?,?,?,?)", (tid, f"{title}-Q{i}", "a", "b", "c", "d", "A", 5))
            return tid
        cls.t_snap = test("Snap"); cls.t_multi = test("Multi", max=3, pol="best"); cls.t_latest = test("Latest", max=3, pol="latest")
        cls.t_late = test("Late"); cls.t_rej = test("Reject", late="reject"); cls.t_neg = test("Neg", neg=2)
        cls.t_race = test("Race"); cls.t_ver = test("Ver"); cls.t_del = test("Del"); cls.t_unl = test("Unlimited", max=0); cls.t_ai = test("AiCtx")
        c.commit(); c.close()

    # ------------------------------------------------------------ helpers
    def conn(self):
        c = sqlite3.connect(self.db); c.row_factory = sqlite3.Row; return c

    def q(self, sql, *a):
        with self.conn() as c:
            return c.execute(sql, a).fetchall()

    def student_client(self, sid):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(student_id=sid, student_name="x", student_class="10", sv=0, _csrf_token=CSRF)
        return cl

    def teacher_client(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(is_teacher=True, _csrf_token=CSRF)
        return cl

    def post(self, cl, url, data=None, **kw):
        with cl.session_transaction() as s:               # a login / impersonation rebuilds the session (new token)
            tok = s.setdefault("_csrf_token", CSRF)
        return cl.post(url, data=data or {}, headers={"X-CSRFToken": tok}, **kw)

    def attempt(self, sid, tid, status=None):
        rows = self.q("SELECT * FROM exam_attempts WHERE test_id=? AND student_id=? ORDER BY attempt_no", tid, sid)
        return [r for r in rows if status is None or r["status"] == status]

    def start(self, cl, sid, tid):
        self.assertEqual(cl.get(f"/test/{tid}").status_code, 200)
        return self.attempt(sid, tid, "in_progress")[0]

    def submit(self, cl, sid, tid, pick="A"):
        run = self.attempt(sid, tid, "in_progress")
        qids = [r["id"] for r in self.q("SELECT id FROM attempt_questions WHERE attempt_id=?", run[0]["id"])] if run else []
        return self.post(cl, f"/test/{tid}/submit", {f"q_{i}": pick for i in qids})

    # ======================================================== EXAM TESTS
    def test_e01_start_creates_snapshot_and_freezes_version(self):
        cl = self.student_client(self.sa); a = self.start(cl, self.sa, self.t_snap)
        snap = self.q("SELECT * FROM attempt_questions WHERE attempt_id=? ORDER BY position", a["id"])
        self.assertEqual(len(snap), 2)
        self.assertEqual(snap[0]["question_text"], "Snap-Q0"); self.assertEqual(snap[0]["correct_option"], "A")
        self.assertEqual(snap[0]["marks"], 5); self.assertIsNotNone(snap[0]["original_question_id"])
        v = self.q("SELECT * FROM exam_versions WHERE id=?", a["version_id"])[0]
        self.assertEqual((v["version_no"], v["is_frozen"]), (1, 1)); self.assertIsNotNone(v["frozen_at"])

    def test_e02_editing_questions_after_start_changes_nothing_for_running_or_old_attempts(self):
        cl = self.student_client(self.sb); a = self.start(cl, self.sb, self.t_ver)
        with self.conn() as c:                       # teacher edits + deletes live questions mid-exam
            c.execute("UPDATE questions SET question_text='CHANGED', correct_option='B', marks=99 WHERE test_id=?", (self.t_ver,))
            c.execute("DELETE FROM questions WHERE id=(SELECT MIN(id) FROM questions WHERE test_id=?)", (self.t_ver,))
            c.commit()
        page = cl.get(f"/test/{self.t_ver}").get_data(as_text=True)
        self.assertIn("Ver-Q0", page); self.assertNotIn("CHANGED", page)           # still the frozen snapshot
        self.submit(cl, self.sb, self.t_ver, "A")
        r = self.q("SELECT * FROM results WHERE test_id=? AND student_id=?", self.t_ver, self.sb)[0]
        self.assertEqual((r["score"], r["total_marks"]), (10, 10))                   # graded against the snapshot
        review = cl.get(f"/test/{self.t_ver}/result").get_data(as_text=True)
        self.assertIn("Ver-Q0", review); self.assertNotIn("CHANGED", review)

    def test_e03_new_attempt_after_edit_uses_new_version_old_attempt_keeps_old(self):
        t = self.t_snap
        a1 = self.attempt(self.sa, t)[0]
        with self.conn() as c:
            c.execute("UPDATE questions SET question_text='V2-text' WHERE test_id=?", (t,)); c.commit()
        cl = self.student_client(self.sc); a2 = self.start(cl, self.sc, t)
        v1, v2 = [self.q("SELECT * FROM exam_versions WHERE id=?", x["version_id"])[0] for x in (a1, a2)]
        self.assertNotEqual(a1["version_id"], a2["version_id"]); self.assertEqual(v2["version_no"], v1["version_no"] + 1)
        self.assertEqual(self.q("SELECT question_text FROM attempt_questions WHERE attempt_id=? AND position=1", a1["id"])[0][0], "Snap-Q0")
        self.assertEqual(self.q("SELECT question_text FROM attempt_questions WHERE attempt_id=? AND position=1", a2["id"])[0][0], "V2-text")

    def test_e04_version_editable_until_first_attempt(self):
        t = self.t_unl
        with self.conn() as c:
            v = self.ex.ensure_version(c, c.execute("SELECT * FROM tests WHERE id=?", (t,)).fetchone())
            c.execute("UPDATE questions SET question_text='edited-before-start' WHERE test_id=?", (t,))
            v2 = self.ex.ensure_version(c, c.execute("SELECT * FROM tests WHERE id=?", (t,)).fetchone()); c.commit()
        self.assertEqual(v, v2)                                                    # same unfrozen version, refreshed
        self.assertEqual(self.q("SELECT question_text FROM exam_version_questions WHERE version_id=? AND position=1", v)[0][0], "edited-before-start")

    def test_e05_server_timer_reload_resumes_same_attempt_and_deadline(self):
        cl = self.student_client(self.sd); a = self.start(cl, self.sd, self.t_late)
        for _ in range(3):
            cl.get(f"/test/{self.t_late}")                                         # reload
        runs = self.attempt(self.sd, self.t_late)
        self.assertEqual(len(runs), 1); self.assertEqual(runs[0]["deadline_at"], a["deadline_at"])
        dl = datetime.fromisoformat(a["deadline_at"]); st = datetime.fromisoformat(a["started_at"])
        self.assertEqual((dl - st).total_seconds(), 30 * 60)                       # duration comes from the server

    def test_e06_browser_cannot_extend_time_and_late_submit_uses_saved_draft(self):
        cl = self.student_client(self.se); a = self.start(cl, self.se, self.t_late)
        qid = self.q("SELECT id FROM attempt_questions WHERE attempt_id=? AND position=1", a["id"])[0][0]
        r = cl.post(f"/test/{self.t_late}/save", data=json.dumps({"answers": {f"q_{qid}": "A"}}),
                    content_type="application/json", headers={"X-CSRFToken": CSRF})
        self.assertTrue(r.get_json()["ok"])                                         # autosave survives a dropped connection
        with self.conn() as c:                                                      # time runs out on the SERVER
            c.execute("UPDATE exam_attempts SET deadline_at=? WHERE id=?", ((datetime.now() - timedelta(minutes=5)).isoformat(), a["id"])); c.commit()
        self.submit(cl, self.se, self.t_late, "A")                                  # tries to submit all-correct late
        done = self.attempt(self.se, self.t_late)[0]
        self.assertEqual(done["status"], "auto_submitted")
        self.assertEqual(done["score"], 5)                                          # only the 1 autosaved answer counts

    def test_e07_late_submission_rejected_when_teacher_chose_reject(self):
        cl = self.student_client(self.sa); a = self.start(cl, self.sa, self.t_rej)
        with self.conn() as c:
            c.execute("UPDATE exam_attempts SET deadline_at=? WHERE id=?", ((datetime.now() - timedelta(minutes=5)).isoformat(), a["id"])); c.commit()
        self.submit(cl, self.sa, self.t_rej)
        self.assertEqual(self.attempt(self.sa, self.t_rej)[0]["status"], "rejected")
        self.assertEqual(self.q("SELECT COUNT(*) FROM results WHERE test_id=?", self.t_rej)[0][0], 0)

    def test_e08_expired_attempt_is_closed_by_server_even_without_submit(self):
        cl = self.student_client(self.sb); a = self.start(cl, self.sb, self.t_rej)
        with self.conn() as c:
            c.execute("UPDATE exam_attempts SET deadline_at=? WHERE id=?", ((datetime.now() - timedelta(minutes=5)).isoformat(), a["id"])); c.commit()
            self.assertEqual(self.ex.sweep_expired(c), 1)
        self.assertEqual(self.attempt(self.sb, self.t_rej)[0]["status"], "rejected")

    def test_e09_double_and_parallel_submission_make_one_result(self):
        cl = self.student_client(self.sa); self.start(cl, self.sa, self.t_race)
        qids = [r["id"] for r in self.q("SELECT id FROM attempt_questions WHERE attempt_id=?", self.attempt(self.sa, self.t_race)[0]["id"])]
        out = []
        def go(): out.append(self.post(self.student_client(self.sa), f"/test/{self.t_race}/submit", {f"q_{i}": "A" for i in qids}).status_code)
        th = [threading.Thread(target=go) for _ in range(5)]; [t.start() for t in th]; [t.join() for t in th]
        self.assertEqual(self.q("SELECT COUNT(*) FROM results WHERE test_id=? AND student_id=?", self.t_race, self.sa)[0][0], 1)
        self.assertEqual(len(self.attempt(self.sa, self.t_race, "submitted")), 1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM answers WHERE attempt_id=?", self.attempt(self.sa, self.t_race)[0]["id"])[0][0], 2)

    def test_e10_multiple_attempts_limit_and_best_vs_latest(self):
        cl = self.student_client(self.sc)
        for pick in ("A", "B", "B"):                                                # 10, 0, 0
            self.start(cl, self.sc, self.t_multi); self.submit(cl, self.sc, self.t_multi, pick)
        self.assertEqual(len(self.attempt(self.sc, self.t_multi, "submitted")), 3)
        cl.get(f"/test/{self.t_multi}")                                             # 4th attempt refused
        self.assertEqual(len(self.attempt(self.sc, self.t_multi)), 3)
        self.assertEqual(self.q("SELECT score FROM results WHERE test_id=? AND student_id=?", self.t_multi, self.sc)[0][0], 10)  # best
        cl2 = self.student_client(self.sc)
        for pick in ("A", "B"):                                                     # latest policy: 10 then 0
            self.start(cl2, self.sc, self.t_latest); self.submit(cl2, self.sc, self.t_latest, pick)
        self.assertEqual(self.q("SELECT score FROM results WHERE test_id=? AND student_id=?", self.t_latest, self.sc)[0][0], 0)
        res = self.q("SELECT attempt_id FROM results WHERE test_id=? AND student_id=?", self.t_multi, self.sc)[0][0]
        self.assertEqual(self.q("SELECT attempt_no FROM exam_attempts WHERE id=?", res)[0][0], 1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM exam_attempts WHERE test_id=? AND student_id=? AND is_counted=1", self.t_multi, self.sc)[0][0], 1)

    def test_e11_negative_marking(self):
        cl = self.student_client(self.sd); self.start(cl, self.sd, self.t_neg); self.submit(cl, self.sd, self.t_neg, "B")
        self.assertEqual(self.q("SELECT score FROM results WHERE test_id=? AND student_id=?", self.t_neg, self.sd)[0][0], 0)   # never below 0
        a = self.attempt(self.sd, self.t_neg)[0]
        self.assertEqual(self.q("SELECT SUM(marks_awarded) FROM answers WHERE attempt_id=?", a["id"])[0][0], -4)

    def test_e12_result_review_survives_question_and_test_changes(self):
        cl = self.student_client(self.se); self.start(cl, self.se, self.t_del); self.submit(cl, self.se, self.t_del, "A")
        with self.conn() as c:
            c.execute("DELETE FROM questions WHERE test_id=?", (self.t_del,)); c.commit()      # teacher wipes the questions
        page = cl.get(f"/test/{self.t_del}/result").get_data(as_text=True)
        self.assertIn("Del-Q0", page); self.assertIn("Del-Q1", page)
        self.assertEqual(self.q("SELECT COUNT(*) FROM answers WHERE result_id IN (SELECT id FROM results WHERE test_id=?)", self.t_del)[0][0], 2)

    def test_e13_old_result_migrated_with_snapshot(self):
        """Migration 008 on a database with a pre-existing result keeps it and builds its snapshot."""
        path = os.path.join(self.tmp, "mig.db"); shutil.copy(__import__("legacy").FIXTURE, path)
        c = sqlite3.connect(path); c.execute("PRAGMA foreign_keys=OFF")
        c.execute("DELETE FROM schema_migrations WHERE id>='008'")
        now = datetime.now().isoformat()
        # rebuild the pre-008 shape of results/answers
        for t in ("exam_attempts", "attempt_questions", "exam_versions", "exam_version_questions", "impersonation_log", "deletion_queue", "attendance_duplicates_archive"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        c.executescript("""DROP TABLE answers; DROP TABLE results;
          CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER NOT NULL, student_id INTEGER NOT NULL, score INTEGER NOT NULL, total_marks INTEGER NOT NULL, submitted_at TEXT NOT NULL, FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE, FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE);
          CREATE UNIQUE INDEX uq_results_test_student ON results(test_id, student_id);
          CREATE TABLE answers (id INTEGER PRIMARY KEY AUTOINCREMENT, result_id INTEGER NOT NULL, question_id INTEGER NOT NULL, selected_option TEXT, is_correct INTEGER DEFAULT 0, FOREIGN KEY (result_id) REFERENCES results(id) ON DELETE CASCADE, FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE);""")
        for t in ("students", "tests", "questions"):
            c.execute(f"DELETE FROM {t}")
        c.execute("INSERT INTO students (id,name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version) VALUES (1,'S','10','1','s@x','1','h',1,?,0,0)", (now,))
        c.execute("INSERT INTO tests (id,title,subject,class_name,duration_minutes,is_active,created_at) VALUES (1,'T','M','10',30,1,?)", (now,))
        c.execute("INSERT INTO questions (id,test_id,question_text,option_a,option_b,option_c,option_d,correct_option,marks) VALUES (1,1,'Old question','a','b','c','d','C',4)")
        c.execute("INSERT INTO results VALUES (1,1,1,4,4,?)", (now,)); c.execute("INSERT INTO answers VALUES (1,1,1,'C',1)")
        c.execute("INSERT INTO attendance (student_id,date,status,note,marked_at) VALUES (1,'2026-01-01','present','',?)", (now,))
        c.commit(); c.close()
        from backend.db import migrations as m
        report = dict(m.run_pending(path, backup=True))
        self.assertEqual(report["008_exam_system"], "applied", report)
        c = sqlite3.connect(path); c.row_factory = sqlite3.Row
        a = c.execute("SELECT * FROM answers").fetchone()
        self.assertEqual((a["question_text"], a["correct_option"], a["selected_option"], a["is_correct"], a["marks"]), ("Old question", "C", "C", 1, 4))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM exam_attempts WHERE status='submitted'").fetchone()[0], 1)
        c.execute("DELETE FROM questions"); c.commit()                                # deleting the question no longer deletes the answer
        self.assertEqual(c.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 1)
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        c.close()

    # ================================================== STUDENT TESTS
    def test_s01_archive_hides_locks_and_keeps_history_then_restore(self):
        t = self.teacher_client(); sid = self.sc
        self.assertTrue(self.q("SELECT COUNT(*) FROM results WHERE student_id=?", sid)[0][0] > 0)
        before = [self.q(f"SELECT COUNT(*) FROM {tb} WHERE student_id=?", sid)[0][0] for tb in ("results", "exam_attempts", "login_history")]
        self.post(t, f"/teacher/students/{sid}/archive")
        st = self.q("SELECT * FROM students WHERE id=?", sid)[0]
        self.assertEqual(st["is_archived"], 1); self.assertEqual(st["is_blocked"], 0)       # archive != block
        after = [self.q(f"SELECT COUNT(*) FROM {tb} WHERE student_id=?", sid)[0][0] for tb in ("results", "exam_attempts", "login_history")]
        self.assertEqual(before, after)                                                      # history preserved
        cl = self.app.test_client()
        with cl.session_transaction() as s: s["_csrf_token"] = CSRF
        r = cl.post("/login", data={"email": "c@t.local", "password": PW}, headers={"X-CSRFToken": CSRF}, environ_base={"REMOTE_ADDR": "10.9.0.1"})
        self.assertTrue(r.headers["Location"].endswith("/login"))                            # archived cannot log in
        self.assertEqual(self.student_client(sid).get("/dashboard").status_code, 302)       # live session is ended
        self.assertNotIn("c@t.local", t.get("/teacher/students").get_data(as_text=True))
        self.assertIn("c@t.local", t.get("/teacher/students?status=archived").get_data(as_text=True))
        self.post(t, f"/teacher/students/{sid}/restore")
        self.assertEqual(self.q("SELECT is_archived FROM students WHERE id=?", sid)[0][0], 0)
        self.assertEqual(self.student_client(sid).get("/dashboard").status_code, 302)   # old sessions stay dead (session_version bumped)
        fresh = self.student_client(sid)
        with fresh.session_transaction() as s_:
            s_["sv"] = self.q("SELECT session_version FROM students WHERE id=?", sid)[0][0]
        self.assertEqual(fresh.get("/dashboard").status_code, 200)               # a new login works again

    def test_s02_blocked_student_cannot_login_and_block_is_separate_from_archive(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s: s["_csrf_token"] = CSRF
        r = cl.post("/login", data={"email": "blk@t.local", "password": PW}, headers={"X-CSRFToken": CSRF}, environ_base={"REMOTE_ADDR": "10.9.0.2"})
        self.assertTrue(r.headers["Location"].endswith("/login"))
        self.assertEqual(self.q("SELECT is_archived FROM students WHERE id=?", self.sblock)[0][0], 0)

    def test_s03_permanent_delete_needs_archive_and_typed_email_and_shows_impact(self):
        t = self.teacher_client(); sid = self.se
        r = t.post(f"/teacher/students/{sid}/delete", data={"confirm_name": "e@t.local"}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(self.q("SELECT COUNT(*) FROM students WHERE id=?", sid)[0][0], 1)  # not archived -> refused
        self.post(t, f"/teacher/students/{sid}/archive")
        page = t.get(f"/teacher/students/{sid}/delete").get_data(as_text=True)
        self.assertIn("exam attempts", page); self.assertIn("results", page)
        self.post(t, f"/teacher/students/{sid}/delete", {"confirm_name": "wrong@x"})
        self.assertEqual(self.q("SELECT COUNT(*) FROM students WHERE id=?", sid)[0][0], 1)
        self.post(t, f"/teacher/students/{sid}/delete", {"confirm_name": "e@t.local"})
        self.assertEqual(self.q("SELECT COUNT(*) FROM students WHERE id=?", sid)[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM results WHERE student_id=?", sid)[0][0], 0)

    def test_s04_raw_delete_of_student_with_history_is_blocked_by_foreign_keys(self):
        with self.conn() as c:
            c.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM students WHERE id=?", (self.sa,))
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM tests WHERE id=?", (self.t_race,))

    def test_s05_unauthorized_access(self):
        anon = self.app.test_client(); st = self.student_client(self.sa)
        for url in ("/teacher/students", "/teacher/tests", "/teacher/cleanup", "/teacher/students/1/delete"):
            self.assertEqual(anon.get(url).status_code, 302, url); self.assertEqual(st.get(url).status_code, 302, url)
        self.assertEqual(anon.get(f"/test/{self.t_snap}").status_code, 302)

    # ================================================= TEACHER / TEST DELETE
    def test_t01_archive_test_keeps_results_and_permanent_delete_is_confirmed(self):
        t = self.teacher_client(); tid = self.t_race
        n = self.q("SELECT COUNT(*) FROM results WHERE test_id=?", tid)[0][0]; self.assertGreater(n, 0)
        self.post(t, f"/teacher/tests/{tid}/archive")
        self.assertEqual(self.q("SELECT COUNT(*) FROM results WHERE test_id=?", tid)[0][0], n)
        self.assertEqual(self.student_client(self.sb).get(f"/test/{tid}").status_code, 404)
        self.post(t, "/teacher/cleanup/action", {"action": "delete_inactive_tests"})           # bulk button archives, never deletes
        self.assertGreater(self.q("SELECT COUNT(*) FROM tests")[0][0], 0)
        self.assertIn("attempts", t.get(f"/teacher/tests/{tid}/delete").get_data(as_text=True))   # impact preview
        self.post(t, f"/teacher/tests/{tid}/delete", {"confirm_name": "nope"})
        self.assertEqual(self.q("SELECT COUNT(*) FROM tests WHERE id=?", tid)[0][0], 1)
        self.post(t, f"/teacher/tests/{tid}/delete", {"confirm_name": "Race"})
        self.assertEqual(self.q("SELECT COUNT(*) FROM tests WHERE id=?", tid)[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM results WHERE test_id=?", tid)[0][0], 0)

    def test_t02_teacher_can_change_rules(self):
        t = self.teacher_client()
        self.post(t, f"/teacher/tests/{self.t_unl}/settings", {"duration_minutes": "45", "max_attempts": "2", "result_policy": "latest",
                                                              "late_policy": "reject", "grace_seconds": "15", "negative_marks": "0.5"})
        r = self.q("SELECT * FROM tests WHERE id=?", self.t_unl)[0]
        self.assertEqual((r["duration_minutes"], r["max_attempts"], r["result_policy"], r["late_policy"], r["grace_seconds"], r["negative_marks"]),
                         (45, 2, "latest", "reject", 15, 0.5))

    # ================================================== IMPERSONATION
    def test_i01_impersonation_is_readonly_audited_and_teacher_pages_closed(self):
        t = self.teacher_client(); sid = self.sb
        self.assertEqual(self.post(t, f"/teacher/students/{sid}/login-as").status_code, 302)
        with t.session_transaction() as s:
            self.assertTrue(s.get("impersonating")); self.assertFalse(s.get("is_teacher"))           # a STUDENT session
        log = self.q("SELECT * FROM impersonation_log WHERE student_id=? ORDER BY id DESC", sid)[0]
        self.assertIsNotNone(log["started_at"]); self.assertIsNone(log["ended_at"])
        self.assertEqual(t.get("/dashboard").status_code, 200)
        for url in ("/teacher/students", "/teacher/tests", "/teacher/cleanup"):
            self.assertEqual(t.get(url).status_code, 302, url)                                       # teacher area closed
        self.assertEqual(t.get("/api/v1/teacher/overview").status_code, 403)
        # every write is refused
        n_before = self.q("SELECT COUNT(*) FROM exam_attempts")[0][0]
        self.assertEqual(self.post(t, "/api/push/subscribe", {}).status_code, 403)             # JSON API: read-only
        self.assertEqual(t.get(f"/test/{self.t_unl}").status_code, 302)                         # opening a test would start a timer
        self.assertEqual(self.q("SELECT COUNT(*) FROM exam_attempts")[0][0], n_before)         # ...so nothing was created
        self.assertEqual(self.post(t, f"/test/{self.t_unl}/submit").status_code, 302)
        self.assertEqual(self.post(t, f"/test/{self.t_unl}/save").status_code, 302)

    def test_i02_exit_returns_to_teacher_and_closes_audit_row(self):
        t = self.teacher_client(); sid = self.sd
        self.post(t, f"/teacher/students/{sid}/login-as")
        r = self.post(t, "/logout")
        self.assertTrue(r.headers["Location"].endswith("/teacher/dashboard"))
        with t.session_transaction() as s:
            self.assertTrue(s.get("is_teacher")); self.assertNotIn("student_id", s); self.assertNotIn("impersonating", s)
        log = self.q("SELECT * FROM impersonation_log WHERE student_id=? ORDER BY id DESC", sid)[0]
        self.assertIsNotNone(log["ended_at"]); self.assertEqual(log["end_reason"], "exit")
        self.assertEqual(t.get("/teacher/students").status_code, 200)

    def test_i03_impersonation_expires(self):
        t = self.teacher_client(); self.post(t, f"/teacher/students/{self.sb}/login-as")
        with t.session_transaction() as s:
            s["impersonation_started"] = (datetime.now() - timedelta(hours=3)).isoformat()
        r = t.get("/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.q("SELECT end_reason FROM impersonation_log ORDER BY id DESC")[0][0], "timeout")

    def test_i04_login_regenerates_session(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(_csrf_token=CSRF, is_teacher=True, evil="planted")
        cl.post("/login", data={"email": "d@t.local", "password": PW}, headers={"X-CSRFToken": CSRF}, environ_base={"REMOTE_ADDR": "10.9.0.3"})
        with cl.session_transaction() as s:
            self.assertEqual(s.get("student_id"), self.sd); self.assertNotIn("is_teacher", s); self.assertNotIn("evil", s)

    # ===================================================== PDF NOTES
    def _upload(self, cl, name, data, title="N"):
        return cl.post("/teacher/notes/upload", data={"title": title, "subject": "S", "class_name": "", "pdf_file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", headers={"X-CSRFToken": CSRF})

    def test_p01_valid_invalid_duplicate_large_shared_and_safe_delete(self):
        t = self.teacher_client(); from backend.config import UPLOAD_FOLDER
        self._upload(t, "same.pdf", PDF, "A"); self._upload(t, "same.pdf", PDF, "B")                 # duplicate filename
        rows = self.q("SELECT * FROM notes WHERE title IN ('A','B') ORDER BY id")
        self.assertEqual(len(rows), 2); self.assertNotEqual(rows[0]["filename"], rows[1]["filename"])  # uuid names never collide
        for r in rows:
            self.assertRegex(r["filename"], r"^[0-9a-f]{32}\.pdf$"); self.assertTrue(os.path.isfile(os.path.join(UPLOAD_FOLDER, r["filename"])))
            self.assertEqual(r["original_filename"], "same.pdf"); self.assertEqual(len(r["file_sha256"]), 64)
        n = self.q("SELECT COUNT(*) FROM notes")[0][0]
        self._upload(t, "fake.pdf", b"<html>not a pdf</html>", "Fake"); self._upload(t, "x.exe", PDF, "Exe")
        self._upload(t, "big.pdf", PDF + b"0" * (16 * 1024 * 1024), "Big"); self._upload(t, "empty.pdf", b"", "Empty")
        self.assertEqual(self.q("SELECT COUNT(*) FROM notes")[0][0], n)                               # all rejected
        # shared file: two records pointing at one stored file (legacy data) -> deleting one keeps the file
        shared = rows[0]["filename"]
        with self.conn() as c:
            c.execute("INSERT INTO notes (title,subject,class_name,filename,uploaded_at) VALUES ('Shared','S','',?,?)", (shared, datetime.now().isoformat())); c.commit()
        sid = self.q("SELECT id FROM notes WHERE title='Shared'")[0][0]
        self.post(t, f"/teacher/notes/{sid}/delete")
        self.assertTrue(os.path.isfile(os.path.join(UPLOAD_FOLDER, shared)))
        self.post(t, f"/teacher/notes/{rows[0]['id']}/delete")                                         # last reference -> file goes
        self.assertFalse(os.path.isfile(os.path.join(UPLOAD_FOLDER, shared)))
        self.assertTrue(os.path.isfile(os.path.join(UPLOAD_FOLDER, rows[1]["filename"])))             # the other note untouched

    def test_p02_database_failure_during_upload_leaves_no_orphan_file(self):
        t = self.teacher_client(); from backend.config import UPLOAD_FOLDER
        before = set(os.listdir(UPLOAD_FOLDER))
        import backend.routes.teacher.notes as notes_mod
        real = notes_mod.get_db
        class Boom:
            def __enter__(s): raise sqlite3.OperationalError("database is locked")
            def __exit__(s, *a): return False
        with mock.patch.object(notes_mod, "get_db", lambda: Boom()):
            self._upload(t, "z.pdf", PDF, "WillFail")
        self.assertEqual(set(os.listdir(UPLOAD_FOLDER)), before)
        self.assertEqual(self.q("SELECT COUNT(*) FROM notes WHERE title='WillFail'")[0][0], 0)

    def test_p03_failed_file_delete_is_queued_and_retried(self):
        from backend.services import note_storage, deletion_queue
        with self.conn() as c:
            with mock.patch.object(note_storage, "remove_file", side_effect=PermissionError("locked")):
                self.assertEqual(note_storage.delete_file_if_unreferenced(c, "ghost.pdf"), "queued for retry")
            self.assertEqual(c.execute("SELECT status FROM deletion_queue WHERE ref='ghost.pdf'").fetchone()[0], "pending")
            st = deletion_queue.run_pending(c, {"file": lambda ref: None})
        self.assertEqual(st["done"], 1)

    # =================================================== PUSH SECURITY
    def _sub(self, cl, endpoint, p="B" * 86 + "AA", a="a" * 22):
        import base64
        key = base64.urlsafe_b64encode(b"\x04" + b"k" * 64).decode().rstrip("=")
        return cl.post("/api/push/subscribe", data=json.dumps({"endpoint": endpoint, "keys": {"p256dh": key, "auth": a}}),
                       content_type="application/json", headers={"X-CSRFToken": CSRF})

    def test_u01_endpoint_validation(self):
        cl = self.student_client(self.sa)
        for bad in ["http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://169.254.169.254/latest", "https://evil.example.com/x",
                    "https://fcm.googleapis.com.evil.com/x", "https://user:pw@fcm.googleapis.com/x", "https://fcm.googleapis.com:8443/x",
                    "https://fcm.googleapis.com/" + "a" * 800, "ftp://fcm.googleapis.com/x", "javascript:alert(1)", ""]:
            self.assertEqual(self._sub(cl, bad).status_code, 400, bad)
        self.assertEqual(self._sub(cl, "https://fcm.googleapis.com/fcm/send/abc").status_code, 200)

    def test_u02_unsubscribe_only_removes_own_and_duplicates_are_handled(self):
        a, b = self.student_client(self.sa), self.student_client(self.sb)
        ep = "https://updates.push.services.mozilla.com/wpush/v2/own-a"
        self._sub(a, ep); self._sub(a, ep)                                                              # duplicate subscribe = one row
        self.assertEqual(self.q("SELECT COUNT(*) FROM push_subscriptions WHERE endpoint=?", ep)[0][0], 1)
        b.post("/api/push/unsubscribe", data=json.dumps({"endpoint": ep}), content_type="application/json", headers={"X-CSRFToken": CSRF})
        self.assertEqual(self.q("SELECT COUNT(*) FROM push_subscriptions WHERE endpoint=?", ep)[0][0], 1)   # B cannot delete A's
        # B cannot hijack A's endpoint with different keys
        r = self._sub(b, ep, a="z" * 22)
        self.assertEqual(r.status_code, 409); self.assertEqual(self.q("SELECT student_id FROM push_subscriptions WHERE endpoint=?", ep)[0][0], self.sa)
        a.post("/api/push/unsubscribe", data=json.dumps({"endpoint": ep}), content_type="application/json", headers={"X-CSRFToken": CSRF})
        self.assertEqual(self.q("SELECT COUNT(*) FROM push_subscriptions WHERE endpoint=?", ep)[0][0], 0)

    def test_u03_sender_skips_unsafe_endpoints_and_removes_expired(self):
        from backend.services import push_service
        with self.conn() as c:
            c.execute("INSERT INTO push_subscriptions (student_id,class_name,endpoint,p256dh,auth,created_at) VALUES (?,?,?,?,?,?)",
                      (self.sa, "10", "https://169.254.169.254/evil", "x", "y", datetime.now().isoformat())); c.commit()
        called = []
        with mock.patch.object(push_service, "WEBPUSH_OK", True), mock.patch.object(push_service, "webpush", lambda **kw: called.append(kw), create=True):
            push_service.send_push_to_class("t", "m", "", "10")
        self.assertFalse([k for k in called if "169.254" in k["subscription_info"]["endpoint"]])   # never contacted
        self.assertEqual(self.q("SELECT COUNT(*) FROM push_subscriptions WHERE endpoint LIKE 'https://169.%'")[0][0], 0)

    # ================================================ HTTP / CSRF / CSV
    def test_h01_logout_and_resend_otp_are_post_only_with_csrf(self):
        cl = self.student_client(self.sa)
        cl.get("/logout"); self.assertEqual(cl.get("/dashboard").status_code, 200)                        # GET did nothing
        self.assertEqual(cl.post("/logout").status_code, 302)                                              # no CSRF -> rejected
        self.assertEqual(cl.get("/dashboard").status_code, 200)
        self.assertEqual(self.app.test_client().get("/resend-otp").status_code, 405)
        t = self.teacher_client(); t.get("/teacher/logout"); self.assertEqual(t.get("/teacher/dashboard").status_code, 200)
        self.post(t, "/teacher/logout"); self.assertEqual(t.get("/teacher/dashboard").status_code, 302)
        self.post(cl, "/logout"); self.assertEqual(cl.get("/dashboard").status_code, 302)

    def test_h02_csrf_failure_never_redirects_to_foreign_site(self):
        cl = self.student_client(self.sa)
        r = cl.post("/teacher/anything", data={}, headers={"Referer": "https://evil.example.com/phish"})
        self.assertNotIn("evil.example.com", r.headers.get("Location", ""))
        r = cl.post("/profile-nope", data={}, headers={"Referer": "http://localhost/dashboard?x=1"})
        from backend.utils.helpers import safe_next
        with self.app.test_request_context("/"):
            self.assertEqual(safe_next("//evil.com"), "/"); self.assertEqual(safe_next("https://evil.com"), "/")
            self.assertEqual(safe_next("/\\evil.com"), "/"); self.assertEqual(safe_next("/dashboard"), "/dashboard")

    def test_h03_csv_formula_injection_sanitised_in_export_only(self):
        t = self.teacher_client(); body = t.get("/teacher/students/export").get_data(as_text=True)
        rows = list(csv.reader(io.StringIO(body)))
        alice = [r for r in rows if "a@t.local" in r][0]
        self.assertEqual(alice[1], "'=Alice"); self.assertTrue(alice[5].startswith("'="))                 # neutralised in the file
        self.assertEqual(self.q("SELECT name FROM students WHERE id=?", self.sa)[0][0], "=Alice")        # database untouched
        from backend.utils.helpers import csv_safe
        for v in ("=1+1", "+1", "-1", "@SUM(A1)"): self.assertTrue(csv_safe(v).startswith("'"))
        self.assertEqual(csv_safe("normal"), "normal"); self.assertEqual(csv_safe(None), "")

    def test_h04_security_headers_and_csp(self):
        r = self.app.test_client().get("/login")
        csp = r.headers["Content-Security-Policy"]
        for part in ("object-src 'none'", "base-uri 'self'", "frame-ancestors 'self'", "form-action 'self'"):
            self.assertIn(part, csp)
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    # ==================================================== DATABASE
    def test_d01_foreign_keys_integrity_and_health_tools(self):
        from backend.db import tools
        rep = tools.check(self.db)
        self.assertEqual(rep["integrity"], "ok"); self.assertEqual(rep["foreign_key_violations"], [])
        self.assertIn("010_support_tables", rep["applied_migrations"])
        self.assertTrue(tools.verify_backup(self.db)["ok"])
        self.assertTrue(tools.restore_test(self.db)["ok"])
        self.assertFalse(tools.verify_backup(os.path.join(self.tmp, "missing.db"))["ok"])
        junk = os.path.join(self.tmp, "junk.db"); open(junk, "wb").write(b"not a database at all" * 50)
        self.assertFalse(tools.verify_backup(junk)["ok"])

    def test_d02_destructive_rollback_refused(self):
        from backend.db import migrations as m
        for mid in ("008_exam_system", "007_question_bank", "010_support_tables"):
            with self.assertRaises(PermissionError):
                m.rollback(self.db, mid, backup=False)

    def test_d03_duplicate_attendance_is_archived_not_lost_and_unique_enforced(self):
        path = os.path.join(self.tmp, "att.db"); shutil.copy(__import__("legacy").FIXTURE, path)
        c = sqlite3.connect(path); c.execute("PRAGMA foreign_keys=OFF")
        c.execute("DELETE FROM schema_migrations WHERE id>='004'")
        c.execute("DROP INDEX IF EXISTS uq_attendance_student_date"); c.execute("DELETE FROM students")
        c.execute("INSERT INTO students (id,name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version) VALUES (1,'S','10','1','s@x','1','h',1,'x',0,0)")
        c.executemany("INSERT INTO attendance (student_id,date,status,note,marked_at) VALUES (1,'2026-02-02',?,?,?)",
                      [("absent", "old", "2026-02-02T08:00:00"), ("present", "new", "2026-02-02T09:00:00")])
        c.commit(); c.close()
        from backend.db import migrations as m
        rep = dict(m.run_pending(path, backup=False))
        self.assertTrue(rep["004_unique_attendance"].startswith("SKIP"))                                  # reported, never silently deleted
        self.assertEqual(rep["008_exam_system"], "applied")                                                # 008 archives then enforces
        c = sqlite3.connect(path)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM attendance").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT note FROM attendance").fetchone()[0], "new")
        self.assertEqual(c.execute("SELECT note FROM attendance_duplicates_archive").fetchone()[0], "old")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO attendance (student_id,date,status,note,marked_at) VALUES (1,'2026-02-02','late','','x')")
        c.close()

    def test_d04_attendance_atomic_upsert(self):
        from backend.services import attendance_service as at
        with self.conn() as c:
            at.mark(c, "2026-01-05", [{"student_id": self.sa, "status": "absent"}])
            at.mark(c, "2026-01-05", [{"student_id": self.sa, "status": "present", "note": "late bus"}]); c.commit()
        rows = self.q("SELECT * FROM attendance WHERE student_id=? AND date='2026-01-05'", self.sa)
        self.assertEqual((len(rows), rows[0]["status"], rows[0]["note"]), (1, "present", "late bus"))

    # ===================================================== AI CONTEXT
    def test_a01_ai_context_uses_snapshot_not_question_bank(self):
        from backend.ai import context
        cl = self.student_client(self.sb); self.start(cl, self.sb, self.t_ai); self.submit(cl, self.sb, self.t_ai, "B")
        with self.conn() as c:
            c.execute("UPDATE questions SET question_text='REWRITTEN LATER', correct_option='D' WHERE test_id=?", (self.t_ai,))
            c.commit()
        with self.conn() as c:
            text = context.learning_summary(c, self.sb)
        self.assertIn("AiCtx-Q0", text); self.assertNotIn("REWRITTEN LATER", text); self.assertIn("correct A", text)
        c = self.conn(); c.execute("DELETE FROM questions WHERE test_id=?", (self.t_ai,)); c.commit(); c.close()
        with self.conn() as c:
            self.assertIn("AiCtx-Q0", context.learning_summary(c, self.sb))                                 # survives deletion too


if __name__ == "__main__":
    unittest.main()
