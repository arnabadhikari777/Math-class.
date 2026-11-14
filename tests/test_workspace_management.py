# -*- coding: utf-8 -*-
"""Test Workspace is the single place to manage tests (page content + every action round trip).
Run: python -m pytest tests/test_workspace_management.py"""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import unittest  # noqa: E402

from appcase import AppCase, q_item  # noqa: E402


class WorkspaceManagement(AppCase):
    def html(self, t, url="/teacher/studio/workspace"):
        return t.get(url).get_data(as_text=True)

    def test_page_lists_summary_and_every_action(self):
        t, tid = self.new_test("WS Summary")
        self.add_questions(t, tid, [q_item("Q1"), q_item("Q2")])
        page = self.html(t)
        for needle in ("WS Summary", "Questions", "Total marks", "Submissions", "Draft", "Manage Questions",
                       "Add Questions", "Import Questions", "Review &amp; Publish", "Rules", "View Results",
                       "Publish", "Delete Test", "Create a New Test", "Choose a test"):
            self.assertIn(needle, page, needle)
        self.assertIn("/teacher/tests/%d/settings" % tid, page)
        self.assertIn("/teacher/tests/%d/review" % tid, page)

    def test_old_all_tests_url_is_the_same_interface(self):
        t, tid = self.new_test("Same Page")
        old = self.html(t, "/teacher/tests")
        self.assertIn("Choose a test", old)
        self.assertIn("Same Page", old)
        self.assertIn("/teacher/tests/%d/questions" % tid, old)          # existing link target kept
        self.assertNotIn("All Tests</h2>", old)                          # no second, competing page

    def test_publish_pause_resume_notify_unpublish_from_workspace(self):
        t, tid = self.new_test("WS Flow")
        self.add_questions(t, tid, [q_item("Only")])
        self.post(t, f"/teacher/tests/{tid}/publish")
        row = lambda: self.q("SELECT status,is_active FROM tests WHERE id=?", tid)[0]
        self.assertEqual(tuple(row()), ("published", 1))
        page = self.html(t)
        self.assertIn("Published", page); self.assertIn("Active", page)
        self.assertIn("Unpublish", page); self.assertIn("Notify again", page); self.assertIn("Pause", page)
        n = len(self.notifications())
        self.post(t, f"/teacher/tests/{tid}/publish")                    # publishing twice never re-notifies
        self.assertEqual(len(self.notifications()), n)
        self.post(t, f"/teacher/tests/{tid}/toggle")
        self.assertEqual(tuple(row()), ("published", 0))
        page = self.html(t)
        self.assertIn("Paused", page); self.assertIn("Resume", page); self.assertNotIn("Notify again", page)
        self.post(t, f"/teacher/tests/{tid}/toggle")
        self.assertEqual(tuple(row()), ("published", 1))
        self.post(t, f"/teacher/tests/{tid}/notify")
        self.assertEqual(len(self.notifications()), n + 1)               # explicit action sends exactly one
        self.post(t, f"/teacher/tests/{tid}/unpublish")
        self.assertEqual(row()["status"], "draft")

    def test_archive_restore_and_permanent_delete_stay_separate(self):
        t, tid = self.new_test("WS Delete")
        self.add_questions(t, tid, [q_item("Only")])
        self.post(t, f"/teacher/tests/{tid}/archive")
        self.assertEqual(self.q("SELECT is_archived FROM tests WHERE id=?", tid)[0][0], 1)
        page = self.html(t)
        self.assertIn("Deleted tests", page); self.assertIn("Restore", page); self.assertIn("Delete forever", page)
        self.assertEqual(self.q("SELECT COUNT(*) FROM questions WHERE test_id=?", tid)[0][0], 1)   # archive keeps data
        self.post(t, f"/teacher/tests/{tid}/restore")
        self.assertEqual(self.q("SELECT is_archived FROM tests WHERE id=?", tid)[0][0], 0)
        self.post(t, f"/teacher/tests/{tid}/archive")
        page = self.html(t, f"/teacher/tests/{tid}/delete")             # GET shows what will be lost, deletes nothing
        self.assertEqual(self.q("SELECT COUNT(*) FROM tests WHERE id=?", tid)[0][0], 1)
        self.post(t, f"/teacher/tests/{tid}/delete")
        self.assertEqual(self.q("SELECT COUNT(*) FROM tests WHERE id=?", tid)[0][0], 0)

    def test_workspace_needs_teacher_login(self):
        c = self.app.test_client()
        self.assertEqual(c.get("/teacher/studio/workspace").status_code, 302)
        self.assertEqual(c.get("/teacher/tests").status_code, 302)
        self.assertEqual(self.student().get("/teacher/studio/workspace").status_code, 302)


if __name__ == "__main__":
    unittest.main()
