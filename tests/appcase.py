# -*- coding: utf-8 -*-
"""Shared harness for the exam-workflow test files (not a test module itself)."""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import importlib, os, shutil, sqlite3, sys, tempfile, unittest  # noqa: E402,E401
from datetime import datetime  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRF = "csrf-test-token"


def q_item(text="2+2?", correct="A", marks=4):
    return {"text": text, "a": "4", "b": "3", "c": "5", "d": "6", "correct": correct, "marks": marks}


class AppCase(unittest.TestCase):
    """Boots a TEMPORARY copy of the project with a fresh migrated DB, one student (class TEN) and helpers."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mathclass-pub-")
        cls.proj = os.path.join(cls.tmp, "proj")
        shutil.copytree(ROOT, cls.proj, ignore=shutil.ignore_patterns("backups", ".secret_key", "__pycache__", ".env", "uploads"))
        cls.db = os.path.join(cls.proj, "database.db")
        if os.path.exists(cls.db):
            os.remove(cls.db)
        __import__("legacy").ensure_legacy_db(cls.db)
        cls.env_backup = dict(os.environ)
        os.environ.update({"FORCE_HTTPS": "0", "SECRET_KEY": "pub-secret", "TEACHER_USERNAME": "sir",
                           "TEACHER_PASSWORD": "TeacherPass123", "EMAIL_ADDRESS": "", "EMAIL_APP_PASSWORD": ""})
        sys.path.insert(0, cls.proj)
        for n in [m for m in sys.modules if m == "app" or m == "backend" or m.startswith("backend.")]:
            del sys.modules[n]
        cls.app = importlib.import_module("app").app
        cls.app.config["TESTING"] = True
        c = sqlite3.connect(cls.db)
        cls.stu = c.execute(
            "INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version)"
            " VALUES ('Sam','TEN','1','s@t.local','1',?,1,?,0,0)", (generate_password_hash("StudentPass123"), datetime.now().isoformat())).lastrowid
        c.commit(); c.close()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.proj)
        os.environ.clear(); os.environ.update(cls.env_backup)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def q(self, sql, *a):
        c = sqlite3.connect(self.db); c.row_factory = sqlite3.Row
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    def teacher(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(is_teacher=True, _csrf_token=CSRF)
        return cl

    def student(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(student_id=self.stu, student_name="Sam", student_class="TEN", sv=0, _csrf_token=CSRF)
        return cl

    def post(self, cl, url, data=None, **kw):
        return cl.post(url, data=data or {}, headers={"X-CSRFToken": CSRF}, **kw)

    def notifications(self):
        return self.q("SELECT * FROM notifications WHERE notif_type='test'")

    def new_test(self, title="Algebra 1", **extra):
        t = self.teacher()
        data = {"title": title, "subject": "Maths", "class_name": "10", "duration_minutes": "30", "negative_marks": "1"}
        data.update(extra)
        before = {r["id"] for r in self.q("SELECT id FROM tests")}
        self.post(t, "/teacher/tests/create", data)
        new = [r for r in self.q("SELECT * FROM tests") if r["id"] not in before]
        self.assertEqual(len(new), 1)
        return t, new[0]["id"]

    def add_questions(self, t, tid, items):
        r = t.post(f"/teacher/studio/tests/{tid}/questions/save", json={"questions": items}, headers={"X-CSRFToken": CSRF})
        return r


    def published_test(self, title="Exam", n=3, neg="1", **extra):
        """Create -> add n questions (all correct answer A, 4 marks) -> publish. Returns (teacher_client, test_id)."""
        t, tid = self.new_test(title, negative_marks=neg, **extra)
        self.add_questions(t, tid, [q_item(f"{title} Q{i}", "A", 4) for i in range(n)])
        self.post(t, f"/teacher/tests/{tid}/publish")
        assert self.q("SELECT status FROM tests WHERE id=?", tid)[0][0] == "published"
        return t, tid

    def attempts(self, tid, sid=None):
        return self.q("SELECT * FROM exam_attempts WHERE test_id=? AND student_id=? ORDER BY id", tid, sid or self.stu)

    def begin(self, cl, tid):
        """The real two-step student flow; returns the response of GET /exam."""
        assert self.post(cl, f"/test/{tid}/continue").status_code == 302
        assert self.post(cl, f"/test/{tid}/start").status_code == 302
        return cl.get(f"/test/{tid}/exam")
