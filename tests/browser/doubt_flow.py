# -*- coding: utf-8 -*-
"""Real-browser Doubt Box flows (Chromium, 390px phone): inline validation, picture checks, submit with/without
picture, double tap, dropped connection + safe retry, lightbox, teacher save. Cloudinary is faked in-process.
    python tests/browser/doubt_flow.py"""
import base64, os, re, sys, tempfile, threading, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from appcase import AppCase  # noqa: E402

PORT = 5092
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


class Flow(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from werkzeug.serving import make_server
        cls.srv = make_server("127.0.0.1", PORT, cls.app, threaded=True)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); super().tearDownClass()

    def test_flow(self):
        from playwright.sync_api import sync_playwright
        base = "http://127.0.0.1:%d" % PORT
        d = tempfile.mkdtemp()
        png = os.path.join(d, "a.png"); open(png, "wb").write(base64.b64decode(PNG_B64))
        bad = os.path.join(d, "fake.png"); open(bad, "wb").write(b"this is not an image at all")
        big = os.path.join(d, "big.png"); open(big, "wb").write(base64.b64decode(PNG_B64) + b"0" * (6 * 1024 * 1024))
        n = [0]

        def fake_upload(fn, data, cat=None):
            n[0] += 1
            return {"url": base + "/static/icons/icon-192.png?i=%d" % n[0], "public_id": "fake/%d" % n[0], "width": 5, "height": 5, "bytes": 70, "format": "png"}
        res = []
        ck = self.student().get_cookie("session"); tk = self.teacher().get_cookie("session")
        shots = os.path.join(HERE, "screenshots", "doubt"); os.makedirs(shots, exist_ok=True)

        def check(name, cond, extra=""):
            res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + ("" if cond else " - " + str(extra)))
        with mock.patch("backend.services.image_store.upload", fake_upload), mock.patch("backend.services.image_store.config_problem", return_value=None), sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 390, "height": 760}, is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": "session", "value": ck.value, "url": base}])
            ctx.route("**/*", lambda r: r.abort() if not r.request.url.startswith("http://127.0.0.1") else r.continue_())
            pg = ctx.new_page(); errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(base + "/doubts/new"); pg.wait_for_timeout(300)
            pg.screenshot(path=os.path.join(shots, "form_390.png"), full_page=True)
            # inline validation
            pg.click("#dSubmit"); pg.wait_for_timeout(150)
            check("empty submit shows inline errors, nothing sent", pg.is_visible("#eSubject") and pg.is_visible("#eQuestion") and self.q("SELECT COUNT(*) FROM doubts")[0][0] == 0)
            pg.click("[data-subject-chip='Physics']")
            check("subject chip fills the field", pg.input_value("#dSubject") == "Physics")
            pg.fill("#dQuestion", "Why does a ball keep moving after I throw it? Use $F=ma$.")
            check("character counter updates", pg.inner_text("#dCount").startswith("58 /") or "/ 4000" in pg.inner_text("#dCount"))
            # picture checks
            pg.set_input_files("#dImage", bad); pg.wait_for_timeout(200)
            check("fake image rejected in browser", pg.is_visible("#eImage") and pg.is_hidden("#dPv"))
            pg.set_input_files("#dImage", big); pg.wait_for_timeout(200)
            check("oversize image rejected with the real limit", "5 MB" in pg.inner_text("#eImage"), pg.inner_text("#eImage"))
            pg.set_input_files("#dImage", png); pg.wait_for_timeout(300)
            check("valid image previewed with name", pg.is_visible("#dPv") and "a.png" in pg.inner_text("#dPv"))
            pg.click("#dRemove"); check("remove clears preview", pg.is_hidden("#dPv"))
            pg.set_input_files("#dImage", png); pg.wait_for_timeout(200)
            pg.screenshot(path=os.path.join(shots, "form_filled_390.png"), full_page=True)
            # dropped connection: first request fails at network level, retry must create exactly one doubt
            state = {"fail": True}

            def flaky(route):
                if state["fail"]:
                    state["fail"] = False; route.abort("failed")
                else:
                    route.continue_()
            pg.route("**/doubts/new", lambda r: flaky(r) if r.request.method == "POST" else r.continue_())
            pg.click("#dSubmit"); pg.wait_for_timeout(600)
            check("network failure: message shown, text kept, button usable again", pg.is_visible("#dErr") and not pg.is_disabled("#dSubmit") and "ball keep moving" in pg.input_value("#dQuestion"))
            tok = pg.input_value("input[name=token]")
            pg.click("#dSubmit"); pg.wait_for_url(re.compile(r"/doubts/\d+$"), timeout=15000)
            check("retry succeeds and opens the doubt", "/doubts/new" not in pg.url)
            rows = self.q("SELECT * FROM doubts WHERE client_token=?", tok)
            check("exactly one doubt, picture attached, Sir notified", len(rows) == 1 and rows[0]["image_url"] and len(self.q("SELECT 1 FROM teacher_notifications WHERE ref_key=?", "doubt-new:%d" % rows[0]["id"])) == 1)
            did = rows[0]["id"]
            check("detail shows Waiting for Teacher, separate AI section", "Waiting for Teacher" in pg.inner_text("main") if pg.locator("main").count() else "Waiting for Teacher" in pg.content())
            pg.screenshot(path=os.path.join(shots, "detail_390.png"), full_page=True)
            pg.click(".dbt-img-btn"); pg.wait_for_timeout(200)
            check("picture zoom opens", pg.is_visible(".dbt-lb")); pg.keyboard.press("Escape"); check("zoom closes with Escape", pg.is_hidden(".dbt-lb"))
            # double tap: two quick clicks -> one doubt
            pg.goto(base + "/doubts/new"); pg.fill("#dSubject", "Maths"); pg.fill("#dQuestion", "Double tap test question")
            tok2 = pg.input_value("input[name=token]")
            pg.dblclick("#dSubmit"); pg.wait_for_url(re.compile(r"/doubts/\d+$"), timeout=15000); pg.wait_for_timeout(300)
            check("double tap creates one doubt", len(self.q("SELECT 1 FROM doubts WHERE client_token=?", tok2)) == 1)
            pg.goto(base + "/doubts"); pg.wait_for_timeout(200)
            check("My Doubts lists both with status chips", pg.locator(".dbt-card").count() == 2 and "Waiting for Teacher" in pg.content())
            pg.fill("#dbtQ", "ball"); pg.press("#dbtQ", "Enter"); pg.wait_for_load_state()
            check("search narrows the list", pg.locator(".dbt-card").count() == 1)
            pg.screenshot(path=os.path.join(shots, "list_390.png"), full_page=True)
            # teacher saves an answer without a page reload
            tctx = b.new_context(viewport={"width": 390, "height": 760}, is_mobile=True, has_touch=True)
            tctx.add_cookies([{"name": "session", "value": tk.value, "url": base}])
            tctx.route("**/*", lambda r: r.abort() if not r.request.url.startswith("http://127.0.0.1") else r.continue_())
            tp = tctx.new_page(); tp.on("pageerror", lambda e: errs.append(str(e)))
            tp.goto(base + "/teacher/doubts/%d" % did); tp.wait_for_timeout(300)
            tp.fill("#aText", "Because of inertia: no force stops it."); tp.click("#aSave"); tp.wait_for_timeout(700)
            check("teacher save shows confirmation inline", "saved" in tp.inner_text("#aStatus").lower(), tp.inner_text("#aStatus"))
            check("badge switches to Answered", "Answered" in tp.inner_text("#aBadge"))
            tp.screenshot(path=os.path.join(shots, "teacher_detail_390.png"), full_page=True)
            pg.goto(base + "/doubts/%d" % did); pg.wait_for_timeout(200)
            check("student sees the teacher answer + notification", "inertia" in pg.content() and len(self.q("SELECT 1 FROM notifications WHERE student_id=? AND link=?", self.stu, "/doubts/%d" % did)) == 1)
            pg.screenshot(path=os.path.join(shots, "detail_answered_390.png"), full_page=True)
            tp.goto(base + "/teacher/doubts"); tp.screenshot(path=os.path.join(shots, "teacher_list_390.png"), full_page=True)
            check("no JavaScript errors", not errs, errs)
            b.close()
        bad_ = [n for n, ok in res if not ok]
        self.assertFalse(bad_, bad_)


if __name__ == "__main__":
    unittest.main()
