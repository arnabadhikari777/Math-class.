# -*- coding: utf-8 -*-
"""Sends emails over SMTP (OTP mails, replies) - with a stage-by-stage diagnosis of every failure.

Stages of one send (each is logged on its own, see _log):
    config -> connect -> starttls (587 only) -> login -> send
Two routes are tried: smtp.gmail.com:587 (STARTTLS), then :465 (SSL). The error of EACH route is kept, so
the real reason (for example "login refused") is never hidden by the error of the second route.

Never logged anywhere: the OTP code, the app password, the full password length of anything but its size.
Local development: LOCAL_OTP_MODE=console prints the OTP in the Flask terminal instead of emailing it
(refused at startup anywhere except a local debug run - see backend/config.py).
"""
import logging
import smtplib
import socket
import ssl
import sys
import threading
from email.mime.text import MIMEText

from backend.config import (EMAIL_ADDRESS, EMAIL_APP_PASSWORD, EMAIL_RE, EMAIL_SENDER_NAME, OTP_EXPIRY_MINUTES,
                            SITE_NAME, local_otp_console_enabled)

log = logging.getLogger("backend.email")
SMTP_HOST = "smtp.gmail.com"
TIMEOUT = 20


class SendResult:
    """Outcome of one send. ``code`` names the failing stage/reason; ``user_message`` is safe to show."""
    def __init__(self, ok, stage=None, code=None, technical=None, user_message=None, attempts=None):
        self.ok, self.stage, self.code = ok, stage, code
        self.technical, self.user_message, self.attempts = technical, user_message, attempts or []

    def __bool__(self):
        return self.ok


# --------------------------------------------------------------------------- helpers
def mask_email(addr):
    addr = (addr or "").strip()
    if "@" not in addr:
        return "(empty)" if not addr else addr[:1] + "***"
    local, domain = addr.split("@", 1)
    return (local[:1] + "***@" + domain) if local else "***@" + domain


def clean_app_password(raw):
    """Google shows an app password as 'abcd efgh ijkl mnop'. Spaces (also non-breaking ones copied from a
    web page) and surrounding quotes are not part of it."""
    return "".join(str(raw or "").strip().strip("'\"").split())


def _log(stage, status, detail=""):
    level = logging.INFO if status in ("ok", "info") else logging.ERROR
    log.log(level, "[SMTP] stage=%s status=%s %s", stage, status, detail)


def config_problem(address=None, password=None):
    """-> (code, developer_text, user_text) or None when the configuration is usable."""
    address = EMAIL_ADDRESS if address is None else address
    password = clean_app_password(EMAIL_APP_PASSWORD if password is None else password)
    if not (address or "").strip():
        return ("CONFIG_NO_ADDRESS", "EMAIL_ADDRESS is empty. Put your Gmail address in .env and restart Flask.",
                "ইমেইল পাঠানোর সেটিং এখনো করা হয়নি। অনুগ্রহ করে Sir-কে জানান।")
    if not EMAIL_RE.match(address.strip()):
        return ("CONFIG_BAD_ADDRESS", "EMAIL_ADDRESS is not a valid email address.",
                "ইমেইল পাঠানোর সেটিং ভুল আছে। অনুগ্রহ করে Sir-কে জানান।")
    if not password:
        return ("CONFIG_NO_PASSWORD", "EMAIL_APP_PASSWORD is empty. Create a Google App Password and put it in .env.",
                "ইমেইল পাঠানোর সেটিং এখনো করা হয়নি। অনুগ্রহ করে Sir-কে জানান।")
    try:
        password.encode("ascii")
    except UnicodeEncodeError:
        return ("CONFIG_PASSWORD_NOT_ASCII",
                "EMAIL_APP_PASSWORD contains a non-English character (copy/paste problem). Retype the 16 letters.",
                "ইমেইল পাঠানোর সেটিং ভুল আছে। অনুগ্রহ করে Sir-কে জানান।")
    return None


