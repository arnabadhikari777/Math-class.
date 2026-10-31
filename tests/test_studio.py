# -*- coding: utf-8 -*-
"""Question Studio + image_store tests (Cloudinary mocked, no network).
Run: python -m unittest tests.test_studio -v"""
import importlib, io, os, shutil, sqlite3, sys, tempfile, unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRF = "tok"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class StoreConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "CLOUDINARY_URL"):
            os.environ.pop(k, None)
        sys.path.insert(0, ROOT)
        self.store = importlib.import_module("backend.services.image_store")

    def tearDown(self):
        self.env.stop(); sys.path.remove(ROOT)

    def test_missing(self):
        self.assertIn("সেট করা নেই", self.store.config_problem())

    def test_key_name_as_cloud_name_is_caught(self):
        os.environ.update({"CLOUDINARY_CLOUD_NAME": "Arnab Math Class", "CLOUDINARY_API_KEY": "123456789012345", "CLOUDINARY_API_SECRET": "s"})
        self.assertIn("Cloud name", self.store.config_problem())

    def test_good_and_url_form(self):
        os.environ.update({"CLOUDINARY_CLOUD_NAME": "k1hfcl8v", "CLOUDINARY_API_KEY": "123456789012345", "CLOUDINARY_API_SECRET": "s"})
        self.assertIsNone(self.store.config_problem())
        for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
            os.environ.pop(k)
        os.environ["CLOUDINARY_URL"] = "cloudinary://123456789012345:sec@k1hfcl8v"
        self.assertIsNone(self.store.config_problem())
        self.assertEqual(self.store.config()["cloud"], "k1hfcl8v")

    def test_friendly_errors(self):
        self.assertIn("Cloud name", self.store._friendly("Invalid cloud_name X"))
        self.assertIn("Secret", self.store._friendly("Invalid Signature abc"))

    def test_validate(self):
        with self.assertRaises(self.store.ImageStoreError):
            self.store.validate("a.png", b"not an image at all")
        self.assertEqual(self.store.validate("a.png", PNG), "png")


