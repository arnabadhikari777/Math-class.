# -*- coding: utf-8 -*-
"""Unique usernames (migration 016) + per-student notification clean-up (migration 017).
Runs on a TEMPORARY copy of the project. Run: python -m pytest tests/test_upgrade_username_notifications.py"""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import sqlite3, unittest  # noqa: E402,E401
from datetime import datetime  # noqa: E402
from unittest import mock  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from appcase import AppCase, CSRF  # noqa: E402

REG = {"name": "Rahul Das", "class_name": "10", "roll": "5", "email": "rahul1@t.local", "mobile": "9876543210",
       "password": "Str0ng!Passw0rd", "confirm_password": "Str0ng!Passw0rd"}


class UsernameUnit(unittest.TestCase):
    def test_slugify(self):
        from backend.utils import usernames as u
        self.assertEqual(u.base_username("Rahul Das"), "rahuldas")
        self.assertEqual(u.base_username("  José  O'Neil-Smith "), "joseoneilsmith")
        self.assertEqual(u.base_username("রাহুল দাস"), "rahuldas")
        self.assertEqual(u.base_username("田中太郎"), "student")      # nothing usable -> deterministic fallback base
        self.assertEqual(u.base_username(""), "student")
        self.assertEqual(u.base_username("Al"), "studental")           # min length 3
        self.assertLessEqual(len(u.base_username("x" * 200)), 26)
        self.assertRegex(u.base_username("<script>alert(1)</script>"), r"^[a-z0-9]+$")

    def test_migration_backfill_and_idempotent(self):
        from backend.db import m016_username as m
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
        c.executemany("INSERT INTO students (id,name,email) VALUES (?,?,?)",
                      [(1, "Rahul Das", "a"), (2, "rahul das", "b"), (3, "রাহুল দাস", "c"), (4, "???", "d"), (5, "???", "e")])
        note = m.up(c)
        names = [r[0] for r in c.execute("SELECT username FROM students ORDER BY id")]
        self.assertEqual(names, ["rahuldas", "rahuldas2", "rahuldas3", "student", "student2"])
        self.assertIn("5", note)
        before = list(c.execute("SELECT id,username FROM students"))
        m.up(c)                                                           # second run: nothing changes
        self.assertEqual(before, list(c.execute("SELECT id,username FROM students")))
        with self.assertRaises(sqlite3.IntegrityError):                   # DB-level, case-insensitive uniqueness
            c.execute("UPDATE students SET username='RAHULDAS' WHERE id=2")

    def test_migration_keeps_existing_usernames(self):
        from backend.db import m016_username as m
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, username TEXT)")
        c.execute("INSERT INTO students VALUES (1,'Rahul Das','custom')")
        c.execute("INSERT INTO students VALUES (2,'Rahul Das',NULL)")
        m.up(c)
        self.assertEqual([r[0] for r in c.execute("SELECT username FROM students ORDER BY id")], ["custom", "rahuldas"])

    def test_assign_retries_when_two_registrations_race(self):
        from backend.utils import usernames as u
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, username TEXT)")
        c.execute("CREATE UNIQUE INDEX uq ON students(username COLLATE NOCASE)")
        c.execute("INSERT INTO students VALUES (1,'Rahul Das','rahuldas')")
        c.execute("INSERT INTO students VALUES (2,'Rahul Das',NULL)")
        with mock.patch.object(u, "is_taken", return_value=False):        # pretend the pre-check lost the race
            self.assertEqual(u.assign_username(c, 2, "Rahul Das"), "rahuldas2")
        self.assertEqual(u.assign_username(c, 2, "Other Name"), "rahuldas2")   # never regenerated