def classify(exc):
    """Map an exception to (code, plain Bengali message for the user, hint for the developer)."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("AUTH_FAILED", "ইমেইল সার্ভারে লগইন করা যায়নি। অনুগ্রহ করে Sir-কে জানান।",
                "Gmail refused the login. Use a 16-letter Google App Password (not the normal password), "
                "2-Step Verification must be ON, and EMAIL_ADDRESS must be the same account.")
    if isinstance(exc, ssl.SSLCertVerificationError):
        return ("TLS_CERT_FAILED", "নিরাপদ সংযোগ তৈরি করা যায়নি। কিছুক্ষণ পরে আবার চেষ্টা করুন।",
                "TLS certificate check failed. Antivirus/VPN/proxy 'HTTPS scanning' or an old Windows "
                "certificate store is the usual cause.")
    if isinstance(exc, ssl.SSLError):
        return ("TLS_FAILED", "নিরাপদ সংযোগ তৈরি করা যায়নি। কিছুক্ষণ পরে আবার চেষ্টা করুন।",
                "TLS handshake failed (antivirus/VPN/proxy or wrong port).")
    if isinstance(exc, socket.gaierror):
        return ("DNS_FAILED", "ইন্টারনেট সংযোগ বা DNS-এ সমস্যা আছে। সংযোগ দেখে আবার চেষ্টা করুন।",
                "smtp.gmail.com could not be resolved: no internet or DNS problem.")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ("CONNECT_TIMEOUT", "ইমেইল সার্ভারের সঙ্গে সংযোগ হচ্ছে না। কিছুক্ষণ পরে আবার চেষ্টা করুন।",
                "Connection timed out: firewall/ISP/hosting blocks outbound SMTP (ports 587/465).")
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return ("CONNECT_FAILED", "ইমেইল সার্ভারের সঙ্গে সংযোগ হচ্ছে না। কিছুক্ষণ পরে আবার চেষ্টা করুন।",
                "Could not open the connection: firewall/antivirus/network blocks outbound SMTP.")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return ("RECIPIENT_REFUSED", "এই ইমেইল ঠিকানায় মেইল পাঠানো যায়নি। ঠিকানাটি আবার দেখুন।",
                "The server refused the recipient address.")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return ("SENDER_REFUSED", "ইমেইল পাঠানো যায়নি। অনুগ্রহ করে Sir-কে জানান।",
                "The server refused the sender address (EMAIL_ADDRESS must match the logged-in account).")
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return ("SERVER_DISCONNECTED", "ইমেইল সার্ভারের সঙ্গে সংযোগ কেটে গেছে। আবার চেষ্টা করুন।",
                "The server closed the connection (often a daily Gmail limit or a blocked network).")
    if isinstance(exc, smtplib.SMTPException):
        return ("SMTP_ERROR", "ইমেইল পাঠানো যায়নি। কিছুক্ষণ পরে আবার চেষ্টা করুন।", "SMTP error from the server.")
    return ("UNKNOWN", "ইমেইল পাঠানো যায়নি। কিছুক্ষণ পরে আবার চেষ্টা করুন।", "Unexpected error.")


_PRIORITY = ["AUTH_FAILED", "TLS_CERT_FAILED", "RECIPIENT_REFUSED", "SENDER_REFUSED", "TLS_FAILED", "CONNECT_TIMEOUT",
             "DNS_FAILED", "CONNECT_FAILED", "SERVER_DISCONNECTED", "SMTP_ERROR", "UNKNOWN"]


def _attempt(port, address, password, to_email, raw, host=SMTP_HOST):
    """One route. -> (ok, stage, exception). Every stage is logged."""
    stage = "connect"
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            server = smtplib.SMTP_SSL(host, 465, context=ctx, timeout=TIMEOUT)
            _log("connect", "ok", "port=465 ssl")
        else:
            server = smtplib.SMTP(host, 587, timeout=TIMEOUT)
            _log("connect", "ok", "port=587")
        with server:
            if port != 465:
                stage = "starttls"
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
                _log("starttls", "ok", "port=587")
            stage = "login"
            server.login(address, password)
            _log("login", "ok", "port=%d" % port)
            stage = "send"
            server.sendmail(address, [to_email], raw)
            _log("send", "ok", "port=%d to=%s" % (port, mask_email(to_email)))
        return True, "send", None
    except Exception as exc:  # noqa: BLE001 - classified, logged and returned
        code, _user, hint = classify(exc)
        _log(stage, "FAILED", "port=%d code=%s error=%s: %s | hint: %s" % (port, code, type(exc).__name__, exc, hint))
        return False, stage, exc


def send_message(to_email, subject, body):
    """Send one plain-text mail. -> SendResult. Never raises."""
    to_email = (to_email or "").strip()
    if not EMAIL_RE.match(to_email):  # also blocks header-injection (\r\n)
        return SendResult(False, "config", "INVALID_RECIPIENT", "invalid recipient",
                          "ইমেইল ঠিকানাটি সঠিক নয়।")
    problem = config_problem()
    if problem:
        code, dev, user = problem
        _log("config", "FAILED", "code=%s %s" % (code, dev))
        return SendResult(False, "config", code, dev, user)
    password = clean_app_password(EMAIL_APP_PASSWORD)
    _log("config", "ok", "sender=%s app_password_chars=%d" % (mask_email(EMAIL_ADDRESS), len(password)))
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = "%s <%s>" % (EMAIL_SENDER_NAME, EMAIL_ADDRESS)
    msg["To"] = to_email
    raw = msg.as_string()
    attempts = []
    for port in (587, 465):
        ok, stage, exc = _attempt(port, EMAIL_ADDRESS, password, to_email, raw)
        if ok:
            return SendResult(True, "send", None, None, None, attempts)
        attempts.append((port, stage, exc))
    best = min(((classify(e)[0], p, st, e) for p, st, e in attempts),
               key=lambda t: _PRIORITY.index(t[0]) if t[0] in _PRIORITY else 99)
    code, port, stage, exc = best
    _c, user_msg, hint = classify(exc)
    log.error("[EMAIL FAILED] to=%s stage=%s code=%s port=%d error=%s", mask_email(to_email), stage, code, port, exc)
    technical = "; ".join("port %d failed at %s: %s: %s" % (p, st, type(e).__name__, e) for p, st, e in attempts)
    return SendResult(False, stage, code, technical + " | " + hint, user_msg, attempts)


def _send_email(to_email, subject, body):
    """Compatibility wrapper: -> (ok, error_text)."""
    r = send_message(to_email, subject, body)
    return (True, None) if r.ok else (False, r.technical)


def _send_email_async(to_email, subject, body):
    """Send in a background thread so the response time is identical whether or
    not the email address belongs to a real account (no account enumeration)."""
    threading.Thread(target=_send_email, args=(to_email, subject, body), daemon=True).start()


# --------------------------------------------------------------------------- OTP mail
def _otp_texts(otp_code, purpose):
    if purpose == "password_reset":
        subject = f"Password Reset OTP - {SITE_NAME}"
        intro = (f"We received a request to reset the password of your {SITE_NAME} account.\n\n"
                 f"Your One-Time Password (OTP) is: {otp_code}")
        outro = ("If you did NOT request this, ignore this email — your password will not "
                 "change. Never share this code with anyone.")
    else:
        subject = f"Your Verification OTP - {SITE_NAME}"
        intro = f"Welcome to {SITE_NAME}!\n\nYour One-Time Password (OTP) is: {otp_code}"
        outro = "If you did not request this, ignore this email."
    body = f"""Hello,

