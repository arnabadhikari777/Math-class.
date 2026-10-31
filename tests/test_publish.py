# -*- coding: utf-8 -*-
"""Draft / publish workflow + migration 011. Runs on a TEMPORARY copy of the project.
Run: python -m unittest tests.test_publish -v   (or: python -m unittest discover -s tests)"""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import importlib, os, shutil, sqlite3, sys, tempfile, unittest  # noqa: E402,E401
from datetime import datetime  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from appcase import AppCase, ROOT, CSRF, q_item  # noqa: E402


class PublishTests(AppCase):
    # ------------------------------------------------------------ tests
    def test_create_is_draft_hidden_and_silent(self):
        n0 = len(self.notifications())
        t, tid = self.new_test("Silent")
        row = self.q("SELECT * FROM tests WHERE id=?", tid)[0]
        self.assertEqual((row["status"], row["is_active"], row["notified_at"]), ("draft", 0, None))
        self.assertEqual(len(self.notifications()), n0)
        self.assertFalse("Silent" in self.student().get("/dashboard").get_data(as_text=True))
        self.assertEqual(self.student().get(f"/test/{tid}").status_code, 404)

    def test_no_attempt_or_result_policy_in_create_form(self):
        html = self.teacher().get("/teacher/tests/create").get_data(as_text=True)
        self.assertFalse("max_attempts" in html or "result_policy" in html or "Best attempt" in html)

    def test_questions_accumulate_with_no_notification(self):
        n0 = len(self.notifications())
        t, tid = self.new_test("Accumulate")
        self.assertEqual(self.add_questions(t, tid, [q_item("Q one")]).status_code, 200)
        self.assertEqual(self.add_questions(t, tid, [q_item("Q two"), q_item("Q three")]).status_code, 200)
        self.assertEqual(self.add_questions(t, tid, [q_item("Q four"), q_item("Q five"), q_item("Q six")]).status_code, 200)
        texts = [r["question_text"] for r in self.q("SELECT question_text FROM questions WHERE test_id=? ORDER BY id", tid)]
        self.assertEqual(texts, ["Q one", "Q two", "Q three", "Q four", "Q five", "Q six"])
        self.assertEqual(len(self.notifications()), n0)

    def test_empty_test_cannot_be_published(self):
        t, tid = self.new_test("Empty")
        self.post(t, f"/teacher/tests/{tid}/publish")
        row = self.q("SELECT * FROM tests WHERE id=?", tid)[0]
        self.assertEqual((row["status"], row["is_active"]), ("draft", 0))

    def test_invalid_question_blocks_publish(self):
        t, tid = self.new_test("Invalid")
        with sqlite3.connect(self.db) as c:   # a question that bypassed the studio validation
            c.execute("INSERT INTO questions (test_id,question_text,option_a,option_b,option_c,option_d,correct_option,marks)"
                      " VALUES (?,?,?,?,?,?,?,?)", (tid, "bad", "a", "b", "c", "d", "Z", 1))
        self.post(t, f"/teacher/tests/{tid}/publish")
        self.assertEqual(self.q("SELECT status FROM tests WHERE id=?", tid)[0][0], "draft")

    def test_publish_notifies_exactly_once_even_on_double_click_and_republish(self):
        t, tid = self.new_test("Once")
        self.add_questions(t, tid, [q_item()])
        n0 = len(self.notifications())
        self.post(t, f"/teacher/tests/{tid}/publish")
        self.post(t, f"/teacher/tests/{tid}/publish")        # accidental second click
        self.assertEqual(len(self.notifications()), n0 + 1)
        row = self.q("SELECT * FROM tests WHERE id=?", tid)[0]
        self.assertEqual((row["status"], row["is_active"]), ("published", 1))
        self.assertIsNotNone(row["notified_at"])
        self.assertTrue(f'/test/{tid}"' in self.student().get("/dashboard").get_data(as_text=True))
        # unpublish -> hidden, silent; republish -> visible, still silent
        self.post(t, f"/teacher/tests/{tid}/unpublish")
        self.assertEqual(self.q("SELECT is_active, status FROM tests WHERE id=?", tid)[0][:], (0, "draft"))
        self.assertFalse(f'/test/{tid}"' in self.student().get("/dashboard").get_data(as_text=True))
        self.post(t, f"/teacher/tests/{tid}/publish")
        self.assertEqual(len(self.notifications()), n0 + 1)
        # an explicit teacher action may notify again
        self.post(t, f"/teacher/tests/{tid}/notify")
        self.assertEqual(len(self.notifications()), n0 + 2)

    def test_published_test_cannot_be_edited_until_unpublished(self):
        t, tid = self.new_test("Locked")
        self.add_questions(t, tid, [q_item("keep me")])
        self.post(t, f"/teacher/tests/{tid}/publish")
        r = self.add_questions(t, tid, [q_item("sneaky")])
        self.assertEqual(r.status_code, 409)
        qid = self.q("SELECT id FROM questions WHERE test_id=?", tid)[0][0]
        self.post(t, f"/teacher/questions/delete/{qid}/{tid}")
        self.assertEqual(len(self.q("SELECT id FROM questions WHERE test_id=?", tid)), 1)
        self.post(t, f"/teacher/tests/{tid}/settings", {"duration_minutes": "99"})
        self.assertEqual(self.q("SELECT duration_minutes FROM tests WHERE id=?", tid)[0][0], 30)
        self.post(t, f"/teacher/tests/{tid}/unpublish")
        self.assertEqual(self.add_questions(t, tid, [q_item("now ok")]).status_code, 200)

    def test_delete_question_is_scoped_to_its_test(self):
        t, tid = self.new_test("Scoped A")
        t2, other = self.new_test("Scoped B")
        self.add_questions(t, tid, [q_item("A1")])
        self.add_questions(t, other, [q_item("B1")])
        qb = self.q("SELECT id FROM questions WHERE test_id=?", other)[0][0]
        self.post(t, f"/teacher/questions/delete/{qb}/{tid}")      # wrong test id in the URL
        self.assertEqual(len(self.q("SELECT id FROM questions WHERE id=?", qb)), 1)

    def test_toggle_cannot_activate_a_draft(self):
        t, tid = self.new_test("NoToggle")
        self.post(t, f"/teacher/tests/{tid}/toggle")
        self.assertEqual(self.q("SELECT is_active FROM tests WHERE id=?", tid)[0][0], 0)

    def test_review_page_shows_totals(self):
        t, tid = self.new_test("Review me")
        self.add_questions(t, tid, [q_item(marks=4), q_item(marks=3)])
        html = t.get(f"/teacher/tests/{tid}/review").get_data(as_text=True)
        self.assertTrue("Total marks: 7" in html and "Publish Test" in html)


