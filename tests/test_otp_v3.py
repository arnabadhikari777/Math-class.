# -*- coding: utf-8 -*-
"""OTP + SMTP diagnosis + LOCAL_OTP_MODE tests. No real email is sent and no network is used.
A pass here does NOT prove that Gmail delivers: use scripts/check_smtp.py --send-to for that.
Run: python -m unittest tests.test_otp_v3 -v"""
import sys as _sys, os as _os  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import contextlib, io, logging, re, smtplib, socket, sqlite3, ssl, unittest  # noqa: E402,E401
from datetime import datetime, timedelta  # noqa: E402
from unittest import mock  # noqa: E402

from appcase import AppCase, CSRF  # noqa: E402

REG = {"name": "Newbie", "class_name": "10", "roll": "5", "email": "new@t.local", "mobile": "9876543210",
       "password": "Str0ng!Passw0rd", "confirm_password": "Str0ng!Passw0rd"}


class Base(AppCase):
    def anon(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_csrf_token"] = CSRF
        return c

    def form(self, c, url, data, **kw):
        d = dict(data); d["csrf_token"] = CSRF
        return c.post(url, data=d, **kw)

    def otp_rows(self, email, purpose="registration"):
        return self.q("SELECT * FROM otp_codes WHERE email=? AND purpose=? ORDER BY id", email, purpose)

    def register(self, c, **over):
        return self.form(c, "/register", dict(REG, **over), follow_redirects=True)


# --------------------------------------------------------------------- local console mode
class LocalConsoleMode(Base):
    """LOCAL_OTP_MODE=console on a local debug run: OTP appears in the terminal only."""
    @classmethod
    def setUpClass(cls):
        cls._saved = dict(_os.environ)
        _os.environ.update({"LOCAL_OTP_MODE": "console", "FLASK_DEBUG": "1"})
        try:
            super().setUpClass()
            _os.environ.update({"LOCAL_OTP_MODE": "console", "FLASK_DEBUG": "1", "FORCE_HTTPS": "0"})
        except Exception:
            _os.environ.clear(); _os.environ.update(cls._saved); raise

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        _os.environ.clear(); _os.environ.update(cls._saved)

    def run_reg(self, c, **over):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = self.register(c, **over)
        return r, err.getvalue()

    def code_from(self, text):
        m = re.search(r"OTP\s*:\s*(\d{6})", text)
        self.assertTrue(m, "no OTP banner in terminal output: %r" % text)
        return m.group(1)

    def test_registration_end_to_end_with_terminal_otp(self):
        c = self.anon()
        r, term = self.run_reg(c)
        code = self.code_from(term)
        html = r.get_data(as_text=True)
        self.assertNotIn(code, html)                                   # never in the browser response
        self.assertIn("Terminal", html)
        rows = self.otp_rows("new@t.local")
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["otp_code"], code)                 # stored hashed
        self.assertEqual(len(rows[0]["otp_code"]), 64)
        r = self.form(c, "/verify-otp", {"otp_code": "000000" if code != "000000" else "111111"}, follow_redirects=True)
        self.assertIn("Incorrect OTP", r.get_data(as_text=True))
        r = self.form(c, "/verify-otp", {"otp_code": code}, follow_redirects=True)
        self.assertIn("Account verified", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT is_verified FROM students WHERE email='new@t.local'")[0][0], 1)
        # single use: the same code cannot verify again
        c2 = self.anon()
        with c2.session_transaction() as s:
            s["pending_email"] = "new@t.local"
        r = self.form(c2, "/verify-otp", {"otp_code": code}, follow_redirects=True)
        self.assertIn("No OTP found", r.get_data(as_text=True))

    def test_code_never_reaches_logs_or_json(self):
        c = self.anon()
        with self.assertLogs(level="DEBUG") as cap, contextlib.redirect_stderr(io.StringIO()) as term:
            logging.getLogger("backend.email").info("probe")
            self.register(c, email="logs@t.local")
        code = self.code_from(term.getvalue())
        self.assertNotIn(code, "\n".join(cap.output))

    def test_cooldown_resend_and_max_attempts_still_apply(self):
        c = self.anon()
        _, term = self.run_reg(c, email="cool@t.local")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = self.form(c, "/resend-otp", {}, follow_redirects=True)
        self.assertIn("Please wait", r.get_data(as_text=True))          # cooldown (60 s)
        self.assertEqual(err.getvalue().count("LOCAL OTP MODE"), 0)     # and nothing was printed
        code = self.code_from(term)
        wrong = "999999" if code != "999999" else "888888"
        for _ in range(5):
            self.form(c, "/verify-otp", {"otp_code": wrong}, follow_redirects=True)
        r = self.form(c, "/verify-otp", {"otp_code": code}, follow_redirects=True)   # burned after 5 wrong tries
        self.assertNotIn("Account verified", r.get_data(as_text=True))

    def test_expired_otp_is_refused(self):
        c = self.anon()
        _, term = self.run_reg(c, email="old@t.local")
        code = self.code_from(term)
        k = sqlite3.connect(self.db)
        k.execute("UPDATE otp_codes SET expires_at=? WHERE email='old@t.local'", ((datetime.now() - timedelta(minutes=1)).isoformat(),))
        k.commit(); k.close()
        r = self.form(c, "/verify-otp", {"otp_code": code}, follow_redirects=True)
        self.assertIn("expired", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT is_verified FROM students WHERE email='old@t.local'")[0][0], 0)

    def test_password_reset_flow_with_terminal_otp(self):
        c = self.anon()
        self.run_reg(c, email="reset@t.local")
        k = sqlite3.connect(self.db); k.execute("UPDATE students SET is_verified=1 WHERE email='reset@t.local'"); k.commit(); k.close()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.form(c, "/forgot-password", {"email": "reset@t.local"}, follow_redirects=True)
        code = self.code_from(err.getvalue())
        r = self.form(c, "/reset-password", {"email": "reset@t.local", "otp_code": code, "password": "N3w!Passw0rdXY",
                                             "confirm_password": "N3w!Passw0rdXY"}, follow_redirects=True)
        self.assertNotIn(code, r.get_data(as_text=True))
        self.assertEqual(len(self.otp_rows("reset@t.local", "password_reset")), 1)
        self.assertEqual(self.otp_rows("reset@t.local", "password_reset")[0]["is_used"], 1)
        # unknown email: generic answer and no OTP printed
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.form(self.anon(), "/forgot-password", {"email": "ghost@t.local"})
        self.assertNotIn("LOCAL OTP MODE", err.getvalue())

    def test_refused_when_the_request_is_not_localhost(self):
        from backend.core import app
        from backend.services import email_service as es
        for host, allowed in (("http://localhost:5000", True), ("http://127.0.0.1:5000", True), ("http://[::1]:5000", True),
                              ("http://school.example.com", False), ("http://192.168.1.20:5000", False),
                              ("http://localhost.evil.com", False)):
            with app.test_request_context("/", base_url=host):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    r = es.send_otp_result("a@t.local", "123456", "registration")
                self.assertEqual(r.ok, allowed, host)
                self.assertEqual("123456" in err.getvalue(), allowed, host)
                if not allowed:
                    self.assertEqual(r.code, "LOCAL_MODE_NOT_LOCAL")

    def test_other_security_is_not_switched_off(self):
        c = self.app.test_client()                                      # no CSRF token -> still rejected
        r = c.post("/register", data=dict(REG, email="csrf@t.local"))
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(self.q("SELECT COUNT(*) FROM students WHERE email='csrf@t.local'")[0][0], 0)


# --------------------------------------------------------------------- startup safety (pure function)
class StartupValidation(unittest.TestCase):
    def check(self, **kw):
        import importlib.util
        spec = importlib.util.spec_from_file_location("cfg_under_test", _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "backend", "config.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m, m.validate_local_otp_mode(**kw)

    def refused(self, **kw):
        m, _ = self.check(mode="", debug=True, force_https=False, environ={})
        with self.assertRaises(m.ConfigurationError):
            m.validate_local_otp_mode(**kw)

    def test_allowed_only_on_local_debug(self):
        self.check(mode="console", debug=True, force_https=False, environ={})
        self.check(mode="", debug=False, force_https=True, environ={"PYTHONANYWHERE_DOMAIN": "x"})   # off = always fine

    def test_refused_everywhere_else(self):
        self.refused(mode="console", debug=False, force_https=False, environ={})
        self.refused(mode="console", debug=True, force_https=True, environ={})
        self.refused(mode="console", debug=True, force_https=False, environ={"PYTHONANYWHERE_DOMAIN": "me.pythonanywhere.com"})
        self.refused(mode="console", debug=False, force_https=True, environ={"PYTHONANYWHERE_SITE": "www.pythonanywhere.com"})
        self.refused(mode="file", debug=True, force_https=False, environ={})                         # unknown value

    def test_app_does_not_start_in_production_with_console_mode(self):
        import subprocess, sys
        root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        env = dict(_os.environ, LOCAL_OTP_MODE="console", FLASK_DEBUG="0", FORCE_HTTPS="1", SECRET_KEY="x")
        p = subprocess.run([sys.executable, "-c", "import backend.bootstrap"], cwd=root, env=env, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ConfigurationError", p.stderr)
        self.assertIn("LOCAL_OTP_MODE=console is for local development only", p.stderr)


# --------------------------------------------------------------------- real-mail path with SMTP mocked
class SmtpPath(Base):
    def setUp(self):
        from backend.services import email_service as es
        self.es = es
        self.patches = [mock.patch.object(es, "EMAIL_ADDRESS", "teacher@gmail.com"),
                        mock.patch.object(es, "EMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def fake_smtp(self, login_exc=None, connect_exc=None, starttls_exc=None):
        sent = []

        class S:
            def __init__(s, *a, **k):
                s.a = a
                if connect_exc:
                    raise connect_exc
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def ehlo(s): pass
            def starttls(s, context=None):
                if starttls_exc: raise starttls_exc
            def login(s, u, p):
                sent.append(("login", u, p))
                if login_exc: raise login_exc
            def sendmail(s, f, to, raw): sent.append(("mail", to, raw))
        return S, sent

    def test_success_strips_spaces_from_app_password_and_logs_every_stage(self):
        S, sent = self.fake_smtp()
        with mock.patch.object(self.es.smtplib, "SMTP", S), self.assertLogs("backend.email", "INFO") as cap:
            r = self.es.send_message("kid@t.local", "s", "body")
        self.assertTrue(r.ok)
        self.assertEqual(sent[0], ("login", "teacher@gmail.com", "abcdefghijklmnop"))
        log = "\n".join(cap.output)
        for stage in ("stage=config", "stage=connect", "stage=starttls", "stage=login", "stage=send"):
            self.assertIn(stage, log)
        self.assertNotIn("abcdefghijklmnop", log); self.assertNotIn("abcd efgh", log)
        self.assertNotIn("teacher@gmail.com", log)                       # address is masked
        self.assertIn("t***@gmail.com", log)

    def test_each_failure_is_classified_with_its_stage(self):
        cases = [
            (dict(login_exc=smtplib.SMTPAuthenticationError(535, b"bad")), "AUTH_FAILED", "login"),
            (dict(connect_exc=socket.timeout("t")), "CONNECT_TIMEOUT", "connect"),
            (dict(connect_exc=ConnectionRefusedError("no")), "CONNECT_FAILED", "connect"),
            (dict(connect_exc=socket.gaierror(-2, "x")), "DNS_FAILED", "connect"),
            (dict(starttls_exc=ssl.SSLCertVerificationError("cert")), "TLS_CERT_FAILED", "starttls"),
        ]
        for kw, code, stage in cases:
            S, _ = self.fake_smtp(**kw)
            def ssl_route(*a, _kw=kw, **k):
                raise (_kw.get("connect_exc") or _kw.get("starttls_exc") or socket.timeout("465"))
            ssl_cls = S if "login_exc" in kw else ssl_route
            with mock.patch.object(self.es.smtplib, "SMTP", S), mock.patch.object(self.es.smtplib, "SMTP_SSL", ssl_cls), \
                    self.assertLogs("backend.email", "ERROR"):
                r = self.es.send_message("kid@t.local", "s", "b")
            self.assertFalse(r.ok)
            self.assertEqual(r.code, code, kw)
            self.assertEqual(r.stage, stage, kw)
            self.assertTrue(re.search(r"[\u0980-\u09FF]", r.user_message))   # plain Bengali for the user
            self.assertIn("port 587", r.technical); self.assertIn("port 465", r.technical)   # the 587 error is no longer hidden

    def test_auth_error_of_587_is_not_overwritten_by_465_connect_error(self):
        calls = {}

        class S587:
            def __init__(s, *a, **k): pass
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def ehlo(s): pass
            def starttls(s, context=None): pass
            def login(s, u, p): raise smtplib.SMTPAuthenticationError(535, b"BadCredentials")

        def ssl_fail(*a, **k):
            raise socket.timeout("465 blocked")
        with mock.patch.object(self.es.smtplib, "SMTP", S587), mock.patch.object(self.es.smtplib, "SMTP_SSL", ssl_fail), \
                self.assertLogs("backend.email", "ERROR"):
            r = self.es.send_message("kid@t.local", "s", "b")
        self.assertEqual(r.code, "AUTH_FAILED")

    def test_config_problems_are_found_before_any_connection(self):
        for addr, pw, code in (("", "x" * 16, "CONFIG_NO_ADDRESS"), ("nope", "x" * 16, "CONFIG_BAD_ADDRESS"),
                               ("a@gmail.com", "", "CONFIG_NO_PASSWORD"), ("a@gmail.com", "pässwörd", "CONFIG_PASSWORD_NOT_ASCII")):
            with mock.patch.object(self.es, "EMAIL_ADDRESS", addr), mock.patch.object(self.es, "EMAIL_APP_PASSWORD", pw), \
                    mock.patch.object(self.es.smtplib, "SMTP") as smtp, self.assertLogs("backend.email", "ERROR"):
                r = self.es.send_message("kid@t.local", "s", "b")
            self.assertEqual(r.code, code)
            smtp.assert_not_called()

    def test_invalid_recipient_and_header_injection(self):
        for bad in ("nope", "a@b.co\r\nBcc: x@y.z", ""):
            self.assertEqual(self.es.send_message(bad, "s", "b").code, "INVALID_RECIPIENT")

    def test_registration_smtp_failure_shows_simple_bengali_and_no_code(self):
        c = self.anon()
        S, _ = self.fake_smtp(login_exc=smtplib.SMTPAuthenticationError(535, b"bad"))
        with mock.patch.object(self.es.smtplib, "SMTP", S), mock.patch.object(self.es.smtplib, "SMTP_SSL", S), \
                self.assertLogs("backend.email", "ERROR"):
            r = self.register(c, email="fail@t.local")
        html = r.get_data(as_text=True)
        self.assertIn("লগইন করা যায়নি", html)
        self.assertNotIn("535", html); self.assertNotIn("smtp", html.lower().replace("smtp.", "x"))
        self.assertEqual(self.otp_rows("fail@t.local"), [])              # useless code removed; resend is possible at once

    def test_registration_success_hashes_code_and_verifies(self):
        c = self.anon()
        S, sent = self.fake_smtp()
        with mock.patch.object(self.es.smtplib, "SMTP", S):
            r = self.register(c, email="ok@t.local")
        self.assertIn("OTP sent to your email", r.get_data(as_text=True))
        mail = [x for x in sent if x[0] == "mail"][0][2]
        import email as em
        body = em.message_from_string(mail).get_payload(decode=True).decode()
        code = re.search(r"OTP\) is: (\d{6})", body).group(1)
        self.assertNotIn(code, r.get_data(as_text=True))
        r = self.form(c, "/verify-otp", {"otp_code": code}, follow_redirects=True)
        self.assertIn("Account verified", r.get_data(as_text=True))

    def test_already_registered_and_invalid_email(self):
        c = self.anon()
        r = self.register(c, email="Sam@x.local")                        # fresh -> goes to OTP step (mail fails silently here)
        k = sqlite3.connect(self.db); k.execute("UPDATE students SET is_verified=1 WHERE email='sam@x.local'"); k.commit(); k.close()
        r = self.register(self.anon(), email="sam@x.local")
        self.assertIn("already exists", r.get_data(as_text=True))
        r = self.register(self.anon(), email="not-an-email")
        self.assertIn("valid email", r.get_data(as_text=True))

    def test_background_reset_failure_is_only_logged(self):
        S, _ = self.fake_smtp(connect_exc=socket.timeout("x"))
        with mock.patch.object(self.es.smtplib, "SMTP", S), mock.patch.object(self.es.smtplib, "SMTP_SSL", S), \
                self.assertLogs("backend.email", "ERROR"):
            r = self.es.send_otp_result("kid@t.local", "123456", "password_reset")
        self.assertFalse(r.ok)

    def test_console_mode_off_never_prints(self):
        S, _ = self.fake_smtp()
        err = io.StringIO()
        with mock.patch.object(self.es.smtplib, "SMTP", S), contextlib.redirect_stderr(err):
            self.es.send_otp_result("kid@t.local", "424242", "registration")
        self.assertNotIn("424242", err.getvalue())


if __name__ == "__main__":
    unittest.main()
