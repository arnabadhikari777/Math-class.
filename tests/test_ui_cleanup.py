# -*- coding: utf-8 -*-
"""Shared-layout cleanup: no student 3-dot button, no global footer, public Contact button, clickable dashboard cards."""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import re, sqlite3, unittest  # noqa: E402,E401
from appcase import AppCase, ROOT  # noqa: E402


def read(*p):
    return open(_os.path.join(ROOT, *p), encoding="utf-8").read()


class UiCleanup(AppCase):
    def test_student_pages_have_no_quick_actions_button_or_footer(self):
        stu = self.student()
        for url in ("/dashboard", "/doubts", "/doubts/new", "/notes", "/profile"):
            r = stu.get(url); self.assertEqual(r.status_code, 200, url)
            b = r.get_data(as_text=True)
            for bad in ('id="qaBtn"', 'id="qaMenu"', 'class="qa"', "<footer", "Get the app</a> ·"):
                self.assertNotIn(bad, b, (url, bad))
            self.assertIn("bottom-nav", b)                       # bottom navigation kept

    def test_assets_have_no_obsolete_quick_action_or_footer_code(self):
        css = read("static", "css", "app.css")
        self.assertIsNone(re.search(r"(^|[\s,}])\.qa(-btn|-menu)?\b", css))
        self.assertIsNone(re.search(r"(^|\n)footer\b", css))
        for f in ("shell.js", "doubts.js"):
            self.assertNotIn("qaBtn", read("static", "js", f)); self.assertNotIn("qaMenu", read("static", "js", f))
        self.assertNotIn("footer", read("static", "css", "chat.css"))

    def test_teacher_pages_keep_their_bottom_more_button_and_have_no_footer(self):
        b = self.teacher().get("/teacher/dashboard").get_data(as_text=True)
        self.assertIn('id="moreBtn"', b); self.assertNotIn("<footer", b)

    def test_public_header_contact_is_direct_single_and_menu_keeps_teacher(self):
        for url in ("/", "/login", "/register", "/contact"):
            b = self.app.test_client().get(url).get_data(as_text=True); 
            self.assertNotIn("<footer", b, url)
            head = b[b.index('<header class="topbar">'):b.index("</header>")]
            self.assertEqual(head.count('class="pub-contact'), 1, url)               # direct button, once
            menu = head[head.index('id="pubMenu"'):]
            self.assertNotIn("/contact", menu); self.assertIn("Teacher login", menu)   # not inside the 3-dot menu
            self.assertIn("Login", head); self.assertIn("Register", head); self.assertIn('id="themeBtn"', head)

    def test_teacher_dashboard_cards_are_whole_card_links(self):
        t = self.teacher(); b = t.get("/teacher/dashboard").get_data(as_text=True)
        cards = re.findall(r'<a class="stat stat-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, flags=re.S)
        self.assertEqual([c[0] for c in cards], ["/teacher/students", "/teacher/students?status=pending", "/teacher/tests", "/teacher/contacts"])
        for _h, inner in cards:
            self.assertNotIn("<a ", inner)                                            # no nested links
        for href, _ in cards:
            self.assertEqual(t.get(href).status_code, 200, href)

    def test_pending_card_count_matches_pending_list(self):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version,username,is_archived)"
                  " VALUES ('P1','TEN','8','p1@t.l','1','x',0,'2026-01-01',0,0,'p_one',0)")
        c.execute("INSERT INTO students (name,class_name,roll,email,mobile,password_hash,is_verified,registered_at,is_blocked,session_version,username,is_archived)"
                  " VALUES ('P2','TEN','9','p2@t.l','1','x',0,'2026-01-01',0,0,'p_two',1)")   # archived: must not be counted
        c.commit(); c.close()
        t = self.teacher()
        dash = t.get("/teacher/dashboard").get_data(as_text=True)
        n = int(re.search(r'Pending approval: (\d+)\.', dash).group(1))
        lst = t.get("/teacher/students?status=pending").get_data(as_text=True)
        self.assertEqual(n, 1); self.assertIn("P1", lst); self.assertNotIn("P2", lst)

    def test_student_dashboard_four_actions_keep_destinations(self):
        b = self.student().get("/dashboard").get_data(as_text=True)
        acts = re.findall(r'<a class="sd-act" href="([^"]+)"', b)
        self.assertEqual(acts, ["/doubts/new", "/notes", "/chat", "/settings"])
        self.assertEqual(b.count('class="sd-act"'), 4)


if __name__ == "__main__":
    unittest.main()