class Migration011Tests(unittest.TestCase):
    def test_existing_tests_stay_visible_and_breakdown_is_backfilled(self):
        tmp = tempfile.mkdtemp(prefix="mig011-")
        try:
            sys.path.insert(0, ROOT)
            for n in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
                del sys.modules[n]
            migrations = importlib.import_module("backend.db.migrations")
            db = os.path.join(tmp, "database.db")
            __import__("legacy").ensure_legacy_db(db)
            full = list(migrations.MIGRATIONS)
            migrations.MIGRATIONS[:] = [m for m in full if m[0] != "011_publish_marking"]
            migrations.run_pending(db, backup=False)
            c = sqlite3.connect(db); c.row_factory = sqlite3.Row
            now = datetime.now().isoformat()
            on = c.execute("INSERT INTO tests (title,subject,class_name,duration_minutes,is_active,created_at) VALUES ('On','M','10',30,1,?)", (now,)).lastrowid
            off = c.execute("INSERT INTO tests (title,subject,class_name,duration_minutes,is_active,created_at) VALUES ('Off','M','10',30,0,?)", (now,)).lastrowid
            sid = c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version)"
                            " VALUES ('S','10','1','x@t','1','h',1,?,0,0)", (now,)).lastrowid
            vid = c.execute("INSERT INTO exam_versions (test_id,version_no,content_hash,negative_marks,is_frozen,created_at) VALUES (?,1,'h',1,1,?)", (on, now)).lastrowid
            aid = c.execute("INSERT INTO exam_attempts (version_id,test_id,student_id,attempt_no,status,started_at,deadline_at,duration_seconds,grace_seconds,score,total_marks)"
                            " VALUES (?,?,?,1,'submitted',?,?,1800,0,3,12)", (vid, on, sid, now, now)).lastrowid
            rid = c.execute("INSERT INTO results (test_id,student_id,score,total_marks,submitted_at,attempt_id) VALUES (?,?,3,12,?,?)", (on, sid, now, aid)).lastrowid
            for pos, (sel, ok, got) in enumerate([("A", 1, 4), ("B", 0, -1), (None, 0, 0)], 1):
                c.execute("INSERT INTO answers (result_id,attempt_id,question_id,position,option_a,option_b,option_c,option_d,correct_option,marks,negative_marks,selected_option,is_correct,marks_awarded)"
                          " VALUES (?,?,?,?,'a','b','c','d','A',4,1,?,?,?)", (rid, aid, pos, pos, sel, ok, got))
            c.commit(); c.close()
            migrations.MIGRATIONS[:] = full
            self.assertIn(("011_publish_marking", "applied"), migrations.run_pending(db, backup=False))
            c = sqlite3.connect(db); c.row_factory = sqlite3.Row
            for tid in (on, off):
                t = c.execute("SELECT * FROM tests WHERE id=?", (tid,)).fetchone()
                self.assertEqual((t["status"], t["result_release"]), ("published", "immediate"))
                self.assertEqual(t["notified_at"], t["created_at"])           # never re-notified
            self.assertEqual(c.execute("SELECT is_active FROM tests WHERE id=?", (off,)).fetchone()[0], 0)
            a = c.execute("SELECT * FROM exam_attempts WHERE id=?", (aid,)).fetchone()
            self.assertEqual((a["correct_count"], a["wrong_count"], a["unanswered_count"], a["positive_marks"], a["negative_marks_total"]),
                             (1, 1, 1, 4, 1))
            r = c.execute("SELECT * FROM results WHERE id=?", (rid,)).fetchone()
            self.assertEqual((r["correct_count"], r["negative_marks_total"], r["score"]), (1, 1, 3))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 3)   # nothing deleted
            c.close()
        finally:
            sys.path.remove(ROOT)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
