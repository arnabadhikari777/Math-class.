# -*- coding: utf-8 -*-
"""Real-browser (Chromium) layout audit at the 9 required widths, student + teacher, light + dark.

Boots a TEMPORARY copy of the project with a throw-away DB (never touches database.db) using the same
harness as the unit tests, serves it on a local port, seeds a published test / doubts / notifications and
logs in with signed session cookies. Cloudinary is faked. External CDNs (Font Awesome, Google Fonts, KaTeX)
are NOT reachable in an offline sandbox, so icons/fonts fall back; layout rules are still measured.

    python tests/browser/mobile_audit.py            # prints a report, exits 1 on any failure
    MOBILE_AUDIT_SHOTS=1 python tests/browser/mobile_audit.py   # also saves screenshots to tests/browser/screenshots
"""
import json, os, sys, threading, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE)))
from appcase import AppCase, CSRF, q_item  # noqa: E402

WIDTHS = [320, 360, 375, 390, 412, 430, 480, 768, 1024, 1366, 1920]
PORT = 5091
SHOTS = os.environ.get("MOBILE_AUDIT_SHOTS") == "1"

JS_AUDIT = r"""
() => {
  const W = window.innerWidth, out = {W, issues: []};
  const de = document.documentElement;
  out.scrollW = de.scrollWidth;
  if (de.scrollWidth > W + 1) {
    const bad = [];
    document.querySelectorAll('body *').forEach(e => {
      const r = e.getBoundingClientRect();
      if (r.width && r.right > W + 1 && getComputedStyle(e).position !== 'fixed') {
        let p = e.parentElement, scrolls = false;
        while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { scrolls = true; break; } p = p.parentElement; }
        if (!scrolls && bad.length < 4) bad.push((e.id ? '#' + e.id : e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).join('.') : '')) + ' right=' + Math.round(r.right));
      }
    });
    out.issues.push('horizontal overflow: scrollWidth ' + de.scrollWidth + ' > ' + W + ' :: ' + bad.join(' | '));
  }
  // header controls must be visible and inside the viewport
  const need = [['.btn-logout', 'logout'], ['#themeBtn', 'theme'], ['#notifBtn, a.notif-btn', 'notifications']];
  const hasHeader = !!document.querySelector('.topbar');
  if (hasHeader) {
    need.forEach(([sel, name]) => {
      const els = [...document.querySelectorAll('.topbar ' + sel)];
      if (!els.length) return;
      const reachable = els.some(e => {
        const cs = getComputedStyle(e), r = e.getBoundingClientRect();
        if (cs.display === 'none' || cs.visibility === 'hidden' || r.width < 1) return false;
        return r.left >= -1 && r.right <= W + 1;
      });
      // logout may live inside an account menu: acceptable only if the menu trigger is reachable
      if (!reachable) {
        const trig = document.querySelector('.topbar [data-account-trigger]');
        const tr = trig && trig.getBoundingClientRect();
        if (!(name === 'logout' && tr && tr.width > 0 && tr.left >= -1 && tr.right <= W + 1)) out.issues.push('header: ' + name + ' not reachable');
      }
    });
    const tb = document.querySelector('.topbar');
    if (tb.scrollWidth > tb.clientWidth + 1) out.issues.push('header content wider than header (' + tb.scrollWidth + '>' + tb.clientWidth + ')');
  }
  // floating controls must not overlap each other
  const fl = ['.bottom-nav', '.qa-btn', '.aiw-fab'].map(s => [s, document.querySelector(s)]).filter(([s, e]) => e && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 0);
  for (let i = 0; i < fl.length; i++) for (let j = i + 1; j < fl.length; j++) {
    const a = fl[i][1].getBoundingClientRect(), b = fl[j][1].getBoundingClientRect();
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) out.issues.push('overlap: ' + fl[i][0] + ' x ' + fl[j][0]);
  }
  // tap targets in header / bottom nav
  document.querySelectorAll('.topbar button, .topbar a.notif-btn, .bottom-nav a, .bottom-nav button').forEach(e => {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    if (cs.display !== 'none' && r.width > 0 && (r.height < 36 || r.width < 36)) out.issues.push('small tap target ' + (e.id || e.className) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
  });
  // text inputs must be >= 16px on phones (iOS zoom)
  if (W <= 480) document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]), select, textarea').forEach(e => {
    const cs = getComputedStyle(e), r = e.getBoundingClientRect();
    if (cs.display !== 'none' && r.width > 0 && parseFloat(cs.fontSize) < 16) out.issues.push('input font <16px: ' + (e.name || e.id));
  });
  return out;
}
"""


