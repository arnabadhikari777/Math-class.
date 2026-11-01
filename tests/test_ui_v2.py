# -*- coding: utf-8 -*-
"""Tests for the v2 hardening + redesign work (CSP nonces, fail-closed checks, dashboards, Question Studio,
chat widget files). Runs on a TEMPORARY copy of the project; the real database is never touched.
Run: python -m unittest tests.test_ui_v2 -v   (or: python -m unittest discover -s tests)"""
import sys as _sys, os as _os  # noqa: E402,E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import os, re, sqlite3, unittest  # noqa: E402,E401
from unittest import mock  # noqa: E402

from appcase import AppCase, CSRF, q_item  # noqa: E402

TEACHER_PAGES = ["/teacher/dashboard", "/teacher/students", "/teacher/tests", "/teacher/bank/questions",
                 "/teacher/studio/library", "/teacher/announcements", "/teacher/notes/upload", "/teacher/cleanup",
                 "/teacher/activity", "/teacher/tests/create"]
STUDENT_PAGES = ["/dashboard", "/notes", "/profile"]
PUBLIC_PAGES = ["/", "/login", "/register", "/teacher/login", "/contact"]


class UiV2(AppCase):
    # ------------------------------------------------------------------ CSP
    def test_csp_uses_nonce_not_unsafe_inline_for_scripts(self):
        a = self.app.test_client().get("/login")
        b = self.app.test_client().get("/login")
        csp = a.headers["Content-Security-Policy"]
        script_src = [d for d in csp.split(";") if d.strip().startswith("script-src ")][0]
        self.assertNotIn("unsafe-inline", script_src)
        self.assertIn("script-src-attr 'none'", csp)
        n1 = re.search(r"'nonce-([^']+)'", csp).group(1)
        n2 = re.search(r"'nonce-([^']+)'", b.headers["Content-Security-Policy"]).group(1)
        self.assertNotEqual(n1, n2)                      # a fresh nonce for every request
        self.assertGreaterEqual(len(n1), 16)

    def _check_page(self, cl, url):
        r = cl.get(url)
        self.assertEqual(r.status_code, 200, url)
        html = r.get_data(as_text=True)
        nonce = re.search(r"'nonce-([^']+)'", r.headers["Content-Security-Policy"]).group(1)
        for m in re.finditer(r"<script\b([^>]*)>", html):
            attrs = m.group(1)
            if "src=" in attrs:
                continue
            self.assertIn('nonce="%s"' % nonce, attrs, "inline <script> without the nonce on %s" % url)
        self.assertIsNone(re.search(r"\son(click|submit|change|input|load|error|focus|blur|keyup|keydown)=", html),
                          "inline event handler on %s" % url)

    def test_every_page_has_nonce_on_inline_scripts_and_no_inline_handlers(self):
        for url in PUBLIC_PAGES:
            self._check_page(self.app.test_client(), url)
        t = self.teacher()
        for url in TEACHER_PAGES:
            self._check_page(t, url)
        s = self.student()
        for url in STUDENT_PAGES:
            self._check_page(s, url)

    def test_confirm_dialogs_became_data_attributes(self):
        self.new_test("Confirm me")
        html = self.teacher().get("/teacher/cleanup").get_data(as_text=True)
        self.assertIn("data-confirm=", html)
        js = open(os.path.join(self.proj, "static", "js", "app.js"), encoding="utf-8").read()
        self.assertIn("data-confirm", js)

    # ------------------------------------------------------- fail-closed checks
    def test_rate_limiter_refuses_when_database_fails_and_logs(self):
        from backend.middleware import rate_limit
        with mock.patch.object(rate_limit, "get_db", side_effect=sqlite3.OperationalError("locked")):
            with self.assertLogs(rate_limit._log, level="ERROR"):
                self.assertFalse(rate_limit._bucket_ok("x:1", 5, 60))
            with self.assertLogs(rate_limit._log, level="ERROR"):
                self.assertTrue(rate_limit._bucket_ok("x:1", 5, 60, fail_open=True))

    def test_rate_limiter_still_limits_normally(self):
        from backend.middleware import rate_limit
        results = [rate_limit._bucket_ok("unit:%s" % self.id(), 3, 60) for _ in range(5)]
        self.assertEqual(results, [True, True, True, False, False])

    def test_ai_limiter_is_the_shared_one(self):
        from backend.routes.api import ai_api
        from backend.middleware import rate_limit
        self.assertIs(ai_api._bucket_ok, rate_limit._bucket_ok)

    def test_session_check_fails_closed_with_503(self):
        from backend.middleware import auth
        cl = self.student()
        with mock.patch.object(auth, "get_db", side_effect=sqlite3.OperationalError("locked")):
            r = cl.get("/api/notifications")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.student().get("/dashboard").status_code, 200)   # and back to normal afterwards

    def test_logout_still_kills_the_old_cookie_with_the_merged_check(self):
        cl = self.app.test_client()
        cl.get("/login")                                  # gets a session with its own CSRF token
        with cl.session_transaction() as s:
            tok = s["_csrf_token"]
        r = cl.post("/login", data={"email": "s@t.local", "password": "StudentPass123", "csrf_token": tok})
        self.assertEqual(r.status_code, 302)
        with cl.session_transaction() as s:
            jti = s.get("jti"); tok = s.get("_csrf_token") or tok   # login starts a brand-new session
        self.assertTrue(jti)
        self.assertEqual(cl.get("/dashboard").status_code, 200)
        with cl.session_transaction() as s:
            s["_csrf_token"] = CSRF
        cl.post("/logout", data={"csrf_token": CSRF})
        stolen = self.app.test_client()                   # someone who copied the cookie before logout
        with stolen.session_transaction() as s:
            s.update(student_id=self.stu, student_name="Sam", sv=0, jti=jti)
        self.assertEqual(stolen.get("/dashboard").status_code, 302)

    # ------------------------------------------------------------ connections
    def test_with_get_db_closes_the_connection(self):
        from backend.db import get_db
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    # -------------------------------------------------------------- dashboards
    def test_student_dashboard_is_simple_and_has_notes(self):
        self.q("SELECT 1")
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO notes (title,subject,class_name,filename,uploaded_at) VALUES ('Quadratics','Maths','TEN','q.pdf','2026-11-01T10:00:00')")
        c.commit(); c.close()
        t, tid = self.published_test("Dash exam")
        html = self.student().get("/dashboard").get_data(as_text=True)
        for gone in ("XP", "streak", "Smart coach", "Score trend", "Subject strength", "JEE Main Session"):
            self.assertNotIn(gone, html)
        for there in ("Available tests", "Study notes", "Quadratics", "Recent results", "Dash exam"):
            self.assertIn(there, html)

    def test_teacher_dashboard_sections(self):
        self.published_test("Teacher view exam")
        html = self.teacher().get("/teacher/dashboard").get_data(as_text=True)
        for there in ("Recent tests", "Recent results", "Quick actions", "Teacher view exam", "Students"):
            self.assertIn(there, html)
        self.assertNotIn("Smart", html)

    def test_teacher_sidebar_groups_and_links(self):
        html = self.teacher().get("/teacher/dashboard").get_data(as_text=True)
        for text in ("Main", "Communication", "Advanced", "Question Studio", "Image Library", "Notices", "Inbox",
                     "Activity Log", "Data Cleanup", "Export students"):
            self.assertIn(text, html)

    def test_no_bottom_gamification_or_theme_toggle_in_shell(self):
        html = self.student().get("/dashboard").get_data(as_text=True)
        self.assertNotIn("theme-seg", html)
        self.assertNotIn("jeeToggle", html)

    # --------------------------------------------------------- Question Studio
    def test_old_bank_image_page_redirects_to_the_one_library(self):
        r = self.teacher().get("/teacher/bank/images")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/teacher/studio/library", r.headers["Location"])
        r = self.teacher().get("/teacher/bank/")
        self.assertIn("/teacher/bank/questions", r.headers["Location"])

    def test_studio_pages_share_the_same_tabs(self):
        for url in ("/teacher/bank/questions", "/teacher/studio/library"):
            html = self.teacher().get(url).get_data(as_text=True)
            for tab in ("Saved questions", "Test questions", "Image Library"):
                self.assertIn(tab, html, url)

    # ------------------------------------------------------------ exam + math
    def test_exam_page_loads_math_renderer_and_keeps_question_text(self):
        t, tid = self.new_test("Math exam")
        self.add_questions(t, tid, [q_item("Solve $x^2=4$", "A", 4)])
        self.post(t, "/teacher/tests/%d/publish" % tid)
        cl = self.student()
        from legacy import open_exam
        r = open_exam(cl, tid)
        html = r.get_data(as_text=True)
        self.assertIn("Solve $x^2=4$", html)             # the raw text is kept for math.js to render
        self.assertIn("js/math.js", html)
        self.assertIn('class="q-card', html.replace("\n", " ").replace("  ", " ")) if 'class="q-card' in html else self.assertIn("q-card", html)

    def test_math_js_only_uses_the_allowed_cdn(self):
        js = open(os.path.join(self.proj, "static", "js", "math.js"), encoding="utf-8").read()
        hosts = set(re.findall(r"https?://([^/\"']+)", js))
        self.assertEqual(hosts, {"cdnjs.cloudflare.com"})

    # ----------------------------------------------------------- chat widget
    def test_chat_widget_does_not_lock_page_scroll_and_has_minimize(self):
        js = open(os.path.join(self.proj, "static", "js", "ai-widget.js"), encoding="utf-8").read()
        css = open(os.path.join(self.proj, "static", "css", "ai-widget.css"), encoding="utf-8").read()
        self.assertNotIn("body.style.overflow", js)
        self.assertIn("aiw-min", js)
        self.assertIn("aiw-open", js)
        self.assertIn("68dvh", css)                      # phone layout is a bottom sheet, not 100dvh
        self.assertNotRegex(css.split("v2 - white theme")[1], r"height:\s*100dvh")

    def test_widget_present_for_students_only(self):
        self.assertIn("ai-widget.js", self.student().get("/dashboard").get_data(as_text=True))
        self.assertNotIn("ai-widget.js", self.teacher().get("/teacher/dashboard").get_data(as_text=True))

    # ------------------------------------------------------------------- push
    def test_push_button_hidden_without_vapid_keys(self):
        html = self.student().get("/dashboard").get_data(as_text=True)
        self.assertNotIn("pushSubBtn\"", html.split("<script")[0])
        from backend import config
        self.assertFalse(config.PUSH_ENABLED)

    def test_requirements_has_no_openai_package(self):
        reqs = open(os.path.join(self.proj, "requirements.txt"), encoding="utf-8").read().lower()
        self.assertNotRegex(reqs, r"^openai\b")


if __name__ == "__main__":
    unittest.main()
