# -*- coding: utf-8 -*-
"""Stage 5/6: delete dialog (no typing), soft delete + restore, persistent login / real logout,
teacher result summary + answer sheet + PDF, removal of the old unused template."""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json, sqlite3, unittest  # noqa: E401,E402
from datetime import datetime  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from appcase import AppCase, CSRF, q_item  # noqa: E402

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))


class Stage56(AppCase):
    def mk_student(self, email="login@t.local", pw="Passw0rd!x"):
        with sqlite3.connect(self.db) as c:
            sid = c.execute(
                "INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version)"
                " VALUES ('Login Kid','TEN','77',?,?,?,1,?,0,0)",
                (email, "9" + email[:9].encode().hex()[:9], generate_password_hash(pw), datetime.now().isoformat())).lastrowid
        return sid, email, pw

    def login(self, remember):
        sid, email, pw = self.mk_student(email=f"r{int(remember)}{id(self) % 9999}@t.local")
        cl = self.app.test_client()
        with cl.session_transaction() as s:
            s["_csrf_token"] = CSRF
        data = {"email": email, "password": pw, "remember_form": "1", "csrf_token": CSRF}
        if remember:
            data["remember"] = "1"
        r = cl.post("/login", data=data, headers={"X-CSRFToken": CSRF})
        return cl, r

    # ------------------------------------------------------------ delete dialog
    def test_delete_uses_dialog_soft_delete_restore_and_permanent_without_typing(self):
        t, tid = self.published_test("DelMe", n=2)
        cl = self.student(); self.begin(cl, tid)
        page = t.get("/teacher/tests").get_data(as_text=True)
        for needle in ("Are you sure you want to delete this test?", "Delete Test", "js-confirm", "cfCancel"):
            self.assertIn(needle, page)
        self.assertNotIn("Type the exact name", page)
        self.post(t, f"/teacher/tests/{tid}/archive")                        # what the dialog's red button submits
        self.assertEqual(self.q("SELECT is_archived FROM tests WHERE id=?", tid)[0][0], 1)
        self.assertEqual(len(self.attempts(tid)), 1)                         # history kept
        self.assertEqual(self.student().get(f"/test/{tid}").status_code, 404)  # students can no longer open it
        self.assertIn("Deleted tests", t.get("/teacher/tests").get_data(as_text=True))
        self.post(t, f"/teacher/tests/{tid}/restore")
        self.assertEqual(self.q("SELECT is_archived FROM tests WHERE id=?", tid)[0][0], 0)
        self.post(t, f"/teacher/tests/{tid}/delete")                          # forever: Cancel / Delete, nothing to type
        self.assertEqual(self.q("SELECT COUNT(*) FROM tests WHERE id=?", tid)[0][0], 0)

    # ------------------------------------------------------------ login / logout
    def test_remember_me_cookie_and_flags(self):
        cl, r = self.login(remember=True)
        self.assertEqual(r.status_code, 302)
        ck = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("session=")][0].lower()
        self.assertIn("httponly", ck); self.assertIn("samesite=lax", ck)
        self.assertTrue("expires=" in ck or "max-age=" in ck)                 # persistent: survives closing the browser
        self.assertEqual(cl.get("/dashboard").status_code, 200)
        cl2, r2 = self.login(remember=False)
        ck2 = [h for h in r2.headers.getlist("Set-Cookie") if h.startswith("session=")][0].lower()
        self.assertFalse("expires=" in ck2 or "max-age=" in ck2)              # browser-session cookie only
        self.assertNotIn("password", ck.replace("passw0rd", ""))              # nothing secret in the cookie text

    def test_logout_really_invalidates_the_session(self):
        cl, r = self.login(remember=True)
        stolen = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("session=")][0].split(";")[0].split("=", 1)[1]
        self.assertEqual(cl.get("/dashboard").status_code, 200)
        with cl.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        out = cl.post("/logout", data={"csrf_token": CSRF}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(out.status_code, 302)
        self.assertEqual(cl.get("/dashboard").status_code, 302)               # protected page closed
        # a copy of the old cookie (browser history / another device) is dead too
        thief = self.app.test_client()
        thief.set_cookie("session", stolen)
        self.assertEqual(thief.get("/dashboard").status_code, 302)
        self.assertEqual(self.q("SELECT COUNT(*) FROM revoked_sessions")[0][0] >= 1, True)

    def test_old_sessions_without_id_keep_working_and_teacher_logout_revokes(self):
        self.assertEqual(self.student().get("/dashboard").status_code, 200)   # session made before the upgrade
        t = self.teacher()
        with t.session_transaction() as s:
            s["jti"] = "teacher-jti-1"
        self.assertEqual(t.get("/teacher/tests").status_code, 200)
        t.post("/teacher/logout", data={"csrf_token": CSRF}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(self.q("SELECT COUNT(*) FROM revoked_sessions WHERE jti='teacher-jti-1'")[0][0], 1)
        t2 = self.teacher()
        with t2.session_transaction() as s:
            s["jti"] = "teacher-jti-1"
        self.assertEqual(t2.get("/teacher/tests").status_code, 302)           # replaying the revoked id fails

    # ------------------------------------------------------------ teacher results
    def test_teacher_results_summary_answer_sheet_and_pdf(self):
        t, tid = self.new_test("Rep", negative_marks="1")
        self.add_questions(t, tid, [q_item(f"Rep Q{i}", "A", 4) for i in range(4)])
        self.post(t, f"/teacher/tests/{tid}/publish")
        cl = self.student(); self.begin(cl, tid)
        ids = [r["id"] for r in self.q("SELECT id FROM attempt_questions WHERE attempt_id=? ORDER BY position", self.attempts(tid)[0]["id"])]
        pick = {f"q_{ids[0]}": {"v": "A", "t": 1}, f"q_{ids[1]}": {"v": "A", "t": 1}, f"q_{ids[2]}": {"v": "B", "t": 1}}
        self.post(cl, f"/test/{tid}/submit", {"answers_json": json.dumps(pick)})        # 8 - 1 = 7 / 16, one unanswered
        page = t.get(f"/teacher/tests/{tid}/results").get_data(as_text=True)
        for needle in ("Average", "Highest", "Lowest", "Passed", "Print", "7 / 16", "2 / 1 / 1", "−1"):
            self.assertIn(needle, page)
        rid = self.q("SELECT id FROM results WHERE test_id=?", tid)[0][0]
        sheet = t.get(f"/teacher/tests/{tid}/result/{rid}").get_data(as_text=True)
        for needle in ("Incorrect −1", "Not answered 0", "Correct +4", "Negative deducted"):
            self.assertIn(needle, sheet)
        pdf = t.get(f"/teacher/tests/{tid}/result/{rid}/pdf")
        self.assertIn(pdf.status_code, (200, 302))
        if pdf.status_code == 200:
            self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertEqual(self.student().get(f"/teacher/tests/{tid}/results").status_code, 302)   # students can't open it

    def test_rules_reject_nonsense_numbers(self):
        t, tid = self.new_test("Rules", negative_marks="1")
        self.post(t, f"/teacher/tests/{tid}/settings", {"duration_minutes": "30", "negative_marks": "nan", "grace_seconds": "x"})
        self.assertEqual(self.q("SELECT negative_marks FROM tests WHERE id=?", tid)[0][0], 0)
        self.post(t, f"/teacher/tests/{tid}/settings", {"duration_minutes": "0", "negative_marks": "1"})      # invalid duration
        self.assertEqual(self.q("SELECT duration_minutes FROM tests WHERE id=?", tid)[0][0], 30)

    def test_old_unused_add_questions_template_is_gone(self):
        self.assertFalse(_os.path.exists(_os.path.join(ROOT, "templates", "teacher", "add_questions.html")))
        self.assertTrue(self.teacher().get("/teacher/tests").status_code == 200)


if __name__ == "__main__":
    unittest.main()