JS_INTERACT = r"""
async () => {
  const issues = [], W = innerWidth, H = innerHeight, sleep = ms => new Promise(r => setTimeout(r, ms));
  const inside = (e, name) => { const r = e.getBoundingClientRect(); if (r.width < 1 || r.left < -1 || r.right > W + 1 || r.top < -1 || r.bottom > H + 1) issues.push(name + ' outside viewport ' + JSON.stringify([r.left|0, r.top|0, r.right|0, r.bottom|0])); };
  const acct = document.querySelector('[data-account-trigger]');
  if (acct && acct.getBoundingClientRect().width > 0) {
    acct.click(); await sleep(60);
    const m = document.getElementById('acctMenu');
    if (!m || m.hidden) issues.push('account menu did not open'); else {
      inside(m, 'account menu');
      const out = m.querySelector('.acct-out'); if (!out || out.getBoundingClientRect().bottom > H) issues.push('logout not visible in account menu');
      if (acct.getAttribute('aria-expanded') !== 'true') issues.push('aria-expanded not set');
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); await sleep(40);
      if (!m.hidden) issues.push('Escape did not close account menu');
      acct.click(); await sleep(40); document.body.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); await sleep(40);
      if (!m.hidden) issues.push('outside tap did not close account menu');
    }
  }
  const pm = document.querySelector('.pub-more [data-menu-trigger]');
  if (pm && getComputedStyle(pm).display !== 'none' && pm.getBoundingClientRect().width > 0) { pm.click(); await sleep(60); const p = document.getElementById('pubMenu'); if (!p || p.hidden) issues.push('public menu did not open'); else inside(p, 'public menu'); }
  const nb = document.getElementById('notifBtn');
  if (nb) { nb.click(); await sleep(250); const np = document.getElementById('notifPanel'); if (!np || np.hidden) issues.push('notification panel did not open'); else inside(np, 'notification panel'); nb.click(); await sleep(150); }
  const qb = document.getElementById('qaBtn');
  if (qb && getComputedStyle(qb.parentElement).display !== 'none') { qb.click(); await sleep(60); const qm = document.getElementById('qaMenu'); if (qm.hidden) issues.push('quick actions did not open'); else inside(qm, 'quick actions menu'); qb.click(); }
  if (document.querySelector('.bottom-nav')) {
    await sleep(400); document.body.classList.add('kb-open'); await sleep(350);
    const bn = document.querySelector('.bottom-nav'); if (getComputedStyle(bn).display !== 'none' && bn.getBoundingClientRect().top < H - 4) issues.push('bottom nav still visible with keyboard open');
    document.body.classList.remove('kb-open');
  }
  return issues;
}
"""