class UsernameFlows(AppCase):
    def anon(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_csrf_token"] = CSRF
        return c

    def reg(self, **over):
        d = dict(REG, **over); d["csrf_token"] = CSRF
        return self.anon().post("/register", data=d, follow_redirects=True)

    def uname(self, email):
        return self.q("SELECT username FROM students WHERE email=?", email)[0][0]

    def test_register_generates_unique_usernames_for_same_name(self):
        self.reg(email="r1@t.local")
        self.reg(email="r2@t.local")
        self.reg(email="r3@t.local", name="RAHUL  DAS")
        self.assertEqual([self.uname(e) for e in ("r1@t.local", "r2@t.local", "r3@t.local")],
                         ["rahuldas", "rahuldas2", "rahuldas3"])

    def test_reregistering_unverified_email_keeps_username(self):
        self.reg(email="again@t.local", name="Priya Sen")
        first = self.uname("again@t.local")
        self.reg(email="again@t.local", name="Priya Sen Changed")
        self.assertEqual(self.uname("again@t.local"), first)
        self.assertEqual(self.q("SELECT COUNT(*) FROM students WHERE username=?", first)[0][0], 1)

    def test_bengali_registration(self):
        self.reg(email="bn@t.local", name="অর্ণব অধিকারী")
        self.assertRegex(self.uname("bn@t.local"), r"^[a-z0-9]+$")

    def test_teacher_admit_gets_username(self):
        t = self.teacher()
        self.post(t, "/teacher/students/admit", {"name": "Admit Me", "class_name": "10", "email": "adm@t.local",
                                                 "mobile": "9876543210", "password": "Str0ng!Passw0rd", "roll": "9"})
        self.assertEqual(self.uname("adm@t.local"), "admitme")

    def test_username_survives_login_and_is_not_regenerated(self):
        c = self.app.test_client()
        db = sqlite3.connect(self.db)
        db.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,username)"
                   " VALUES ('Login Guy','TEN','7','lg@t.local','1',?,1,?,'fixedname')",
                   (generate_password_hash("StudentPass123"), datetime.now().isoformat()))
        db.commit(); db.close()
        for _ in range(2):
            c.post("/login", data={"email": "lg@t.local", "password": "StudentPass123", "csrf_token": CSRF},
                   headers={"X-CSRFToken": CSRF})
            c.get("/profile")
            c.post("/logout", data={"csrf_token": CSRF}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(self.uname("lg@t.local"), "fixedname")

    def test_profile_shows_username_and_lazily_assigns_for_legacy_row(self):
        # self.stu was inserted without a username (like a row created by older code)
        html = self.student().get("/profile").get_data(as_text=True)
        self.assertEqual(self.uname("s@t.local"), "sam")
        self.assertIn("@sam", html)
        self.assertIn("data-copy", html)

    def test_username_is_escaped(self):
        c = sqlite3.connect(self.db)
        c.execute("UPDATE students SET username=? WHERE id=?", ('x"><b>', self.stu)); c.commit(); c.close()
        html = self.student().get("/profile").get_data(as_text=True)
        self.assertNotIn('x"><b>', html)
        c = sqlite3.connect(self.db)
        c.execute("UPDATE students SET username='sam' WHERE id=?", (self.stu,)); c.commit(); c.close()

    def test_profile_hides_internal_notes(self):
        c = sqlite3.connect(self.db)
        c.execute("UPDATE students SET notes_internal='SECRET-TEACHER-NOTE' WHERE id=?", (self.stu,)); c.commit(); c.close()
        self.assertNotIn("SECRET-TEACHER-NOTE", self.student().get("/profile").get_data(as_text=True))


class NotificationIsolation(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        c = sqlite3.connect(cls.db)
        mk = lambda n, e, cls_: c.execute(
            "INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version)"
            " VALUES (?,?,?,?,?,?,1,?,0,0)", (n, cls_, "1", e, "1", generate_password_hash("x"), datetime.now().isoformat())).lastrowid
        cls.other = mk("Other", "o@t.local", "TEN")
        cls.nine = mk("Nine", "n@t.local", "NINE")
        c.commit(); c.close()

    def as_student(self, sid, cls_="TEN"):
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s.update(student_id=sid, student_name="x", student_class=cls_, sv=0, _csrf_token=CSRF)
        return cl

    def seed(self):
        c = sqlite3.connect(self.db)
        c.execute("PRAGMA foreign_keys=ON")
        for t in ("notification_dismissals", "notification_reads", "notifications", "announcements"):
            c.execute(f"DELETE FROM {t}")
        now = datetime.now().isoformat()
        ins = lambda title, cls_, sid: c.execute(
            "INSERT INTO notifications (title,message,notif_type,class_name,link,created_at,student_id) VALUES (?,?,?,?,?,?,?)",
            (title, "m", "info", cls_, "", now, sid)).lastrowid
        ids = {"shared_ten": ins("shared ten", "TEN", None), "shared_all": ins("shared all", "", None),
               "shared_nine": ins("shared nine", "NINE", None), "mine": ins("mine", "", self.stu),
               "theirs": ins("theirs", "", self.other)}
        c.execute("INSERT INTO announcements (title,content,class_name,created_at,is_active) VALUES ('A','B','TEN',?,1)", (now,))
        c.commit(); c.close()
        return ids

    def titles(self, cl):
        return [n["title"] for n in cl.get("/api/notifications").get_json()["notifications"]]

    def test_list_marks_unread_and_bell_mark_all_read(self):
        self.seed()
        cl = self.student()
        data = cl.get("/api/notifications").get_json()
        self.assertEqual(data["unread_count"], 3)                          # shared ten + shared all + mine
        r = self.post(cl, "/api/notifications/mark-read", {})
        self.assertEqual(r.status_code, 200)
        r = cl.post("/api/notifications/mark-read", json={}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.get_json()["marked"], 0)                         # second call is a no-op (no duplicates)
        data = cl.get("/api/notifications").get_json()
        self.assertEqual(data["unread_count"], 0)
        self.assertEqual(len(data["notifications"]), 3)                     # history preserved
        self.assertEqual(self.q("SELECT COUNT(*) FROM notification_reads WHERE student_id=?", self.other)[0][0], 0)

    def test_mark_read_ignores_ids_the_student_cannot_see(self):
        ids = self.seed()
        cl = self.student()
        r = cl.post("/api/notifications/mark-read", json={"ids": [ids["theirs"], ids["shared_nine"], "x"]},
                    headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.get_json()["marked"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM notification_reads")[0][0], 0)

    def test_all_clean_is_per_student(self):
        ids = self.seed()
        me, other, nine = self.student(), self.as_student(self.other), self.as_student(self.nine, "NINE")
        self.post(me, "/api/notifications/mark-read", {})
        r = me.post("/api/notifications/clear", json={}, headers={"X-CSRFToken": CSRF})
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual((j["personal_deleted"], j["shared_dismissed"], j["unread_count"]), (1, 2, 0))
        self.assertEqual(self.titles(me), [])
        # other student of the same class: shared still visible, own personal untouched
        self.assertEqual(sorted(self.titles(other)), ["shared all", "shared ten", "theirs"])
        self.assertEqual(sorted(self.titles(nine)), ["shared all", "shared nine"])
        # physical rows: shared notifications and announcements survive, only my personal row is gone
        self.assertEqual(self.q("SELECT COUNT(*) FROM notifications WHERE id=?", ids["mine"])[0][0], 0)
        for k in ("shared_ten", "shared_all", "shared_nine", "theirs"):
            self.assertEqual(self.q("SELECT COUNT(*) FROM notifications WHERE id=?", ids[k])[0][0], 1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM announcements")[0][0], 1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM notification_reads WHERE student_id=?", self.stu)[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM notification_dismissals WHERE student_id!=?", self.stu)[0][0], 0)

    def test_new_notification_after_clean_is_visible(self):
        self.seed()
        me = self.student()
        self.post(me, "/api/notifications/clear", {})
        from backend.services.notification_service import create_notification
        with self.app.app_context():
            create_notification("Fresh", "x", "info", "TEN")
        self.assertEqual(self.titles(me), ["Fresh"])
        self.assertIn("Fresh", [n["title"] for n in self.as_student(self.other).get("/api/notifications").get_json()["notifications"]])

    def test_clear_requires_login_csrf_and_blocks_impersonation(self):
        self.seed()
        anon = self.app.test_client()
        with anon.session_transaction() as s:
            s["_csrf_token"] = CSRF
        self.assertIn(anon.post("/api/notifications/clear", json={}, headers={"X-CSRFToken": CSRF}).status_code, (302, 401, 403))
        me = self.student()
        r = me.post("/api/notifications/clear", json={}, headers={"X-CSRFToken": "wrong"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.titles(me)), 3)
        imp = self.student()
        with imp.session_transaction() as s:
            s["impersonating"] = True
            s["impersonation_started"] = datetime.now().isoformat()
        r = imp.post("/api/notifications/clear", json={}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.status_code, 403)               # existing global read-only guard for "view as student"
        r = imp.post("/api/notifications/mark-read", json={}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.q("SELECT COUNT(*) FROM notifications WHERE student_id=?", self.stu)[0][0], 1)

    def test_student_id_in_body_is_ignored(self):
        ids = self.seed()
        me = self.student()
        me.post("/api/notifications/clear", json={"student_id": self.other}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(self.q("SELECT COUNT(*) FROM notifications WHERE id=?", ids["theirs"])[0][0], 1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM notification_dismissals WHERE student_id=?", self.other)[0][0], 0)

    def test_unread_badge_in_page_honours_dismissals(self):
        self.seed()
        me = self.student()
        self.assertIn('id="notifBadge"', me.get("/dashboard").get_data(as_text=True))
        self.post(me, "/api/notifications/clear", {})
        self.assertNotIn('id="notifBadge"', me.get("/dashboard").get_data(as_text=True))

    def test_push_unsubscribe_removes_only_this_devices_row(self):
        c = sqlite3.connect(self.db)
        for sid, ep in ((self.stu, "https://fcm.googleapis.com/fcm/send/mine"), (self.stu, "https://fcm.googleapis.com/fcm/send/mine2"),
                        (self.other, "https://fcm.googleapis.com/fcm/send/theirs")):
            c.execute("INSERT OR REPLACE INTO push_subscriptions (student_id,class_name,endpoint,p256dh,auth,created_at) VALUES (?,?,?,?,?,?)",
                      (sid, "TEN", ep, "k", "a", "now"))
        c.commit(); c.close()
        me = self.student()
        r = me.post("/api/push/unsubscribe", json={"endpoint": "https://fcm.googleapis.com/fcm/send/mine"}, headers={"X-CSRFToken": CSRF})
        self.assertTrue(r.get_json()["ok"])
        me.post("/api/push/unsubscribe", json={"endpoint": "https://fcm.googleapis.com/fcm/send/theirs"}, headers={"X-CSRFToken": CSRF})
        left = {r[0] for r in self.q("SELECT endpoint FROM push_subscriptions")}
        self.assertNotIn("https://fcm.googleapis.com/fcm/send/mine", left)
        self.assertIn("https://fcm.googleapis.com/fcm/send/mine2", left)    # my other device untouched
        self.assertIn("https://fcm.googleapis.com/fcm/send/theirs", left)   # someone else's untouched


if __name__ == "__main__":
    unittest.main()
