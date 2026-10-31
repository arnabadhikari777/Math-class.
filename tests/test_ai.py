# -*- coding: utf-8 -*-
"""AI assistant tests. Run:  python -m unittest tests.test_ai -v   (or discover -s tests)

SAFETY: like test_backend.py, everything runs on a temporary COPY of the project;
database.db in the real project is never opened. The AI provider is MOCKED
(requests.post is patched) - no real Gemini/Groq call is made by this suite.
"""
import sys as _sys, os as _os  # TESTS_DIR_ON_PATH
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import requests
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_backend import CSRF, PASSWORD, ROOT, _same_old_data, _snapshot  # noqa: E402

GKEY, QKEY = "test-gemini-KEY-123", "test-groq-KEY-456"


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


def gemini_ok(text="Gemini answer"):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def groq_ok(text="Groq answer"):
    return FakeResp(200, {"choices": [{"message": {"content": text}}]})


class Router:
    """requests.post replacement: decides per URL and records every call."""

    def __init__(self, gemini=None, groq=None):
        self.gemini, self.groq, self.calls = gemini or gemini_ok(), groq or groq_ok(), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        r = self.gemini if "googleapis" in url else self.groq
        if isinstance(r, Exception):
            raise r
        return r

    def system_text(self, i=-1):
        c = self.calls[i]["json"]
        if "systemInstruction" in c:
            return c["systemInstruction"]["parts"][0]["text"]
        return c["messages"][0]["content"]


class AITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mathclass-ai-test-")
        cls.proj = os.path.join(cls.tmp, "proj")
        shutil.copytree(ROOT, cls.proj, ignore=shutil.ignore_patterns(
            "backups", ".secret_key", "__pycache__", ".env", "tests"))
        shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(cls.proj, "tests"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        cls.db = os.path.join(cls.proj, "database.db")
        __import__("legacy").ensure_legacy_db(cls.db)
        cls.original_snapshot = _snapshot(cls.db)
        cls._env_backup = dict(os.environ)
        os.environ.update({"FORCE_HTTPS": "0", "SECRET_KEY": "unit-test-secret", "TEACHER_USERNAME": "sir",
                           "TEACHER_PASSWORD": "TeacherPass123", "EMAIL_ADDRESS": "", "EMAIL_APP_PASSWORD": "",
                           "GEMINI_API_KEY": GKEY, "GROQ_API_KEY": QKEY,
                           "AI_PROVIDER_ORDER": "gemini,gpt-oss", "AI_RATE_PER_10MIN": "1000",
                           "AI_RATE_PER_DAY": "10000"})
        sys.path.insert(0, cls.proj)
        for name in [m for m in sys.modules if m == "app" or m == "backend" or m.startswith("backend.")]:
            del sys.modules[name]
        cls.appmod = importlib.import_module("app")
        cls.app = cls.appmod.app
        cls.app.config["TESTING"] = True
        cls._seed()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.proj)
        os.environ.clear()
        os.environ.update(cls._env_backup)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _seed(cls):
        conn = sqlite3.connect(cls.db)
        conn.execute("PRAGMA foreign_keys=ON")
        now = datetime.now().isoformat()
        ph = generate_password_hash(PASSWORD)

        def student(name, roll, email):
            return conn.execute(
                """INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,
                   registered_at,is_blocked,session_version) VALUES (?,?,?,?,?,?,1,?,0,0)""",
                (name, "10", roll, email, "9000000000", ph, now)).lastrowid

        def test(title, dur=30):
            tid = conn.execute(
                "INSERT INTO tests (title,subject,class_name,duration_minutes,is_active,created_at) "
                "VALUES (?,?,?,?,1,?)", (title, "Algebra", "10", dur, now)).lastrowid
            qs = [conn.execute(
                """INSERT INTO questions (test_id,question_text,option_a,option_b,option_c,option_d,
                   correct_option,marks) VALUES (?,?,?,?,?,?,?,?)""",
                (tid, f"{title}-question-{i}", "a", "b", "c", "d", "A", 5)).lastrowid for i in range(2)]
            return tid, qs

        cls.sa = student("Alice", "11", "alice@test.local")
        cls.sb = student("Bob", "12", "bob@test.local")
        cls.sc = student("Carol", "13", "carol@test.local")          # never chats
        cls.t_done, qs = test("DoneTestAlice")
        cls.t_live, _ = test("LiveTest")
        cls.t_bob, qb = test("BobSecretTest")
        def finished(tid, sid, qids, picks, score):
            """A submitted attempt with a frozen snapshot, exactly what the exam engine writes."""
            from backend.services import exam_service
            conn.execute("UPDATE tests SET status='published' WHERE is_active=1")
            conn.commit()
            vid = conn.execute("INSERT INTO exam_versions (test_id,version_no,content_hash,is_frozen,created_at) "
                               "VALUES (?,1,'h',1,?)", (tid, now)).lastrowid
            aid = conn.execute(
                """INSERT INTO exam_attempts (test_id,student_id,version_id,attempt_no,status,started_at,deadline_at,
                   duration_seconds,submitted_at,score,total_marks,is_counted) VALUES (?,?,?,1,'submitted',?,?,1800,?,?,10,1)""",
                (tid, sid, vid, now, now, now, score)).lastrowid
            rid = conn.execute("INSERT INTO results (test_id,student_id,score,total_marks,submitted_at,attempt_id) "
                               "VALUES (?,?,?,10,?,?)", (tid, sid, score, now, aid)).lastrowid
            for pos, (qid, pick) in enumerate(zip(qids, picks), 1):
                q = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
                ok = 1 if pick == "A" else 0
                conn.execute(
                    """INSERT INTO answers (result_id,attempt_id,question_id,position,question_text,option_a,option_b,
                       option_c,option_d,correct_option,marks,selected_option,is_correct,marks_awarded)
                       VALUES (?,?,?,?,?,?,?,?,?,?,5,?,?,?)""",
                    (rid, aid, qid, pos, q[2], "a", "b", "c", "d", "A", pick, ok, 5 if ok else 0))
        finished(cls.t_done, cls.sa, qs, ["A", "C"], 5)
        finished(cls.t_bob, cls.sb, qb[:1], ["D"], 0)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------- helpers
    def dbc(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def student_client(self, sid):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s.update(student_id=sid, student_name="x", student_class="10", sv=0, _csrf_token=CSRF)
        return c

    def teacher_client(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s.update(is_teacher=True, _csrf_token=CSRF)
        return c

    def impersonating_client(self, sid):
        c = self.student_client(sid)
        with c.session_transaction() as s:
            s.pop("is_teacher", None)      # a view-as-student session is a STUDENT session (read-only)
            s.update(impersonating=True, impersonation_started=datetime.now().isoformat())
        return c

    def chat(self, c, body, router=None):
        router = router or Router()
        with mock.patch("backend.ai.provider.requests.post", router):
            r = c.post("/api/v1/ai/chat", data=json.dumps(body), content_type="application/json",
                       headers={"X-CSRFToken": CSRF})
        return r, router

    # ------------------------------------------------ 2. student chat works
    def test_01_student_chat_returns_provider_answer_and_saves_for_right_student(self):
        c = self.student_client(self.sa)
        r, router = self.chat(c, {"message": "What is a quadratic?"}, Router(gemini=gemini_ok("It is ax²+bx+c.")))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()["data"]
        self.assertEqual(d["message"]["content"], "It is ax²+bx+c.")
        self.assertEqual(len(router.calls), 1)
        self.assertIn("googleapis.com", router.calls[0]["url"])
        self.assertEqual(router.calls[0]["headers"]["x-goog-api-key"], GKEY)
        with self.dbc() as conn:
            conv = conn.execute("SELECT * FROM ai_conversations WHERE id=?", (d["conversation_id"],)).fetchone()
            self.assertEqual(conv["student_id"], self.sa)
            msgs = conn.execute("SELECT role, content, provider FROM ai_messages WHERE conversation_id=? ORDER BY id",
                                (conv["id"],)).fetchall()
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[1]["provider"], "gemini")

    def test_02_follow_up_uses_server_history_and_same_conversation(self):
        c = self.student_client(self.sa)
        r, _ = self.chat(c, {"message": "first question"})
        cid = r.get_json()["data"]["conversation_id"]
        r2, router = self.chat(c, {"message": "second question", "conversation_id": cid})
        self.assertEqual(r2.get_json()["data"]["conversation_id"], cid)
        sent = router.calls[0]["json"]["contents"]
        self.assertEqual([p["parts"][0]["text"] for p in sent][-3:], ["first question", "Gemini answer", "second question"])

    def test_03_spoofed_identity_in_body_is_ignored(self):
        c = self.student_client(self.sa)
        r, _ = self.chat(c, {"message": "hello", "student_id": self.sb, "email": "bob@test.local", "name": "Bob"})
        cid = r.get_json()["data"]["conversation_id"]
        with self.dbc() as conn:
            self.assertEqual(conn.execute("SELECT student_id FROM ai_conversations WHERE id=?", (cid,)).fetchone()[0], self.sa)

    # --------------------------------------------- 3. fallback / failures
    def test_10_fallback_to_second_provider_when_first_fails(self):
        c = self.student_client(self.sa)
        r, router = self.chat(c, {"message": "fallback please"}, Router(gemini=FakeResp(500)))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["data"]["message"]["content"], "Groq answer")
        self.assertEqual(len(router.calls), 2)
        self.assertIn("groq.com", router.calls[1]["url"])
        with self.dbc() as conn:
            self.assertEqual(conn.execute("SELECT provider FROM ai_messages WHERE id=?",
                             (r.get_json()["data"]["message"]["id"],)).fetchone()[0], "gpt-oss")

    def test_11_timeout_then_fallback(self):
        c = self.student_client(self.sa)
        r, _ = self.chat(c, {"message": "timeout case"}, Router(gemini=requests.Timeout("slow")))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["data"]["message"]["content"], "Groq answer")

    def test_12_all_providers_fail_is_safe_and_saves_nothing(self):
        c = self.student_client(self.sb)
        with self.dbc() as conn:
            before = conn.execute("SELECT COUNT(*) FROM ai_messages").fetchone()[0]
        r, _ = self.chat(c, {"message": "everything is down"},
                         Router(gemini=requests.ConnectionError(GKEY), groq=FakeResp(429)))
        self.assertEqual(r.status_code, 503)
        body = r.get_data(as_text=True)
        self.assertEqual(r.get_json()["error"]["code"], "ai_unavailable")
        self.assertNotIn(GKEY, body)
        self.assertNotIn(QKEY, body)
        self.assertNotIn("Traceback", body)
        with self.dbc() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_messages").fetchone()[0], before)

    def test_13_no_keys_configured(self):
        c = self.student_client(self.sa)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "GROQ_API_KEY": ""}):
            r, router = self.chat(c, {"message": "hi"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"]["code"], "ai_not_configured")
        self.assertEqual(router.calls, [])

    # ------------------------------------------- 9. authn / csrf / input
    def test_20_anonymous_and_missing_csrf_rejected(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/api/v1/ai/status").status_code, 401)
        self.assertEqual(anon.get("/api/v1/ai/conversations").status_code, 401)
        r = anon.post("/api/v1/ai/chat", data=json.dumps({"message": "x"}), content_type="application/json",
                      headers={"X-CSRFToken": CSRF})
        self.assertIn(r.status_code, (400, 401))
        c = self.student_client(self.sa)
        r = c.post("/api/v1/ai/chat", data=json.dumps({"message": "x"}), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_21_input_validation(self):
        c = self.student_client(self.sa)
        for body in [{}, {"message": ""}, {"message": "   "}, {"message": 5}, {"message": "x" * 2001},
                     {"message": "ok", "conversation_id": "1"}, {"message": "ok", "conversation_id": True},
                     {"message": "ok", "test_id": "7"}]:
            r, router = self.chat(c, body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(router.calls, [], body)
        r = c.post("/api/v1/ai/chat", data="not json", content_type="application/json", headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/v1/ai/chat", data=json.dumps({"message": "a" * 30000}), content_type="application/json",
                   headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.status_code, 413)

    def test_22_rate_limit(self):
        c = self.student_client(self.sc)
        with mock.patch.dict(os.environ, {"AI_RATE_PER_10MIN": "2"}):
            codes = [self.chat(c, {"message": f"m{i}"})[0].status_code for i in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    # -------------------------------------------------------- 7. isolation
    def test_30_student_cannot_read_or_continue_other_students_chat(self):
        a, b = self.student_client(self.sa), self.student_client(self.sb)
        cid = self.chat(a, {"message": "Alice private question"})[0].get_json()["data"]["conversation_id"]
        self.assertEqual(b.get(f"/api/v1/ai/conversations/{cid}/messages").status_code, 404)
        r, router = self.chat(b, {"message": "hijack", "conversation_id": cid})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(router.calls, [])
        ids = [x["id"] for x in b.get("/api/v1/ai/conversations").get_json()["data"]]
        self.assertNotIn(cid, ids)
        self.assertEqual(a.get(f"/api/v1/ai/conversations/{cid}/messages").status_code, 200)

    def test_31_prompt_contains_own_verified_results_only(self):
        a, b = self.student_client(self.sa), self.student_client(self.sb)
        _, ra = self.chat(a, {"message": "why did I lose marks?"})
        sa = ra.system_text()
        self.assertIn("DoneTestAlice", sa)
        self.assertIn("5/10", sa)
        self.assertIn("DoneTestAlice-question-1", sa)       # a wrong answer from a SUBMITTED test
        self.assertNotIn("BobSecretTest", sa)
        _, rb = self.chat(b, {"message": "help"})
        self.assertIn("BobSecretTest", rb.system_text())
        self.assertNotIn("DoneTestAlice", rb.system_text())

    def test_32_browser_test_id_cannot_inject_results(self):
        """test_id is only a reference: a hint pointing at another student's test adds nothing to the prompt."""
        a = self.student_client(self.sa)
        r, router = self.chat(a, {"message": "hint", "test_id": self.t_bob})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("BobSecretTest-question", router.system_text())
        self.assertNotIn("0/10", router.system_text())

    # ------------------------------------------------- test-mode (agreed rule)
    def test_40_test_mode_only_while_a_test_is_open_and_unsubmitted(self):
        c = self.student_client(self.sc)
        _, r0 = self.chat(c, {"message": "before test"})
        self.assertNotIn("TEST MODE", r0.system_text())
        __import__("legacy").open_exam(c, self.t_live)                                  # opens the test -> test_attempts row
        _, r1 = self.chat(c, {"message": "during test"})
        self.assertIn("TEST MODE", r1.system_text())
        self.assertNotIn("LiveTest-question", r1.system_text())        # the open test's questions never enter the prompt
        st = c.get("/api/v1/ai/status").get_json()["data"]
        self.assertTrue(st["test_mode"])
        self.assertTrue(st["enabled"])
        # client cannot opt out by omitting/changing test_id (server decides)
        _, r2 = self.chat(c, {"message": "claiming no test", "test_id": self.t_done})
        self.assertIn("TEST MODE", r2.system_text())
        # after submitting the test mode ends
        with self.dbc() as conn:
            qids = [r[0] for r in conn.execute("SELECT id FROM questions WHERE test_id=?", (self.t_live,))]
        c.post(f"/test/{self.t_live}/submit", data={f"q_{q}": "A" for q in qids}, headers={"X-CSRFToken": CSRF})
        _, r3 = self.chat(c, {"message": "after test"})
        self.assertNotIn("TEST MODE", r3.system_text())
        self.assertIn("LiveTest", r3.system_text())                    # now it is verified submitted data

    def test_41_expired_attempt_is_not_test_mode(self):
        old = (datetime.now() - timedelta(hours=5)).isoformat()
        with self.dbc() as conn:
            vid = conn.execute("INSERT INTO exam_versions (test_id,version_no,content_hash,is_frozen,created_at) "
                               "VALUES (?,9,'x',1,?)", (self.t_live, old)).lastrowid
            conn.execute("INSERT INTO exam_attempts (test_id,student_id,version_id,attempt_no,status,started_at,"
                         "deadline_at,duration_seconds) VALUES (?,?,?,1,'in_progress',?,?,1800)",
                         (self.t_live, self.sb, vid, old, old))
            conn.commit()
        _, router = self.chat(self.student_client(self.sb), {"message": "late"})
        self.assertNotIn("TEST MODE", router.system_text())

    # -------------------------------------------------- impersonation
    def test_50_teacher_viewing_as_student_cannot_chat_or_monitor(self):
        c = self.impersonating_client(self.sa)
        r, router = self.chat(c, {"message": "I am sir"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(router.calls, [])
        self.assertEqual(c.get("/api/v1/teacher/ai/students").status_code, 403)
        self.assertEqual(c.get("/teacher/ai-chats").status_code, 302)
        self.assertTrue(c.get("/api/v1/ai/status").get_json()["data"]["viewing_as_teacher"])

    # ------------------------------------------ 5/6. Sir monitoring + access
    def test_60_students_and_anonymous_cannot_use_monitoring(self):
        s, anon = self.student_client(self.sa), self.app.test_client()
        for path in ["/api/v1/teacher/ai/students", "/api/v1/teacher/ai/students/1/conversations",
                     "/api/v1/teacher/ai/conversations/1/messages", "/api/v1/teacher/ai/search?q=ab"]:
            self.assertEqual(s.get(path).status_code, 403, path)
            self.assertEqual(anon.get(path).status_code, 401, path)
        self.assertEqual(s.get("/teacher/ai-chats").status_code, 302)
        self.assertEqual(anon.get("/teacher/ai-chats").status_code, 302)

    def test_61_teacher_can_browse_student_chats_in_order(self):
        a = self.student_client(self.sa)
        cid = self.chat(a, {"message": "order one"})[0].get_json()["data"]["conversation_id"]
        self.chat(a, {"message": "order two", "conversation_id": cid})
        self.chat(a, {"message": "order three", "conversation_id": cid})
        t = self.teacher_client()
        self.assertEqual(t.get("/teacher/ai-chats").status_code, 200)
        lst = t.get("/api/v1/teacher/ai/students").get_json()
        names = [s["name"] for s in lst["data"]]
        self.assertIn("Alice", names)
        self.assertNotIn("Carol", [s["name"] for s in lst["data"] if s["conversations"] == 0])
        self.assertTrue(all("email" not in s and "mobile" not in s for s in lst["data"]))
        convs = t.get(f"/api/v1/teacher/ai/students/{self.sa}/conversations").get_json()["data"]
        self.assertEqual(convs["student"]["name"], "Alice")
        self.assertIn(cid, [c["id"] for c in convs["conversations"]])
        m = t.get(f"/api/v1/teacher/ai/conversations/{cid}/messages").get_json()
        texts = [x["content"] for x in m["data"]["messages"]]
        user_texts = [x for x in texts if x.startswith("order")]
        self.assertEqual(user_texts, ["order one", "order two", "order three"])
        ids = [x["id"] for x in m["data"]["messages"]]
        self.assertEqual(ids, sorted(ids))
        stamps = [x["created_at"] for x in m["data"]["messages"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(m["data"]["student"]["id"], self.sa)
        self.assertEqual(m["data"]["messages"][0]["role"], "user")
        self.assertEqual(t.get("/api/v1/teacher/ai/conversations/999999/messages").status_code, 404)
        self.assertEqual(t.get("/api/v1/teacher/ai/students/999999/conversations").status_code, 404)

    def test_62_teacher_pagination_search_and_test_context(self):
        a = self.student_client(self.sa)
        cid = self.chat(a, {"message": "needle-xyz topic", "test_id": self.t_done})[0].get_json()["data"]["conversation_id"]
        for i in range(4):
            self.chat(a, {"message": f"filler {i}", "conversation_id": cid})
        t = self.teacher_client()
        p1 = t.get(f"/api/v1/teacher/ai/conversations/{cid}/messages?per_page=3&page=1").get_json()
        p2 = t.get(f"/api/v1/teacher/ai/conversations/{cid}/messages?per_page=3&page=2").get_json()
        self.assertEqual(len(p1["data"]["messages"]), 3)
        self.assertEqual(p1["meta"]["total"], 10)
        self.assertLess(p1["data"]["messages"][-1]["id"], p2["data"]["messages"][0]["id"])
        self.assertEqual(p1["data"]["test"]["title"], "DoneTestAlice")
        self.assertEqual(p1["data"]["test"]["result"]["score"], 5)
        hit = t.get("/api/v1/teacher/ai/search?q=needle-xyz").get_json()["data"]
        self.assertEqual(hit[0]["conversation_id"], cid)
        self.assertEqual(hit[0]["student"]["name"], "Alice")
        self.assertEqual(t.get("/api/v1/teacher/ai/search?q=%25%25").get_json()["data"], [])  # LIKE wildcards are literal
        self.assertEqual(t.get("/api/v1/teacher/ai/search?q=a").status_code, 400)
        q = t.get("/api/v1/teacher/ai/students?q=Ali").get_json()["data"]
        self.assertEqual([s["name"] for s in q], ["Alice"])

    def test_63_conversation_with_unrelated_test_hint_is_dropped(self):
        s = self.student_client(self.sa)
        with self.dbc() as conn:
            t9 = conn.execute("INSERT INTO tests (title,subject,class_name,duration_minutes,is_active,created_at) "
                              "VALUES ('Other','M','9',10,1,?)", (datetime.now().isoformat(),)).lastrowid
            conn.execute("UPDATE tests SET status='published' WHERE is_active=1")
            conn.commit()
        cid = self.chat(s, {"message": "wrong class hint", "test_id": t9})[0].get_json()["data"]["conversation_id"]
        with self.dbc() as conn:
            self.assertIsNone(conn.execute("SELECT test_id FROM ai_conversations WHERE id=?", (cid,)).fetchone()[0])

    # ------------------------------------------------------ UI integration
    def test_70_widget_only_for_students_and_test_page_intact(self):
        s = self.student_client(self.sa)
        page = __import__("legacy").open_exam(s, self.t_bob).get_data(as_text=True)
        self.assertIn("ai-widget.js", page)
        self.assertIn(f"/test/{self.t_bob}/submit", page)             # the test form is still there
        self.assertIn("ai-widget.js", s.get("/dashboard").get_data(as_text=True))
        t = self.teacher_client()
        self.assertNotIn("ai-widget.js", t.get("/teacher/dashboard").get_data(as_text=True))
        self.assertIn("AI Chats", t.get("/teacher/dashboard").get_data(as_text=True))
        self.assertNotIn("ai-widget.js", self.app.test_client().get("/login").get_data(as_text=True))
        self.assertEqual(s.get("/static/js/ai-widget.js").status_code, 200)
        self.assertEqual(s.get("/static/css/ai-widget.css").status_code, 200)

    def test_71_secret_keys_never_in_pages_or_static(self):
        s = self.student_client(self.sa)
        for path in ["/dashboard", "/static/js/ai-widget.js", "/api/v1/ai/status", "/teacher/ai-chats"]:
            body = s.get(path).get_data(as_text=True)
            self.assertNotIn(GKEY, body)
            self.assertNotIn(QKEY, body)
        self.assertNotIn(GKEY, self.teacher_client().get("/teacher/ai-chats").get_data(as_text=True))


    def test_72_status_reports_disabled_without_keys_and_widget_never_hides_button(self):
        c = self.student_client(self.sa)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "GROQ_API_KEY": ""}):
            st = c.get("/api/v1/ai/status").get_json()["data"]
        self.assertFalse(st["enabled"])
        with open(os.path.join(self.proj, "static", "js", "ai-widget.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertNotIn("fab.style.display", js)   # regression: button used to vanish when AI was off
        self.assertIn("ai-widget.js?v=", c.get("/dashboard").get_data(as_text=True))

    def test_73_real_provider_call_failure_is_safe(self):
        """No mock: keys are fake and the sandbox has no network, so both providers really fail."""
        c = self.student_client(self.sa)
        with mock.patch.dict(os.environ, {"AI_TIMEOUT_SECONDS": "5"}):
            r = c.post("/api/v1/ai/chat", data=json.dumps({"message": "real call"}),
                       content_type="application/json", headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"]["code"], "ai_unavailable")
        self.assertNotIn(GKEY, r.get_data(as_text=True))

    # ------------------------------------------- AI choice + hidden real names
    REAL = ("gemini", "groq", "gpt-oss", "qwen", "compound")

    def assert_no_real_names(self, text):
        """Real provider names must not appear in anything the server/JS adds. (The AI's own reply text is
        its content; the mock answers here literally say 'Gemini answer', so 'content' is excluded.)"""
        try:
            def strip(x):
                if isinstance(x, dict):
                    return {k: strip(v) for k, v in x.items() if k != "content"}
                if isinstance(x, list):
                    return [strip(i) for i in x]
                return x
            text = json.dumps(strip(json.loads(text)))
        except ValueError:
            pass                                    # not JSON (e.g. the widget JS): check it whole
        low = text.lower()
        for word in self.REAL:
            self.assertNotIn(word, low, f"real provider name '{word}' leaked to student: {text[:200]}")

    def test_90_student_sees_only_alias_choices(self):
        c = self.student_client(self.sa)
        raw = c.get("/api/v1/ai/status").get_data(as_text=True)
        data = json.loads(raw)["data"]
        self.assertEqual([x["label"] for x in data["choices"]], ["Anubhab Dutta", "Arnab Adhikari"])
        self.assert_no_real_names(raw)
        self.assert_no_real_names(c.get("/static/js/ai-widget.js").get_data(as_text=True))

    def test_91_choice_routes_to_right_provider_and_labels_alias(self):
        c = self.student_client(self.sa)
        r, router = self.chat(c, {"message": "use arnab", "ai": "arnab"})
        d = r.get_json()["data"]
        self.assertIn("groq.com", router.calls[0]["url"])
        self.assertEqual(d["message"]["answered_by"]["label"], "Arnab Adhikari")
        self.assertFalse(d["fallback"])
        self.assert_no_real_names(r.get_data(as_text=True))
        r, router = self.chat(c, {"message": "use anubhab", "ai": "anubhab"})
        self.assertIn("googleapis.com", router.calls[0]["url"])
        self.assertEqual(r.get_json()["data"]["message"]["answered_by"]["label"], "Anubhab Dutta")
        self.assert_no_real_names(r.get_data(as_text=True))

    def test_92_fallback_is_reported_with_aliases_only(self):
        c = self.student_client(self.sa)
        r, router = self.chat(c, {"message": "arnab is down", "ai": "arnab"}, Router(groq=FakeResp(500)))
        d = r.get_json()["data"]
        self.assertTrue(d["fallback"])
        self.assertEqual(d["message"]["answered_by"]["label"], "Anubhab Dutta")
        self.assertIn("googleapis.com", router.calls[-1]["url"])
        self.assert_no_real_names(r.get_data(as_text=True))

    def test_93_persona_name_in_prompt_matches_who_answers(self):
        c = self.student_client(self.sa)
        _, router = self.chat(c, {"message": "who are you", "ai": "arnab"})
        self.assertIn("Arnab Adhikari", router.system_text())
        _, router = self.chat(c, {"message": "who are you", "ai": "arnab"}, Router(groq=FakeResp(500)))
        self.assertIn("Anubhab Dutta", router.system_text())       # fallback answered, so that persona is used

    def test_94_invalid_or_unavailable_choice_rejected(self):
        c = self.student_client(self.sa)
        for bad in ["gemini", "groq", "x", 5, ["arnab"]]:
            r, router = self.chat(c, {"message": "hi", "ai": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(router.calls, [])
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):    # only Gemini has a key
            st = c.get("/api/v1/ai/status").get_json()["data"]["choices"]
            self.assertEqual([x["id"] for x in st], ["anubhab"])
            r, _ = self.chat(c, {"message": "hi", "ai": "arnab"})
            self.assertEqual(r.status_code, 400)

    def test_95_history_for_student_has_alias_but_teacher_sees_real_names(self):
        a = self.student_client(self.sa)
        cid = self.chat(a, {"message": "history check", "ai": "arnab"})[0].get_json()["data"]["conversation_id"]
        raw = a.get(f"/api/v1/ai/conversations/{cid}/messages").get_data(as_text=True)
        self.assert_no_real_names(raw)
        self.assertIn("Arnab Adhikari", raw)
        t = self.teacher_client()
        msgs = t.get(f"/api/v1/teacher/ai/conversations/{cid}/messages").get_json()["data"]["messages"]
        bot = [m for m in msgs if m["role"] == "assistant"][0]
        self.assertEqual(bot["provider"], "gpt-oss")
        self.assertEqual(bot["provider_label"], "Groq \u00b7 gpt-oss")
        self.assertIsNone([m for m in msgs if m["role"] == "user"][0]["provider_label"])

    # ------------------------------------------------------------ database
    def test_80_migration_006_is_additive_idempotent_reversible(self):
        from backend.db import migrations
        with self.dbc() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"ai_conversations", "ai_messages"} <= tables)
        self.assertIn("006_ai_chat", migrations.applied_ids(self.db))
        self.assertEqual(migrations.run_pending(self.db), [])           # second run does nothing
        shutil.copy(self.db, self.db + ".bak-test")
        try:
            migrations.rollback(self.db, "006_ai_chat", backup=False, force_destructive=True)
            with self.dbc() as conn:
                t2 = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertFalse({"ai_conversations", "ai_messages"} & t2)
                self.assertTrue({"students", "tests", "results", "answers", "questions"} <= t2)
        finally:
            shutil.copy(self.db + ".bak-test", self.db)                 # restore the temp copy for later tests

    def test_92_every_saved_name_is_listed_and_unusable_pick_falls_back(self):
        c = self.student_client(self.sa)
        with mock.patch.dict(os.environ, {"AI_PROVIDER_ORDER": "", "AI_NAME_MISTRAL": "Priya Das"}):   # mistral: name, no key
            data = c.get("/api/v1/ai/status").get_json()["data"]
            labels = [x["label"] for x in data["choices"]]
            self.assertIn("Priya Das", labels)
            blob = repr(data).lower()
            for real in ("mistral", "gemini", "groq", "openai", "deepseek"):
                self.assertNotIn(real, blob)
            r, _ = self.chat(c, {"message": "pick the keyless one", "ai": "ai8"})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.get_json()["data"]["fallback"])          # a working AI answered instead

    def test_93_teacher_ai_test_is_teacher_only_and_never_shows_keys(self):
        hdr = {"X-CSRFToken": CSRF}
        self.assertIn(self.student_client(self.sa).post("/api/v1/teacher/ai/test", headers=hdr).status_code, (401, 403))
        with mock.patch("backend.ai.provider.requests.post", Router()):
            r = self.teacher_client().post("/api/v1/teacher/ai/test", headers=hdr)
        self.assertEqual(r.status_code, 200)
        rows = r.get_json()["data"]["results"]
        self.assertTrue(rows and all(x["status"] == "ok" for x in rows), rows)
        self.assertNotIn(GKEY, r.get_data(as_text=True))
        self.assertNotIn(QKEY, r.get_data(as_text=True))

    def test_99_original_records_untouched(self):
        now = _snapshot(self.db)
        for table, rows in self.original_snapshot.items():
            if table not in now:      # test_attempts is intentionally replaced by exam_attempts (migration 009)
                continue
            self.assertTrue(_same_old_data(rows, now[table]), f"data changed in {table}")


if __name__ == "__main__":
    unittest.main()