{intro}

This code is valid for the next {OTP_EXPIRY_MINUTES} minutes and can be used only once.
Do not share it with anyone.

OTP is sent only to your email address (not to mobile).

{outro}

Best regards,
{EMAIL_SENDER_NAME}
"""
    return subject, body


def local_console_allowed():
    """Second safety net (the first is the startup check): inside a web request the host must be localhost."""
    try:
        from urllib.parse import urlsplit
        from flask import has_request_context, request
        if has_request_context():
            host = (urlsplit("//" + (request.host or "")).hostname or "").lower()   # handles "localhost:5000", "[::1]:5000"
            return host in {"localhost", "127.0.0.1", "::1"}
    except Exception:  # noqa: BLE001
        return False
    return True


def _print_local_otp(email, otp_code, purpose):
    """Terminal only (stderr, NOT the logging system, so it never reaches a log file or the browser)."""
    bar = "=" * 62
    print("\n%s\n  LOCAL OTP MODE (development only - no email was sent)\n  purpose : %s\n  email   : %s\n"
          "  OTP     : %s   (valid %d min, single use)\n%s\n"
          % (bar, purpose, email, otp_code, OTP_EXPIRY_MINUTES, bar), file=sys.stderr, flush=True)


def send_otp_result(to_email, otp_code, purpose="registration"):
    """-> SendResult for an OTP mail (or the local console delivery)."""
    if local_otp_console_enabled():
        if not local_console_allowed():
            log.error("[OTP] LOCAL_OTP_MODE=console refused: request host is not localhost")
            return SendResult(False, "config", "LOCAL_MODE_NOT_LOCAL", "console OTP refused: not a localhost request",
                              "Local OTP mode শুধু localhost-এ চলে। http://127.0.0.1:5000 দিয়ে খুলুন।")
        _print_local_otp(to_email, otp_code, purpose)
        log.info("[OTP] local console delivery for %s (code not logged)", mask_email(to_email))
        return SendResult(True, "local_console")
    subject, body = _otp_texts(otp_code, purpose)
    return send_message(to_email, subject, body)


def send_otp_email(to_email, otp_code, purpose="registration"):
    """Compatibility wrapper: -> (ok, error_text)."""
    r = send_otp_result(to_email, otp_code, purpose)
    return (True, None) if r.ok else (False, r.technical)
