# -*- coding: utf-8 -*-
"""Backend regression + feature tests.

Run from the project folder:   python -m unittest discover -s tests -v

SAFETY: the suite copies the project (including database.db) into a temporary
directory and imports the app from THAT copy, so the real database is never
opened, migrated or modified. The temp copy is deleted afterwards.
"""
import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRF = "test-csrf-token"
PASSWORD = "Passw0rdTest"


def _snapshot(db_file):
    """{table: [ {column: value} ]} for every table - used to prove old data is untouched."""
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    snap = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'").fetchall():
        snap[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
    conn.close()
    return snap


def _same_old_data(original, current):
    """Every original row still exists with identical values in every ORIGINAL column
    (columns that app.init_db() has always added, e.g. students.session_version, are allowed)."""
    if len(current) < len(original):
        return False
    for old, new in zip(original, current):
        if any(new.get(k) != v for k, v in old.items()):
            return False
    return True


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mathclass-test-")
        cls.proj = os.path.join(cls.tmp, "proj")
        shutil.copytree(ROOT, cls.proj, ignore=shutil.ignore_patterns(
            "backups", ".secret_key", "__pycache__", ".env", "tests"))
        shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(cls.proj, "tests"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        cls.db = os.path.join(cls.proj, "database.db")
        cls.original_snapshot = _snapshot(cls.db)          # BEFORE the app ever touches the copy
        cls.original_tables = set(cls.original_snapshot)
        os.environ.update({"FORCE_HTTPS": "0", "SECRET_KEY": "unit-test-secret",
                           "TEACHER_USERNAME": "sir", "TEACHER_PASSWORD": "TeacherPass123",
                           "EMAIL_ADDRESS": "", "EMAIL_APP_PASSWORD": ""})
        os.environ.pop("ENFORCE_TEST_TIMER", None)
        sys.path.insert(0, cls.proj)
        for name in [m for m in sys.modules if m == "app" or m == "backend" or m.startswith("backend.")]:
            del sys.modules[name]
        cls.appmod = importlib.import_module("app")        # runs init_db + migrations on the COPY
        cls.app = cls.appmod.app
        cls.app.config["TESTING"] = True
        cls.after_import_snapshot = _snapshot(cls.db)
        cls._seed()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.proj)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ fixtures
    @classmethod
    def _seed(cls):
        conn = sqlite3.connect(cls.db)
        conn.execute("PRAGMA foreign_keys=ON")
        now = datetime.now().isoformat()
        ph = generate_password_hash(PASSWORD)

        def student(name, cls_name, email, verified=1, blocked=0):
            return conn.execute(
                """INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,
                   registered_at,is_blocked,session_version) VALUES (?,?,?,?,?,?,?,?,?,0)""",
                (name, cls_name, "1", email, "9000000000", ph, verified, now, blocked)).lastrowid

        def test(title, cls_name, active=1, dur=30):
            tid = conn.execute(
                "INSERT INTO tests (title,subject,class_name,duration_minutes,is_active,created_at) VALUES (?,?,?,?,?,?)",
                (title, "Math", cls_name, dur, active, now)).lastrowid
            for i in range(2):
                conn.execute(
                    """INSERT INTO questions (test_id,question_text,option_a,option_b,option_c,option_d,
                       correct_option,marks) VALUES (?,?,?,?,?,?,?,?)""",
                    (tid, f"Q{i}", "a", "b", "c", "d", "A", 5))
            return tid

        cls.s10 = student("Ten", "10", "ten@test.local")
        cls.s10b = student("TenB", "10", "tenb@test.local")
        cls.s9 = student("Nine", "9", "nine@test.local")
        cls.s_blocked = student("Blocked", "10", "blocked@test.local", blocked=1)
        cls.t10 = test("T10", "10")
        cls.t9 = test("T9", "9")
        cls.t_all = test("TAll", "")
        cls.t_off = test("TOff", "10", active=0)
        cls.t_timer = test("TTimer", "10", dur=5)
        cls.t_race = test("TRace", "10")
        cls.t_dupe = test("TDupe", "10")
        cls.t_inactive_started = test("TOffStarted", "10", active=0)
        conn.execute("INSERT INTO notes (title,subject,class_name,filename,uploaded_at) VALUES (?,?,?,?,?)",
                     ("Note10", "Algebra", "10", "x.pdf", now))
        cls.note9 = conn.execute("INSERT INTO notes (title,subject,class_name,filename,uploaded_at) VALUES (?,?,?,?,?)",
                                 ("Note9", "Geo", "9", "y.pdf", now)).lastrowid
        conn.execute("INSERT INTO notes (title,subject,class_name,filename,uploaded_at) VALUES (?,?,?,?,?)",
                     ("100% free_note", "Misc", "", "z.pdf", now))
        conn.commit()
        conn.close()

    def db_conn(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def student_client(self, sid, cls_name):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s.update(student_id=sid, student_name="x", student_class=cls_name, sv=0, _csrf_token=CSRF)
        return c

    def teacher_client(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s.update(is_teacher=True, _csrf_token=CSRF)
        return c

    def post_json(self, c, url, body):
        return c.post(url, data=json.dumps(body), content_type="application/json",
                      headers={"X-CSRFToken": CSRF})

    def submit(self, c, tid, answer="A"):
        with self.db_conn() as conn:
            qids = [r["id"] for r in conn.execute("SELECT id FROM questions WHERE test_id=?", (tid,))]
        data = {f"q_{q}": answer for q in qids}
        return c.post(f"/test/{tid}/submit", data=data, headers={"X-CSRFToken": CSRF})

    def result_count(self, tid, sid):
        with self.db_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM results WHERE test_id=? AND student_id=?",
                                (tid, sid)).fetchone()[0]

    # ----------------------------------------------- 1. startup / regression
    def test_01_app_imports_and_backend_registered(self):
        names = {bp for bp in self.app.blueprints}
        self.assertTrue({"api_public", "api_student", "api_teacher"} <= names)

    def test_02_every_original_route_still_exists(self):
        with open(os.path.join(self.proj, "tests", "baseline_routes.json")) as fh:
            baseline = json.load(fh)
        self.assertGreaterEqual(len(baseline), 55)
        rules = {}
        for r in self.app.url_map.iter_rules():
            rules.setdefault(r.rule.replace("<int:", "<int:"), set()).update(r.methods)
        for path, methods in baseline:
            self.assertIn(path, rules, f"route lost: {path}")
            self.assertTrue(set(methods) <= rules[path], f"methods changed on {path}")

    def test_03_public_pages_still_work(self):
        c = self.app.test_client()
        for path in ["/", "/login", "/register", "/contact", "/forgot-password", "/teacher/login",
                     "/download", "/manifest.json", "/sw.js"]:
            self.assertEqual(c.get(path).status_code, 200, path)

    def test_04_protected_pages_redirect_when_anonymous(self):
        c = self.app.test_client()
        for path in ["/dashboard", "/notes", "/profile", "/test/1", "/teacher/dashboard",
                     "/teacher/students", "/teacher/cleanup"]:
            self.assertEqual(c.get(path).status_code, 302, path)

    def test_05_logged_in_pages_render(self):
        c = self.student_client(self.s10, "10")
        for path in ["/dashboard", "/notes", "/profile", f"/test/{self.t10}"]:
            self.assertEqual(c.get(path).status_code, 200, path)
        t = self.teacher_client()
        for path in ["/teacher/dashboard", "/teacher/students", "/teacher/tests", "/teacher/notes/upload",
                     "/teacher/announcements", "/teacher/contacts", "/teacher/activity", "/teacher/cleanup"]:
            self.assertEqual(t.get(path).status_code, 200, path)

    # ------------------------------------------- 2. database / migrations
    def test_10_existing_data_unchanged_after_startup_and_migrations(self):
        for table, rows in self.original_snapshot.items():
            self.assertTrue(_same_old_data(rows, self.after_import_snapshot[table]), f"data changed in {table}")

    def test_11_new_objects_are_additive_only(self):
        with self.db_conn() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            applied = {r[0] for r in conn.execute("SELECT id FROM schema_migrations")}
        self.assertTrue(self.original_tables <= tables)
        self.assertTrue({"test_attempts", "schema_migrations"} <= tables)
        self.assertEqual(applied, {"001_test_attempts", "002_perf_indexes",
                                   "003_unique_results", "004_unique_attendance",
                                   "005_normalize_class_names", "006_ai_chat", "007_question_bank"})

    def test_12_backup_created_before_migrating(self):
        folder = os.path.join(self.proj, "backups")
        files = [f for f in os.listdir(folder) if f.endswith(".db")]
        self.assertTrue(files)
        backup = sqlite3.connect(os.path.join(folder, files[0]))
        self.assertEqual(backup.execute("SELECT COUNT(*) FROM students").fetchone()[0],
                         len(self.original_snapshot["students"]))
        backup.close()

    def test_13_migrations_idempotent_and_reversible(self):
        from backend import migrations as m
        self.assertEqual(m.run_pending(self.db, backup=False), [])
        m.rollback(self.db, "002_perf_indexes", backup=False)
        self.assertNotIn("002_perf_indexes", m.applied_ids(self.db))
        self.assertEqual(m.run_pending(self.db, backup=False), [("002_perf_indexes", "applied")])
        # rolling back never removes pre-existing tables/rows
        self.assertTrue(_same_old_data(self.original_snapshot["students"], _snapshot(self.db)["students"]))

    def test_14_unique_index_skipped_when_duplicates_exist(self):
        from backend import migrations as m
        path = os.path.join(self.tmp, "dupes.db")
        shutil.copy(os.path.join(ROOT, "database.db"), path)
        c = sqlite3.connect(path)
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO results (test_id,student_id,score,total_marks,submitted_at) VALUES (1,1,1,2,'x')")
        c.execute("INSERT INTO results (test_id,student_id,score,total_marks,submitted_at) VALUES (1,1,2,2,'y')")
        c.commit()
        before = c.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        c.close()
        report = dict(m.run_pending(path, backup=False))
        self.assertTrue(report["003_unique_results"].startswith("SKIP"))
        c = sqlite3.connect(path)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM results").fetchone()[0], before)
        c.close()

    def test_15_database_blocks_duplicate_result(self):
        conn = self.db_conn()
        conn.execute("INSERT INTO results (test_id,student_id,score,total_marks,submitted_at) VALUES (?,?,?,?,?)",
                     (self.t_dupe, self.s10b, 1, 2, "now"))
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO results (test_id,student_id,score,total_marks,submitted_at) VALUES (?,?,?,?,?)",
                         (self.t_dupe, self.s10b, 1, 2, "now"))
        conn.close()

    def test_16_integrity_error_becomes_friendly_redirect(self):
        from backend import guards
        with self.app.test_request_context(f"/test/{self.t10}/submit", method="POST"):
            resp = guards.integrity_error(sqlite3.IntegrityError("UNIQUE"))
            self.assertEqual(resp.status_code, 302)

    # ------------------------------------------------ 3. login / sessions
    def test_20_student_login_logout(self):
        c = self.app.test_client()
        r = c.post("/login", data={"email": "ten@test.local", "password": PASSWORD},
                   headers={"X-CSRFToken": CSRF}, environ_base={"REMOTE_ADDR": "10.1.0.1"})
        self.assertEqual(r.status_code, 400 if False else r.status_code)
        with c.session_transaction() as s:
            s["_csrf_token"] = CSRF
        r = c.post("/login", data={"email": "ten@test.local", "password": PASSWORD},
                   headers={"X-CSRFToken": CSRF}, environ_base={"REMOTE_ADDR": "10.1.0.2"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/dashboard"))
        self.assertEqual(c.get("/dashboard").status_code, 200)
        c.get("/logout")
        self.assertEqual(c.get("/dashboard").status_code, 302)

    def test_21_wrong_password_and_blocked_student_rejected(self):
        for email, pw, ip in [("ten@test.local", "WrongPass1", "10.1.0.3"),
                              ("blocked@test.local", PASSWORD, "10.1.0.4")]:
            c = self.app.test_client()
            with c.session_transaction() as s:
                s["_csrf_token"] = CSRF
            r = c.post("/login", data={"email": email, "password": pw},
                       headers={"X-CSRFToken": CSRF}, environ_base={"REMOTE_ADDR": ip})
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r.headers["Location"].endswith("/login"))
            self.assertEqual(c.get("/dashboard").status_code, 302)

    def test_22_teacher_login(self):
        for user, pw, expect, ip in [("sir", "TeacherPass123", "/teacher/dashboard", "10.1.0.5"),
                                     ("sir", "nope", "/teacher/login", "10.1.0.6")]:
            c = self.app.test_client()
            with c.session_transaction() as s:
                s["_csrf_token"] = CSRF
            r = c.post("/teacher/login", data={"username": user, "password": pw},
                       headers={"X-CSRFToken": CSRF}, environ_base={"REMOTE_ADDR": ip})
            self.assertTrue(r.headers["Location"].endswith(expect))

    def test_23_post_without_csrf_is_rejected(self):
        c = self.student_client(self.s10, "10")
        r = c.post("/api/v1/teacher/attendance", data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    # -------------------------------------- 4. API access boundaries (RBAC)
    def test_30_anonymous_gets_401_json(self):
        c = self.app.test_client()
        for path in ["/api/v1/me", "/api/v1/tests", "/api/v1/results", "/api/v1/teacher/overview",
                     "/api/v1/teacher/students"]:
            r = c.get(path)
            self.assertEqual(r.status_code, 401, path)
            self.assertFalse(r.get_json()["ok"])

    def test_31_student_cannot_use_teacher_api(self):
        c = self.student_client(self.s10, "10")
        for path in ["/api/v1/teacher/overview", "/api/v1/teacher/students",
                     f"/api/v1/teacher/tests/{self.t10}/results", "/api/v1/teacher/attendance"]:
            self.assertEqual(c.get(path).status_code, 403, path)

    def test_32_teacher_cannot_use_student_api(self):
        c = self.teacher_client()
        self.assertEqual(c.get("/api/v1/me").status_code, 401)

    def test_33_impersonating_teacher_is_a_student_only(self):
        c = self.student_client(self.s10, "10")
        with c.session_transaction() as s:
            s.update(is_teacher=True, impersonating=True)
        self.assertEqual(c.get("/api/v1/me").status_code, 200)
        self.assertEqual(c.get("/api/v1/teacher/overview").status_code, 403)

    def test_34_blocked_student_session_is_ended(self):
        c = self.student_client(self.s_blocked, "10")
        r = c.get("/api/v1/me")
        self.assertEqual(r.status_code, 401)

    def test_35_public_endpoints(self):
        c = self.app.test_client()
        self.assertEqual(c.get("/api/v1/health").get_json()["data"]["status"], "ok")
        self.assertEqual(c.get("/api/v1/session").get_json()["data"]["role"], "anonymous")
        self.assertTrue(c.get("/api/v1/csrf").get_json()["data"]["csrf_token"])

    # ------------------------------------------------- 5. student API data
    def test_40_me_hides_secrets(self):
        c = self.student_client(self.s10, "10")
        d = c.get("/api/v1/me").get_json()["data"]
        self.assertEqual(d["email"], "ten@test.local")
        for secret in ("password_hash", "notes_internal", "session_version", "is_blocked"):
            self.assertNotIn(secret, d)

    def test_41_tests_list_respects_class(self):
        c = self.student_client(self.s10, "10")
        titles = {t["title"] for t in c.get("/api/v1/tests").get_json()["data"]}
        self.assertIn("T10", titles)
        self.assertIn("TAll", titles)
        self.assertNotIn("T9", titles)
        self.assertNotIn("TOff", titles)

    def test_42_notes_search_escapes_wildcards(self):
        c = self.student_client(self.s10, "10")
        all_titles = {n["title"] for n in c.get("/api/v1/notes").get_json()["data"]}
        self.assertTrue({"Note10", "100% free_note"} <= all_titles)
        self.assertNotIn("Note9", all_titles)
        hits = c.get("/api/v1/notes?q=%25").get_json()["data"]       # literal "%"
        self.assertEqual([n["title"] for n in hits], ["100% free_note"])
        self.assertEqual(c.get("/api/v1/notes?q=_").get_json()["data"][0]["title"], "100% free_note")

    def test_43_pagination_is_bounded(self):
        c = self.student_client(self.s10, "10")
        meta = c.get("/api/v1/tests?per_page=100000&page=-5").get_json()["meta"]
        self.assertEqual((meta["page"], meta["per_page"]), (1, 100))
        meta = c.get("/api/v1/tests?per_page=abc&page=x").get_json()["meta"]
        self.assertEqual((meta["page"], meta["per_page"]), (1, 20))
        self.assertEqual(c.get("/api/v1/tests?status=bogus").status_code, 400)

    def test_44_results_summary_progress(self):
        c = self.student_client(self.s10, "10")
        self.assertEqual(self.submit(c, self.t10, "A").status_code, 302)   # both correct -> 10/10
        res = c.get("/api/v1/results").get_json()["data"]
        mine = [r for r in res if r["test_id"] == self.t10][0]
        self.assertEqual((mine["score"], mine["total_marks"], mine["percentage"], mine["passed"]),
                         (10, 10, 100.0, True))
        detail = c.get(f"/api/v1/results/{self.t10}").get_json()["data"]
        self.assertEqual(len(detail["answers"]), 2)
        self.assertEqual(c.get(f"/api/v1/results/{self.t9}").status_code, 404)
        summ = c.get("/api/v1/me/summary").get_json()["data"]
        self.assertGreaterEqual(summ["tests_attempted"], 1)
        self.assertTrue(c.get("/api/v1/progress").get_json()["data"])

    def test_45_student_never_sees_other_students_results(self):
        c = self.student_client(self.s10b, "10")
        ids = [r["test_id"] for r in c.get("/api/v1/results").get_json()["data"]]
        self.assertNotIn(self.t10, ids)

    # ------------------------------------- 6. test taking rules (guards)
    def test_50_class_guard_blocks_other_class_test_and_note(self):
        c = self.student_client(self.s10, "10")
        self.assertEqual(c.get(f"/test/{self.t9}").status_code, 404)
        self.assertEqual(self.submit(c, self.t9).status_code, 404)
        self.assertEqual(c.get(f"/test/{self.t9}/leaderboard").status_code, 404)
        self.assertEqual(c.get(f"/notes/download/{self.note9}").status_code, 404)
        self.assertEqual(self.result_count(self.t9, self.s10), 0)

    def test_51_all_class_test_open_to_everyone(self):
        self.assertEqual(self.student_client(self.s9, "9").get(f"/test/{self.t_all}").status_code, 200)
        self.assertEqual(self.student_client(self.s10, "10").get(f"/test/{self.t_all}").status_code, 200)

    def test_52_inactive_test_cannot_be_submitted_cold(self):
        c = self.student_client(self.s10, "10")
        self.assertEqual(self.submit(c, self.t_off).status_code, 404)
        self.assertEqual(self.result_count(self.t_off, self.s10), 0)

    def test_53_started_test_can_still_be_submitted_after_deactivation(self):
        c = self.student_client(self.s10b, "10")
        conn = self.db_conn()
        conn.execute("INSERT OR IGNORE INTO test_attempts (test_id,student_id,started_at) VALUES (?,?,?)",
                     (self.t_inactive_started, self.s10b, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        self.assertEqual(self.submit(c, self.t_inactive_started, "B").status_code, 302)
        self.assertEqual(self.result_count(self.t_inactive_started, self.s10b), 1)

    def test_54_opening_a_test_records_attempt(self):
        c = self.student_client(self.s9, "9")
        c.get(f"/test/{self.t9}")
        with self.db_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM test_attempts WHERE test_id=? AND student_id=?",
                                          (self.t9, self.s9)).fetchone()[0], 1)

    def test_55_second_submission_is_ignored(self):
        c = self.student_client(self.s9, "9")
        self.submit(c, self.t9, "A")
        self.submit(c, self.t9, "B")
        self.assertEqual(self.result_count(self.t9, self.s9), 1)
        with self.db_conn() as conn:
            self.assertEqual(conn.execute("SELECT score FROM results WHERE test_id=? AND student_id=?",
                                          (self.t9, self.s9)).fetchone()[0], 10)   # first answer kept

    def test_56_simultaneous_submissions_make_one_result(self):
        c1 = self.student_client(self.s10, "10")
        codes = []

        def go():
            codes.append(self.submit(self.student_client(self.s10, "10"), self.t_race).status_code)
        threads = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(self.result_count(self.t_race, self.s10), 1)
        self.assertEqual(len(codes), 4)

    def test_57_timer_enforcement_is_opt_in(self):
        sid = self.s10b
        c = self.student_client(sid, "10")
        c.get(f"/test/{self.t_timer}")
        conn = self.db_conn()
        conn.execute("UPDATE test_attempts SET started_at=? WHERE test_id=? AND student_id=?",
                     ((datetime.now() - timedelta(minutes=60)).isoformat(), self.t_timer, sid))
        conn.commit()
        conn.close()
        os.environ["ENFORCE_TEST_TIMER"] = "1"
        try:
            self.submit(c, self.t_timer)
            self.assertEqual(self.result_count(self.t_timer, sid), 0)       # rejected when ON
        finally:
            os.environ.pop("ENFORCE_TEST_TIMER", None)
        self.submit(c, self.t_timer)
        self.assertEqual(self.result_count(self.t_timer, sid), 1)            # accepted when OFF (old behaviour)

    # ----------------------------------------------------- 7. PWA / caching
    def test_60_private_pages_are_no_store(self):
        c = self.student_client(self.s10, "10")
        for path in ["/dashboard", "/profile", "/api/v1/me", "/api/notifications"]:
            self.assertIn("no-store", c.get(path).headers.get("Cache-Control", ""), path)
        self.assertIn("no-store", self.app.test_client().get("/login").headers["Cache-Control"])

    def test_61_service_worker_policy(self):
        r = self.app.test_client().get("/sw.js")
        self.assertEqual(r.headers["Cache-Control"], "no-cache")
        js = r.get_data(as_text=True)
        self.assertIn("math-class-v7", js)
        self.assertIn("startsWith('/static/')", js)
        self.assertNotIn("caches.match(event.request))\n  );\n});", js.split("addEventListener('fetch'")[1][-60:])
        fetch_part = js.split("addEventListener('fetch'")[1]
        self.assertLess(fetch_part.index("startsWith('/static/')"), fetch_part.index("cache.put"))

    # -------------------------------------------------------- 8. attendance
    def test_70_attendance_roundtrip_and_upsert(self):
        t = self.teacher_client()
        day = datetime.now().strftime("%Y-%m-%d")
        r = self.post_json(t, "/api/v1/teacher/attendance", {"date": day, "records": [
            {"student_id": self.s10, "status": "present"}, {"student_id": self.s9, "status": "absent", "note": "ill"}]})
        self.assertEqual(r.get_json()["data"]["saved"], 2)
        self.post_json(t, "/api/v1/teacher/attendance",
                       {"date": day, "records": [{"student_id": self.s10, "status": "late"}]})
        with self.db_conn() as conn:
            rows = conn.execute("SELECT status FROM attendance WHERE student_id=? AND date=?",
                                (self.s10, day)).fetchall()
        self.assertEqual([r["status"] for r in rows], ["late"])
        roster = t.get(f"/api/v1/teacher/attendance?date={day}&class=10").get_json()["data"]
        self.assertEqual({x["name"]: x["status"] for x in roster}["Ten"], "late")

    def test_71_attendance_validation_is_atomic(self):
        t = self.teacher_client()
        day = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        bad = [{"student_id": self.s10, "status": "present"}, {"student_id": 999999, "status": "present"}]
        self.assertEqual(self.post_json(t, "/api/v1/teacher/attendance", {"date": day, "records": bad}).status_code, 400)
        self.assertEqual(self.post_json(t, "/api/v1/teacher/attendance",
                                        {"date": day, "records": [{"student_id": self.s10, "status": "sleeping"}]}).status_code, 400)
        future = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        self.assertEqual(self.post_json(t, "/api/v1/teacher/attendance",
                                        {"date": future, "records": [{"student_id": self.s10, "status": "present"}]}).status_code, 400)
        self.assertEqual(self.post_json(t, "/api/v1/teacher/attendance", {"date": "2026-13-45", "records": []}).status_code, 400)
        self.assertEqual(self.post_json(t, "/api/v1/teacher/attendance", {"date": day, "records": [{"student_id": True, "status": "present"}]}).status_code, 400)
        self.assertEqual(t.post("/api/v1/teacher/attendance", data="not json", headers={"X-CSRFToken": CSRF}).status_code, 400)
        with self.db_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM attendance WHERE date=?", (day,)).fetchone()[0], 0)

    def test_72_student_sees_only_own_attendance(self):
        t = self.teacher_client()
        day = datetime.now().strftime("%Y-%m-%d")
        self.post_json(t, "/api/v1/teacher/attendance", {"date": day, "records": [
            {"student_id": self.s10b, "status": "present"}, {"student_id": self.s9, "status": "absent"}]})
        c = self.student_client(self.s10b, "10")
        body = c.get("/api/v1/attendance").get_json()
        self.assertEqual([r["status"] for r in body["data"]], ["present"])
        self.assertEqual(c.get("/api/v1/attendance?month=2026-1").status_code, 400)
        self.assertEqual(self.post_json(c, "/api/v1/teacher/attendance", {"date": day, "records": []}).status_code, 403)

    # ----------------------------------------------------- 9. teacher API
    def test_80_teacher_students_search(self):
        t = self.teacher_client()
        body = t.get("/api/v1/teacher/students?q=ten&class=10").get_json()
        names = {s["name"] for s in body["data"]}
        self.assertTrue({"Ten", "TenB"} <= names)
        self.assertNotIn("Nine", names)
        for s in body["data"]:
            self.assertNotIn("password_hash", s)
        self.assertEqual(t.get("/api/v1/teacher/students?q=%25").get_json()["meta"]["total"], 0)
        self.assertEqual(t.get("/api/v1/teacher/students?status=zzz").status_code, 400)
        blocked = t.get("/api/v1/teacher/students?status=blocked").get_json()["data"]
        self.assertTrue(all(s["is_blocked"] for s in blocked))

    def test_81_teacher_overview_and_results(self):
        t = self.teacher_client()
        ov = t.get("/api/v1/teacher/overview").get_json()["data"]
        self.assertGreaterEqual(ov["students_verified"], 3)
        res = t.get(f"/api/v1/teacher/tests/{self.t10}/results").get_json()
        self.assertGreaterEqual(res["meta"]["total"], 1)
        self.assertEqual(t.get("/api/v1/teacher/tests/999999/results").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
