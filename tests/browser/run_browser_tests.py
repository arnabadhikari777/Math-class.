# -*- coding: utf-8 -*-
"""Real-browser (Playwright/Chromium) smoke test on a TEMPORARY copy of the project with a throw-away database.
Cloudinary is replaced by a fake inside the server process, no email is sent (LOCAL_OTP_MODE=console).

    pip install playwright && python -m playwright install chromium
    python tests/browser/run_browser_tests.py
"""
import os, re, shutil, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 5077
BASE = "http://127.0.0.1:%d" % PORT
tmp = tempfile.mkdtemp(prefix="browser-")
proj = os.path.join(tmp, "p")
shutil.copytree(ROOT, proj, ignore=shutil.ignore_patterns("backups", ".secret_key", "__pycache__", ".env", "database.db", "tests"))
LAUNCH = '''
import itertools, sys
from unittest import mock
n = itertools.count(1)
def fake(url, data=None, files=None, timeout=None):
    if url.endswith("/destroy"):
        return mock.Mock(status_code=200, json=lambda: {"result": "ok"})
    i = next(n); import time; time.sleep(0.4)
    return mock.Mock(status_code=200, json=lambda: {"secure_url": "http://127.0.0.1:%d/static/icons/icon-192.png?v=%%d" %% i, "public_id": "fake/%%d" %% i, "width": 5, "height": 5, "bytes": 70, "format": "png"})
import backend.services.image_store as st
st.requests.post = fake
from backend.bootstrap import app
app.run(host="127.0.0.1", port=%d, debug=False, use_reloader=False)
''' % (PORT, PORT)
env = dict(os.environ, FLASK_DEBUG="1", FORCE_HTTPS="0", LOCAL_OTP_MODE="console", SECRET_KEY="browser-test",
           TEACHER_USERNAME="sir", TEACHER_PASSWORD="TeacherPass123", CLOUDINARY_CLOUD_NAME="demo",
           CLOUDINARY_API_KEY="123456789012345", CLOUDINARY_API_SECRET="s", PYTHONUNBUFFERED="1")
shutil.copy(os.path.join(ROOT, "tests", "fixtures", "legacy_007_schema_only.db"), os.path.join(proj, "database.db"))
log = open(os.path.join(tmp, "server.log"), "w+")
srv = subprocess.Popen([sys.executable, "-c", LAUNCH], cwd=proj, env=env, stdout=log, stderr=subprocess.STDOUT)
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (" - " + extra if extra and not cond else ""))


def terminal_otp():
    time.sleep(0.5); log.flush(); log.seek(0)
    m = re.findall(r"OTP\s*:\s*(\d{6})", log.read())
    return m[-1] if m else None