class MobileAudit(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from werkzeug.serving import make_server
        cls.srv = make_server("127.0.0.1", PORT, cls.app, threaded=True)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    def _seed(self):
        self.t, self.tid = self.published_test("Audit Exam", n=3)
        import sqlite3
        c = sqlite3.connect(self.db)
        now = "2026-11-15T10:00:00"
        self.d_wait = c.execute("INSERT INTO doubts (student_id,subject,chapter,topic,question,created_at) VALUES (?,?,?,?,?,?)",
                               (self.stu, "Maths", "Quadratic Equations", "Discriminant", "How do I find the roots of $x^2-5x+6=0$ without factoring? " * 3, now)).lastrowid
        self.d_ans = c.execute("INSERT INTO doubts (student_id,subject,question,teacher_answer,teacher_answered_at,teacher_updated_at,created_at,teacher_seen) VALUES (?,?,?,?,?,?,?,1)",
                              (self.stu, "Physics", "Why does a ball keep moving? " + "a" * 80, "Because of inertia: $F=ma$.", now, now, now)).lastrowid
        for i in range(3):
            c.execute("INSERT INTO teacher_notifications (title,message,link,ref_key,created_at) VALUES (?,?,?,?,?)",
                      ("New doubt: Maths", "Sam (TEN) asked a new doubt with a fairly long message " * 2, "/teacher/doubts/%d" % self.d_wait, "t%d" % i, now))
        c.commit(); c.close()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        super().tearDownClass()

    def cookie(self, cl):
        c = cl.get_cookie("session")
        return {"name": "session", "value": c.value, "url": "http://127.0.0.1:%d" % PORT}

    def test_layout(self):
        from playwright.sync_api import sync_playwright
        self._seed()
        stu_ck, tea_ck = self.cookie(self.student()), self.cookie(self.teacher())
        student_pages = ["/dashboard", "/notes", "/doubts", "/doubts/new", "/doubts/%d" % self.d_wait, "/doubts/%d" % self.d_ans,
                         "/chat", "/profile", "/settings", "/test/%d" % self.tid, "/test/%d/leaderboard" % self.tid]
        teacher_pages = ["/teacher/dashboard", "/teacher/students", "/teacher/tests", "/teacher/notes/upload", "/teacher/doubts",
                         "/teacher/doubts/%d" % self.d_wait, "/teacher/doubts/%d" % self.d_ans, "/teacher/login-activity", "/teacher/announcements",
                         "/teacher/contacts", "/teacher/activity", "/teacher/cleanup", "/teacher/tests/%d/results" % self.tid]
        public_pages = ["/", "/login", "/register", "/contact", "/download", "/teacher/login", "/forgot-password"]
        report, fails = [], 0
        shot_dir = os.path.join(HERE, "screenshots", "mobile")
        if SHOTS: os.makedirs(shot_dir, exist_ok=True)
        with sync_playwright() as p:
            b = p.chromium.launch()
            for role, ck, pages in (("student", stu_ck, student_pages), ("teacher", tea_ck, teacher_pages), ("public", None, public_pages)):
                for theme in ("light", "dark"):
                    for W in WIDTHS:
                        if theme == "dark" and W not in (320, 390, 768, 1366): continue
                        ctx = b.new_context(viewport={"width": W, "height": 740 if W < 700 else 900}, device_scale_factor=1,
                                            has_touch=W < 700, is_mobile=W < 700, color_scheme=theme)
                        if ck: ctx.add_cookies([ck])
                        ctx.add_init_script("try{localStorage.setItem('amc-theme','%s')}catch(e){}" % theme)
                        ctx.route("**/*", lambda route: route.abort() if not route.request.url.startswith("http://127.0.0.1") else route.continue_())
                        for path in pages:
                            pg = ctx.new_page()
                            errs = []
                            pg.on("pageerror", lambda e, errs=errs: errs.append("JS: " + str(e)[:160]))
                            pg.on("console", lambda m, errs=errs: errs.append("console: " + m.text[:160]) if m.type == "error" and "ERR_FAILED" not in m.text and "Failed to load resource" not in m.text else None)
                            try:
                                resp = pg.goto("http://127.0.0.1:%d%s" % (PORT, path), wait_until="domcontentloaded", timeout=20000)
                                pg.wait_for_timeout(250)
                                st = resp.status if resp else 0
                                res = pg.evaluate(JS_AUDIT)
                                issues = list(res["issues"]) + errs
                                if st >= 400: issues.append("HTTP %d" % st)
                                if path in ("/dashboard", "/teacher/dashboard", "/login") and W <= 1024:
                                    issues += pg.evaluate(JS_INTERACT)
                                if SHOTS and theme == "light" and W in (360, 768): pg.screenshot(path=os.path.join(shot_dir, "%s_%s_%d.png" % (role, path.strip("/").replace("/", "_") or "home", W)))
                            except Exception as e:  # noqa: BLE001
                                issues = ["EXC " + str(e)[:150]]
                            if issues:
                                fails += 1
                                report.append("%s %s %dpx %s: %s" % (role, theme, W, path, "; ".join(issues)))
                            pg.close()
                        ctx.close()
            b.close()
        print("\n".join(report) if report else "no layout issues")
        print("\nAUDIT SUMMARY: %d page/viewport combinations with issues" % fails)
        json.dump(report, open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "mobile_audit_last.json"), "w"), indent=1)
        self.assertEqual(fails, 0, "\n".join(report[:40]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
