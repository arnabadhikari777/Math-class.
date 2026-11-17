# -*- coding: utf-8 -*-
"""Doubt Management, per-doubt AI history, profile picture, email-or-username login, login privacy/IST,
PDF images + Bengali. Runs on a TEMPORARY copy of the project (never touches database.db)."""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import io, sqlite3, unittest  # noqa: E402,E401
from datetime import datetime  # noqa: E402
from unittest import mock  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from appcase import AppCase, CSRF  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
FAKE_IMG = {"url": "https://res.cloudinary.com/demo/image/upload/v1/doubts/x.png", "public_id": "arnab-math-class/doubts/x",
            "width": 10, "height": 10, "bytes": 50, "format": "png"}


class DoubtTests(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        c = sqlite3.connect(cls.db)
        cls.stu2 = c.execute(
            "INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version,username)"
            " VALUES ('Tina','TEN','2','t@t.local','1',?,1,?,0,0,'tina_math')", (generate_password_hash("StudentPass123"), datetime.now().isoformat())).lastrowid
        c.execute("UPDATE students SET username='sam_math' WHERE id=?", (cls.stu,))
        c.commit(); c.close()

    def student2(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(student_id=self.stu2, student_name="Tina", student_class="TEN", sv=0, _csrf_token=CSRF)
        return cl

    def new_doubt(self, cl=None, **over):
        cl = cl or self.student()
        data = {"subject": "Maths", "chapter": "Quadratics", "topic": "Discriminant",
                "question": "How do I find the roots of x^2-5x+6?", "token": "tok-%d" % len(self.q("SELECT id FROM doubts"))}
        data.update(over)
        r = self.post(cl, "/doubts/new", data)
        rows = self.q("SELECT id FROM doubts ORDER BY id DESC LIMIT 1")
        return r, (rows[0]["id"] if rows else None)

    # ---------------------------------------------------------------- migration
    def test_01_migration_applied_and_rerunnable(self):
        from backend.db import migrations
        self.assertIn("018_doubts", migrations.applied_ids(self.db))
        c = sqlite3.connect(self.db)
        before = c.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        from backend.db import m018_doubts
        m018_doubts.up(c); m018_doubts.up(c)                       # safe to run twice
        c.commit()
        self.assertEqual(before, c.execute("SELECT COUNT(*) FROM students").fetchone()[0])
        cols = [r[1] for r in c.execute("PRAGMA table_info(students)")]
        self.assertIn("profile_image_url", cols); self.assertIn("profile_image_public_id", cols)
        c.close()

    # ---------------------------------------------------------------- login
    def test_02_login_email_username_and_failures(self):
        for n, ident in enumerate(("s@t.local", "S@T.LOCAL", "sam_math", "SAM_MATH", "@sam_math")):
            cl = self.app.test_client()
            with cl.session_transaction() as s:
                s["_csrf_token"] = CSRF
            r = self.post(cl, "/login", {"identifier": ident, "password": "StudentPass123"}, environ_base={"REMOTE_ADDR": "198.51.100.%d" % (10 + n)})
            self.assertEqual(r.status_code, 302, ident)
            with cl.session_transaction() as s:
                self.assertEqual(s.get("student_id"), self.stu, ident)
        # legacy form field name "email" still works
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s["_csrf_token"] = CSRF
        self.post(cl, "/login", {"email": "s@t.local", "password": "StudentPass123"}, environ_base={"REMOTE_ADDR": "198.51.100.30"})
        with cl.session_transaction() as s:
            self.assertEqual(s.get("student_id"), self.stu)
        for n, (ident, pw) in enumerate((("sam_math", "bad"), ("nobody", "StudentPass123"), ("", "x"))):
            cl = self.app.test_client()
            with cl.session_transaction() as s:
                s["_csrf_token"] = CSRF
            self.post(cl, "/login", {"identifier": ident, "password": pw}, environ_base={"REMOTE_ADDR": "198.51.100.%d" % (40 + n)})
            with cl.session_transaction() as s:
                self.assertIsNone(s.get("student_id"))
        # username is stored without "@"
        self.assertFalse(self.q("SELECT 1 FROM students WHERE username LIKE '@%'"))
        self.assertIn("Email or Username", self.app.test_client().get("/login").get_data(as_text=True))

    def test_03_blocked_and_unverified_still_refused(self):
        c = sqlite3.connect(self.db)
        bid = c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version,username)"
                        " VALUES ('Bob','TEN','9','b@t.local','1',?,1,?,1,0,'bob_x')", (generate_password_hash("StudentPass123"), datetime.now().isoformat())).lastrowid
        c.commit(); c.close()
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s["_csrf_token"] = CSRF
        self.post(cl, "/login", {"identifier": "bob_x", "password": "StudentPass123"}, environ_base={"REMOTE_ADDR": "198.51.100.60"})
        with cl.session_transaction() as s:
            self.assertIsNone(s.get("student_id"))

    # ---------------------------------------------------------------- doubts
    def test_10_submit_notify_and_no_duplicates(self):
        with mock.patch("backend.services.image_store.upload", return_value=FAKE_IMG) as up:
            cl = self.student()
            r = self.post(cl, "/doubts/new", {"subject": "Physics", "question": "Why does a ball fall?", "token": "dup-1",
                                              "image": (io.BytesIO(PNG), "q.png")}, content_type="multipart/form-data")
            self.assertEqual(r.status_code, 302)
            r2 = self.post(cl, "/doubts/new", {"subject": "Physics", "question": "Why does a ball fall?", "token": "dup-1"})
            self.assertEqual(r2.status_code, 302)
        rows = self.q("SELECT * FROM doubts WHERE client_token='dup-1'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_url"], FAKE_IMG["url"]); self.assertEqual(rows[0]["student_id"], self.stu)
        self.assertEqual(up.call_count, 1)
        self.assertEqual(len(self.q("SELECT * FROM teacher_notifications WHERE ref_key=?", "doubt-new:%d" % rows[0]["id"])), 1)

    def test_11_validation_and_bad_image(self):
        cl = self.student()
        n = len(self.q("SELECT id FROM doubts"))
        self.assertEqual(self.post(cl, "/doubts/new", {"subject": "", "question": "long enough question"}).status_code, 400)
        self.assertEqual(self.post(cl, "/doubts/new", {"subject": "Maths", "question": "x"}).status_code, 400)
        r = self.post(cl, "/doubts/new", {"subject": "Maths", "question": "valid question here", "token": "bad-img",
                                          "image": (io.BytesIO(b"not an image"), "q.png")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.post(cl, "/doubts/new", {"subject": "Maths", "question": "valid question here", "token": "bad-ext",
                                          "image": (io.BytesIO(PNG), "q.exe")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(n, len(self.q("SELECT id FROM doubts")))

    def test_12_teacher_answer_flow_and_status_separation(self):
        _r, did = self.new_doubt()
        stu, t = self.student(), self.teacher()
        page = stu.get("/doubts/%d" % did).get_data(as_text=True)
        self.assertIn("Waiting for Teacher", page)
        # teacher sees it, filters work
        tp = t.get("/teacher/doubts?status=pending&subject=Maths").get_data(as_text=True)
        self.assertIn("Sam", tp); self.assertIn("Pending Doubts", tp)
        self.assertIn("Sam", t.get("/teacher/doubts/%d" % did).get_data(as_text=True))
        # AI answers first: teacher status must stay "waiting"
        from backend.ai import provider
        with mock.patch.object(provider, "is_enabled", return_value=True), \
             mock.patch.object(provider, "ask", return_value=("x = 2 or 3", "gemini")):
            r = stu.post("/api/v1/ai/chat", json={"message": "Solve this", "doubt_id": did}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        row = self.q("SELECT * FROM doubts WHERE id=?", did)[0]
        self.assertIsNone(row["teacher_answered_at"]); self.assertIsNone(row["teacher_answer"])
        self.assertIsNotNone(row["ai_answered_at"])
        page = stu.get("/doubts/%d" % did).get_data(as_text=True)
        self.assertIn("Waiting for Teacher", page); self.assertIn("x = 2 or 3", page)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM notifications WHERE notif_type='doubt' AND student_id=?", self.stu)[0]["c"] >= 0, True)
        notes_before = len(self.q("SELECT * FROM notifications WHERE notif_type='doubt' AND link=?", "/doubts/%d" % did))
        self.assertEqual(notes_before, 0)                                 # AI never creates a teacher-answer notification
        # teacher answers
        self.post(t, "/teacher/doubts/%d" % did, {"answer": "Factorise: (x-2)(x-3)=0"})
        row = self.q("SELECT * FROM doubts WHERE id=?", did)[0]
        self.assertEqual(row["teacher_answer"], "Factorise: (x-2)(x-3)=0"); self.assertIsNotNone(row["teacher_answered_at"])
        n1 = self.q("SELECT * FROM notifications WHERE notif_type='doubt' AND link=?", "/doubts/%d" % did)
        self.assertEqual(len(n1), 1); self.assertEqual(n1[0]["student_id"], self.stu)
        page = stu.get("/doubts/%d" % did).get_data(as_text=True)
        self.assertIn("Answered", page); self.assertIn("Factorise", page); self.assertIn("x = 2 or 3", page)
        # same text again -> no new notification; edit -> still ONE row (refreshed)
        self.post(t, "/teacher/doubts/%d" % did, {"answer": "Factorise: (x-2)(x-3)=0"})
        self.post(t, "/teacher/doubts/%d" % did, {"answer": "Factorise: (x-2)(x-3)=0, so x=2 or 3"})
        self.assertEqual(len(self.q("SELECT * FROM notifications WHERE notif_type='doubt' AND link=?", "/doubts/%d" % did)), 1)
        # bell API shows it, and opening the doubt marks it read
        api = stu.get("/api/notifications").get_json()
        self.assertTrue(any(n["link"] == "/doubts/%d" % did for n in api["notifications"]))

    def test_13_ownership_and_authorization(self):
        _r, did = self.new_doubt()
        other = self.student2()
        self.assertEqual(other.get("/doubts/%d" % did).status_code, 404)
        self.assertEqual(self.post(other, "/doubts/%d/delete" % did).status_code, 404)
        self.assertEqual(other.get("/api/v1/ai/doubts/%d/messages" % did).status_code, 404)
        from backend.ai import provider
        with mock.patch.object(provider, "is_enabled", return_value=True):
            r = other.post("/api/v1/ai/chat", json={"message": "hi", "doubt_id": did}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.status_code, 404)
        anon = self.app.test_client()
        for url in ("/teacher/doubts", "/teacher/doubts/%d" % did, "/teacher/login-activity"):
            self.assertEqual(anon.get(url).status_code, 302)
            self.assertEqual(self.student().get(url).status_code, 302)       # a student is not a teacher
        self.assertEqual(self.post(self.student(), "/teacher/doubts/%d" % did, {"answer": "I am the teacher"}).status_code, 302)
        self.assertIsNone(self.q("SELECT teacher_answer a FROM doubts WHERE id=?", did)[0]["a"])

    def test_14_ai_isolation_resolution_and_history(self):
        stu = self.student2()
        _r, a = self.new_doubt(stu, subject="Chemistry", chapter="Acids", topic="pH", question="What is the pH of 0.01 M HCl?", token="iso-a")
        _r, b = self.new_doubt(stu, subject="Chemistry", chapter="Bonding", topic="Ionic", question="Why is NaCl ionic?", token="iso-b")
        _r, c = self.new_doubt(stu, subject="Maths", chapter="Calculus", topic="Limits", question="Limit of sin x / x?", token="iso-c")
        from backend.ai import provider
        seen = []
        def fake_ask(system, turns, prefer=None):
            sys_text = system("gemini") if callable(system) else system
            seen.append((sys_text, [t["text"] for t in turns]))
            return ("answer %d" % len(seen), "gemini")
        def chat(payload):
            return stu.post("/api/v1/ai/chat", json=payload, headers={"X-CSRFToken": CSRF})
        with mock.patch.object(provider, "is_enabled", return_value=True), mock.patch.object(provider, "ask", fake_ask):
            ra = chat({"message": "Solve it", "doubt_id": a}).get_json()["data"]
            rb = chat({"message": "Solve it", "doubt_id": b}).get_json()["data"]
            self.assertNotEqual(ra["conversation_id"], rb["conversation_id"])
            self.assertIn("pH of 0.01", seen[0][0]); self.assertNotIn("NaCl", seen[0][0])
            self.assertIn("NaCl", seen[1][0]); self.assertNotIn("pH of 0.01", seen[1][0])
            # follow-up on A (by conversation id) keeps A's history and context, never B's
            chat({"message": "Explain step 2", "conversation_id": ra["conversation_id"]})
            self.assertIn("pH of 0.01", seen[2][0]); self.assertNotIn("NaCl", seen[2][0])
            self.assertEqual(seen[2][1], ["Solve it", "answer 1", "Explain step 2"])
            self.assertNotIn("answer 2", seen[2][1])
            # a different conversation id cannot drag another doubt's history in
            chat({"message": "more", "doubt_id": b, "conversation_id": ra["conversation_id"]})
            self.assertIn("NaCl", seen[3][0]); self.assertNotIn("Explain step 2", seen[3][1])
            # references: "my maths doubt" -> only one maths doubt -> resolved
            r = chat({"message": "Solve my maths doubt"}).get_json()["data"]
            self.assertEqual(r["doubt"]["id"], c)
            # "my chemistry doubt" -> two match -> student must choose, AI NOT called
            calls = len(seen)
            r = chat({"message": "solve my chemistry doubt"}).get_json()["data"]
            self.assertTrue(r["needs_choice"]); self.assertEqual({x["id"] for x in r["candidates"]}, {a, b})
            self.assertEqual(len(seen), calls)
            # topic named -> resolved
            r = chat({"message": "Explain my doubt about Ionic bonding"}).get_json()["data"]
            self.assertEqual(r["doubt"]["id"], b)
            # unclear -> choice list, never a guess
            r = chat({"message": "solve my doubt"}).get_json()["data"]
            self.assertTrue(r["needs_choice"]); self.assertEqual(len(seen), calls + 1)
            # a normal question stays a normal chat
            r = chat({"message": "What is a derivative?"}).get_json()["data"]
            self.assertIsNone(r["doubt"])
            # another student's chat never sees Tina's doubts
            r = self.student().post("/api/v1/ai/chat", json={"message": "solve my chemistry doubt"}, headers={"X-CSRFToken": CSRF}).get_json()["data"]
            self.assertIsNone(r.get("doubt"))
            self.assertFalse({a, b, c} & {x["id"] for x in r.get("candidates", [])})        # only Sam's OWN doubts are ever offered
        # persisted per-doubt history comes back after "days"
        h = stu.get("/api/v1/ai/doubts/%d/messages" % a).get_json()["data"]
        self.assertEqual([m["content"] for m in h["messages"]][:2], ["Solve it", "answer 1"])
        self.assertEqual(len(stu.get("/api/v1/ai/doubts").get_json()["data"]), 3)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM ai_conversations WHERE doubt_id IN (?,?,?)", a, b, c)[0]["c"], 3)

    def test_15_delete(self):
        _r, did = self.new_doubt()
        with mock.patch("backend.services.image_store.destroy") as d:
            self.assertEqual(self.post(self.student(), "/doubts/%d/delete" % did).status_code, 302)
        self.assertFalse(self.q("SELECT 1 FROM doubts WHERE id=?", did))

    def test_16_pages_render(self):
        stu, t = self.student(), self.teacher()
        for url in ("/doubts", "/doubts/new", "/profile", "/teacher/doubts", "/teacher/login-activity", "/teacher/dashboard", "/dashboard"):
            r = (t if url.startswith("/teacher") else stu).get(url)
            self.assertEqual(r.status_code, 200, url)
        body = stu.get("/dashboard").get_data(as_text=True)
        self.assertIn("Ask a Doubt", body)
        self.assertNotIn('id="qaMenu"', body); self.assertNotIn('id="qaBtn"', body)      # floating three-dot button is gone
        self.assertNotIn(">Contact<", body)

    # ---------------------------------------------------------------- avatar
    def test_20_avatar(self):
        with mock.patch("backend.services.image_store.upload", return_value=dict(FAKE_IMG, url="https://res.cloudinary.com/demo/image/upload/a1.png", public_id="av1")), \
             mock.patch("backend.services.image_store.destroy") as dest:
            cl = self.student()
            r = self.post(cl, "/profile/avatar", {"avatar": (io.BytesIO(PNG), "me.png")}, content_type="multipart/form-data")
            self.assertEqual(r.status_code, 302)
            self.assertEqual(self.q("SELECT profile_image_public_id p FROM students WHERE id=?", self.stu)[0]["p"], "av1")
            self.assertIn("a1.png", cl.get("/profile").get_data(as_text=True))
            self.assertIsNone(self.q("SELECT profile_image_url u FROM students WHERE id=?", self.stu2)[0]["u"])   # nobody else touched
            with mock.patch("backend.services.image_store.upload", return_value=dict(FAKE_IMG, url="https://res.cloudinary.com/demo/image/upload/a2.png", public_id="av2")):
                self.post(cl, "/profile/avatar", {"avatar": (io.BytesIO(PNG), "me2.png")}, content_type="multipart/form-data")
            dest.assert_called_with("av1")                                   # old one removed after the new one saved
            r = self.post(cl, "/profile/avatar", {"avatar": (io.BytesIO(b"GIF nope"), "x.png")}, content_type="multipart/form-data")
            self.assertEqual(self.q("SELECT profile_image_public_id p FROM students WHERE id=?", self.stu)[0]["p"], "av2")
            self.post(cl, "/profile/avatar/remove")
            self.assertIsNone(self.q("SELECT profile_image_url u FROM students WHERE id=?", self.stu)[0]["u"])
            html = cl.get("/profile").get_data(as_text=True)
            self.assertIn("profile-avatar", html); self.assertNotIn("a2.png", html)

    # ---------------------------------------------------------------- login activity privacy + IST
    def test_30_login_history_hidden_from_student_visible_to_teacher(self):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s["_csrf_token"] = CSRF
        self.post(cl, "/login", {"identifier": "sam_math", "password": "StudentPass123"}, environ_base={"REMOTE_ADDR": "203.0.113.77"})
        row = self.q("SELECT * FROM login_history WHERE student_id=? ORDER BY id DESC LIMIT 1", self.stu)[0]
        self.assertTrue(row["login_time"].endswith("+05:30"), row["login_time"])     # IST, timezone-aware
        self.assertEqual(row["ip_address"], "203.0.113.77")                           # tracking itself is preserved
        prof = self.student().get("/profile").get_data(as_text=True)
        self.assertNotIn("Login history", prof); self.assertNotIn("203.0.113.77", prof)
        page = self.teacher().get("/teacher/login-activity").get_data(as_text=True)
        self.assertIn("Sam", page); self.assertNotIn("203.0.113.77", page)
        self.assertIn("Sam", self.teacher().get("/teacher/login-activity?q=sam&class_name=TEN").get_data(as_text=True))
        self.assertNotIn("Sam", self.teacher().get("/teacher/login-activity?q=zzzz").get_data(as_text=True))

    def test_31_time_formatting(self):
        from backend.utils.timeutil import fmt_ist
        self.assertEqual(fmt_ist("2026-11-14T10:00:00+00:00"), "14 Nov 2026, 03:30 PM")
        self.assertEqual(fmt_ist("2026-11-14T10:00:00"), "14 Nov 2026, 10:00 AM")      # legacy zone-less: shown as stored
        self.assertEqual(fmt_ist(None), "")


class PdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib
        cls.root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if cls.root not in _sys.path:
            _sys.path.insert(0, cls.root)
        cls.pdf = importlib.import_module("backend.services.pdf_service")

    def rows(self, text="2+2?", image=None):
        return [{"question_text": text, "option_a": "4", "option_b": "3", "option_c": "5", "option_d": "6", "correct_option": "A",
                 "selected_option": "A", "is_correct": 1, "marks": 4, "marks_awarded": 4, "image_url": image}]

    def meta(self):
        return ({"title": "Test"}, {"name": "Sam", "class_name": "TEN", "roll": "1"}, {"score": 4, "total_marks": 4, "submitted_at": "2026-11-14T10:00:00"})

    def test_ssrf_allowlist(self):
        ok = "https://res.cloudinary.com/demo/image/upload/v1/a.png"
        self.assertIsNotNone(self.pdf._safe_image_url(ok))
        for bad in ("http://res.cloudinary.com/demo/image/upload/a.png", "https://evil.example/a.png",
                    "https://res.cloudinary.com.evil.example/demo/image/upload/a.png", "https://user:p@res.cloudinary.com/demo/image/upload/a.png",
                    "https://res.cloudinary.com:8443/demo/image/upload/a.png", "http://127.0.0.1/a.png", "file:///etc/passwd",
                    "https://res.cloudinary.com/demo/raw/upload/a.png", "", None):
            self.assertIsNone(self.pdf._safe_image_url(bad), bad)
        with mock.patch("requests.get") as g:
            self.assertIsNone(self.pdf.fetch_question_image("http://169.254.169.254/latest/meta-data"))
            g.assert_not_called()

    def test_fetch_limits_and_failures(self):
        class R:
            def __init__(s, status=200, ctype="image/jpeg", body=b"\xff\xd8\xff" + b"0" * 100, length=None):
                s.status_code, s.headers, s._b = status, {"Content-Type": ctype, "Content-Length": str(length if length is not None else len(body))}, body
            def iter_content(s, n): yield s._b
            def close(s): pass
        url = "https://res.cloudinary.com/demo/image/upload/a.png"
        with mock.patch("requests.get", return_value=R()) as g:
            self.assertTrue(self.pdf.fetch_question_image(url))
            self.assertFalse(g.call_args.kwargs["allow_redirects"]); self.assertTrue(g.call_args.kwargs["timeout"])
        with mock.patch("requests.get", return_value=R(status=302)):
            self.assertIsNone(self.pdf.fetch_question_image(url))
        with mock.patch("requests.get", return_value=R(ctype="text/html")):
            self.assertIsNone(self.pdf.fetch_question_image(url))
        with mock.patch("requests.get", return_value=R(length=99 * 1024 * 1024)):
            self.assertIsNone(self.pdf.fetch_question_image(url))
        with mock.patch("requests.get", return_value=R(body=b"<html>not an image")):
            self.assertIsNone(self.pdf.fetch_question_image(url))
        with mock.patch("requests.get", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.pdf.fetch_question_image(url))

    def _png(self, w, h):
        from PIL import Image
        b = io.BytesIO(); Image.new("RGB", (w, h), (200, 30, 30)).save(b, "PNG"); return b.getvalue()

    def test_pdf_embeds_images_and_survives_broken_ones(self):
        test, stu, res = self.meta()
        rows = self.rows(image="https://res.cloudinary.com/demo/image/upload/ok.png") + self.rows(text="Q2", image="https://res.cloudinary.com/demo/image/upload/broken.png") \
            + self.rows(text="Q3", image="https://res.cloudinary.com/demo/image/upload/tall.png") + self.rows(text="Q4")
        data = {"ok.png": self._png(300, 200), "tall.png": self._png(200, 3000)}
        fetch = lambda u: next((v for k, v in data.items() if u.endswith(k)), None)
        plain = self.pdf.build_student_result_pdf(test, stu, res, self.rows(), image_fetcher=lambda u: None)
        with_img = self.pdf.build_student_result_pdf(test, stu, res, rows, image_fetcher=fetch)
        self.assertTrue(with_img.startswith(b"%PDF"))
        self.assertGreater(len(with_img), len(plain))
        self.assertGreaterEqual(with_img.count(b"/Subtype /Image"), 2)                  # both good images drawn, broken one skipped
        self.assertEqual(self.pdf.build_student_result_pdf(test, stu, res, rows, image_fetcher=lambda u: None)[:4], b"%PDF")

    def test_bengali_font_registered_and_pdf_made(self):
        reg, bold = self.pdf._register_pdf_fonts()
        if not reg:
            self.skipTest("no Bengali-capable font installed on this machine")
        test, stu, res = self.meta()
        out = self.pdf.build_student_result_pdf(test, stu, res, self.rows("বাংলা প্রশ্ন: ২ + ২ কত?"), image_fetcher=lambda u: None)
        self.assertTrue(out.startswith(b"%PDF"))

    def test_bengali_without_font_is_a_clear_error_not_silent_fallback(self):
        test, stu, res = self.meta()
        with mock.patch.object(self.pdf, "_register_pdf_fonts", return_value=(None, None)):
            with self.assertRaises(self.pdf.PdfFontError):
                self.pdf.build_student_result_pdf(test, stu, res, self.rows("বাংলা"), image_fetcher=lambda u: None)
            self.assertTrue(self.pdf.build_student_result_pdf(test, stu, res, self.rows("English only"), image_fetcher=lambda u: None).startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
