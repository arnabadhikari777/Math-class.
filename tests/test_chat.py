# -*- coding: utf-8 -*-
"""Student chat: privacy, requests, messages, delivered/seen, online/last-seen, notifications, read-only view-as."""
import sqlite3
import time
import unittest
from datetime import datetime

from werkzeug.security import generate_password_hash

from appcase import AppCase, CSRF


class ChatTests(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        c = sqlite3.connect(cls.db)
        ids = []
        for name in ("Ana", "Bob", "Cy"):
            ids.append(c.execute(
                "INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version)"
                " VALUES (?,?,?,?,?,?,1,?,0,0)",
                (name, "TEN", "9", name.lower() + "@t.local", "1", generate_password_hash("StudentPass123"),
                 datetime.now().isoformat())).lastrowid)
        c.commit(); c.close()
        cls.ana, cls.bob, cls.cy = ids

    def as_(self, sid, name="X"):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(student_id=sid, student_name=name, student_class="TEN", sv=0, _csrf_token=CSRF)
        return cl

    def js(self, cl, method, url, body=None):
        fn = getattr(cl, method)
        kw = {"headers": {"X-CSRFToken": CSRF}}
        if body is not None:
            kw["json"] = body
        r = fn(url, **kw)
        return r.status_code, r.get_json()

    # ---------------------------------------------------------------- privacy default + settings page
    def test_01_default_public_and_settings_page(self):
        a = self.as_(self.ana)
        self.assertEqual(a.get("/settings").status_code, 200)
        self.assertIn(b"Public", a.get("/settings").data)
        st, d = self.js(a, "get", "/api/chat/chats")
        self.assertTrue(d["data"]["privacy_public"])                       # nothing changed -> public
        r = a.post("/settings/chat-privacy", data={"privacy": "private", "csrf_token": CSRF})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self.js(a, "get", "/api/chat/chats")[1]["data"]["privacy_public"])
        a.post("/settings/chat-privacy", data={"privacy": "public", "csrf_token": CSRF})
        self.assertTrue(self.js(a, "get", "/api/chat/chats")[1]["data"]["privacy_public"])
        # garbage value is refused, state unchanged
        a.post("/settings/chat-privacy", data={"privacy": "x", "csrf_token": CSRF})
        self.assertTrue(self.js(a, "get", "/api/chat/chats")[1]["data"]["privacy_public"])

    def test_02_dashboard_has_settings_and_chat_buttons(self):
        html = self.as_(self.ana).get("/dashboard").get_data(as_text=True)
        self.assertIn("/settings", html)
        self.assertIn("/chat", html)

    # ---------------------------------------------------------------- public chat + delivered/seen/online
    def test_03_public_message_flow(self):
        a, b = self.as_(self.ana), self.as_(self.bob)
        st, d = self.js(a, "post", "/api/chat/start", {"student_id": self.bob})
        self.assertEqual(st, 200)
        cid = d["data"]["conversation_id"]
        self.assertEqual(self.js(a, "post", "/api/chat/start", {"student_id": self.bob})[1]["data"]["conversation_id"], cid)

        st, d = self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": "11111111-aaaa", "body": "  hello Bob  "})
        self.assertEqual(st, 201)
        mid = d["data"]["message"]["id"]
        self.assertEqual(d["data"]["message"]["body"], "hello Bob")
        self.assertEqual(d["data"]["message"]["status"], "sent")
        # retry with same uuid: no duplicate
        st, d2 = self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": "11111111-aaaa", "body": "hello Bob"})
        self.assertEqual((st, d2["data"]["created"]), (200, False))
        self.assertEqual(len(self.q("SELECT id FROM chat_messages WHERE conversation_id=?", cid)), 1)

        # Bob has not opened anything: still 'sent'. His bell got a notification WITHOUT the message text.
        self.assertEqual(self.q("SELECT status FROM chat_messages WHERE id=?", mid)[0][0], "sent")
        n = self.q("SELECT * FROM notifications WHERE student_id=? AND notif_type='chat'", self.bob)
        self.assertEqual(len(n), 1)
        self.assertNotIn("hello", (n[0]["message"] + n[0]["title"]).lower())
        self.assertEqual(n[0]["link"], f"/chat/{cid}")
        # Ana / Cy never see Bob's notification
        self.assertEqual(len(self.q("SELECT 1 FROM notifications WHERE student_id=?", self.ana)), 0)
        bell_cy = self.as_(self.cy).get("/api/notifications").get_json()["notifications"]
        self.assertFalse([x for x in bell_cy if x["type"] == "chat"])
        bell_bob = b.get("/api/notifications").get_json()["notifications"]
        self.assertTrue([x for x in bell_bob if x["type"] == "chat"])

        # Bob's app is open (chat list / summary) -> delivered
        st, d = self.js(b, "get", "/api/chat/summary")
        self.assertEqual(d["data"]["messages"], 1)
        self.assertEqual(self.q("SELECT status FROM chat_messages WHERE id=?", mid)[0][0], "delivered")
        st, d = self.js(b, "get", "/api/chat/chats")
        self.assertEqual(d["data"]["chats"][0]["unread"], 1)

        # Ana polls and learns it was delivered
        since = "2000-01-01T00:00:00Z"
        st, d = self.js(a, "get", f"/api/chat/c/{cid}/poll?after={mid}&since={since}")
        self.assertEqual([u["status"] for u in d["data"]["updates"] if u["id"] == mid], ["delivered"])
        self.assertTrue(d["data"]["peer"]["online"])                      # Bob just touched the site

        # Bob opens the room -> seen; bell entry cleared
        self.js(b, "get", f"/api/chat/c/{cid}/messages")
        st, d = self.js(b, "post", f"/api/chat/c/{cid}/seen", {"upto": mid})
        self.assertEqual(d["data"]["updated"], 1)
        self.assertEqual(self.q("SELECT status FROM chat_messages WHERE id=?", mid)[0][0], "seen")
        bell_bob = b.get("/api/notifications").get_json()["notifications"]
        self.assertTrue(all(x["is_read"] for x in bell_bob if x["type"] == "chat"))
        st, d = self.js(a, "get", f"/api/chat/c/{cid}/poll?after={mid}&since={since}")
        self.assertEqual([u["status"] for u in d["data"]["updates"] if u["id"] == mid], ["seen"])

        # typing indicator + last seen
        self.js(b, "post", f"/api/chat/c/{cid}/typing", {"typing": True})
        self.assertTrue(self.js(a, "get", f"/api/chat/c/{cid}/poll?after={mid}")[1]["data"]["peer"]["typing"])
        c = sqlite3.connect(self.db)
        c.execute("UPDATE chat_profiles SET last_seen_at='2020-01-01T10:00:00.000000' WHERE student_id=?", (self.bob,))
        c.commit(); c.close()
        peer = self.js(a, "get", f"/api/chat/c/{cid}/messages")[1]["data"]["peer"]
        self.assertFalse(peer["online"])
        self.assertEqual(peer["last_seen"], "2020-01-01T10:00:00.000000Z")

        # delete for everyone: only the sender may
        self.assertEqual(self.js(b, "post", f"/api/chat/messages/{mid}/delete")[0], 403)
        self.assertEqual(self.js(a, "post", f"/api/chat/messages/{mid}/delete")[0], 200)
        m = self.js(b, "get", f"/api/chat/c/{cid}/messages")[1]["data"]["messages"][0]
        self.assertTrue(m["deleted"]); self.assertEqual(m["body"], "")

    # ---------------------------------------------------------------- strangers can never read/write
    def test_04_outsider_blocked(self):
        a = self.as_(self.ana)
        cid = self.js(a, "post", "/api/chat/start", {"student_id": self.cy})[1]["data"]["conversation_id"]
        b = self.as_(self.bob)
        self.assertEqual(self.js(b, "get", f"/api/chat/c/{cid}/messages")[0], 404)
        self.assertEqual(self.js(b, "post", f"/api/chat/c/{cid}/messages", {"uuid": "22222222-bbbb", "body": "x"})[0], 404)
        self.assertEqual(self.js(b, "get", f"/api/chat/c/{cid}/poll")[0], 404)
        self.assertEqual(b.get(f"/chat/{cid}").status_code, 404)
        self.assertEqual(self.as_(self.cy).get(f"/chat/{cid}").status_code, 200)
        self.assertEqual(self.app.test_client().get("/api/chat/chats").status_code, 401)   # logged out
        # CSRF still enforced
        r = a.post(f"/api/chat/c/{cid}/messages", json={"uuid": "33333333-cccc", "body": "x"})
        self.assertEqual(r.status_code, 400)

    def test_05_validation(self):
        a = self.as_(self.ana)
        cid = self.js(a, "post", "/api/chat/start", {"student_id": self.cy})[1]["data"]["conversation_id"]
        self.assertEqual(self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": "44444444-dddd", "body": "   "})[0], 400)
        self.assertEqual(self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": "44444444-dddd", "body": "x" * 2001})[0], 400)
        self.assertEqual(self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": "bad uuid!", "body": "ok"})[0], 400)
        self.assertEqual(self.js(a, "post", "/api/chat/start", {"student_id": self.ana})[0], 400)        # myself
        self.assertEqual(self.js(a, "post", "/api/chat/start", {"student_id": 99999})[0], 404)
        # script text stays text (the page renders it with textContent; the API stores it verbatim)
        st, d = self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": "55555555-eeee", "body": "<script>alert(1)</script>"})
        self.assertEqual(st, 201)

    # ---------------------------------------------------------------- private account = request first
    def test_06_private_requires_request(self):
        c = sqlite3.connect(self.db)
        eve = c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version)"
                        " VALUES ('Eve','TEN','7','eve@t.local','1',?,1,?,0,0)",
                        (generate_password_hash("StudentPass123"), datetime.now().isoformat())).lastrowid
        c.commit(); c.close()
        e, a = self.as_(eve), self.as_(self.ana)
        e.post("/settings/chat-privacy", data={"privacy": "private", "csrf_token": CSRF})
        st, d = self.js(a, "post", "/api/chat/start", {"student_id": eve})
        self.assertEqual((st, d["error"]["code"]), (403, "needs_request"))
        people = self.js(a, "get", "/api/chat/people?q=Eve")[1]["data"]["people"]
        self.assertEqual([(p["name"], p["is_public"]) for p in people], [("Eve", False)])
        self.assertEqual(self.js(a, "post", "/api/chat/requests", {"student_id": eve})[0], 201)
        self.assertEqual(self.js(a, "post", "/api/chat/requests", {"student_id": eve})[1]["error"]["code"], "already_pending")
        self.assertTrue(self.q("SELECT 1 FROM notifications WHERE student_id=? AND link='/chat?tab=requests'", eve))
        reqs = self.js(e, "get", "/api/chat/chats")[1]["data"]["requests"]
        self.assertEqual(len(reqs), 1)
        # only the receiver may answer
        self.assertEqual(self.js(a, "post", f"/api/chat/requests/{reqs[0]['id']}/accept")[0], 404)
        st, d = self.js(e, "post", f"/api/chat/requests/{reqs[0]['id']}/accept")
        self.assertEqual(st, 200)
        cid = d["data"]["conversation_id"]
        self.assertEqual(self.js(e, "post", f"/api/chat/requests/{reqs[0]['id']}/accept")[0], 400)      # answered once
        self.assertEqual(self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": "66666666-ffff", "body": "hi"})[0], 201)
        self.assertTrue(self.q("SELECT 1 FROM notifications WHERE student_id=? AND link=?", self.ana, f"/chat/{cid}"))

    # ---------------------------------------------------------------- one bell row per chat, push only when away
    def test_07_one_bell_row_per_chat(self):
        a = self.as_(self.ana)
        cid = self.js(a, "post", "/api/chat/start", {"student_id": self.bob})[1]["data"]["conversation_id"]
        for i in range(3):
            self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": f"7777777{i}-gggg", "body": f"m{i}"})
        rows = self.q("SELECT * FROM notifications WHERE student_id=? AND link=?", self.bob, f"/chat/{cid}")
        self.assertEqual(len(rows), 1)
        self.assertIn("new messages", rows[0]["message"])

    # ---------------------------------------------------------------- teacher viewing as a student changes nothing
    def test_08_view_as_student_is_read_only(self):
        a = self.as_(self.ana)
        cid = self.js(a, "post", "/api/chat/start", {"student_id": self.bob})[1]["data"]["conversation_id"]
        self.js(a, "post", f"/api/chat/c/{cid}/messages", {"uuid": "88888888-hhhh", "body": "secret"})
        c = sqlite3.connect(self.db)
        c.execute("UPDATE chat_messages SET status='sent' WHERE conversation_id=?", (cid,))
        c.commit(); c.close()
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(student_id=self.bob, student_name="Bob", student_class="TEN", sv=0, _csrf_token=CSRF,
                     impersonating=True, impersonation_started=datetime.now().isoformat(), is_teacher=True)
        before = self.q("SELECT last_seen_at FROM chat_profiles WHERE student_id=?", self.bob)
        self.assertEqual(cl.get("/api/chat/chats").status_code, 200)
        self.js(cl, "get", f"/api/chat/c/{cid}/messages")
        self.js(cl, "post", f"/api/chat/c/{cid}/seen", {"upto": 99999})
        self.assertEqual(self.q("SELECT status FROM chat_messages WHERE conversation_id=?", cid)[0][0], "sent")
        self.assertEqual(self.js(cl, "post", f"/api/chat/c/{cid}/messages", {"uuid": "99999999-iiii", "body": "x"})[0], 403)
        self.assertEqual(before, self.q("SELECT last_seen_at FROM chat_profiles WHERE student_id=?", self.bob))

    # ---------------------------------------------------------------- pages
    def test_09_pages_render(self):
        a = self.as_(self.ana)
        for url in ("/chat", "/chat?tab=people", "/settings", "/student/chat"):
            r = a.get(url)
            self.assertIn(r.status_code, (200, 302), url)
        self.assertTrue(a.get("/student/chat").headers["Location"].endswith("/chat"))
        self.assertEqual(self.app.test_client().get("/chat").status_code, 302)    # login required
        self.assertIn("chat.js", a.get("/dashboard").get_data(as_text=True))

    def test_10_blocked_student_not_listed(self):
        c = sqlite3.connect(self.db)
        c.execute("UPDATE students SET is_blocked=1 WHERE id=?", (self.cy,))
        c.commit(); c.close()
        a = self.as_(self.ana)
        names = [p["name"] for p in self.js(a, "get", "/api/chat/people")[1]["data"]["people"]]
        self.assertNotIn("Cy", names)
        self.assertEqual(self.js(a, "post", "/api/chat/start", {"student_id": self.cy})[0], 404)


if __name__ == "__main__":
    unittest.main()