class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="studio-test-")
        cls.proj = os.path.join(cls.tmp, "p")
        shutil.copytree(ROOT, cls.proj, ignore=shutil.ignore_patterns("backups", ".secret_key", "__pycache__", ".env", "tests"))
        cls.db = os.path.join(cls.proj, "database.db")
        os.environ.update({"FORCE_HTTPS": "0", "SECRET_KEY": "x", "TEACHER_USERNAME": "sir", "TEACHER_PASSWORD": "TeacherPass123",
                           "CLOUDINARY_CLOUD_NAME": "demo", "CLOUDINARY_API_KEY": "123456789012345", "CLOUDINARY_API_SECRET": "s"})
        sys.path.insert(0, cls.proj)
        for n in [m for m in sys.modules if m == "app" or m == "backend" or m.startswith("backend.")]:
            del sys.modules[n]
        cls.app = importlib.import_module("app").app
        cls.app.config["TESTING"] = True
        c = sqlite3.connect(cls.db)
        now = datetime.now().isoformat()
        cls.tid = c.execute("INSERT INTO tests (title,subject,class_name,duration_minutes,is_active,created_at) VALUES ('T','M','TEN',30,1,?)", (now,)).lastrowid
        c.commit(); c.close()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.proj); shutil.rmtree(cls.tmp, ignore_errors=True)

    def teacher(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s["is_teacher"] = True; s["_csrf_token"] = CSRF
        return cl

    def q(self, sql, args=()):
        c = sqlite3.connect(self.db); c.row_factory = sqlite3.Row
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()

    def fake_upload(self, name):
        return {"url": "https://res.cloudinary.com/demo/image/upload/v1/arnab/%s" % name, "public_id": "arnab/" + name,
                "width": 5, "height": 5, "bytes": 72, "format": "png"}

    def upload(self, cl, name="a.png", **form):
        with mock.patch("backend.services.image_store.upload", side_effect=lambda f, d, category=None: self.fake_upload(f)):
            return cl.post("/teacher/studio/api/upload", data=dict(form, images=(io.BytesIO(PNG), name)),
                           headers={"X-CSRFToken": CSRF}, content_type="multipart/form-data")

    def save(self, cl, items, tid=None):
        return cl.post("/teacher/studio/tests/%d/questions/save" % (tid or self.tid), json={"questions": items},
                       headers={"X-CSRFToken": CSRF})

    def test_login_required(self):
        r = self.app.test_client().get("/teacher/studio/library")
        self.assertEqual(r.status_code, 302)
        r = self.app.test_client().get("/teacher/studio/api/images")
        self.assertEqual(r.status_code, 401)

    def test_old_route_redirects_to_studio(self):
        r = self.teacher().get("/teacher/tests/%d/questions" % self.tid)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/teacher/studio/tests/%d/questions" % self.tid, r.headers["Location"])

    def test_pages_render(self):
        cl = self.teacher()
        self.assertEqual(cl.get("/teacher/studio/library").status_code, 200)
        r = cl.get("/teacher/studio/tests/%d/questions" % self.tid)
        self.assertEqual(r.status_code, 200)
        self.assertIn("ImagePicker", r.get_data(as_text=True))

    def test_upload_new_category_then_picker_lists_it(self):
        cl = self.teacher()
        r = self.upload(cl, "calc1.png", new_category="Calculus")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertTrue(d["ok"]); self.assertEqual(d["category"]["name"], "Calculus")
        imgs = cl.get("/teacher/studio/api/images").get_json()["images"]
        self.assertTrue(any(i["name"] == "calc1.png" and i["category"] == "Calculus" for i in imgs))

    def test_upload_bad_cloud_reports_friendly_error(self):
        cl = self.teacher()
        with mock.patch.dict(os.environ, {"CLOUDINARY_CLOUD_NAME": "Arnab Math Class"}):
            r = cl.post("/teacher/studio/api/upload", data={"new_category": "X", "images": (io.BytesIO(PNG), "a.png")},
                        headers={"X-CSRFToken": CSRF}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Cloud name", " ".join(r.get_json()["errors"]))

    def test_save_question_with_image_text_and_image_only(self):
        cl = self.teacher()
        iid = self.upload(cl, "geo.png", new_category="Geometry").get_json()["images"][0]["id"]
        r = self.save(cl, [
            {"text": "চিত্রটি দেখে উত্তর দাও", "image_id": iid, "a": "1", "b": "2", "c": "3", "d": "4", "correct": "b", "marks": 2},
            {"text": "", "image_id": iid, "a": "x", "b": "y", "c": "z", "d": "w", "correct": "A", "marks": 1},
        ])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        rows = self.q("SELECT * FROM questions WHERE test_id=? AND bank_image_id=?", (self.tid, iid))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["image_url"].startswith("https://res.cloudinary.com") for r in rows))
        self.assertEqual(sorted(r["correct_option"] for r in rows), ["A", "B"])
        # student-facing page still shows the image URL via qimg / take_test column
        self.assertIn("res.cloudinary.com", self.q("SELECT image_url FROM questions WHERE bank_image_id=?", (iid,))[0][0])

    def test_save_is_atomic_and_reports_which_question(self):
        cl = self.teacher()
        before = self.q("SELECT COUNT(*) FROM questions")[0][0]
        r = self.save(cl, [
            {"text": "ok", "a": "1", "b": "2", "c": "3", "d": "4", "correct": "A"},
            {"text": "", "a": "1", "b": "2", "c": "3", "d": "4", "correct": "A"},      # no text, no image
            {"text": "q", "a": "1", "b": "", "c": "3", "d": "4", "correct": "A"},      # missing option
        ])
        self.assertEqual(r.status_code, 400)
        idx = [e["index"] for e in r.get_json()["errors"]]
        self.assertEqual(idx, [1, 2])
        self.assertEqual(self.q("SELECT COUNT(*) FROM questions")[0][0], before)  # nothing saved

    def test_missing_image_rejected(self):
        r = self.save(self.teacher(), [{"text": "t", "image_id": 99999, "a": "1", "b": "2", "c": "3", "d": "4", "correct": "A"}])
        self.assertEqual(r.status_code, 400)

    def test_set_and_remove_image_on_existing_question(self):
        cl = self.teacher()
        iid = self.upload(cl, "m.png", new_category="Matrix").get_json()["images"][0]["id"]
        self.save(cl, [{"text": "old q", "a": "1", "b": "2", "c": "3", "d": "4", "correct": "C"}])
        qid = self.q("SELECT id FROM questions WHERE question_text='old q'")[0][0]
        cl.post("/teacher/studio/questions/%d/image" % qid, data={"image_id": str(iid), "csrf_token": CSRF})
        self.assertEqual(self.q("SELECT bank_image_id FROM questions WHERE id=?", (qid,))[0][0], iid)
        cl.post("/teacher/studio/questions/%d/image" % qid, data={"image_id": "", "csrf_token": CSRF})
        self.assertIsNone(self.q("SELECT image_url FROM questions WHERE id=?", (qid,))[0][0])

    def test_used_image_cannot_be_deleted_unused_can(self):
        cl = self.teacher()
        used = self.upload(cl, "u.png", new_category="Used").get_json()["images"][0]["id"]
        free = self.upload(cl, "f.png", new_category="Used").get_json()["images"][0]["id"]
        self.save(cl, [{"text": "uses", "image_id": used, "a": "1", "b": "2", "c": "3", "d": "4", "correct": "A"}])
        with mock.patch("backend.services.image_store.destroy", return_value=True) as d:
            cl.post("/teacher/studio/library/images/%d/delete" % used, data={"csrf_token": CSRF})
            self.assertEqual(d.call_count, 0)
            self.assertEqual(len(self.q("SELECT 1 FROM bank_images WHERE id=?", (used,))), 1)
            cl.post("/teacher/studio/library/images/%d/delete" % free, data={"csrf_token": CSRF})
            self.assertEqual(d.call_count, 1)
            self.assertEqual(len(self.q("SELECT 1 FROM bank_images WHERE id=?", (free,))), 0)

    def test_csrf_required(self):
        cl = self.teacher()
        r = cl.post("/teacher/studio/tests/%d/questions/save" % self.tid, json={"questions": []})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
