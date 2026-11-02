# -*- coding: utf-8 -*-
"""Gmail SMTP checker - finds out WHICH step of email sending fails. Does not start the website.

    python scripts/check_smtp.py                       checks config, network, TLS and Gmail login (sends nothing)
    python scripts/check_smtp.py --send-to you@x.com   ALSO sends one real test mail (it contains no OTP)

Secrets are never printed: only the sender address (masked) and how many characters the app password has.
"""
import argparse
import os
import platform
import smtplib
import socket
import ssl
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import config  # noqa: E402  (loads .env exactly like the website does)
from backend.services import email_service as es  # noqa: E402

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"
problems = []


def line(tag, text):
    print("%s %s" % (tag, text))
    if tag == BAD:
        problems.append(text)


def env_checks():
    path = os.path.join(ROOT, ".env")
    line(OK, "Python %s on %s" % (platform.python_version(), platform.platform()))
    if not os.path.isfile(path):
        line(BAD, ".env file not found at %s - copy .env.example to .env and fill it in." % path)
        return
    line(OK, ".env found: %s" % path)
    raw = open(path, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        line(BAD, ".env was saved with a BOM (Notepad 'UTF-8 with BOM'): the first variable name is damaged. "
                  "Re-save as plain 'UTF-8' in VS Code (bottom-right encoding > Save with Encoding).")
    try:
        from dotenv import dotenv_values
        file_vals = dotenv_values(path)
        for key in ("EMAIL_ADDRESS", "EMAIL_APP_PASSWORD"):
            in_file, in_env = file_vals.get(key), os.environ.get(key)
            if in_file and in_env and in_file != in_env:
                line(BAD, "%s in .env differs from the value of the SAME name set in Windows/the terminal. "
                          "The Windows value wins and your .env change is ignored. Remove the system variable "
                          "(Settings > System > About > Advanced system settings > Environment Variables) or run "
                          "'Remove-Item Env:%s' in PowerShell." % (key, key))
    except ImportError:
        line(WARN, "python-dotenv is not installed: .env is NOT read. Run: pip install -r requirements.txt")


def config_checks():
    problem = es.config_problem()
    if problem:
        line(BAD, "%s - %s" % (problem[0], problem[1]))
        return False
    pw = es.clean_app_password(config.EMAIL_APP_PASSWORD)
    line(OK, "EMAIL_ADDRESS = %s ; EMAIL_APP_PASSWORD has %d characters" % (es.mask_email(config.EMAIL_ADDRESS), len(pw)))
    if len(pw) != 16:
        line(WARN, "A Google App Password has exactly 16 letters (you have %d). A normal Gmail password will NOT work." % len(pw))
    if config.LOCAL_OTP_MODE:
        line(WARN, "LOCAL_OTP_MODE=%s is set: the website prints OTPs in the terminal and does not email them. "
                   "This script still tests real Gmail." % config.LOCAL_OTP_MODE)
    return True


def network_checks():
    try:
        ip = socket.gethostbyname(es.SMTP_HOST)
        line(OK, "DNS: %s -> %s" % (es.SMTP_HOST, ip))
    except OSError as exc:
        line(BAD, "DNS lookup failed (%s). No internet, or DNS blocked." % exc)
        return False
    reachable = False
    for port in (587, 465):
        try:
            socket.create_connection((es.SMTP_HOST, port), timeout=8).close()
            line(OK, "TCP port %d reachable" % port)
            reachable = True
        except OSError as exc:
            line(BAD, "TCP port %d NOT reachable (%s). Firewall, antivirus, router or ISP blocks outbound SMTP." % (port, exc))
    return reachable


def login_check(port):
    stage = "connect"
    try:
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(es.SMTP_HOST, 465, context=ctx, timeout=15) if port == 465 else smtplib.SMTP(es.SMTP_HOST, 587, timeout=15)
        with server:
            line(OK, "port %d: connected" % port)
            if port == 587:
                stage = "starttls"
                server.ehlo(); server.starttls(context=ctx); server.ehlo()
                line(OK, "port 587: STARTTLS succeeded")
            stage = "login"
            server.login(config.EMAIL_ADDRESS, es.clean_app_password(config.EMAIL_APP_PASSWORD))
            line(OK, "port %d: Gmail login accepted" % port)
        return True
    except Exception as exc:  # noqa: BLE001
        code, _user, hint = es.classify(exc)
        line(BAD, "port %d failed at '%s' [%s]: %s: %s\n         -> %s" % (port, stage, code, type(exc).__name__, exc, hint))
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send-to", help="also send one real test email to this address")
    args = ap.parse_args()
    print("== 1. Environment ==")
    env_checks()
    print("\n== 2. Configuration ==")
    if not config_checks():
        return finish()
    print("\n== 3. Network ==")
    net = network_checks()
    if net:
        print("\n== 4. TLS + Gmail login ==")
        ok587, ok465 = login_check(587), login_check(465)
        if args.send_to and (ok587 or ok465):
            print("\n== 5. Real test email ==")
            r = es.send_message(args.send_to, "SMTP test - %s" % config.SITE_NAME,
                                "This is a test email from scripts/check_smtp.py. No OTP is in it.")
            line(OK if r.ok else BAD, "test email %s" % ("sent to %s - check the inbox AND spam folder" % args.send_to
                 if r.ok else "failed at %s [%s]: %s" % (r.stage, r.code, r.technical)))
    return finish()


def finish():
    print()
    if problems:
        print("RESULT: %d problem(s). Fix the first [FAIL] line above, then run this script again." % len(problems))
        return 1
    print("RESULT: everything checked passed. (A passing check does not prove a mail reached an inbox: use --send-to.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
