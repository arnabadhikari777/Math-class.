# -*- coding: utf-8 -*-
"""Question Bank + Image Library tests (Cloudinary is mocked; no network).
Run: python -m unittest tests.test_question_bank -v   (works on a temp COPY of the project)"""
import importlib, io, os, shutil, sqlite3, sys, tempfile, unittest
from datetime import datetime
from unittest import mock

from werkzeug.security import generate_password_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRF = "tok"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class QBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="qbank-test-")
        cls.proj = os.path.join(cls.tmp, "p")
        shutil.copytree(ROOT, cls.proj, ignore=shutil.ignore_patterns("backups", ".secret_key", "__pycache__", ".env", "tests"))
        cls.db = os.path.join(cls.proj, "database.db")
        os.environ.update({"FORCE_HTTPS": "0", "SECRET_KEY": "x", "TEACHER_USERNAME": "sir", "TEACHER_PASSWORD": "TeacherPass123",
                           "CLOUDINARY_CLOUD_NAME": "demo", "CLOUDINARY_API_KEY": "k", "CLOUDINARY_API_SECRET": "s"})
        sys.path.insert(0, cls.proj)
        for n in [m for m in sys.modules if m == "app" or m == "backend" or m.startswith("backend.")]:
            del sys.modules[n]
        cls.app = importlib.import_module("app").app
        cls.app.config["TESTING"] = True
        c = sqlite3.connect(cls.db)
        now = datetime.now().isoformat()
        cls.sid = c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,session_version) VALUES ('S','TEN','1','s@t.l','9','%s',1,'%s',0)" % (generate_password_hash("Passw0rd1"), now)).lastrowid
        cls.tid = c.execute("INSERT INTO tests (title,subject,class_name,duration_minutes,is_active,created_at) VALUES ('T','M','TEN',30,1,?)", (now,)).lastrowid
        c.execute("INSERT INTO questions (test_id,question_text,option_a,option_b,option_c,option_d,correct_option,marks) VALUES (?,?,?,?,?,?,?,?)", (cls.tid, "old", "a", "b", "c", "d", "A", 2))
        c.commit(); c.close()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.proj); shutil.rmtree(cls.tmp, ignore_errors=True)

    def teacher(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s.update(is_teacher=True, _csrf_token=CSRF)
        return c

    def student(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s.update(student_id=self.sid, student_name="S", student_class="TEN", sv=0, _csrf_token=CSRF)
        return c

    def q(self, sql, *a):
        c = sqlite3.connect(self.db); c.row_factory = sqlite3.Row
        try: return c.execute(sql, a).fetchall()
        finally: c.close()

    def post(self, c, url, data=None, **kw):
        d = dict(data or {}); d["csrf_token"] = CSRF
        return c.post(url, data=d, **kw)

    def fake_upload(self, name, data):
        return {"url": "https://res.cloudinary.com/demo/image/upload/v1/arnab/%s" % name, "public_id": "arnab/" + name,
                "width": 10, "height": 10, "bytes": len(data), "format": "png"}

    # ---------------------------------------------------------------
    def test_01_migration_safe(self):
        self.assertEqual(len(self.q("SELECT * FROM questions WHERE question_text='old'")), 1)
        cols = {r[1] for r in self.q("PRAGMA table_info(questions)")}
        self.assertTrue({"image_url", "bank_image_id"} <= cols)
        self.assertEqual(len(self.q("SELECT id FROM schema_migrations WHERE id='007_question_bank'")), 1)

    def test_02_access_control(self):
        for url in ("/teacher/bank/images", "/teacher/bank/questions", "/teacher/bank/questions/new"):
            self.assertEqual(self.app.test_client().get(url).status_code, 302)       # anonymous
            self.assertEqual(self.student().get(url).status_code, 302)               # student
        r = self.student().post("/teacher/bank/images/upload", data={"csrf_token": CSRF})
        self.assertEqual(r.status_code, 302)
        r = self.teacher().post("/teacher/bank/categories/create", data={"name": "x"})  # no CSRF
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(len(self.q("SELECT * FROM bank_categories WHERE name='x'")), 0)

    def test_03_full_flow(self):
        t = self.teacher()
        self.post(t, "/teacher/bank/categories/create", {"name": "Calculus"})
        self.post(t, "/teacher/bank/categories/create", {"name": "calculus"})  # duplicate (case-insens.)
        cat = self.q("SELECT id FROM bank_categories")[0]["id"]
        self.assertEqual(len(self.q("SELECT * FROM bank_categories")), 1)
        with mock.patch("backend.services.cloudinary_client.requests.post") as rp:
            rp.return_value = mock.Mock(status_code=200, json=lambda: {"secure_url": "https://res.cloudinary.com/demo/image/upload/v1/a.png", "public_id": "arnab/a", "width": 5, "height": 5, "bytes": 72, "format": "png"})
            self.post(t, "/teacher/bank/images/upload", {"category_id": cat, "images": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
            self.post(t, "/teacher/bank/images/upload", {"category_id": cat, "images": (io.BytesIO(b"<html>"), "evil.png")}, content_type="multipart/form-data")  # fake image
            self.post(t, "/teacher/bank/images/upload", {"category_id": cat, "images": (io.BytesIO(PNG), "a.exe")}, content_type="multipart/form-data")  # bad ext
            self.assertEqual(rp.call_count, 1)  # only the valid one reached Cloudinary
            sent = rp.call_args.kwargs["data"]
            self.assertNotIn("s", [v for k, v in sent.items() if k == "api_secret"])
            self.assertNotIn("api_secret", sent)
        img = self.q("SELECT * FROM bank_images")
        self.assertEqual(len(img), 1)
        iid = img[0]["id"]
        # question with image, and text-only question
        self.post(t, "/teacher/bank/questions/new", {"question_text": "Fig?", "image_id": iid, "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4", "correct_option": "B"})
        self.post(t, "/teacher/bank/questions/new", {"question_text": "Plain", "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4", "correct_option": "C"})
        r = self.post(t, "/teacher/bank/questions/new", {"question_text": "", "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4", "correct_option": "C"})
        self.assertEqual(r.status_code, 400)  # neither text nor image
        r = self.post(t, "/teacher/bank/questions/new", {"question_text": "x", "image_id": 9999, "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4", "correct_option": "C"})
        self.assertEqual(r.status_code, 400)
        qs = self.q("SELECT * FROM bank_questions ORDER BY id")
        self.assertEqual(len(qs), 2)
        # same library image reusable in a second question
        self.post(t, "/teacher/bank/questions/new", {"question_text": "Again", "image_id": iid, "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4", "correct_option": "A"})
        # import into test (multi-select)
        ids = [x["id"] for x in self.q("SELECT id FROM bank_questions ORDER BY id")]
        self.post(t, "/teacher/bank/import/%d" % self.tid, {"question_id": ids, "marks": "3"})
        rows = self.q("SELECT * FROM questions WHERE test_id=? ORDER BY id", self.tid)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["image_url"], None)
        self.assertEqual(rows[1]["image_url"], "https://res.cloudinary.com/demo/image/upload/v1/a.png")
        self.assertEqual(rows[1]["marks"], 3)
        # student sees text + image; submit scoring still works
        sc = self.student()
        html = sc.get("/test/%d" % self.tid).get_data(as_text=True)
        self.assertIn("Fig?", html); self.assertIn("a.png", html)
        form = {"csrf_token": CSRF}
        for r_ in rows:
            form["q_%d" % r_["id"]] = r_["correct_option"]
        sc.post("/test/%d/submit" % self.tid, data=form)
        res = self.q("SELECT * FROM results WHERE test_id=?", self.tid)[0]
        self.assertEqual((res["score"], res["total_marks"]), (11, 11))
        self.assertIn("a.png", sc.get("/test/%d/result" % self.tid).get_data(as_text=True))
        self.assertIn("a.png", t.get("/teacher/tests/%d/result/%d" % (self.tid, res["id"])).get_data(as_text=True))
        # editing bank question does not touch the test copy
        bid = ids[0]
        self.post(t, "/teacher/bank/questions/%d/edit" % bid, {"question_text": "CHANGED", "image_id": iid, "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4", "correct_option": "B"})
        self.assertEqual(self.q("SELECT question_text FROM questions WHERE id=?", rows[1]["id"])[0][0], "Fig?")
        # image in use -> delete blocked, Cloudinary not called
        with mock.patch("backend.services.cloudinary_client.requests.post") as rp:
            self.post(t, "/teacher/bank/images/%d/delete" % iid)
            self.assertEqual(rp.call_count, 0)
        self.assertEqual(len(self.q("SELECT * FROM bank_images")), 1)
        # delete bank questions: image + test copies survive
        for i in ids:
            self.post(t, "/teacher/bank/questions/%d/delete" % i)
        self.assertEqual(len(self.q("SELECT * FROM bank_questions")), 0)
        self.assertEqual(len(self.q("SELECT * FROM bank_images")), 1)
        self.assertEqual(len(self.q("SELECT * FROM questions WHERE test_id=?", self.tid)), 4)
        # test copies still reference it -> still blocked
        self.post(t, "/teacher/bank/images/%d/delete" % iid)
        self.assertEqual(len(self.q("SELECT * FROM bank_images")), 1)
        # category with images cannot be deleted
        self.post(t, "/teacher/bank/categories/%d/delete" % cat)
        self.assertEqual(len(self.q("SELECT * FROM bank_categories")), 1)
        # unused image deletes fine (and Cloudinary failure keeps the record)
        c = sqlite3.connect(self.db); c.execute("DELETE FROM questions WHERE bank_image_id=?", (iid,)); c.commit(); c.close()
        import requests
        with mock.patch("backend.services.cloudinary_client.requests.post", side_effect=requests.ConnectionError()):
            self.post(t, "/teacher/bank/images/%d/delete" % iid)
        self.assertEqual(len(self.q("SELECT * FROM bank_images")), 1)
        with mock.patch("backend.services.cloudinary_client.requests.post") as rp:
            rp.return_value = mock.Mock(status_code=200, json=lambda: {"result": "ok"})
            self.post(t, "/teacher/bank/images/%d/delete" % iid)
        self.assertEqual(len(self.q("SELECT * FROM bank_images")), 0)

    def test_04_cloudinary_signature_and_missing_config(self):
        from backend.services import cloudinary_client as cdn
        import hashlib
        self.assertEqual(cdn._sign({"timestamp": "1", "folder": "f"}, "S"), hashlib.sha1(b"folder=f&timestamp=1S").hexdigest())
        with mock.patch.dict(os.environ, {"CLOUDINARY_API_SECRET": ""}):
            with self.assertRaises(cdn.CloudinaryError):
                cdn.upload_image("a.png", PNG)
        with self.assertRaises(cdn.CloudinaryError):
            cdn.upload_image("a.png", PNG + b"0" * (6 * 1024 * 1024))  # over 5 MB


if __name__ == "__main__":
    unittest.main()
