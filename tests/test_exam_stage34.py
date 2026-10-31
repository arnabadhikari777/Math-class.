# -*- coding: utf-8 -*-
"""Stage 3 (exam page, autosave, timer, final submit) and stage 4 (negative marking, result visibility).
Run: python -m unittest tests.test_exam_stage34 -v      (runs on a TEMPORARY copy; the real database is never opened)"""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json, sqlite3, time, unittest  # noqa: E401,E402
from datetime import datetime, timedelta  # noqa: E402
from appcase import AppCase, CSRF, q_item  # noqa: E402


def now_ms():
    return int(time.time() * 1000)


class Stage34(AppCase):
    # ---------------------------------------------------------------- helpers
    def qids(self, tid):
        att = self.attempts(tid)[0]
        return [r["id"] for r in self.q("SELECT id FROM attempt_questions WHERE attempt_id=? ORDER BY position", att["id"])]

    def save(self, cl, tid, delta):
        return cl.post(f"/test/{tid}/save", data=json.dumps({"answers": delta}),
                       headers={"X-CSRFToken": CSRF, "Content-Type": "application/json"})

    def finish(self, cl, tid, picks):
        """picks: list of 'A'..'D' or None, one per question (in order). Submits the way the page does."""
        ids = self.qids(tid)
        t = now_ms()
        data = {f"q_{i}": {"v": p, "t": t} for i, p in zip(ids, picks) if p}
        return self.post(cl, f"/test/{tid}/submit", {"answers_json": json.dumps(data)})

    def play(self, title, picks, neg="1", marks=4, **extra):
        """Create+publish a test with len(picks) questions (correct answer is always A), take it, submit picks."""
        t, tid = self.new_test(title, negative_marks=neg, **extra)
        self.add_questions(t, tid, [q_item(f"{title} Q{i}", "A", marks) for i in range(len(picks))])
        self.post(t, f"/teacher/tests/{tid}/publish")
        cl = self.student(); self.begin(cl, tid)
        self.finish(cl, tid, picks)
        return t, cl, tid

    def result(self, tid):
        return self.q("SELECT * FROM results WHERE test_id=? AND student_id=?", tid, self.stu)[0]

    # ================================================================ STAGE 3
    def test_migration_012_columns_exist(self):
        cols = {r["name"] for r in self.q("PRAGMA table_info(exam_attempts)")}
        self.assertTrue({"draft_meta", "draft_rev"} <= cols)

    def test_exam_page_has_navigation_confirm_dialog_and_no_forced_answers(self):
        t, tid = self.published_test("UI", n=3)
        html = self.begin(self.student(), tid).get_data(as_text=True)
        for needle in ("id=\"pal\"", "Previous", "Skip", "Next", "Clear answer", "Not visited", "Unanswered", "Submit your test?",
                       "answers_json", "Saving"):
            self.assertIn(needle, html)
        self.assertNotIn("required", html.split("<form", 1)[1].split("</form>")[0].replace("csrf", ""))   # no forced answers

    def test_autosave_returns_authoritative_answers_and_ignores_older_changes(self):
        t, tid = self.published_test("Race", n=3)
        cl = self.student(); self.begin(cl, tid)
        a, b, c = self.qids(tid)
        base = now_ms()
        d = self.save(cl, tid, {f"q_{a}": {"v": "A", "t": base + 2000}, f"q_{b}": {"v": "C", "t": base}}).get_json()
        self.assertTrue(d["ok"]); self.assertEqual(d["answers"], {str(a): "A", str(b): "C"})
        self.assertIsInstance(d["remaining"], int)
        # a stale tab / late retry / old local copy arrives afterwards with OLDER timestamps -> ignored
        d = self.save(cl, tid, {f"q_{a}": {"v": "D", "t": base + 1000}}).get_json()
        self.assertEqual(d["answers"][str(a)], "A")
        # a genuinely newer change wins
        d = self.save(cl, tid, {f"q_{a}": {"v": "B", "t": base + 3000}}).get_json()
        self.assertEqual(d["answers"][str(a)], "B")
        # clearing is a timestamped change too (an older "A" cannot bring it back)
        self.save(cl, tid, {f"q_{a}": {"v": None, "t": base + 4000}})
        d = self.save(cl, tid, {f"q_{a}": {"v": "A", "t": base + 3500}}).get_json()
        self.assertNotIn(str(a), d["answers"])
        # unknown question ids / invalid options are ignored; legacy payload still works
        d = self.save(cl, tid, {"q_999999": {"v": "A", "t": base}, f"q_{c}": {"v": "Z", "t": base}}).get_json()
        self.assertNotIn(str(c), d["answers"]); self.assertNotIn("999999", d["answers"])
        d = self.save(cl, tid, {f"q_{c}": "D"}).get_json()
        self.assertEqual(d["answers"][str(c)], "D")

    def test_refresh_keeps_saved_answers(self):
        t, tid = self.published_test("Refresh2", n=2)
        cl = self.student(); self.begin(cl, tid)
        a, b = self.qids(tid)
        self.save(cl, tid, {f"q_{a}": {"v": "C", "t": now_ms()}})
        html = cl.get(f"/test/{tid}/exam").get_data(as_text=True)
        self.assertIn(f'"{a}": "C"', html)
        self.assertEqual(len(self.attempts(tid)), 1)

    def test_submit_merges_last_page_answers_newest_wins(self):
        t, tid = self.published_test("Merge", n=2)
        cl = self.student(); self.begin(cl, tid)
        a, b = self.qids(tid)
        base = now_ms()
        self.save(cl, tid, {f"q_{a}": {"v": "A", "t": base + 5000}, f"q_{b}": {"v": "B", "t": base}})
        # the page's last answers: a is OLDER (stale tab) -> server keeps A; b is NEWER -> becomes A (last second change)
        self.post(cl, f"/test/{tid}/submit", {"answers_json": json.dumps(
            {f"q_{a}": {"v": "C", "t": base + 100}, f"q_{b}": {"v": "A", "t": base + 9000}})})
        rows = {r["question_id"]: r for r in self.q("SELECT * FROM answers WHERE attempt_id=?", self.attempts(tid)[0]["id"])}
        sel = sorted(r["selected_option"] for r in rows.values())
        self.assertEqual(sel, ["A", "A"])
        self.assertEqual(self.result(tid)["score"], 8)

    def test_submit_with_unanswered_questions_is_allowed_and_scores_zero(self):
        t, cl, tid = self.play("Blank", [None, None, None])
        r = self.result(tid)
        self.assertEqual((r["score"], r["correct_count"], r["wrong_count"], r["unanswered_count"]), (0, 0, 0, 3))
        self.assertEqual(self.q("SELECT COUNT(*) FROM answers WHERE attempt_id=? AND selected_option IS NULL AND marks_awarded=0",
                                self.attempts(tid)[0]["id"])[0][0], 3)

    def test_double_submit_and_autosave_after_submit(self):
        t, cl, tid = self.play("Twice", ["A", "B", None])
        r2 = self.finish(cl, tid, ["A", "A", "A"])                       # second tab / double click
        self.assertEqual(r2.status_code, 302); self.assertTrue(r2.headers["Location"].endswith(f"/test/{tid}/result"))
        self.assertEqual(len(self.q("SELECT id FROM results WHERE test_id=?", tid)), 1)
        self.assertEqual(self.result(tid)["score"], 3)                     # 4 - 1: the second submit changed nothing
        d = self.save(cl, tid, {"q_1": {"v": "A", "t": now_ms()}})
        self.assertEqual(d.status_code, 409); self.assertTrue(d.get_json()["closed"])
        self.assertTrue(d.get_json()["redirect"].endswith(f"/test/{tid}/result"))
        # the attempt cannot be restarted
        self.post(cl, f"/test/{tid}/continue"); self.post(cl, f"/test/{tid}/start")
        self.assertEqual(len(self.attempts(tid)), 1)

    def test_server_deadline_is_authoritative(self):
        t, tid = self.published_test("Clock", n=2)
        cl = self.student(); self.begin(cl, tid)
        a, b = self.qids(tid)
        self.save(cl, tid, {f"q_{a}": {"v": "A", "t": now_ms()}})
        att = self.attempts(tid)[0]
        past = (datetime.now() - timedelta(seconds=5)).isoformat()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE exam_attempts SET deadline_at=? WHERE id=?", (past, att["id"]))
        d = self.save(cl, tid, {f"q_{b}": {"v": "A", "t": now_ms()}}).get_json()   # after the deadline: not accepted
        self.assertEqual((d["state"], d["remaining"]), ("closed", 0))
        self.assertNotIn(str(b), d["answers"])
        # last-second submit inside the grace window still counts what the page sends
        self.post(cl, f"/test/{tid}/submit", {"answers_json": json.dumps({f"q_{b}": {"v": "A", "t": now_ms()}})})
        self.assertEqual(self.result(tid)["score"], 8)

    def test_expired_attempt_is_finalized_from_saved_answers(self):
        t, tid = self.published_test("Expire", n=2)
        cl = self.student(); self.begin(cl, tid)
        a, b = self.qids(tid)
        self.save(cl, tid, {f"q_{a}": {"v": "A", "t": now_ms()}, f"q_{b}": {"v": "B", "t": now_ms()}})
        att = self.attempts(tid)[0]
        gone = (datetime.now() - timedelta(hours=1)).isoformat()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE exam_attempts SET deadline_at=? WHERE id=?", (gone, att["id"]))
        r = cl.get(f"/test/{tid}/exam")                                      # refresh after time ran out
        self.assertEqual(r.status_code, 302)
        row = self.attempts(tid)[0]
        self.assertEqual(row["status"], "auto_submitted")
        self.assertEqual(self.result(tid)["score"], 3)                       # 4 - 1 from the saved draft
        self.assertEqual(len(self.q("SELECT id FROM results WHERE test_id=?", tid)), 1)

    # ================================================================ STAGE 4
    def test_spec_example_21_marks(self):
        # 10 questions: 6 correct, 3 incorrect, 1 unanswered; +4 / -1  ->  (6x4) - (3x1) = 21
        t, cl, tid = self.play("Spec", ["A"] * 6 + ["B"] * 3 + [None])
        r = self.result(tid)
        self.assertEqual((r["score"], r["total_marks"]), (21, 40))
        self.assertEqual((r["correct_count"], r["wrong_count"], r["unanswered_count"]), (6, 3, 1))
        self.assertEqual((r["positive_marks"], r["negative_marks_total"]), (24, 3))
        aid = self.attempts(tid)[0]["id"]
        self.assertEqual(self.q("SELECT SUM(marks_awarded) FROM answers WHERE attempt_id=?", aid)[0][0], 21)   # deducted once
        page = cl.get(f"/test/{tid}/result").get_data(as_text=True)
        for needle in ("Marks breakdown", "Correct answers", "Incorrect answers", "Unanswered", "Positive marks earned",
                       "Negative marks deducted", "Final score", "21 / 40", "+24", "−3", "52.5%"):
            self.assertIn(needle, page)

    def test_all_correct_all_wrong_all_unanswered(self):
        _, _, t1 = self.play("AllOK", ["A"] * 5)
        self.assertEqual((self.result(t1)["score"], self.result(t1)["wrong_count"]), (20, 0))
        _, _, t2 = self.play("AllBad", ["B"] * 5)
        r = self.result(t2)
        self.assertEqual((r["score"], r["negative_marks_total"], r["positive_marks"]), (-5, 5, 0))
        _, cl, t3 = self.play("AllNone", [None] * 5)
        r = self.result(t3)
        self.assertEqual((r["score"], r["negative_marks_total"]), (0, 0))
        self.assertEqual(self.q("SELECT COUNT(*) FROM answers WHERE attempt_id=? AND marks_awarded<0", self.attempts(t3)[0]["id"])[0][0], 0)
        self.assertEqual(cl.get(f"/test/{t2}/result").status_code, 200)       # a negative total still shows a result page

    def test_negative_marking_disabled(self):
        _, _, tid = self.play("NoNeg", ["A", "B", "B", None], neg="0")
        r = self.result(tid)
        self.assertEqual((r["score"], r["negative_marks_total"], r["wrong_count"]), (4, 0, 2))

    def test_fractional_negative_marking(self):
        _, _, tid = self.play("Frac", ["A", "A", "B", "B", "B"], neg="0.33")       # 8 - 3 x 0.33
        r = self.result(tid)
        self.assertAlmostEqual(r["score"], 7.01, places=4)
        self.assertAlmostEqual(r["negative_marks_total"], 0.99, places=4)
        _, cl, t2 = self.play("Frac2", ["B", "B"], neg="0.5")
        self.assertEqual(self.result(t2)["score"], -1)
        self.assertIn("−1", cl.get(f"/test/{t2}/result").get_data(as_text=True))

    def test_score_is_never_taken_from_the_browser(self):
        t, tid = self.published_test("Forge", n=2)
        cl = self.student(); self.begin(cl, tid)
        ids = self.qids(tid)
        self.post(cl, f"/test/{tid}/submit", {"answers_json": json.dumps({f"q_{ids[0]}": {"v": "B", "t": now_ms()}}),
                                              "score": "999", "total_marks": "999", "negative_marks": "0"})
        self.assertEqual(self.result(tid)["score"], -1)                      # the server's snapshot decides

    def test_result_release_manual_hides_marks_until_teacher_releases(self):
        t, cl, tid = self.play("Manual", ["A", "A", "B"], result_release="manual")
        page = cl.get(f"/test/{tid}/result")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Results will be available after publication by your teacher", html)
        self.assertNotIn("Marks breakdown", html); self.assertNotIn("Answer Review", html)
        self.assertEqual(cl.get(f"/test/{tid}/leaderboard").status_code, 302)
        api = cl.get("/api/v1/results").get_data(as_text=True)
        self.assertNotIn("Manual", api)
        # the dashboard shows a submitted test as done (no "start again"), and the other student-facing data stays hidden
        self.assertNotIn(f"/test/{tid}/start", cl.get("/dashboard").get_data(as_text=True))
        teach = t.get(f"/teacher/tests/{tid}/results").get_data(as_text=True)    # teacher always sees marks
        self.assertIn("Release results to students", teach); self.assertIn("7", teach)
        self.post(t, f"/teacher/tests/{tid}/results/release", {"action": "release"})
        html = cl.get(f"/test/{tid}/result").get_data(as_text=True)
        self.assertIn("Marks breakdown", html); self.assertIn("7 / 12", html)
        self.assertIn("Manual", cl.get("/api/v1/results").get_data(as_text=True))
        self.assertEqual(cl.get(f"/test/{tid}/leaderboard").status_code, 200)
        self.post(t, f"/teacher/tests/{tid}/results/release", {"action": "hide"})
        self.assertIn("Results will be available", cl.get(f"/test/{tid}/result").get_data(as_text=True))

    def test_immediate_release_and_release_endpoint_is_teacher_only(self):
        t, cl, tid = self.play("Now", ["A"])
        self.assertIn("Marks breakdown", cl.get(f"/test/{tid}/result").get_data(as_text=True))
        r = self.post(cl, f"/teacher/tests/{tid}/results/release", {"action": "release"})   # a student cannot release
        self.assertIn(r.status_code, (302, 401, 403))
        self.assertIsNone(self.q("SELECT results_released_at FROM tests WHERE id=?", tid)[0][0])

    def test_students_only_see_their_own_result_and_class_tests(self):
        t, cl, tid = self.play("Mine", ["A", "B"])
        with sqlite3.connect(self.db) as c:
            other = c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version)"
                              " VALUES ('Zed','NINE','2','z@t.local','2','x',1,?,0,0)", (datetime.now().isoformat(),)).lastrowid
        z = self.app.test_client()
        with z.session_transaction() as s:
            s.update(student_id=other, student_name="Zed", student_class="NINE", sv=0, _csrf_token=CSRF)
        self.assertEqual(z.get(f"/test/{tid}/result").status_code, 404)       # no result of his own -> nothing to see
        self.assertEqual(z.get(f"/test/{tid}/exam").status_code, 404)         # wrong class: blocked on the server
        self.assertEqual(self.save(z, tid, {}).status_code, 404)
        self.assertEqual(self.post(z, f"/test/{tid}/start").status_code, 404)


if __name__ == "__main__":
    unittest.main()
