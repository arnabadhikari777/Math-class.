# -*- coding: utf-8 -*-
"""My Questions / Test Workspace / Image category default (Cloudinary is mocked; no network).
Run: python -m unittest tests.test_workspace_v3 -v"""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import io, os, sqlite3, unittest  # noqa: E402,E401
from unittest import mock  # noqa: E402

from appcase import AppCase, CSRF, q_item  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
_counter = [0]


def fake_post(url, data=None, files=None, timeout=None):
    """Stands in for requests.post to Cloudinary: upload -> unique file, destroy -> ok."""
    if url.endswith("/image/destroy"):
        return mock.Mock(status_code=200, json=lambda: {"result": "ok"})
    _counter[0] += 1
    n = _counter[0]
    return mock.Mock(status_code=200, json=lambda: {
        "secure_url": "https://res.cloudinary.com/demo/image/upload/v1/f%d.png" % n, "public_id": "arnab/f%d" % n,
        "width": 5, "height": 5, "bytes": 72, "format": "png"})


class V3(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ.update({"CLOUDINARY_CLOUD_NAME": "demo", "CLOUDINARY_API_KEY": "123456789012345",
                           "CLOUDINARY_API_SECRET": "s"})   # restored by AppCase.tearDownClass

    # ------------------------------------------------------------ helpers
    def bank_q(self, t, text="Q", correct="A", image_id=None, a="1"):
        data = {"question_text": text, "option_a": a, "option_b": "2", "option_c": "3", "option_d": "4",
                "correct_option": correct, "csrf_token": CSRF}
        if image_id:
            data["image_id"] = image_id
        return t.post("/teacher/bank/questions/new", data=data)

    def bank_ids(self):
        return [r["id"] for r in self.q("SELECT id FROM bank_questions ORDER BY id")]

    def upload(self, t, **form):
        data = dict(form)
        data["images"] = (io.BytesIO(PNG), form.pop("fname", "a.png")) if "fname" in form else (io.BytesIO(PNG), "a.png")
        data.pop("fname", None)
        with mock.patch("backend.services.image_store.requests.post", side_effect=fake_post):
            return t.post("/teacher/studio/api/upload", data=data, headers={"X-CSRFToken": CSRF},
                          content_type="multipart/form-data")

    def count(self, sql, *a):
        return self.q(sql, *a)[0][0]

    # ------------------------------------------------------------ migration
    def test_01_migration_014_is_additive_and_recorded(self):
        cols = {r[1] for r in self.q("PRAGMA table_info(questions)")}
        self.assertTrue({"sort_order", "source_bank_id"} <= cols)
        self.assertIn("content_hash", {r[1] for r in self.q("PRAGMA table_info(bank_questions)")})
        self.assertEqual(self.count("SELECT COUNT(*) FROM schema_migrations WHERE id='014_workspace'"), 1)
        self.assertEqual(self.q("PRAGMA integrity_check")[0][0], "ok")
        self.assertEqual(self.q("PRAGMA foreign_key_check"), [])

    def test_02_existing_questions_keep_their_order_without_sort_order(self):
        t, tid = self.new_test("Order legacy")
        c = sqlite3.connect(self.db)   # rows exactly like the ones that existed before this upgrade
        for i in range(3):
            c.execute("INSERT INTO questions (test_id,question_text,option_a,option_b,option_c,option_d,correct_option,marks) "
                      "VALUES (?,?,?,?,?,?,?,?)", (tid, "L%d" % i, "a", "b", "c", "d", "A", 1))
        c.commit(); c.close()
        from backend.services import question_library as qlib
        from backend.db import get_db
        with get_db() as conn:
            self.assertEqual([r["question_text"] for r in qlib.ordered_questions(conn, tid)], ["L0", "L1", "L2"])

    # ------------------------------------------------------------ names and navigation
    def test_10_new_names_everywhere_old_names_gone(self):
        t, tid = self.new_test("Nav test")
        self.bank_q(t, "Nav Q")
        pages = ["/teacher/dashboard", "/teacher/bank/questions", "/teacher/bank/questions/new", "/teacher/studio/workspace",
                 "/teacher/studio/library", "/teacher/studio/tests/%d/questions" % tid, "/teacher/bank/import/%d" % tid,
                 "/teacher/tests"]
        for url in pages:
            r = t.get(url)
            self.assertEqual(r.status_code, 200, url)
            html = r.get_data(as_text=True)
            for old in ("Question Bank", "Question Studio", "Test questions", "Saved questions"):
                self.assertNotIn(old, html, "%s still shows %r" % (url, old))
        html = t.get("/teacher/bank/questions").get_data(as_text=True)
        for new in ("My Questions", "Test Workspace", "Image Library"):
            self.assertIn(new, html)
        self.assertIn("<title>My Questions", html.replace("\n", ""))

    def test_11_manage_questions_opens_the_selected_test_workspace(self):
        t, tid = self.new_test("Mathematics Chapter 1")
        self.assertIn("/teacher/tests/%d/questions" % tid, t.get("/teacher/tests").get_data(as_text=True))
        r = t.get("/teacher/tests/%d/questions" % tid)                # legacy URL + old bookmark
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/teacher/studio/tests/%d/questions" % tid))
        r = t.get("/teacher/studio/tests/%d/questions" % tid)
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Mathematics Chapter 1", html)
        self.assertIn("Selected test", html)
        self.assertIn("Class: TEN", html)
        self.assertIn("Total marks", html)
        self.assertIn("Manage Tests", html)                           # breadcrumb
        self.assertIn("Import from My Questions", html)

    def test_12_same_workspace_header_on_every_entry_point(self):
        t, tid = self.new_test("Entry points")
        b = self.bank_ids()
        for url in ("/teacher/studio/tests/%d/questions" % tid, "/teacher/bank/import/%d" % tid):
            html = t.get(url).get_data(as_text=True)
            self.assertIn('id="wsHead"', html, url)
            self.assertIn("Entry points", html, url)
        # the sidebar "Test Workspace" goes to a test picker, never to a page without a selected test
        html = t.get("/teacher/studio/workspace").get_data(as_text=True)
        self.assertIn("Choose a test", html)
        self.assertIn("/teacher/studio/tests/%d/questions" % tid, html)

    def test_13_nothing_can_be_created_or_imported_without_a_test(self):
        t = self.teacher()
        self.assertEqual(t.get("/teacher/bank/import/99999").status_code, 404)
        self.assertEqual(t.post("/teacher/bank/import/99999", data={"csrf_token": CSRF, "question_id": "1"}).status_code, 404)
        r = t.post("/teacher/studio/tests/99999/questions/save", json={"questions": [q_item()]}, headers={"X-CSRFToken": CSRF})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(t.get("/teacher/studio/tests/99999/questions").status_code, 404)

    def test_14_access_control(self):
        t, tid = self.new_test("Locked")
        urls = ["/teacher/studio/workspace", "/teacher/studio/tests/%d/questions" % tid, "/teacher/bank/import/%d" % tid]
        for u in urls:
            self.assertEqual(self.app.test_client().get(u).status_code, 302, u)
            self.assertEqual(self.student().get(u).status_code, 302, u)
        r = self.student().post("/teacher/studio/questions/1/save-to-library", data={"csrf_token": CSRF})
        self.assertEqual(r.status_code, 302)

    # ------------------------------------------------------------ My Questions library
    def test_20_saving_to_my_questions_does_not_touch_any_test(self):
        t, tid = self.new_test("Lib only")
        before = self.count("SELECT COUNT(*) FROM questions")
        self.bank_q(t, "Library only question")
        self.assertEqual(self.count("SELECT COUNT(*) FROM questions"), before)
        self.assertEqual(self.count("SELECT COUNT(*) FROM questions WHERE test_id=?", tid), 0)

    def test_21_duplicate_in_my_questions_is_refused(self):
        t = self.teacher()
        self.assertEqual(self.bank_q(t, "Dup lib", a="same").status_code, 302)
        n = len(self.bank_ids())
        r = self.bank_q(t, "  Dup   lib ", a="same")               # same after trimming spaces
        self.assertEqual(r.status_code, 409)
        self.assertIn("আগে থেকেই My Questions-এ আছে", r.get_data(as_text=True))
        self.assertEqual(len(self.bank_ids()), n)
        self.assertEqual(self.bank_q(t, "Dup lib", a="different").status_code, 302)   # different option = different question

    def test_22_editing_into_an_existing_question_is_refused(self):
        t = self.teacher()
        self.bank_q(t, "E1"); self.bank_q(t, "E2")
        ids = self.bank_ids()[-2:]
        r = t.post("/teacher/bank/questions/%d/edit" % ids[1], data={"csrf_token": CSRF, "question_text": "E1", "option_a": "1",
                   "option_b": "2", "option_c": "3", "option_d": "4", "correct_option": "A"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.q("SELECT question_text FROM bank_questions WHERE id=?", ids[1])[0][0], "E2")

    # ------------------------------------------------------------ import
    def test_30_import_multiple_reports_count_and_keeps_originals(self):
        t, tid = self.new_test("Import multi")
        for i in range(3):
            self.bank_q(t, "IM%d" % i, a="im%d" % i)
        ids = self.bank_ids()[-3:]
        r = t.post("/teacher/bank/import/%d" % tid, data={"csrf_token": CSRF, "question_id": ids, "marks": "2"}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("3 টি প্রশ্ন", html)
        self.assertIn("Import multi", html)
        rows = self.q("SELECT * FROM questions WHERE test_id=? ORDER BY COALESCE(sort_order,id)", tid)
        self.assertEqual([x["question_text"] for x in rows], ["IM0", "IM1", "IM2"])
        self.assertEqual([x["source_bank_id"] for x in rows], ids)
        self.assertEqual({x["marks"] for x in rows}, {2})
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_questions WHERE id IN (%s)" % ",".join(map(str, ids))), 3)

    def test_31_importing_twice_is_refused_by_the_backend(self):
        t, tid = self.new_test("Import twice")
        self.bank_q(t, "Once only", a="once")
        bid = self.bank_ids()[-1]
        t.post("/teacher/bank/import/%d" % tid, data={"csrf_token": CSRF, "question_id": bid})
        r = t.post("/teacher/bank/import/%d" % tid, data={"csrf_token": CSRF, "question_id": [bid, bid]}, follow_redirects=True)
        self.assertEqual(self.count("SELECT COUNT(*) FROM questions WHERE test_id=?", tid), 1)
        self.assertIn("আগেই আছে", r.get_data(as_text=True))

    def test_32_database_itself_refuses_a_duplicate_link(self):
        t, tid = self.new_test("DB unique")
        self.bank_q(t, "DBU", a="dbu")
        bid = self.bank_ids()[-1]
        c = sqlite3.connect(self.db)
        sql = ("INSERT INTO questions (test_id,question_text,option_a,option_b,option_c,option_d,correct_option,marks,source_bank_id) "
               "VALUES (?,?,?,?,?,?,?,?,?)")
        c.execute(sql, (tid, "x", "a", "b", "c", "d", "A", 1, bid))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute(sql, (tid, "y", "a", "b", "c", "d", "A", 1, bid))
        c.rollback(); c.close()

    def test_33_identical_content_already_in_test_is_refused_even_without_link(self):
        t, tid = self.new_test("Legacy dup")
        self.add_questions(t, tid, [{"text": "Same text", "a": "x", "b": "2", "c": "3", "d": "4", "correct": "A", "marks": 1}])
        self.bank_q(t, "Same text", a="x")
        bid = self.bank_ids()[-1]
        t.post("/teacher/bank/import/%d" % tid, data={"csrf_token": CSRF, "question_id": bid})
        self.assertEqual(self.count("SELECT COUNT(*) FROM questions WHERE test_id=?", tid), 1)
        html = t.get("/teacher/bank/import/%d" % tid).get_data(as_text=True)
        self.assertIn("এই Test-এ আছে", html)

    def test_34_import_nothing_selected_and_unknown_ids(self):
        t, tid = self.new_test("Import none")
        r = t.post("/teacher/bank/import/%d" % tid, data={"csrf_token": CSRF}, follow_redirects=True)
        self.assertIn("কোনো প্রশ্ন বাছা হয়নি", r.get_data(as_text=True))
        r = t.post("/teacher/bank/import/%d" % tid, data={"csrf_token": CSRF, "question_id": "987654"}, follow_redirects=True)
        self.assertIn("পাওয়া যায়নি", r.get_data(as_text=True))
        self.assertEqual(self.count("SELECT COUNT(*) FROM questions WHERE test_id=?", tid), 0)

    def test_35_import_keeps_image_relation_and_copies_are_independent(self):
        t, tid = self.new_test("Import image")
        r = self.upload(t, new_category="Figures")
        img = r.get_json()["images"][0]
        self.bank_q(t, "With picture", image_id=img["id"], a="pic")
        bid = self.bank_ids()[-1]
        t.post("/teacher/bank/import/%d" % tid, data={"csrf_token": CSRF, "question_id": bid})
        row = self.q("SELECT * FROM questions WHERE test_id=?", tid)[0]
        self.assertEqual((row["bank_image_id"], row["image_url"]), (img["id"], img["url"]))
        # editing the test copy leaves the library question untouched
        t.post("/teacher/studio/tests/%d/questions/%d/edit" % (tid, row["id"]), data={
            "csrf_token": CSRF, "question_text": "EDITED IN TEST", "option_a": "p", "option_b": "q", "option_c": "r",
            "option_d": "s", "correct_option": "C", "marks": "5", "image_id": img["id"]})
        self.assertEqual(self.q("SELECT question_text FROM bank_questions WHERE id=?", bid)[0][0], "With picture")
        self.assertEqual(self.q("SELECT question_text, marks FROM questions WHERE id=?", row["id"])[0][:], ("EDITED IN TEST", 5))
        # and editing the library question leaves the test copy untouched
        t.post("/teacher/bank/questions/%d/edit" % bid, data={"csrf_token": CSRF, "question_text": "LIB CHANGED", "option_a": "pic",
               "option_b": "2", "option_c": "3", "option_d": "4", "correct_option": "A", "image_id": img["id"]})
        self.assertEqual(self.q("SELECT question_text FROM questions WHERE id=?", row["id"])[0][0], "EDITED IN TEST")

    # ------------------------------------------------------------ published test protection
    def test_40_published_test_cannot_receive_imports(self):
        t, tid = self.published_test("Pub import", n=1)
        self.bank_q(t, "Late arrival", a="late")
        bid = self.bank_ids()[-1]
        before = self.count("SELECT COUNT(*) FROM questions WHERE test_id=?", tid)
        r = t.post("/teacher/bank/import/%d" % tid, data={"csrf_token": CSRF, "question_id": bid}, follow_redirects=True)
        self.assertIn("Published", r.get_data(as_text=True))
        self.assertEqual(self.count("SELECT COUNT(*) FROM questions WHERE test_id=?", tid), before)
        r = t.get("/teacher/bank/import/%d" % tid)                    # the import page itself is not offered
        self.assertEqual(r.status_code, 302)

    def test_41_published_test_blocks_every_question_change(self):
        t, tid = self.published_test("Pub locked", n=2)
        qs = self.q("SELECT * FROM questions WHERE test_id=? ORDER BY id", tid)
        snapshot = [tuple(r) for r in qs]
        h = {"X-CSRFToken": CSRF}
        r = t.post("/teacher/studio/tests/%d/questions/save" % tid, json={"questions": [q_item("new")]}, headers=h)
        self.assertEqual(r.status_code, 409)
        t.post("/teacher/studio/tests/%d/questions/%d/edit" % (tid, qs[0]["id"]), data={
            "csrf_token": CSRF, "question_text": "HACK", "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4",
            "correct_option": "B", "marks": "9"})
        t.post("/teacher/studio/tests/%d/questions/%d/move" % (tid, qs[1]["id"]), data={"csrf_token": CSRF, "dir": "up"})
        t.post("/teacher/studio/questions/%d/image" % qs[0]["id"], data={"csrf_token": CSRF, "image_id": ""})
        t.post("/teacher/questions/delete/%d/%d" % (qs[0]["id"], tid), data={"csrf_token": CSRF})
        self.assertEqual([tuple(r) for r in self.q("SELECT * FROM questions WHERE test_id=? ORDER BY id", tid)], snapshot)
        html = t.get("/teacher/studio/tests/%d/questions" % tid).get_data(as_text=True)
        self.assertIn("This test is published", html)
        self.assertNotIn('id="saveBtn"', html)                          # frontend hides the create form too
        self.assertNotIn("Import from My Questions", html)

    def test_42_unpublish_makes_it_editable_again(self):
        t, tid = self.published_test("Pub then draft", n=1)
        self.post(t, "/teacher/tests/%d/unpublish" % tid)
        r = self.add_questions(t, tid, [q_item("again")])
        self.assertEqual(r.status_code, 200)

    # ------------------------------------------------------------ workspace: create, order, save to library
    def test_50_workspace_questions_go_to_the_test_not_to_my_questions(self):
        t, tid = self.new_test("WS create")
        lib = len(self.bank_ids())
        r = self.add_questions(t, tid, [q_item("W1"), q_item("W2")])
        self.assertEqual(r.get_json()["added"], 2)
        self.assertEqual(len(self.bank_ids()), lib)
        self.assertEqual(self.count("SELECT COUNT(*) FROM questions WHERE test_id=?", tid), 2)

    def test_51_reorder_changes_order_used_by_review_and_exam(self):
        t, tid = self.new_test("Reorder")
        self.add_questions(t, tid, [q_item("First"), q_item("Second"), q_item("Third")])
        ids = [r["id"] for r in self.q("SELECT id FROM questions WHERE test_id=? ORDER BY id", tid)]
        t.post("/teacher/studio/tests/%d/questions/%d/move" % (tid, ids[2]), data={"csrf_token": CSRF, "dir": "up"})
        t.post("/teacher/studio/tests/%d/questions/%d/move" % (tid, ids[2]), data={"csrf_token": CSRF, "dir": "up"})
        html = t.get("/teacher/studio/tests/%d/questions" % tid).get_data(as_text=True)
        self.assertLess(html.index("1. Third"), html.index("2. First"))
        self.assertLess(html.index("2. First"), html.index("3. Second"))
        self.add_questions(t, tid, [q_item("Fourth")])                 # a new question goes to the end
        order = [r["question_text"] for r in self.q("SELECT * FROM questions WHERE test_id=? ORDER BY COALESCE(sort_order,id), id", tid)]
        self.assertEqual(order, ["Third", "First", "Second", "Fourth"])
        self.post(t, "/teacher/tests/%d/publish" % tid)
        from backend.services import exam_service  # noqa: F401
        c = sqlite3.connect(self.db); c.row_factory = sqlite3.Row
        from backend.db import get_db
        from backend.services.exam_service import ensure_version
        with get_db() as conn:
            test = conn.execute("SELECT * FROM tests WHERE id=?", (tid,)).fetchone()
            vid = ensure_version(conn, test); conn.commit()
        pos = [r["question_text"] for r in c.execute("SELECT * FROM exam_version_questions WHERE version_id=? ORDER BY position", (vid,))]
        c.close()
        self.assertEqual(pos, ["Third", "First", "Second", "Fourth"])
        # the edge moves do nothing and do not fail
        self.assertEqual(self.post(t, "/teacher/tests/%d/unpublish" % tid).status_code, 302)

    def test_52_move_rejects_bad_direction_and_foreign_question(self):
        t, tid = self.new_test("Move guard")
        t2, tid2 = self.new_test("Move guard 2")
        self.add_questions(t, tid, [q_item("A1")]); self.add_questions(t, tid2, [q_item("B1")])
        qid2 = self.q("SELECT id FROM questions WHERE test_id=?", tid2)[0][0]
        self.assertEqual(t.post("/teacher/studio/tests/%d/questions/%d/move" % (tid, qid2), data={"csrf_token": CSRF, "dir": "up"}).status_code, 404)
        self.assertEqual(t.post("/teacher/studio/tests/%d/questions/%d/move" % (tid2, qid2), data={"csrf_token": CSRF, "dir": "sideways"}).status_code, 400)

    def test_53_save_to_my_questions_once_and_duplicate_refused(self):
        t, tid = self.new_test("Save lib")
        self.add_questions(t, tid, [{"text": "Keep me", "a": "k1", "b": "2", "c": "3", "d": "4", "correct": "D", "marks": 3}])
        qid = self.q("SELECT id FROM questions WHERE test_id=?", tid)[0][0]
        n = len(self.bank_ids())
        r = t.post("/teacher/studio/questions/%d/save-to-library" % qid, data={"csrf_token": CSRF}, follow_redirects=True)
        self.assertIn("My Questions-এ সংরক্ষিত হয়েছে", r.get_data(as_text=True))
        self.assertEqual(len(self.bank_ids()), n + 1)
        row = self.q("SELECT * FROM bank_questions ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual((row["question_text"], row["correct_option"]), ("Keep me", "D"))
        r = t.post("/teacher/studio/questions/%d/save-to-library" % qid, data={"csrf_token": CSRF}, follow_redirects=True)
        self.assertIn("আগে থেকেই My Questions-এ আছে", r.get_data(as_text=True))
        self.assertEqual(len(self.bank_ids()), n + 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM questions WHERE test_id=?", tid), 1)   # the test is unchanged
        self.assertIn("My Questions-এ আছে", t.get("/teacher/studio/tests/%d/questions" % tid).get_data(as_text=True))

    def test_54_save_to_my_questions_also_works_from_a_published_test(self):
        t, tid = self.published_test("Pub save", n=1)
        qid = self.q("SELECT id FROM questions WHERE test_id=?", tid)[0][0]
        before = tuple(self.q("SELECT * FROM questions WHERE id=?", qid)[0])
        n = len(self.bank_ids())
        t.post("/teacher/studio/questions/%d/save-to-library" % qid, data={"csrf_token": CSRF})
        self.assertEqual(len(self.bank_ids()), n + 1)
        self.assertEqual(tuple(self.q("SELECT * FROM questions WHERE id=?", qid)[0]), before)   # read-only on the test

    def test_55_preview_and_totals_in_workspace(self):
        t, tid = self.new_test("Totals")
        self.add_questions(t, tid, [q_item("T1", marks=4), q_item("T2", marks=6)])
        html = t.get("/teacher/studio/tests/%d/questions" % tid).get_data(as_text=True)
        self.assertIn("Questions: 2", html)
        self.assertIn("Total marks: 10", html)
        self.assertIn("Preview (as students see it)", html)

    # ------------------------------------------------------------ image category default
    def test_60_default_category_is_the_test_name_and_is_created_once(self):
        t, tid = self.new_test("Mathematics Chapter 1")
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Mathematics Chapter 1'"), 0)
        info = t.get("/teacher/studio/api/images?test_id=%d" % tid).get_json()
        self.assertEqual(info["default_category"], {"id": None, "name": "Mathematics Chapter 1", "exists": False})
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Mathematics Chapter 1'"), 0)   # asking creates nothing
        r = self.upload(t, test_id=tid)
        d = r.get_json()
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["category"]["name"], "Mathematics Chapter 1")
        cat = self.q("SELECT * FROM bank_categories WHERE name='Mathematics Chapter 1'")
        self.assertEqual(len(cat), 1)
        img = self.q("SELECT * FROM bank_images WHERE id=?", d["images"][0]["id"])[0]
        self.assertEqual(img["category_id"], cat[0]["id"])
        # a second upload (same test) lands in the SAME category; no duplicate is created
        self.upload(t, test_id=tid)
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Mathematics Chapter 1' COLLATE NOCASE"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_images WHERE category_id=?", cat[0]["id"]), 2)
        info = t.get("/teacher/studio/api/images?test_id=%d" % tid).get_json()
        self.assertEqual((info["default_category"]["exists"], info["default_category"]["id"]), (True, cat[0]["id"]))

    def test_61_existing_category_with_same_name_other_case_is_reused(self):
        t, tid = self.new_test("Algebra Basics")
        t.post("/teacher/studio/library/category", data={"csrf_token": CSRF, "name": "algebra basics"})
        self.upload(t, test_id=tid)
        names = [r["name"] for r in self.q("SELECT name FROM bank_categories WHERE name LIKE 'algebra basics' COLLATE NOCASE")]
        self.assertEqual(names, ["algebra basics"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_images i JOIN bank_categories c ON c.id=i.category_id WHERE c.name='algebra basics'"), 1)

    def test_62_teacher_can_pick_another_or_a_new_category(self):
        t, tid = self.new_test("Geometry Test")
        t.post("/teacher/studio/library/category", data={"csrf_token": CSRF, "name": "Shapes"})
        shapes = self.q("SELECT id FROM bank_categories WHERE name='Shapes'")[0][0]
        d = self.upload(t, test_id=tid, category_id=shapes).get_json()           # chose another existing category
        self.assertEqual(self.q("SELECT category_id FROM bank_images WHERE id=?", d["images"][0]["id"])[0][0], shapes)
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Geometry Test'"), 0)   # default NOT created
        d = self.upload(t, test_id=tid, new_category="Brand New").get_json()      # typed a new one
        self.assertEqual(d["category"]["name"], "Brand New")
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Brand New'"), 1)
        d = self.upload(t, test_id=tid, new_category="brand new").get_json()      # typed again with other case: no duplicate
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Brand New' COLLATE NOCASE"), 1)

    def test_63_plain_library_upload_still_asks_the_teacher_to_choose(self):
        t = self.teacher()
        with mock.patch("backend.services.image_store.requests.post", side_effect=fake_post) as rp:
            r = t.post("/teacher/studio/library/upload", data={"csrf_token": CSRF, "images": (io.BytesIO(PNG), "a.png")},
                       content_type="multipart/form-data", follow_redirects=True)
            self.assertEqual(rp.call_count, 0)
        self.assertIn("Category বেছে নিন", r.get_data(as_text=True))
        r = t.post("/teacher/studio/api/upload", data={"images": (io.BytesIO(PNG), "a.png")}, headers={"X-CSRFToken": CSRF},
                   content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)                              # no test, no category: nothing is guessed

    def test_64_cloudinary_failure_leaves_no_category_and_no_image_row(self):
        t, tid = self.new_test("Fails to upload")
        images, cats = self.count("SELECT COUNT(*) FROM bank_images"), self.count("SELECT COUNT(*) FROM bank_categories")
        bad = mock.Mock(status_code=401, json=lambda: {"error": {"message": "Invalid Signature"}})
        with mock.patch("backend.services.image_store.requests.post", return_value=bad):
            r = t.post("/teacher/studio/api/upload", data={"test_id": tid, "images": (io.BytesIO(PNG), "a.png")},
                       headers={"X-CSRFToken": CSRF}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_images"), images)
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories"), cats)

    def test_65_database_failure_after_upload_cleans_cloudinary_and_leaves_nothing(self):
        t, tid = self.new_test("Db fails")
        images, cats = self.count("SELECT COUNT(*) FROM bank_images"), self.count("SELECT COUNT(*) FROM bank_categories")
        calls = []

        def spy(url, data=None, files=None, timeout=None):
            calls.append(url)
            return fake_post(url, data, files, timeout)
        with mock.patch("backend.services.image_store.requests.post", side_effect=spy), \
                mock.patch("backend.services.image_service.insert_image", side_effect=sqlite3.OperationalError("disk I/O error")):
            r = t.post("/teacher/studio/api/upload", data={"test_id": tid, "images": (io.BytesIO(PNG), "a.png")},
                       headers={"X-CSRFToken": CSRF}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(any(u.endswith("/image/upload") for u in calls))
        self.assertTrue(any(u.endswith("/image/destroy") for u in calls))          # the orphan file was removed again
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_images"), images)
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories"), cats)  # category creation rolled back too

    def test_66_old_images_keep_their_category_when_reused_or_test_renamed(self):
        t, tid = self.new_test("Old category test")
        t.post("/teacher/studio/library/category", data={"csrf_token": CSRF, "name": "Legacy Cat"})
        legacy = self.q("SELECT id FROM bank_categories WHERE name='Legacy Cat'")[0][0]
        d = self.upload(t, category_id=legacy).get_json()
        img_id = d["images"][0]["id"]
        self.add_questions(t, tid, [{"text": "uses old", "image_id": img_id, "a": "1", "b": "2", "c": "3", "d": "4", "correct": "A", "marks": 1}])
        qid = self.q("SELECT id FROM questions WHERE test_id=?", tid)[0][0]
        t.post("/teacher/studio/questions/%d/image" % qid, data={"csrf_token": CSRF, "image_id": img_id})
        c = sqlite3.connect(self.db); c.execute("UPDATE tests SET title='Renamed test' WHERE id=?", (tid,)); c.commit(); c.close()
        self.upload(t, test_id=tid)                                             # new default category follows the NEW name
        self.assertEqual(self.q("SELECT category_id FROM bank_images WHERE id=?", img_id)[0][0], legacy)
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Old category test'"), 0)   # nothing renamed/created for the old name
        self.assertEqual(self.q("SELECT name FROM bank_categories WHERE id=?", legacy)[0][0], "Legacy Cat")

    def test_67_category_with_images_still_cannot_be_deleted(self):
        t, tid = self.new_test("Protect cat")
        d = self.upload(t, test_id=tid).get_json()
        cid = d["category"]["id"]
        t.post("/teacher/studio/library/category/%d/delete" % cid, data={"csrf_token": CSRF})
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE id=?", cid), 1)

    def test_68_multiple_files_and_picker_shows_new_image_at_once(self):
        t, tid = self.new_test("Multi upload")
        with mock.patch("backend.services.image_store.requests.post", side_effect=fake_post):
            r = t.post("/teacher/studio/api/upload", data={"test_id": tid, "images": [(io.BytesIO(PNG), "a.png"), (io.BytesIO(PNG), "b.png")]},
                       headers={"X-CSRFToken": CSRF}, content_type="multipart/form-data")
        d = r.get_json()
        self.assertEqual(len(d["images"]), 2)
        listing = t.get("/teacher/studio/api/images?category=%d" % d["category"]["id"]).get_json()["images"]
        self.assertEqual({i["id"] for i in listing}, {i["id"] for i in d["images"]})
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Multi upload'"), 1)

    def test_69_workspace_passes_test_id_and_name_to_the_picker(self):
        t, tid = self.new_test("Mathematics Chapter 1")
        html = t.get("/teacher/studio/tests/%d/questions" % tid).get_data(as_text=True)
        self.assertIn("var TEST_ID = %d;" % tid, html)
        self.assertIn("Mathematics Chapter 1", html)
        self.assertIn("ডিফল্টভাবে এই নামের Category", html)
        self.assertIn("ipCatSel", html); self.assertIn("ipNewCat", html); self.assertIn("ipBar", html)
        # the general Image Library page has no test context
        self.assertNotIn("var TEST_ID", t.get("/teacher/studio/library").get_data(as_text=True))

    def test_70_bad_files_are_rejected_before_cloudinary(self):
        t, tid = self.new_test("Bad files")
        with mock.patch("backend.services.image_store.requests.post", side_effect=fake_post) as rp:
            r = t.post("/teacher/studio/api/upload", data={"test_id": tid, "images": (io.BytesIO(b"<html>"), "evil.png")},
                       headers={"X-CSRFToken": CSRF}, content_type="multipart/form-data")
            self.assertEqual(rp.call_count, 0)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_categories WHERE name='Bad files'"), 0)


if __name__ == "__main__":
    unittest.main()
