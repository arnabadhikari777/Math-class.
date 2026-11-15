# -*- coding: utf-8 -*-
"""Doubt Box reliability: races, idempotency, failure paths, JSON mode, filters, image validation, ownership."""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import io, sqlite3, threading, unittest  # noqa: E402,E401
from datetime import datetime  # noqa: E402
from unittest import mock  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from appcase import AppCase, CSRF  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40
IMG = {"url": "https://res.cloudinary.com/demo/image/upload/v1/doubts/%s.png", "public_id": "arnab-math-class/doubts/%s",
       "width": 10, "height": 10, "bytes": 50, "format": "png"}
JSONH = {"X-Requested-With": "fetch", "Accept": "application/json", "X-CSRFToken": CSRF}


_ip = [0]


def ip():
    _ip[0] += 1
    return {"REMOTE_ADDR": "203.0.113.%d" % (_ip[0] % 250 + 1)}


def img(n):
    return {k: (v % n if isinstance(v, str) and "%s" in v else v) for k, v in IMG.items()}


class Reliability(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        c = sqlite3.connect(cls.db)
        cls.stu2 = c.execute(
            "INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version,username)"
            " VALUES ('Tina','TEN','2','t@t.local','1',?,1,?,0,0,'tina_r')", (generate_password_hash("StudentPass123"), datetime.now().isoformat())).lastrowid
        c.commit(); c.close()

    def other(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(student_id=self.stu2, student_name="Tina", student_class="TEN", sv=0, _csrf_token=CSRF)
        return cl

    def send(self, cl, token, files=None, headers=None, **over):
        data = {"subject": "Maths", "question": "How do I factor x^2-5x+6?", "token": token}
        data.update(over)
        if files:
            data["image"] = files
        return cl.post("/doubts/new", data=data, headers=headers or {"X-CSRFToken": CSRF}, content_type="multipart/form-data", environ_base=ip())

    def rows(self, token):
        return self.q("SELECT * FROM doubts WHERE client_token=?", token)

    # ------------------------------------------------------------ races / idempotency
    def test_01_real_thread_race_same_token_never_500_never_duplicates(self):
        for rnd in range(8):
            tok = "race-%d" % rnd
            codes, gate = [], threading.Barrier(4)

            def go():
                cl = self.student(); gate.wait()
                codes.append(self.send(cl, tok).status_code)
            ts = [threading.Thread(target=go) for _ in range(4)]
            [t.start() for t in ts]; [t.join() for t in ts]
            self.assertTrue(all(c == 302 for c in codes), (rnd, codes))
            self.assertEqual(len(self.rows(tok)), 1, rnd)
            self.assertEqual(len(self.q("SELECT 1 FROM teacher_notifications WHERE ref_key=?", "doubt-new:%d" % self.rows(tok)[0]["id"])), 1)

    def test_02_integrity_conflict_fallback_returns_winner(self):
        """The window between 'does it exist?' and INSERT: simulate it by hiding the first lookup."""
        from backend.services import doubt_service as ds
        from backend.db.connection import get_db
        with get_db() as c:
            first, created = ds.create_doubt(c, self.stu, "Maths", "", "", "first question", None, "win-1"); c.commit()
        self.assertTrue(created)
        real, calls = ds.find_by_token, []

        def blind(conn, sid, token):
            calls.append(1)
            return None if len(calls) == 1 else real(conn, sid, token)
        with mock.patch.object(ds, "find_by_token", blind), get_db() as c:
            did, created = ds.create_doubt(c, self.stu, "Maths", "", "", "second question", None, "win-1")
        self.assertEqual((did, created), (first, False))
        self.assertEqual(len(self.rows("win-1")), 1)

    def test_03_other_integrity_errors_are_not_swallowed(self):
        from backend.services import doubt_service as ds
        from backend.db.connection import get_db
        with get_db() as c, self.assertRaises(sqlite3.IntegrityError):
            ds.create_doubt(c, 999999, "Maths", "", "", "orphan", None, "fk-1")      # student does not exist (FK)

    def test_04_lost_race_discards_only_its_own_picture(self):
        gate = threading.Barrier(2, timeout=5)
        ups, destroyed = [], []

        def fake_upload(fn, data, cat=None):
            i = img("p%d" % (len(ups) + 1)); ups.append(i)
            try: gate.wait()
            except threading.BrokenBarrierError: pass
            return i
        codes = []

        def go():
            codes.append(self.send(self.student(), "pic-race", (io.BytesIO(PNG), "q.png")).status_code)
        with mock.patch("backend.services.image_store.upload", fake_upload), \
             mock.patch("backend.services.image_service.discard_orphan", lambda pid, why="": destroyed.append(pid)):
            ts = [threading.Thread(target=go) for _ in range(2)]
            [t.start() for t in ts]; [t.join() for t in ts]
        rows = self.rows("pic-race")
        self.assertEqual(len(rows), 1); self.assertEqual(codes, [302, 302])
        kept = rows[0]["image_public_id"]
        self.assertTrue(all(p != kept for p in destroyed), "the saved picture must never be deleted")
        self.assertEqual(len(ups) - len(destroyed), 1)

    # ------------------------------------------------------------ failure paths
    def test_10_cloudinary_failure_keeps_text_and_creates_nothing(self):
        from backend.services import image_store
        with mock.patch("backend.services.image_store.upload", side_effect=image_store.ImageStoreError("Could not connect to Cloudinary.")):
            r = self.send(self.student(), "cf-1", (io.BytesIO(PNG), "q.png"))
        self.assertEqual(r.status_code, 502)
        page = r.get_data(as_text=True)
        self.assertIn("How do I factor", page); self.assertIn('value="cf-1"', page)
        self.assertNotIn("Traceback", page)
        self.assertEqual(len(self.rows("cf-1")), 0)

    def test_11_db_failure_after_upload_deletes_orphan_and_hides_traceback(self):
        from backend.services import doubt_service as ds
        gone = []
        with mock.patch("backend.services.image_store.upload", return_value=img("o1")), \
             mock.patch.object(ds, "create_doubt", side_effect=RuntimeError("boom secret detail")), \
             mock.patch("backend.services.image_service.discard_orphan", lambda pid, why="": gone.append(pid)):
            r = self.send(self.student(), "dbf-1", (io.BytesIO(PNG), "q.png"))
        self.assertEqual(r.status_code, 500)
        page = r.get_data(as_text=True)
        self.assertNotIn("boom secret detail", page); self.assertNotIn("Traceback", page)
        self.assertRegex(page, r"tell Sir this code: [0-9A-F]{6}")
        self.assertIn("How do I factor", page)
        self.assertEqual(gone, ["arnab-math-class/doubts/o1"]); self.assertEqual(len(self.rows("dbf-1")), 0)

    def test_12_locked_database_is_503_and_retry_works(self):
        from backend.services import doubt_service as ds
        with mock.patch.object(ds, "submit_doubt", side_effect=sqlite3.OperationalError("database is locked")):
            r = self.send(self.student(), "lock-1")
        self.assertEqual(r.status_code, 503); self.assertIn("busy", r.get_data(as_text=True))
        self.assertEqual(self.send(self.student(), "lock-1").status_code, 302)
        self.assertEqual(len(self.rows("lock-1")), 1)

    def test_13_teacher_notification_failure_still_saves_the_doubt(self):
        from backend.services import doubt_service as ds
        with mock.patch.object(ds, "notify_teacher_new", side_effect=sqlite3.OperationalError("no such table")):
            r = self.send(self.student(), "nf-1")
        self.assertEqual(r.status_code, 302)
        row = self.rows("nf-1")[0]
        self.assertEqual(row["teacher_seen"], 0)                       # still listed as NEW for Sir
        self.assertEqual(len(self.q("SELECT 1 FROM teacher_notifications WHERE ref_key=?", "doubt-new:%d" % row["id"])), 0)

    def test_14_json_mode(self):
        cl = self.student()
        r = self.send(cl, "js-1", headers=JSONH)
        j = r.get_json(); self.assertTrue(j["ok"]); self.assertTrue(j["created"]); self.assertIn("/doubts/", j["redirect"])
        r = self.send(cl, "js-1", headers=JSONH)                       # retry after a dropped connection
        j2 = r.get_json(); self.assertTrue(j2["ok"]); self.assertFalse(j2["created"]); self.assertEqual(j2["id"], j["id"])
        r = self.send(cl, "js-2", headers=JSONH, question="x")
        self.assertEqual(r.status_code, 400); self.assertEqual(r.get_json()["field"], "question")
        r = self.send(cl, "js-3", (io.BytesIO(b"nope"), "a.png"), headers=JSONH)
        self.assertEqual(r.status_code, 400); self.assertEqual(r.get_json()["field"], "image")
        self.assertEqual(len(self.rows("js-3")), 0)

    # ------------------------------------------------------------ images
    def test_20_image_validation_content_first(self):
        from backend.services import image_store as st
        self.assertEqual(st.validate("camera", JPG), "jpeg")             # no extension: content decides
        self.assertEqual(st.validate("x.jfif", JPG), "jpeg")
        self.assertEqual(st.validate("renamed.png", JPG), "jpeg")
        with self.assertRaises(st.ImageStoreError): st.validate("a.png", b"GIF" + b"x" * 30)
        with self.assertRaises(st.ImageStoreError): st.validate("a.exe", PNG)
        with self.assertRaises(st.ImageStoreError) as e: st.validate("IMG_1.heic", HEIC)
        self.assertIn("HEIC", str(e.exception))
        with self.assertRaises(st.ImageStoreError): st.validate("a.png", b"")

    def test_21_size_limit_is_one_number_for_server_and_page(self):
        from backend.services import image_store as st
        with mock.patch.dict(_os.environ, {"BANK_IMAGE_MAX_MB": "2"}):
            self.assertEqual(st.max_mb(), 2)
            page = self.student().get("/doubts/new").get_data(as_text=True)
            self.assertIn('data-max-bytes="%d"' % (2 * 1048576), page); self.assertIn("up to 2 MB", page)
            with self.assertRaises(st.ImageStoreError) as e: st.validate("a.png", PNG + b"0" * (2 * 1048576))
            self.assertIn("2 MB", str(e.exception))

    def test_22_413_gives_a_student_message_not_a_teacher_page(self):
        r = self.student().post("/doubts/new", data={"subject": "Maths", "question": "x" * 10, "image": (io.BytesIO(b"0" * (56 * 1024 * 1024)), "a.png")},
                                headers=JSONH, content_type="multipart/form-data", environ_base=ip())
        self.assertEqual(r.status_code, 413); self.assertEqual(r.get_json()["field"], "image")

    # ------------------------------------------------------------ student list / ownership
    def test_30_search_status_filters_pagination_and_ownership(self):
        c = sqlite3.connect(self.db)
        for i in range(14):
            c.execute("INSERT INTO doubts (student_id,subject,chapter,question,created_at) VALUES (?,?,?,?,?)",
                      (self.stu2, "Physics", "Motion", "Tina question %d about velocity" % i, "2026-11-0%dT10:00:00" % (1 + i % 9)))
        c.execute("INSERT INTO doubts (student_id,subject,question,teacher_answer,teacher_answered_at,created_at) VALUES (?,?,?,?,?,?)",
                  (self.stu2, "Maths", "Answered one 100%_done", "ok", "2026-11-01T10:00:00", "2026-11-01T10:00:00"))
        c.commit(); c.close()
        t = self.other()
        p1 = t.get("/doubts").get_data(as_text=True)
        self.assertIn("Page 1 of 2", p1)
        self.assertIn("Page 2 of 2", t.get("/doubts?page=2&q=velocity").get_data(as_text=True))
        ans = t.get("/doubts?status=answered").get_data(as_text=True)
        self.assertIn("Answered one", ans); self.assertNotIn("Tina question", ans)
        self.assertIn("100%_done", t.get("/doubts?q=100%25_done").get_data(as_text=True))     # LIKE wildcards are literal
        self.assertNotIn("Tina question 1", t.get("/doubts?q=zzzzzz").get_data(as_text=True))
        self.assertIn("No doubts match your search", t.get("/doubts?q=zzzzzz").get_data(as_text=True))
        mine = self.student().get("/doubts?q=Tina").get_data(as_text=True)
        self.assertNotIn("Tina question", mine)                                                 # never another student's data
        did = self.q("SELECT id FROM doubts WHERE student_id=? LIMIT 1", self.stu2)[0]["id"]
        self.assertEqual(self.student().get("/doubts/%d" % did).status_code, 404)

    # ------------------------------------------------------------ teacher
    def test_40_teacher_answer_json_edit_no_duplicate_notifications_and_nav(self):
        r = self.send(self.student(), "t-1"); did = self.rows("t-1")[0]["id"]
        t = self.teacher()
        j = t.post("/teacher/doubts/%d" % did, data={"answer": "First answer"}, headers=JSONH).get_json()
        self.assertTrue(j["ok"]); self.assertTrue(j["notified"])
        j = t.post("/teacher/doubts/%d" % did, data={"answer": "Edited answer"}, headers=JSONH).get_json()
        self.assertTrue(j["ok"])
        n = self.q("SELECT * FROM notifications WHERE student_id=? AND link=?", self.stu, "/doubts/%d" % did)
        self.assertEqual(len(n), 1)                                                            # edit refreshes, never duplicates
        self.assertEqual(self.q("SELECT teacher_answer FROM doubts WHERE id=?", did)[0][0], "Edited answer")
        bad = t.post("/teacher/doubts/%d" % did, data={"answer": " "}, headers=JSONH)
        self.assertEqual(bad.status_code, 400)
        page = t.get("/teacher/doubts/%d" % did).get_data(as_text=True)
        self.assertIn("Edited answer", page); self.assertIn("Doubt navigation", page)
        self.assertEqual(self.student().get("/doubts/%d" % did).status_code, 200)
        self.assertIn("Edited answer", self.student().get("/doubts/%d" % did).get_data(as_text=True))

    def test_41_student_notification_failure_is_reported_to_teacher(self):
        self.send(self.student(), "t-2"); did = self.rows("t-2")[0]["id"]
        with mock.patch("backend.services.notification_service.notify_student", return_value=False):
            j = self.teacher().post("/teacher/doubts/%d" % did, data={"answer": "Answer text"}, headers=JSONH).get_json()
        self.assertTrue(j["ok"]); self.assertFalse(j["notified"]); self.assertIn("could not be created", j["message"])
        self.assertEqual(self.q("SELECT teacher_answer FROM doubts WHERE id=?", did)[0][0], "Answer text")

    def test_42_teacher_search_and_auth(self):
        self.send(self.student(), "t-3", question="Unique zebra question here")
        t = self.teacher()
        self.assertIn("Unique zebra", t.get("/teacher/doubts?q=zebra").get_data(as_text=True))
        self.assertIn("Sam", t.get("/teacher/doubts?q=Sam&status=pending").get_data(as_text=True))
        self.assertEqual(self.app.test_client().get("/teacher/doubts").status_code, 302)
        self.assertEqual(self.student().get("/teacher/doubts").status_code, 302)

    def test_50_header_has_account_menu_with_logout_for_student_and_teacher(self):
        s = self.student().get("/doubts").get_data(as_text=True)
        self.assertIn('data-account-trigger', s); self.assertEqual(s.count('action="/logout"'), 2)
        t = self.teacher().get("/teacher/doubts").get_data(as_text=True)
        self.assertIn('data-account-trigger', t); self.assertEqual(t.count('action="/teacher/logout"'), 2)

    def test_51_avatar_in_session_after_login(self):
        c = sqlite3.connect(self.db); c.execute("UPDATE students SET profile_image_url='https://res.cloudinary.com/demo/a.png' WHERE id=?", (self.stu,)); c.commit(); c.close()
        cl = self.app.test_client()
        with cl.session_transaction() as s: s["_csrf_token"] = CSRF
        cl.post("/login", data={"identifier": "s@t.local", "password": "StudentPass123"}, headers={"X-CSRFToken": CSRF}, environ_base={"REMOTE_ADDR": "198.51.100.77"})
        with cl.session_transaction() as s:
            self.assertEqual(s.get("student_avatar"), "https://res.cloudinary.com/demo/a.png")


if __name__ == "__main__":
    unittest.main()