try:
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/login", timeout=1); break
        except Exception:
            time.sleep(0.5)
    from playwright.sync_api import sync_playwright
    png = os.path.join(tmp, "a.png")
    import base64
    open(png, "wb").write(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
    shots = os.path.join(ROOT, "tests", "browser", "screenshots"); os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        # ---------------- student registration with terminal OTP
        s = b.new_context().new_page()
        s.goto(BASE + "/register")
        s.fill("input[name=name]", "Browser Kid"); s.select_option("select[name=class_name]", index=1)
        s.fill("input[name=roll]", "7"); s.fill("input[name=email]", "kid@t.local"); s.fill("input[name=mobile]", "9876543210")
        s.fill("input[name=password]", "Str0ng!Passw0rd"); s.fill("input[name=confirm_password]", "Str0ng!Passw0rd")
        s.click("button[type=submit]"); s.wait_for_url("**/verify-otp")
        code = terminal_otp()
        check("OTP printed in server terminal", code is not None)
        check("OTP not visible in the browser page", code and code not in s.content())
        s.fill("input[name=otp_code]", code); s.click("button[type=submit]"); s.wait_for_url("**/login")
        check("registration verified via terminal OTP", "verified" in s.content().lower())
        s.fill("input[name=email]", "kid@t.local"); s.fill("input[name=password]", "Str0ng!Passw0rd"); s.click("button[type=submit]")
        s.wait_for_url("**/dashboard"); check("student can log in after OTP", "/dashboard" in s.url)
        # ---------------- teacher
        t = b.new_context(viewport={"width": 1100, "height": 900}).new_page()
        t.goto(BASE + "/teacher/login"); t.fill("input[name=username]", "sir"); t.fill("input[name=password]", "TeacherPass123")
        t.click("button[type=submit]"); t.wait_for_url("**/teacher/dashboard")
        t.goto(BASE + "/teacher/tests/create")
        t.fill("input[name=title]", "Mathematics Chapter 1"); t.fill("input[name=subject]", "Maths")
        t.select_option("select[name=class_name]", index=1); t.fill("input[name=duration_minutes]", "30")
        t.click("button:has-text(\"Create Draft\")"); t.wait_for_url("**/teacher/studio/tests/*/questions")
        check("create test lands in its Test Workspace", "Mathematics Chapter 1" in t.inner_text("#wsHead"))
        t.goto(BASE + "/teacher/tests")
        t.click("text=Manage Questions"); t.wait_for_url("**/teacher/studio/tests/*/questions")
        check("Manage Questions opens the Test Workspace of that test", "Selected test" in t.inner_text("#wsHead"))
        nav = t.inner_text(".subnav")
        check("same tabs: My Questions / Test Workspace / Image Library", all(x in nav for x in ("My Questions", "Test Workspace", "Image Library")))
        t.click("#blocks .pick"); t.click(".ip-tab[data-tab=up]")
        sel = t.eval_on_selector("#ipCatSel", "e => e.options[e.selectedIndex].text")
        check("picker default category = test name", sel.startswith("Mathematics Chapter 1"), sel)
        check("picker shows selected test banner", "Mathematics Chapter 1" in t.inner_text("#ipCtx"))
        t.set_input_files("#ipFile", [png, png])
        t.wait_for_selector(".ip-card.ip-new", timeout=15000)
        check("2 uploaded images shown immediately, marked new", t.locator(".ip-card.ip-new").count() == 2)
        check("success message shown with category", "Mathematics Chapter 1" in t.inner_text("#ipLibMsg"))
        t.screenshot(path=os.path.join(shots, "image_picker_after_upload.png"))
        t.click(".ip-card.ip-new >> nth=0")
        t.fill("#blocks .f-text", "Figure question"); 
        for k in "abcd": t.fill("#blocks .f-" + k, "opt " + k)
        t.click("#saveBtn"); t.wait_for_selector("text=Figure question")
        check("question with image saved into the test", "Figure question" in t.content())
        t.screenshot(path=os.path.join(shots, "test_workspace.png"), full_page=True)
        # second picker open: category now exists and is default; other category selectable
        t.click("#blocks .pick") if t.locator("#blocks .pick").count() else t.click(".set-img >> nth=0")
        t.click(".ip-tab[data-tab=up]")
        sel = t.eval_on_selector("#ipCatSel", "e => e.options[e.selectedIndex].text")
        n_opts = t.eval_on_selector("#ipCatSel", "e => [...e.options].filter(o => o.text.startsWith('Mathematics Chapter 1')).length")
        check("existing category is the default and listed once", sel.startswith("Mathematics Chapter 1") and n_opts == 1, "%s %s" % (sel, n_opts))
        t.keyboard.press("Escape")
        # import page + unpublished navigation
        t.click("#importBtn"); t.wait_for_url("**/teacher/bank/import/*")
        check("import page keeps the same selected-test header", "Mathematics Chapter 1" in t.inner_text("#wsHead"))
        t.click("text=Test Workspace-এ ফিরুন"); t.wait_for_url("**/teacher/studio/tests/*/questions")
        t.set_viewport_size({"width": 390, "height": 800}); t.screenshot(path=os.path.join(shots, "test_workspace_mobile.png"))
        check("mobile layout has no horizontal scroll", t.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        b.close()
finally:
    srv.terminate(); log.close(); shutil.rmtree(tmp, ignore_errors=True)
failed = [n for n, ok in results if not ok]
print("\nBrowser checks: %d total, %d passed, %d failed" % (len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
