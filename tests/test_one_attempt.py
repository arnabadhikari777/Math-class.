# -*- coding: utf-8 -*-
"""One attempt per student + two-step start. Run: python -m unittest tests.test_one_attempt -v"""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import sqlite3, unittest  # noqa: E401,E402
from datetime import datetime  # noqa: E402
from appcase import AppCase  # noqa: E402


class OneAttemptTests(AppCase):
    def submit_all(self, cl, tid, pick="A"):
        att = self.attempts(tid)[0]
        qids = [r["id"] for r in self.q("SELECT id FROM attempt_questions WHERE attempt_id=?", att["id"])]
        return self.post(cl, f"/test/{tid}/submit", {f"q_{i}": pick for i in qids})

    def test_opening_info_and_confirm_pages_never_start_an_attempt(self):
        t, tid = self.published_test("Info only")
        cl = self.student()
        info = cl.get(f"/test/{tid}")
        self.assertEqual(info.status_code, 200)
        html = info.get_data(as_text=True)
        self.assertTrue("Continue to Confirmation" in html and "Maths" in html and "30" in html)
        self.assertEqual(len(self.attempts(tid)), 0)
        # the confirm screen cannot be reached directly, before the first step
        r = cl.get(f"/test/{tid}/confirm")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith(f"/test/{tid}"))
        self.assertEqual(len(self.attempts(tid)), 0)

    def test_start_without_first_confirmation_is_refused(self):
        t, tid = self.published_test("No skip")
        cl = self.student()
        r = self.post(cl, f"/test/{tid}/start")                      # skipped step 1
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.attempts(tid)), 0)
        self.assertEqual(cl.get(f"/test/{tid}/exam").status_code, 302)   # no paper without an attempt
        self.assertEqual(len(self.attempts(tid)), 0)

    def test_two_step_flow_creates_exactly_one_attempt(self):
        t, tid = self.published_test("Flow")
        cl = self.student()
        self.assertEqual(self.post(cl, f"/test/{tid}/continue").status_code, 302)
        conf = cl.get(f"/test/{tid}/confirm").get_data(as_text=True)
        self.assertTrue("Are you ready to start this examination?" in conf and "Confirm &amp; Start Test" in conf
                        and "Go Back" in conf)
        self.assertEqual(len(self.attempts(tid)), 0)                 # still nothing after both screens
        self.assertEqual(self.post(cl, f"/test/{tid}/start").status_code, 302)
        self.assertEqual(len(self.attempts(tid)), 1)
        self.post(cl, f"/test/{tid}/start")                          # double click / second tab
        self.post(cl, f"/test/{tid}/continue"); self.post(cl, f"/test/{tid}/start")
        self.assertEqual(len(self.attempts(tid)), 1)
        page = cl.get(f"/test/{tid}/exam")
        self.assertEqual(page.status_code, 200)
        self.assertTrue("Flow Q0" in page.get_data(as_text=True))

    def test_refresh_resumes_same_attempt_with_same_deadline(self):
        t, tid = self.published_test("Refresh")
        cl = self.student(); self.begin(cl, tid)
        first = self.attempts(tid)[0]
        for _ in range(3):
            self.assertEqual(cl.get(f"/test/{tid}/exam").status_code, 200)
            self.assertEqual(cl.get(f"/test/{tid}").status_code, 302)        # info page sends a running student back
        again = self.attempts(tid)
        self.assertEqual((len(again), again[0]["deadline_at"]), (1, first["deadline_at"]))

    def test_after_submit_nothing_can_start_again(self):
        t, tid = self.published_test("Once only")
        cl = self.student(); self.begin(cl, tid)
        self.submit_all(cl, tid)
        self.assertEqual(self.attempts(tid)[0]["status"], "submitted")
        other_tab = self.student()
        info = other_tab.get(f"/test/{tid}")
        self.assertEqual(info.status_code, 302)                       # no "start" screen any more
        self.assertEqual(self.post(other_tab, f"/test/{tid}/continue").status_code, 302)
        self.post(other_tab, f"/test/{tid}/start")
        self.assertEqual(other_tab.get(f"/test/{tid}/exam").status_code, 302)
        self.assertEqual(len(self.attempts(tid)), 1)
        self.assertEqual(len(self.q("SELECT id FROM results WHERE test_id=?", tid)), 1)

    def test_dashboard_shows_result_not_start_after_submit(self):
        t, tid = self.published_test("Dash")
        cl = self.student(); self.begin(cl, tid); self.submit_all(cl, tid)
        html = cl.get("/dashboard").get_data(as_text=True)
        self.assertFalse(f'/test/{tid}"' in html)                    # no Start link
        self.assertTrue(f"/test/{tid}/result" in html)

    def test_rejected_late_attempt_also_uses_up_the_one_attempt(self):
        t, tid = self.published_test("Rejected", late_policy="reject", grace_seconds="0")
        cl = self.student(); self.begin(cl, tid)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE exam_attempts SET deadline_at='2000-01-01T00:00:00' WHERE test_id=?", (tid,))
        self.submit_all(cl, tid)                                      # too late -> rejected
        self.assertEqual(self.attempts(tid)[0]["status"], "rejected")
        self.begin_expect_blocked(cl, tid)

    def begin_expect_blocked(self, cl, tid):
        self.post(cl, f"/test/{tid}/continue"); self.post(cl, f"/test/{tid}/start")
        self.assertEqual(len(self.attempts(tid)), 1)
        self.assertEqual(cl.get(f"/test/{tid}/exam").status_code, 302)

    def test_old_multi_attempt_history_is_kept_and_blocks_new_attempts(self):
        t, tid = self.published_test("History")
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db) as c:
            vid = c.execute("INSERT INTO exam_versions (test_id,version_no,content_hash,negative_marks,is_frozen,created_at)"
                            " VALUES (?,1,'h',0,1,?)", (tid, now)).lastrowid
            for no in (1, 2):
                c.execute("INSERT INTO exam_attempts (version_id,test_id,student_id,attempt_no,status,started_at,deadline_at,"
                          "duration_seconds,grace_seconds,score,total_marks) VALUES (?,?,?,?,'submitted',?,?,1800,0,5,12)",
                          (vid, tid, self.stu, no, now, now))
        cl = self.student()
        self.post(cl, f"/test/{tid}/continue"); self.post(cl, f"/test/{tid}/start")
        self.assertEqual(len(self.attempts(tid)), 2)                  # nothing added, nothing removed

    def test_other_class_and_draft_tests_cannot_be_started(self):
        t, tid = self.published_test("Other class", class_name="9")
        cl = self.student()                                           # student is in TEN
        self.assertEqual(cl.get(f"/test/{tid}").status_code, 404)
        self.assertEqual(self.post(cl, f"/test/{tid}/continue").status_code, 404)
        self.assertEqual(self.post(cl, f"/test/{tid}/start").status_code, 404)
        self.assertEqual(cl.get(f"/test/{tid}/exam").status_code, 404)
        t2, draft = self.new_test("Draft one")
        self.assertEqual(cl.get(f"/test/{draft}").status_code, 404)
        self.assertEqual(self.post(cl, f"/test/{draft}/start").status_code, 404)
        self.assertEqual(len(self.attempts(tid)) + len(self.attempts(draft)), 0)


if __name__ == "__main__":
    unittest.main()
