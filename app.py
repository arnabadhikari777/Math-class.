# -*- coding: utf-8 -*-
"""
Arnab Math Class — Online Notes + Timed MCQ Test Platform
Features: student OTP (email only), mandatory mobile (display only),
teacher admin (admit/delete/impersonate), contact requests, search,
announcements, leaderboards, answer review, dark mode, PWA, activity log.
"""

import os
import hmac
import time
import secrets
import hashlib
import threading
import logging
from pathlib import Path

# Load .env file if present (for local development)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on real environment variables

import re
import sqlite3
import random
import string
import smtplib
import ssl
import csv
import io
import json
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_from_directory, abort, jsonify, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

# Web Push (real phone/browser notifications for students who allow it)
try:
    from pywebpush import webpush, WebPushException
    WEBPUSH_OK = True
except ImportError:
    WEBPUSH_OK = False

# PDF generation (Bengali-capable via FreeSerif)
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "notes")
STATIC_FOLDER = os.path.join(BASE_DIR, "static")
ALLOWED_EXTENSIONS = {"pdf"}
MAX_CONTENT_LENGTH = 15 * 1024 * 1024

TEACHER_MOBILE = os.environ.get("TEACHER_MOBILE", "")

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
EMAIL_SENDER_NAME = os.environ.get("EMAIL_SENDER_NAME", "Arnab Math Class")

OTP_EXPIRY_MINUTES = 10
OTP_MAX_ATTEMPTS = 5            # wrong guesses allowed per OTP before it is burned
RESET_EMAIL_PER_HOUR = 5        # forgot-password OTP requests per email per hour
RESET_COOLDOWN_SECONDS = 60     # minimum gap between two OTP requests for one email
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PASS_PERCENTAGE = 40
SITE_NAME = "Arnab Math Class"

# --- Web Push (VAPID) config ---
# These keys let the server send real push notifications to a student's
# phone/browser (even when the site/app is closed), for anyone who taps
# "Allow" on the browser's notification permission prompt.
# A default key pair is included so push works out of the box; for
# production it's best to set your own via environment variables
# (generate once and keep them the same forever — changing them makes
# every previously-subscribed browser resubscribe).
VAPID_PUBLIC_KEY = os.environ.get(
    "VAPID_PUBLIC_KEY",
    ""
)
VAPID_PRIVATE_KEY = os.environ.get(
    "VAPID_PRIVATE_KEY",
    ""
)
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", EMAIL_ADDRESS)

app = Flask(__name__)


def _load_secret_key():
    """Use SECRET_KEY from env; otherwise create + reuse a random key file
    (never fall back to a publicly known default)."""
    key = os.environ.get("SECRET_KEY", "").strip()
    if key and key != "CHANGE_THIS_TO_A_RANDOM_SECRET_KEY_1234567890":
        return key
    path = os.path.join(BASE_DIR, ".secret_key")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                saved = fh.read().strip()
            if saved:
                return saved
        new_key = secrets.token_hex(32)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return new_key
    except OSError:
        return secrets.token_hex(32)


app.secret_key = _load_secret_key()
DEBUG_MODE = os.environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
# Redirect http -> https (localhost is always exempt). Set FORCE_HTTPS=0 to disable.
FORCE_HTTPS = os.environ.get("FORCE_HTTPS", "1").strip().lower() not in ("0", "false", "no")
# Trust the proxy's X-Forwarded-For / X-Forwarded-Proto (PythonAnywhere etc.)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config["SESSION_COOKIE_SECURE"] = FORCE_HTTPS and not DEBUG_MODE
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
# Keep users logged in across browser restarts / PWA launches (30 days)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn, table, column, col_def):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                class_name TEXT NOT NULL,
                roll TEXT,
                email TEXT UNIQUE NOT NULL,
                mobile TEXT,
                password_hash TEXT NOT NULL,
                is_verified INTEGER DEFAULT 0,
                registered_at TEXT NOT NULL,
                admitted_by_teacher INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                notes_internal TEXT
            )
        """)
        _ensure_column(conn, "students", "mobile", "TEXT")
        _ensure_column(conn, "students", "admitted_by_teacher", "INTEGER DEFAULT 0")
        _ensure_column(conn, "students", "is_blocked", "INTEGER DEFAULT 0")
        _ensure_column(conn, "students", "notes_internal", "TEXT")

        c.execute("""
            CREATE TABLE IF NOT EXISTS otp_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                otp_code TEXT NOT NULL,
                purpose TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_used INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS login_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                login_time TEXT NOT NULL,
                ip_address TEXT,
                via_teacher INTEGER DEFAULT 0,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)
        _ensure_column(conn, "login_history", "via_teacher", "INTEGER DEFAULT 0")

        c.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                subject TEXT,
                class_name TEXT,
                filename TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                subject TEXT,
                class_name TEXT,
                duration_minutes INTEGER NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                option_a TEXT NOT NULL,
                option_b TEXT NOT NULL,
                option_c TEXT NOT NULL,
                option_d TEXT NOT NULL,
                correct_option TEXT NOT NULL,
                marks INTEGER DEFAULT 1,
                FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                score INTEGER NOT NULL,
                total_marks INTEGER NOT NULL,
                submitted_at TEXT NOT NULL,
                FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                selected_option TEXT,
                is_correct INTEGER DEFAULT 0,
                FOREIGN KEY (result_id) REFERENCES results(id) ON DELETE CASCADE,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                class_name TEXT,
                created_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS contact_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                mobile TEXT,
                subject TEXT,
                message TEXT NOT NULL,
                student_id INTEGER,
                status TEXT DEFAULT 'new',
                created_at TEXT NOT NULL,
                teacher_reply TEXT,
                replied_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS site_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT DEFAULT 'present',
                note TEXT,
                marked_at TEXT NOT NULL,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                notif_type TEXT DEFAULT 'info',
                class_name TEXT DEFAULT '',
                link TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS notification_reads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                read_at TEXT NOT NULL,
                UNIQUE(notification_id, student_id),
                FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)
        # Real push subscriptions: one row per browser/device that tapped
        # "Allow" on notifications. A student can have several (phone +
        # laptop, etc). class_name is copied in at subscribe time so we can
        # target a push by class without a join.
        c.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                class_name TEXT DEFAULT '',
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket TEXT NOT NULL,
                ts REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_rate_bucket ON rate_limit_hits(bucket, ts)")

        # Password-reset support:
        #  - session_version: bumping it logs the student out of every device
        #  - attempts: counts wrong guesses on an OTP (brute-force protection)
        _ensure_column(conn, "students", "session_version", "INTEGER DEFAULT 0")
        _ensure_column(conn, "otp_codes", "attempts", "INTEGER DEFAULT 0")

        conn.commit()


def log_activity(actor, action, detail=""):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO activity_log (actor, action, detail, created_at) VALUES (?,?,?,?)",
                (actor, action, detail, datetime.now().isoformat())
            )
            conn.commit()
    except Exception:
        pass


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_otp():
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _send_email(to_email, subject, body):
    """Send a plain-text email via Gmail SMTP (587 STARTTLS, then 465 SSL).
    Returns (ok, error_text). Never prints OTPs or passwords to the logs."""
    to_email = (to_email or "").strip()
    if not EMAIL_RE.match(to_email):  # also blocks header-injection (\r\n)
        return False, "invalid recipient"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{EMAIL_SENDER_NAME} <{EMAIL_ADDRESS}>"
    msg["To"] = to_email
    raw = msg.as_string()
    last_err = None

    # Method 1: SMTP + STARTTLS (port 587)
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, [to_email], raw)
        return True, None
    except Exception as e:
        last_err = e

    # Method 2: SMTP_SSL (port 465)
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=20) as server:
            server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, [to_email], raw)
        return True, None
    except Exception as e:
        last_err = e

    logging.getLogger(__name__).error("[EMAIL FAILED] to=%s error=%s", to_email, last_err)
    return False, str(last_err)


def _send_email_async(to_email, subject, body):
    """Send in a background thread so the response time is identical whether or
    not the email address belongs to a real account (no account enumeration)."""
    threading.Thread(target=_send_email, args=(to_email, subject, body), daemon=True).start()


def send_otp_email(to_email, otp_code, purpose="registration"):
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
    return _send_email(to_email, subject, body)


def _otp_hash(email, purpose, code):
    """OTPs are stored as an HMAC, never in plain text."""
    key = app.secret_key.encode("utf-8") if isinstance(app.secret_key, str) else app.secret_key
    return hmac.new(key, f"{email}|{purpose}|{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def create_and_send_otp(email, purpose="registration", background=False, cooldown=True):
    """
    Create OTP, invalidate older unused codes for same email+purpose,
    enforce a short resend cooldown, then send email.
    Returns (ok: bool, message: str, otp_code: str|None)
    """
    email = (email or "").strip().lower()
    if not email:
        return False, "Email is required.", None

    now = datetime.now()
    with get_db() as conn:
        # Cooldown: block resend within 60 seconds
        if cooldown:
            recent = conn.execute(
                """SELECT created_at FROM otp_codes
                   WHERE email=? AND purpose=? ORDER BY id DESC LIMIT 1""",
                (email, purpose)
            ).fetchone()
            if recent:
                try:
                    created = datetime.fromisoformat(recent["created_at"])
                    if (now - created).total_seconds() < RESET_COOLDOWN_SECONDS:
                        wait = RESET_COOLDOWN_SECONDS - int((now - created).total_seconds())
                        return False, f"Please wait {wait} seconds before requesting another OTP.", None
                except Exception:
                    pass

        # Invalidate previous unused OTPs for this email+purpose
        conn.execute(
            "UPDATE otp_codes SET is_used=1 WHERE email=? AND purpose=? AND is_used=0",
            (email, purpose)
        )

        otp_code = generate_otp()
        expires = now + timedelta(minutes=OTP_EXPIRY_MINUTES)
        conn.execute(
            """INSERT INTO otp_codes (email, otp_code, purpose, created_at, expires_at, is_used, attempts)
               VALUES (?,?,?,?,?,0,0)""",
            (email, _otp_hash(email, purpose, otp_code), purpose, now.isoformat(), expires.isoformat())
        )
        conn.commit()

    if background:
        # Fire-and-forget (used by forgot-password so timing never leaks anything)
        threading.Thread(target=send_otp_email, args=(email, otp_code, purpose), daemon=True).start()
        return True, "OTP sent.", None

    ok, err = send_otp_email(email, otp_code, purpose)
    if ok:
        return True, "OTP sent to your email. Check inbox and spam folder.", otp_code
    # Email failed — keep the technical reason in the server log only (not shown to the user)
    return False, (
        "Could not send email right now (Gmail limit or configuration). "
        "Please try again in a few minutes, or ask Sir to verify your account."
    ), otp_code


def verify_otp_code(email, entered_code, purpose="registration", generic=False):
    """Check an OTP. Single use, expires, and is burned after OTP_MAX_ATTEMPTS
    wrong guesses. With generic=True every failure returns the same message
    (so nobody can tell whether an account/OTP exists)."""
    email = (email or "").strip().lower()
    entered = (entered_code or "").strip()
    generic_msg = "Invalid or expired OTP. Please request a new one."

    def fail(msg):
        return False, (generic_msg if generic else msg)

    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM otp_codes WHERE email=? AND purpose=? AND is_used=0
               ORDER BY id DESC LIMIT 1""",
            (email, purpose)
        ).fetchone()
        if row is None:
            return fail("No OTP found. Please request a new one.")
        try:
            expired = datetime.now() > datetime.fromisoformat(row["expires_at"])
        except Exception:
            expired = True
        if expired:
            conn.execute("UPDATE otp_codes SET is_used=1 WHERE id=?", (row["id"],))
            conn.commit()
            return fail("OTP expired. Request a new code.")
        attempts = row["attempts"] or 0
        if attempts >= OTP_MAX_ATTEMPTS:
            conn.execute("UPDATE otp_codes SET is_used=1 WHERE id=?", (row["id"],))
            conn.commit()
            return fail("Too many wrong attempts. Request a new code.")

        stored = row["otp_code"] or ""
        match = hmac.compare_digest(stored, _otp_hash(email, purpose, entered))
        if not match and len(stored) == 6 and stored.isdigit():  # OTPs created before hashing
            match = hmac.compare_digest(stored, entered)
        if not match:
            attempts += 1
            conn.execute(
                "UPDATE otp_codes SET attempts=?, is_used=? WHERE id=?",
                (attempts, 1 if attempts >= OTP_MAX_ATTEMPTS else 0, row["id"])
            )
            conn.commit()
            return fail("Incorrect OTP.")

        # Mark used atomically so the same OTP can never succeed twice
        cur = conn.execute("UPDATE otp_codes SET is_used=1 WHERE id=? AND is_used=0", (row["id"],))
        conn.commit()
        if cur.rowcount != 1:
            return fail("No OTP found. Please request a new one.")
    return True, "Verification successful."


def send_push_to_class(title, message, link="", class_name=""):
    """
    Actually push a phone/browser notification to every student who has
    allowed notifications (subscribed), optionally filtered to one class.
    Runs quietly in the background of a request — a failed/expired
    subscription is deleted so the list stays clean, but never blocks or
    breaks the calling request.
    """
    if not WEBPUSH_OK:
        print("[push] pywebpush not installed — run: pip install pywebpush")
        return
    try:
        with get_db() as conn:
            if class_name:
                rows = conn.execute(
                    "SELECT * FROM push_subscriptions WHERE class_name=? OR class_name=''",
                    (class_name,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()

        payload = json.dumps({
            "title": title,
            "body": message,
            "url": link or "/",
        })

        dead_endpoints = []
        for row in rows:
            subscription_info = {
                "endpoint": row["endpoint"],
                "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
            }
            try:
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": f"mailto:{VAPID_CLAIMS_EMAIL}"},
                )
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                # 404/410 = the browser unsubscribed or the subscription expired
                if status in (404, 410):
                    dead_endpoints.append(row["endpoint"])
                else:
                    print(f"[push] failed for one subscriber: {e}")
            except Exception as e:
                print(f"[push] unexpected error for one subscriber: {e}")

        if dead_endpoints:
            with get_db() as conn:
                conn.executemany(
                    "DELETE FROM push_subscriptions WHERE endpoint=?",
                    [(ep,) for ep in dead_endpoints]
                )
                conn.commit()
    except Exception as e:
        print(f"[push] send_push_to_class error: {e}")


def create_notification(title, message, notif_type="info", class_name="", link=""):
    """Insert an in-app notification for students (optionally filtered by class)."""
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO notifications (title, message, notif_type, class_name, link, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (title, message, notif_type, class_name or "", link or "", datetime.now().isoformat())
            )
            conn.commit()
    except Exception as e:
        print(f"[notification] {e}")
    # Also push a real phone/browser notification to whoever has allowed it.
    send_push_to_class(title, message, link=link, class_name=class_name)


def percentage(score, total):
    if total <= 0:
        return 0
    return round((score / total) * 100, 1)


def _normalize_option_letter(raw):
    """Map ক/খ/গ/ঘ or A/B/C/D (any case) to A/B/C/D."""
    if not raw:
        return None
    s = raw.strip().upper()
    bangla_map = {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D", "A": "A", "B": "B", "C": "C", "D": "D"}
    if s in bangla_map:
        return bangla_map[s]
    first = s[0] if s else ""
    bangla_first = {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}
    if first in bangla_first:
        return bangla_first[first]
    if first in "ABCD":
        return first
    return None


def parse_bulk_questions(text):
    """
    Parse bulk question text. Supports Bengali and English markers.
    Returns list of dicts: question_text, option_a..d, correct_option, marks
    """
    if not text or not text.strip():
        return []

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    current = []
    q_start_re = re.compile(r"^\s*(প্রশ্ন|প্রশ্নঃ|Q|Question)\s*[:：.\)]\s*", re.IGNORECASE)

    for line in lines:
        if q_start_re.match(line) and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    results = []
    opt_re = re.compile(
        r"^\s*([ABCDকখগঘ])\s*[).\]:：]\s*(.+)$",
        re.IGNORECASE
    )
    ans_re = re.compile(
        r"^\s*(সঠিক|উত্তর|Answer|Ans|Correct)\s*[:：.\)]\s*(.+)$",
        re.IGNORECASE
    )
    marks_re = re.compile(
        r"^\s*(নম্বর|মার্কস|Marks|Mark)\s*[:：.\)]\s*(\d+)\s*$",
        re.IGNORECASE
    )
    q_line_re = re.compile(
        r"^\s*(প্রশ্ন|প্রশ্নঃ|Q|Question)\s*[:：.\)]\s*(.*)$",
        re.IGNORECASE
    )

    for block in blocks:
        q_text = ""
        options = {}
        correct = None
        marks = 1
        for line in block.split("\n"):
            line_s = line.strip()
            if not line_s:
                continue
            m_q = q_line_re.match(line_s)
            if m_q:
                q_text = m_q.group(2).strip()
                continue
            m_opt = opt_re.match(line_s)
            if m_opt:
                letter = _normalize_option_letter(m_opt.group(1))
                if letter:
                    options[letter] = m_opt.group(2).strip()
                continue
            m_ans = ans_re.match(line_s)
            if m_ans:
                correct = _normalize_option_letter(m_ans.group(2))
                continue
            m_mk = marks_re.match(line_s)
            if m_mk:
                try:
                    marks = max(1, int(m_mk.group(2)))
                except ValueError:
                    marks = 1
                continue
            if q_text and not options:
                q_text += " " + line_s

        if q_text and all(k in options for k in ("A", "B", "C", "D")) and correct in ("A", "B", "C", "D"):
            results.append({
                "question_text": q_text,
                "option_a": options["A"],
                "option_b": options["B"],
                "option_c": options["C"],
                "option_d": options["D"],
                "correct_option": correct,
                "marks": marks,
            })
    return results


def _register_pdf_fonts():
    """Register Unicode fonts for PDF (Bengali support via FreeSerif)."""
    if not REPORTLAB_OK:
        return None, None
    font_paths = [
        "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
    ]
    regular = None
    bold = None
    for p in font_paths:
        if os.path.isfile(p) and "Bold" not in os.path.basename(p) and regular is None:
            try:
                pdfmetrics.registerFont(TTFont("AppFont", p))
                regular = "AppFont"
            except Exception:
                pass
        if os.path.isfile(p) and "Bold" in os.path.basename(p) and bold is None:
            try:
                pdfmetrics.registerFont(TTFont("AppFontBold", p))
                bold = "AppFontBold"
            except Exception:
                pass
    if regular and not bold:
        bold = regular
    return regular, bold


def build_student_result_pdf(test, student, result, answers):
    """Generate PDF bytes of student's answer sheet (Bengali-safe)."""
    if not REPORTLAB_OK:
        return None
    font, font_bold = _register_pdf_fonts()
    if not font:
        font = font_bold = "Helvetica"

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left = 18 * mm
    right = width - 18 * mm
    y = height - 18 * mm
    line_h = 6.5 * mm
    max_w = right - left

    def new_page_if_needed(need=20 * mm):
        nonlocal y
        if y < need:
            c.showPage()
            y = height - 18 * mm
            return True
        return False

    def draw_wrapped(text, x, fontsize=10, bold=False, color=(0, 0, 0)):
        nonlocal y
        f = font_bold if bold else font
        c.setFont(f, fontsize)
        c.setFillColorRGB(*color)
        words = (text or "").split()
        if not words:
            return
        lines = []
        cur = words[0]
        for w in words[1:]:
            trial = cur + " " + w
            if c.stringWidth(trial, f, fontsize) <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        for ln in lines:
            new_page_if_needed(12 * mm)
            c.drawString(x, y, ln)
            y -= line_h * 0.85

    c.setFont(font_bold, 14)
    c.setFillColorRGB(0.12, 0.23, 0.54)
    c.drawString(left, y, "Arnab Math Class — Student Answer Sheet")
    y -= line_h * 1.2
    c.setStrokeColorRGB(0.12, 0.23, 0.54)
    c.setLineWidth(1.5)
    c.line(left, y, right, y)
    y -= line_h * 1.1

    pct = percentage(result["score"], result["total_marks"])
    meta = [
        f"Test: {test['title']}",
        f"Student: {student['name']}  |  Class: {student['class_name']}  |  Roll: {student['roll'] or '-'}",
        f"Score: {result['score']} / {result['total_marks']}  ({pct}%)",
        f"Submitted: {str(result['submitted_at'])[:16].replace('T', ' ')}",
    ]
    for m in meta:
        draw_wrapped(m, left, fontsize=10, bold=True)
        y -= 1 * mm

    y -= 3 * mm
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.line(left, y, right, y)
    y -= line_h

    for i, a in enumerate(answers, 1):
        new_page_if_needed(40 * mm)
        status = "Correct" if a["is_correct"] else "Incorrect"
        color = (0.02, 0.59, 0.41) if a["is_correct"] else (0.86, 0.15, 0.15)
        draw_wrapped(
            f"Q{i}. {a['question_text']}  [{a['marks']} mark(s)]  {status}",
            left, fontsize=10, bold=True, color=color
        )
        y -= 1 * mm
        for letter, key in [("A", "option_a"), ("B", "option_b"), ("C", "option_c"), ("D", "option_d")]:
            prefix = ""
            col = (0.1, 0.1, 0.1)
            if letter == a["correct_option"]:
                prefix = " [Correct]"
                col = (0.02, 0.5, 0.35)
            if letter == a["selected_option"]:
                prefix += " <- Student chose"
                if not a["is_correct"]:
                    col = (0.75, 0.1, 0.1)
            draw_wrapped(f"  {letter}) {a[key]}{prefix}", left, fontsize=9, color=col)
        if not a["selected_option"]:
            draw_wrapped("  (No answer selected)", left, fontsize=9, color=(0.7, 0.1, 0.1))
        y -= 3 * mm

    y -= 4 * mm
    new_page_if_needed(15 * mm)
    c.setFont(font, 8)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(left, y, "Generated by Arnab Math Class")
    c.save()
    buf.seek(0)
    return buf.read()


def normalize_mobile(m):

    if not m:
        return ""
    digits = "".join(c for c in m if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits



# ---------------------------------------------------------------------------
# Security helpers: CSRF, rate limiting, password rules, teacher password hash
# ---------------------------------------------------------------------------
def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = get_csrf_token


def _is_local_host():
    host = (request.host or "").split(":")[0].lower()
    return host in ("localhost", "127.0.0.1", "[::1]", "::1")


@app.before_request
def enforce_https():
    if FORCE_HTTPS and not DEBUG_MODE and not request.is_secure and not _is_local_host():
        return redirect(request.url.replace("http://", "https://", 1), code=301)


@app.before_request
def csrf_protect():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.endpoint == "static":
        return None
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or ""
    expected = session.get("_csrf_token") or ""
    if expected and sent and hmac.compare_digest(sent, expected):
        return None
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"ok": False, "error": "CSRF token missing or invalid."}), 400
    flash("Your session expired or the form was invalid. Please try again.", "error")
    return redirect(request.referrer or url_for("index"))


@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if FORCE_HTTPS and request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def _bucket_ok(bucket, limit, window_seconds):
    """SQLite-backed sliding window for an arbitrary bucket key
    (works across multiple workers). Records a hit when allowed."""
    now = time.time()
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM rate_limit_hits WHERE ts < ?", (now - 86400,))
            used = conn.execute(
                "SELECT COUNT(*) FROM rate_limit_hits WHERE bucket=? AND ts>?",
                (bucket, now - window_seconds)
            ).fetchone()[0]
            if used >= limit:
                conn.commit()
                return False
            conn.execute("INSERT INTO rate_limit_hits (bucket, ts) VALUES (?,?)", (bucket, now))
            conn.commit()
    except Exception:
        return True  # never lock everyone out because of a DB hiccup
    return True


def _rate_ok(name, limit, window_seconds):
    """Per-IP rate limit."""
    return _bucket_ok(f"{name}:{request.remote_addr or 'unknown'}", limit, window_seconds)


def rate_limit(name, limit, window_seconds, methods=("POST",)):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if request.method in methods and not _rate_ok(name, limit, window_seconds):
                minutes = max(1, window_seconds // 60)
                flash(f"Too many attempts. Please wait about {minutes} minute(s) and try again.", "error")
                return redirect(url_for("index") if request.method != "POST" else request.path)
            return f(*args, **kwargs)
        return wrapper
    return deco


def validate_password_strength(password):
    """Returns an error message, or None when the password is acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 128:
        return "Password is too long (max 128 characters)."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain both letters and numbers."
    return None


def _get_setting(key):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM site_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO site_settings (key, value) VALUES (?,?)", (key, value))
        conn.commit()


def _teacher_creds():
    """Teacher username/password come straight from .env and are re-read on every
    login attempt, so editing .env takes effect immediately (no restart, nothing
    stored in the database). Falls back to real environment variables."""
    user, pw = os.environ.get("TEACHER_USERNAME", ""), os.environ.get("TEACHER_PASSWORD", "")
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(Path(BASE_DIR) / ".env")
        user = vals.get("TEACHER_USERNAME") or user
        pw = vals.get("TEACHER_PASSWORD") or pw
    except ImportError:
        env_path = Path(BASE_DIR) / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                k, sep, v = line.partition("=")
                if sep and k.strip() == "TEACHER_USERNAME":
                    user = v.strip().strip("\"'") or user
                elif sep and k.strip() == "TEACHER_PASSWORD":
                    pw = v.strip().strip("\"'") or pw
    except Exception:
        pass
    return (user or "").strip(), pw or ""


def check_teacher_credentials(username, password):
    """Constant-time comparison against .env. Empty config never matches."""
    want_user, want_pw = _teacher_creds()
    if not want_user or not want_pw:
        return False
    user_ok = hmac.compare_digest(username.strip().encode("utf-8"), want_user.encode("utf-8"))
    pw_ok = hmac.compare_digest(password.encode("utf-8"), want_pw.encode("utf-8"))
    return user_ok and pw_ok


@app.before_request
def validate_student_session():
    """Ends a student session when the password was reset (session_version
    changed) or the account got blocked/deleted — this is what makes
    'logout from all devices' work with cookie-based sessions."""
    sid = session.get("student_id")
    if not sid or request.endpoint in (None, "static", "manifest", "service_worker"):
        return None
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT session_version, is_blocked FROM students WHERE id=?", (sid,)
            ).fetchone()
    except Exception:
        return None
    if row is not None and not row["is_blocked"] and (row["session_version"] or 0) == session.get("sv", 0):
        return None
    for k in ("student_id", "student_name", "student_class", "sv", "impersonating"):
        session.pop(k, None)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Session ended. Please log in again."}), 401
    flash("Your session has ended. Please log in again.", "error")
    return redirect(url_for("login"))


def student_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("student_id"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def teacher_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_teacher"):
            flash("Teacher login is required.", "error")
            return redirect(url_for("teacher_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    # Persistent login: send returning users straight to their dashboard
    if session.get("student_id") and not session.get("is_teacher"):
        return redirect(url_for("student_dashboard"))
    if session.get("is_teacher") and not session.get("student_id"):
        return redirect(url_for("teacher_dashboard"))
    if session.get("is_teacher") and session.get("student_id"):
        return redirect(url_for("student_dashboard"))
    with get_db() as conn:
        announcements = conn.execute(
            "SELECT * FROM announcements WHERE is_active=1 ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        total_students = conn.execute(
            "SELECT COUNT(*) AS c FROM students WHERE is_verified=1"
        ).fetchone()["c"]
        total_tests = conn.execute(
            "SELECT COUNT(*) AS c FROM tests WHERE is_active=1"
        ).fetchone()["c"]
        total_notes = conn.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]
    return render_template(
        "index.html", announcements=announcements, site_name=SITE_NAME,
        total_students=total_students, total_tests=total_tests, total_notes=total_notes
    )


@app.route("/download")
def download_app():
    return render_template("download.html", site_name=SITE_NAME)


@app.route("/contact", methods=["GET", "POST"])
@rate_limit("contact", 5, 3600)
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        mobile = normalize_mobile(request.form.get("mobile", ""))
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if not name or not message:
            flash("Name and message are required.", "error")
            return redirect(url_for("contact"))
        sid = session.get("student_id")
        with get_db() as conn:
            conn.execute(
                """INSERT INTO contact_requests
                   (name, email, mobile, subject, message, student_id, status, created_at)
                   VALUES (?,?,?,?,?,?,'new',?)""",
                (name, email, mobile, subject, message, sid, datetime.now().isoformat())
            )
            conn.commit()
        log_activity("public", "contact_request", f"from {name}")
        flash("Your message has been sent to Sir. He will get back to you.", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html", site_name=SITE_NAME)


@app.route("/register", methods=["GET", "POST"])
@rate_limit("register", 10, 3600)
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        class_name = request.form.get("class_name", "").strip()
        roll = request.form.get("roll", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = normalize_mobile(request.form.get("mobile", ""))
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not name or not class_name or not email or not password:
            flash("Please fill all required fields.", "error")
            return redirect(url_for("register"))
        if not EMAIL_RE.match(email):
            flash("Please enter a valid email address.", "error")
            return redirect(url_for("register"))
        if not mobile or len(mobile) < 10:
            flash("A valid 10-digit mobile number is mandatory.", "error")
            return redirect(url_for("register"))
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))
        pw_err = validate_password_strength(password)
        if pw_err:
            flash(pw_err, "error")
            return redirect(url_for("register"))
        with get_db() as conn:
            existing = conn.execute("SELECT * FROM students WHERE email=?", (email,)).fetchone()
            if existing and existing["is_verified"] == 1:
                flash("An account with this email already exists. Please log in.", "error")
                return redirect(url_for("login"))
            password_hash = generate_password_hash(password)
            if existing and existing["is_verified"] == 0:
                conn.execute(
                    """UPDATE students SET name=?, class_name=?, roll=?, mobile=?, password_hash=?
                       WHERE email=?""",
                    (name, class_name, roll, mobile, password_hash, email)
                )
            else:
                conn.execute(
                    """INSERT INTO students
                       (name, class_name, roll, email, mobile, password_hash, is_verified, registered_at)
                       VALUES (?,?,?,?,?,?,0,?)""",
                    (name, class_name, roll, email, mobile, password_hash, datetime.now().isoformat())
                )
            conn.commit()
        session["pending_email"] = email
        ok, msg, _ = create_and_send_otp(email, purpose="registration")
        flash(msg, "success" if ok else "error")
        return redirect(url_for("verify_otp"))
    return render_template("register.html")


@app.route("/verify-otp", methods=["GET", "POST"])
@rate_limit("verify_otp", 10, 600)
def verify_otp():
    email = session.get("pending_email")
    if not email:
        flash("Please complete registration first.", "error")
        return redirect(url_for("register"))
    if request.method == "POST":
        entered_code = request.form.get("otp_code", "")
        ok, msg = verify_otp_code(email, entered_code, purpose="registration")
        if ok:
            with get_db() as conn:
                conn.execute("UPDATE students SET is_verified=1 WHERE email=?", (email,))
                conn.commit()
            session.pop("pending_email", None)
            flash("Account verified! You can now log in.", "success")
            return redirect(url_for("login"))
        flash(msg, "error")
        return redirect(url_for("verify_otp"))
    return render_template("verify_otp.html", email=email)


@app.route("/resend-otp")
@rate_limit("resend_otp", 5, 3600, methods=("GET",))
def resend_otp():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("register"))
    ok, msg, _ = create_and_send_otp(email, purpose="registration")
    flash(msg, "success" if ok else "error")
    return redirect(url_for("verify_otp"))


# ---------------------------------------------------------------------------
# Forgot password (email OTP only)
# ---------------------------------------------------------------------------
RESET_GENERIC_MSG = ("If an account exists for this email, an OTP has been sent. "
                     "Check your inbox and spam folder.")


def _request_password_reset(email):
    """Rate-limit per email (counted even for unknown emails, so limits and
    messages look identical for everyone), then send an OTP only when a
    verified, unblocked account exists. Returns (allowed, message)."""
    h = hashlib.sha256(email.encode("utf-8")).hexdigest()[:32]
    if not _bucket_ok(f"forgot_cd:{h}", 1, RESET_COOLDOWN_SECONDS):
        return False, f"Please wait {RESET_COOLDOWN_SECONDS} seconds before requesting another OTP."
    if not _bucket_ok(f"forgot_hr:{h}", RESET_EMAIL_PER_HOUR, 3600):
        return False, "Too many OTP requests for this email. Please try again after an hour."
    with get_db() as conn:
        student = conn.execute(
            "SELECT is_verified, is_blocked FROM students WHERE email=?", (email,)
        ).fetchone()
    if student and student["is_verified"] == 1 and not student["is_blocked"]:
        create_and_send_otp(email, purpose="password_reset", background=True, cooldown=False)
    return True, RESET_GENERIC_MSG


@app.route("/forgot-password", methods=["GET", "POST"])
@rate_limit("forgot_ip", 10, 3600)
def forgot_password():
    if session.get("student_id") and not session.get("is_teacher"):
        return redirect(url_for("student_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not EMAIL_RE.match(email):
            flash("Please enter a valid email address.", "error")
            return redirect(url_for("forgot_password"))
        ok, msg = _request_password_reset(email)
        flash(msg, "success" if ok else "error")
        session["reset_email"] = email
        return redirect(url_for("reset_password"))
    return render_template("forgot_password.html", email=session.get("reset_email", ""))


@app.route("/forgot-password/resend", methods=["POST"])
@rate_limit("forgot_ip", 10, 3600)
def forgot_password_resend():
    email = (session.get("reset_email") or request.form.get("email", "")).strip().lower()
    if not EMAIL_RE.match(email):
        return redirect(url_for("forgot_password"))
    ok, msg = _request_password_reset(email)
    flash(msg, "success" if ok else "error")
    session["reset_email"] = email
    return redirect(url_for("reset_password"))


@app.route("/reset-password", methods=["GET", "POST"])
@rate_limit("reset_pw", 15, 600)
def reset_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        otp = request.form.get("otp_code", "").strip()
        new_pw = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not EMAIL_RE.match(email) or not otp or not new_pw:
            flash("Please fill all fields.", "error")
            return redirect(url_for("reset_password"))
        # Check the password first so a typo never burns a valid OTP
        if new_pw != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("reset_password"))
        pw_err = validate_password_strength(new_pw)
        if pw_err:
            flash(pw_err, "error")
            return redirect(url_for("reset_password"))

        ok, msg = verify_otp_code(email, otp, purpose="password_reset", generic=True)
        if not ok:
            flash(msg, "error")
            return redirect(url_for("reset_password"))

        with get_db() as conn:
            student = conn.execute(
                "SELECT id, name FROM students WHERE email=? AND is_verified=1 AND is_blocked=0",
                (email,)
            ).fetchone()
            if student is None:
                flash("Invalid or expired OTP. Please request a new one.", "error")
                return redirect(url_for("reset_password"))
            conn.execute(
                """UPDATE students SET password_hash=?,
                       session_version=COALESCE(session_version,0)+1
                   WHERE id=?""",
                (generate_password_hash(new_pw), student["id"])
            )
            conn.execute(
                "UPDATE otp_codes SET is_used=1 WHERE email=? AND purpose='password_reset'",
                (email,)
            )
            conn.commit()
        session.pop("reset_email", None)
        log_activity("student", "password_reset", f"{student['name']} (id={student['id']})")
        _send_email_async(
            email, f"Your password was changed - {SITE_NAME}",
            f"Hello {student['name']},\n\nThe password of your {SITE_NAME} account was just "
            f"changed using an email OTP, and you were logged out from all devices.\n\n"
            f"If this was not you, contact Sir immediately.\n\nBest regards,\n{EMAIL_SENDER_NAME}\n"
        )
        flash("Password changed successfully. You were logged out from all devices — "
              "please log in with your new password.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html", email=session.get("reset_email", ""))


@app.route("/login", methods=["GET", "POST"])
@rate_limit("login", 10, 600)
def login():
    # Already logged in → go to dashboard (persistent session)
    if session.get("student_id") and not session.get("is_teacher"):
        return redirect(url_for("student_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as conn:
            student = conn.execute("SELECT * FROM students WHERE email=?", (email,)).fetchone()
        if student is None or not check_password_hash(student["password_hash"], password):
            flash("Incorrect email or password.", "error")
            return redirect(url_for("login"))
        if student["is_blocked"]:
            flash("Your account has been blocked. Contact Sir.", "error")
            return redirect(url_for("login"))
        if student["is_verified"] == 0:
            session["pending_email"] = email
            ok, msg, _ = create_and_send_otp(email, purpose="registration")
            flash(
                "Account not verified. " + msg,
                "success" if ok else "error"
            )
            return redirect(url_for("verify_otp"))

        session.permanent = True
        session["student_id"] = student["id"]
        session["student_name"] = student["name"]
        session["student_class"] = student["class_name"]
        session["sv"] = student["session_version"] or 0
        session.pop("is_teacher", None)
        session.pop("impersonating", None)
        ip_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO login_history (student_id, login_time, ip_address, via_teacher) VALUES (?,?,?,0)",
                (student["id"], datetime.now().isoformat(), ip_addr)
            )
            conn.commit()
        flash(f"Welcome back, {student['name']}!", "success")
        return redirect(url_for("student_dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    was_impersonating = session.get("impersonating")
    session.clear()
    if was_impersonating:
        session["is_teacher"] = True
        flash("Returned to teacher session.", "success")
        return redirect(url_for("teacher_dashboard"))
    flash("Logged out successfully.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@student_login_required
def student_dashboard():
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (session["student_id"],)).fetchone()
        tests = conn.execute(
            """SELECT * FROM tests WHERE is_active=1
               AND (class_name=? OR class_name='' OR class_name IS NULL)
               ORDER BY created_at DESC""",
            (student["class_name"],)
        ).fetchall()
        my_results = conn.execute(
            """SELECT results.*, tests.title AS test_title FROM results
               JOIN tests ON tests.id = results.test_id
               WHERE results.student_id=? ORDER BY results.submitted_at DESC""",
            (student["id"],)
        ).fetchall()
        attempted_test_ids = {r["test_id"] for r in my_results}
        announcements = conn.execute(
            """SELECT * FROM announcements WHERE is_active=1
               AND (class_name=? OR class_name='' OR class_name IS NULL)
               ORDER BY created_at DESC LIMIT 5""",
            (student["class_name"],)
        ).fetchall()
        total_attempted = len(my_results)
        avg_pct = 0
        if total_attempted:
            avg_pct = round(sum(percentage(r["score"], r["total_marks"]) for r in my_results) / total_attempted, 1)
    return render_template(
        "student_dashboard.html",
        student=student, tests=tests, my_results=my_results,
        attempted_test_ids=attempted_test_ids, announcements=announcements,
        total_attempted=total_attempted, avg_pct=avg_pct
    )


@app.route("/notes")
@student_login_required
def notes_list():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (session["student_id"],)).fetchone()
        if q:
            notes = conn.execute(
                """SELECT * FROM notes WHERE (class_name=? OR class_name='' OR class_name IS NULL)
                   AND (title LIKE ? OR subject LIKE ?) ORDER BY uploaded_at DESC""",
                (student["class_name"], f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            notes = conn.execute(
                """SELECT * FROM notes WHERE class_name=? OR class_name='' OR class_name IS NULL
                   ORDER BY uploaded_at DESC""",
                (student["class_name"],)
            ).fetchall()
    return render_template("notes.html", notes=notes, q=q)


@app.route("/notes/download/<int:note_id>")
@student_login_required
def download_note(note_id):
    with get_db() as conn:
        note = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if note is None:
        abort(404)
    return send_from_directory(
        UPLOAD_FOLDER, note["filename"], as_attachment=True,
        download_name=secure_filename(note["title"]) + ".pdf"
    )


@app.route("/profile")
@student_login_required
def student_profile():
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (session["student_id"],)).fetchone()
        history = conn.execute(
            "SELECT * FROM login_history WHERE student_id=? ORDER BY login_time DESC LIMIT 20",
            (session["student_id"],)
        ).fetchall()
        results = conn.execute(
            """SELECT results.*, tests.title AS test_title FROM results
               JOIN tests ON tests.id = results.test_id
               WHERE results.student_id=? ORDER BY results.submitted_at DESC""",
            (session["student_id"],)
        ).fetchall()
    return render_template("student_profile.html", student=student, history=history, results=results)


@app.route("/test/<int:test_id>")
@student_login_required
def take_test(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=? AND is_active=1", (test_id,)).fetchone()
        if test is None:
            abort(404)
        already = conn.execute(
            "SELECT * FROM results WHERE test_id=? AND student_id=?",
            (test_id, session["student_id"])
        ).fetchone()
        if already:
            flash("You have already submitted this test.", "error")
            return redirect(url_for("student_dashboard"))
        questions = conn.execute("SELECT * FROM questions WHERE test_id=? ORDER BY id", (test_id,)).fetchall()
    if not questions:
        flash("No questions in this test yet.", "error")
        return redirect(url_for("student_dashboard"))
    return render_template("take_test.html", test=test, questions=questions)


@app.route("/test/<int:test_id>/submit", methods=["POST"])
@student_login_required
def submit_test(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        already = conn.execute(
            "SELECT * FROM results WHERE test_id=? AND student_id=?",
            (test_id, session["student_id"])
        ).fetchone()
        if already:
            flash("Already submitted.", "error")
            return redirect(url_for("student_dashboard"))
        questions = conn.execute("SELECT * FROM questions WHERE test_id=?", (test_id,)).fetchall()
        score = 0
        total_marks = 0
        answer_rows = []
        for q in questions:
            total_marks += q["marks"]
            selected = request.form.get(f"q_{q['id']}")
            is_correct = 1 if selected == q["correct_option"] else 0
            if is_correct:
                score += q["marks"]
            answer_rows.append((q["id"], selected, is_correct))
        cur = conn.execute(
            """INSERT INTO results (test_id, student_id, score, total_marks, submitted_at)
               VALUES (?,?,?,?,?)""",
            (test_id, session["student_id"], score, total_marks, datetime.now().isoformat())
        )
        result_id = cur.lastrowid
        for qid, selected, is_correct in answer_rows:
            conn.execute(
                "INSERT INTO answers (result_id, question_id, selected_option, is_correct) VALUES (?,?,?,?)",
                (result_id, qid, selected, is_correct)
            )
        conn.commit()
    return redirect(url_for("test_result", test_id=test_id))


@app.route("/test/<int:test_id>/result")
@student_login_required
def test_result(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        result = conn.execute(
            "SELECT * FROM results WHERE test_id=? AND student_id=?",
            (test_id, session["student_id"])
        ).fetchone()
        if result is None:
            abort(404)
        answers = conn.execute(
            """SELECT a.*, q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
                      q.correct_option, q.marks
               FROM answers a JOIN questions q ON q.id = a.question_id
               WHERE a.result_id=? ORDER BY q.id""",
            (result["id"],)
        ).fetchall()
    pct = percentage(result["score"], result["total_marks"])
    passed = pct >= PASS_PERCENTAGE
    return render_template(
        "test_result.html", test=test, result=result, answers=answers,
        pct=pct, passed=passed, pass_pct=PASS_PERCENTAGE
    )


@app.route("/test/<int:test_id>/leaderboard")
@student_login_required
def test_leaderboard(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        rows = conn.execute(
            """SELECT results.score, results.total_marks, results.submitted_at,
                      students.name, students.roll, students.class_name
               FROM results JOIN students ON students.id = results.student_id
               WHERE results.test_id=?
               ORDER BY results.score DESC, results.submitted_at ASC""",
            (test_id,)
        ).fetchall()
    return render_template("leaderboard.html", test=test, rows=rows)


@app.route("/teacher/login", methods=["GET", "POST"])
@rate_limit("teacher_login", 5, 600)
def teacher_login():
    if session.get("is_teacher") and not session.get("student_id"):
        return redirect(url_for("teacher_dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if check_teacher_credentials(username, password):
            session.clear()
            session.permanent = True
            session["is_teacher"] = True
            log_activity("teacher", "login", "Teacher logged in")
            flash("Welcome, Sir!", "success")
            return redirect(url_for("teacher_dashboard"))
        flash("Invalid credentials.", "error")
        return redirect(url_for("teacher_login"))
    return render_template("teacher_login.html")


@app.route("/teacher/logout")
def teacher_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/teacher/dashboard")
@teacher_login_required
def teacher_dashboard():
    with get_db() as conn:
        total_students = conn.execute("SELECT COUNT(*) AS c FROM students WHERE is_verified=1").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) AS c FROM students WHERE is_verified=0").fetchone()["c"]
        total_notes = conn.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]
        total_tests = conn.execute("SELECT COUNT(*) AS c FROM tests").fetchone()["c"]
        total_results = conn.execute("SELECT COUNT(*) AS c FROM results").fetchone()["c"]
        new_contacts = conn.execute("SELECT COUNT(*) AS c FROM contact_requests WHERE status='new'").fetchone()["c"]
        recent_logins = conn.execute(
            """SELECT lh.login_time, lh.ip_address, lh.via_teacher, s.name, s.class_name
               FROM login_history lh JOIN students s ON s.id = lh.student_id
               ORDER BY lh.login_time DESC LIMIT 10"""
        ).fetchall()
        announcements = conn.execute("SELECT * FROM announcements ORDER BY created_at DESC LIMIT 5").fetchall()
    return render_template(
        "teacher_dashboard.html",
        total_students=total_students, pending=pending,
        total_notes=total_notes, total_tests=total_tests,
        total_results=total_results, new_contacts=new_contacts,
        recent_logins=recent_logins, announcements=announcements
    )


@app.route("/teacher/students")
@teacher_login_required
def students_list():
    q = request.args.get("q", "").strip()
    class_filter = request.args.get("class", "").strip()
    status = request.args.get("status", "all")
    with get_db() as conn:
        sql = "SELECT * FROM students WHERE 1=1"
        params = []
        if q:
            sql += " AND (name LIKE ? OR email LIKE ? OR mobile LIKE ? OR roll LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like, like])
        if class_filter:
            sql += " AND class_name=?"
            params.append(class_filter)
        if status == "verified":
            sql += " AND is_verified=1 AND is_blocked=0"
        elif status == "pending":
            sql += " AND is_verified=0"
        elif status == "blocked":
            sql += " AND is_blocked=1"
        sql += " ORDER BY class_name, name"
        students = conn.execute(sql, params).fetchall()
        classes = [r[0] for r in conn.execute("SELECT DISTINCT class_name FROM students ORDER BY class_name").fetchall()]
    return render_template(
        "students_list.html", students=students, q=q, class_filter=class_filter,
        status=status, classes=classes
    )


@app.route("/teacher/students/admit", methods=["GET", "POST"])
@teacher_login_required
def admit_student():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        class_name = request.form.get("class_name", "").strip()
        roll = request.form.get("roll", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = normalize_mobile(request.form.get("mobile", ""))
        password = request.form.get("password", "") or "student123"
        if not name or not class_name or not email:
            flash("Name, class and email are required.", "error")
            return redirect(url_for("admit_student"))
        if not mobile or len(mobile) < 10:
            flash("Valid 10-digit mobile is mandatory.", "error")
            return redirect(url_for("admit_student"))
        pw_err = validate_password_strength(password)
        if pw_err:
            flash(pw_err, "error")
            return redirect(url_for("admit_student"))
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM students WHERE email=?", (email,)).fetchone()
            if existing:
                flash("Email already registered.", "error")
                return redirect(url_for("admit_student"))
            conn.execute(
                """INSERT INTO students
                   (name, class_name, roll, email, mobile, password_hash,
                    is_verified, registered_at, admitted_by_teacher)
                   VALUES (?,?,?,?,?,?,1,?,1)""",
                (name, class_name, roll, email, mobile,
                 generate_password_hash(password), datetime.now().isoformat())
            )
            conn.commit()
        log_activity("teacher", "admit_student", f"{name} <{email}>")
        flash(f"Student {name} admitted and verified. Default password: {password}", "success")
        return redirect(url_for("students_list"))
    return render_template("admit_student.html")



@app.route("/teacher/students/<int:student_id>/edit", methods=["GET", "POST"])
@teacher_login_required
def edit_student(student_id):
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if not student:
            abort(404)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            class_name = request.form.get("class_name", "").strip()
            roll = request.form.get("roll", "").strip()
            mobile = request.form.get("mobile", "").strip()
            notes_internal = request.form.get("notes_internal", "").strip()
            if not name:
                flash("Name is required.", "error")
                return redirect(url_for("edit_student", student_id=student_id))
            if not class_name:
                flash("Class is required.", "error")
                return redirect(url_for("edit_student", student_id=student_id))
            conn.execute(
                """UPDATE students SET name=?, class_name=?, roll=?, mobile=?, notes_internal=?
                   WHERE id=?""",
                (name, class_name, roll or None, mobile or None, notes_internal or None, student_id)
            )
            conn.commit()
            log_activity("teacher", "edit_student", f"id={student_id} name={name}")
            flash(f"Student details updated: {name}", "success")
            return redirect(url_for("students_list"))
    return render_template("edit_student.html", student=student)


@app.route("/teacher/students/<int:student_id>/verify", methods=["POST"])
@teacher_login_required
def verify_student(student_id):
    with get_db() as conn:
        conn.execute("UPDATE students SET is_verified=1, admitted_by_teacher=1 WHERE id=?", (student_id,))
        conn.commit()
        s = conn.execute("SELECT name FROM students WHERE id=?", (student_id,)).fetchone()
    log_activity("teacher", "verify_student", f"id={student_id} {s['name'] if s else ''}")
    flash("Student verified without OTP.", "success")
    return redirect(request.referrer or url_for("students_list"))


@app.route("/teacher/students/<int:student_id>/delete", methods=["POST"])
@teacher_login_required
def delete_student(student_id):
    with get_db() as conn:
        s = conn.execute("SELECT name, email FROM students WHERE id=?", (student_id,)).fetchone()
        if s:
            conn.execute("DELETE FROM students WHERE id=?", (student_id,))
            conn.commit()
            log_activity("teacher", "delete_student", f"{s['name']} <{s['email']}>")
            flash(f"Student {s['name']} removed.", "success")
        else:
            flash("Student not found.", "error")
    return redirect(url_for("students_list"))


@app.route("/teacher/students/<int:student_id>/block", methods=["POST"])
@teacher_login_required
def block_student(student_id):
    with get_db() as conn:
        row = conn.execute("SELECT is_blocked, name FROM students WHERE id=?", (student_id,)).fetchone()
        if row:
            new_val = 0 if row["is_blocked"] else 1
            conn.execute("UPDATE students SET is_blocked=? WHERE id=?", (new_val, student_id))
            conn.commit()
            log_activity("teacher", "block_toggle", f"{row['name']} -> {new_val}")
            flash("Student blocked." if new_val else "Student unblocked.", "success")
    return redirect(request.referrer or url_for("students_list"))


@app.route("/teacher/students/<int:student_id>/login-as", methods=["POST"])
@teacher_login_required
def login_as_student(student_id):
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=? AND is_verified=1", (student_id,)).fetchone()
    if not student:
        flash("Student not found or not verified.", "error")
        return redirect(url_for("students_list"))
    if student["is_blocked"]:
        flash("Cannot login as a blocked student.", "error")
        return redirect(url_for("students_list"))
    session.permanent = True
    session["student_id"] = student["id"]
    session["student_name"] = student["name"]
    session["student_class"] = student["class_name"]
    session["sv"] = student["session_version"] or 0
    session["impersonating"] = True
    session["is_teacher"] = True
    ip_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO login_history (student_id, login_time, ip_address, via_teacher) VALUES (?,?,?,1)",
            (student["id"], datetime.now().isoformat(), ip_addr)
        )
        conn.commit()
    log_activity("teacher", "impersonate", f"{student['name']} (id={student_id})")
    flash(f"You are now viewing as {student['name']}. Logout to return to teacher panel.", "success")
    return redirect(url_for("student_dashboard"))


@app.route("/teacher/student/<int:student_id>/history")
@teacher_login_required
def student_history(student_id):
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if not student:
            abort(404)
        history = conn.execute(
            "SELECT * FROM login_history WHERE student_id=? ORDER BY login_time DESC", (student_id,)
        ).fetchall()
        results = conn.execute(
            """SELECT results.*, tests.title AS test_title FROM results
               JOIN tests ON tests.id = results.test_id
               WHERE results.student_id=? ORDER BY results.submitted_at DESC""",
            (student_id,)
        ).fetchall()
    return render_template("student_history.html", student=student, history=history, results=results)


@app.route("/teacher/students/export")
@teacher_login_required
def export_students():
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, name, class_name, roll, email, mobile, is_verified, is_blocked, registered_at
               FROM students ORDER BY class_name, name"""
        ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "name", "class", "roll", "email", "mobile", "verified", "blocked", "registered_at"])
    for r in rows:
        writer.writerow([r["id"], r["name"], r["class_name"], r["roll"], r["email"],
                         r["mobile"], r["is_verified"], r["is_blocked"], r["registered_at"]])
    log_activity("teacher", "export_students", f"count={len(rows)}")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=students_export.csv"}
    )


@app.route("/teacher/notes/upload", methods=["GET", "POST"])
@teacher_login_required
def upload_note():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        subject = request.form.get("subject", "").strip()
        class_name = request.form.get("class_name", "").strip()
        # Form field is named pdf_file (must match upload_note.html)
        file = request.files.get("pdf_file") or request.files.get("file")
        if not title:
            flash("Title is required.", "error")
            return redirect(url_for("upload_note"))
        if not file or file.filename == "":
            flash("Please choose a PDF file to upload.", "error")
            return redirect(url_for("upload_note"))
        if not allowed_file(file.filename):
            flash("Only PDF files are allowed.", "error")
            return redirect(url_for("upload_note"))
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        original = secure_filename(file.filename) or "note.pdf"
        if not original.lower().endswith(".pdf"):
            original = original + ".pdf"
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{timestamp}_{original}"
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        try:
            file.save(save_path)
        except Exception as e:
            flash(f"Could not save file: {e}", "error")
            return redirect(url_for("upload_note"))
        with get_db() as conn:
            conn.execute(
                "INSERT INTO notes (title, subject, class_name, filename, uploaded_at) VALUES (?,?,?,?,?)",
                (title, subject, class_name, filename, datetime.now().isoformat())
            )
            conn.commit()
        log_activity("teacher", "upload_note", title)
        create_notification(
            title=f"New note: {title}",
            message=f"A new note has been uploaded" + (f" for class {class_name}" if class_name else "") + ".",
            notif_type="note",
            class_name=class_name or "",
            link=url_for("notes_list"),
        )
        flash("Note uploaded successfully!", "success")
        return redirect(url_for("upload_note"))
    with get_db() as conn:
        notes = conn.execute("SELECT * FROM notes ORDER BY uploaded_at DESC").fetchall()
    return render_template("upload_note.html", notes=notes)


@app.route("/teacher/notes/<int:note_id>/delete", methods=["POST"])
@teacher_login_required
def delete_note(note_id):
    with get_db() as conn:
        note = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if note:
            path = os.path.join(UPLOAD_FOLDER, note["filename"])
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
            conn.commit()
            log_activity("teacher", "delete_note", note["title"])
            flash("Note deleted.", "success")
        else:
            flash("Note not found.", "error")
    return redirect(url_for("upload_note"))


@app.route("/teacher/tests")
@teacher_login_required
def manage_tests():
    with get_db() as conn:
        tests = conn.execute("SELECT * FROM tests ORDER BY created_at DESC").fetchall()
        tests_data = []
        for t in tests:
            q_count = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            r_count = conn.execute(
                "SELECT COUNT(*) AS c FROM results WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            tests_data.append({"test": t, "q_count": q_count, "r_count": r_count})
    return render_template("manage_tests.html", tests_data=tests_data)


@app.route("/teacher/tests/create", methods=["GET", "POST"])
@teacher_login_required
def create_test():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        subject = request.form.get("subject", "").strip()
        class_name = request.form.get("class_name", "").strip()
        duration = request.form.get("duration_minutes", "30").strip()
        if not title:
            flash("Test title is required.", "error")
            return redirect(url_for("create_test"))
        try:
            duration = int(duration)
            if duration < 1:
                raise ValueError
        except ValueError:
            flash("Duration must be a positive number.", "error")
            return redirect(url_for("create_test"))
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO tests (title, subject, class_name, duration_minutes, is_active, created_at)
                   VALUES (?,?,?,?,1,?)""",
                (title, subject, class_name, duration, datetime.now().isoformat())
            )
            test_id = cur.lastrowid
            conn.commit()
        create_notification(
            title=f"New test: {title}",
            message=f"A new test is available" + (f" for class {class_name}" if class_name else "") + f". Duration: {duration} minutes.",
            notif_type="test",
            class_name=class_name or "",
            link=url_for("student_dashboard"),
        )
        flash("Test created! Now add questions.", "success")
        return redirect(url_for("add_questions", test_id=test_id))
    return render_template("create_test.html")


@app.route("/teacher/tests/<int:test_id>/questions", methods=["GET", "POST"])
@teacher_login_required
def add_questions(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        if request.method == "POST":
            q_texts = request.form.getlist("question_text[]")
            opt_a = request.form.getlist("option_a[]")
            opt_b = request.form.getlist("option_b[]")
            opt_c = request.form.getlist("option_c[]")
            opt_d = request.form.getlist("option_d[]")
            corrects = request.form.getlist("correct_option[]")
            marks_list = request.form.getlist("marks[]")
            n = len(q_texts)
            added = 0
            for i in range(n):
                qt = (q_texts[i] if i < len(q_texts) else "").strip()
                a = (opt_a[i] if i < len(opt_a) else "").strip()
                b = (opt_b[i] if i < len(opt_b) else "").strip()
                c = (opt_c[i] if i < len(opt_c) else "").strip()
                d = (opt_d[i] if i < len(opt_d) else "").strip()
                co = (corrects[i] if i < len(corrects) else "").strip()
                mk = (marks_list[i] if i < len(marks_list) else "1").strip()
                if not all([qt, a, b, c, d, co]):
                    continue
                if co not in ("A", "B", "C", "D"):
                    continue
                try:
                    marks = max(1, int(mk))
                except ValueError:
                    marks = 1
                conn.execute(
                    """INSERT INTO questions
                       (test_id, question_text, option_a, option_b, option_c, option_d, correct_option, marks)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (test_id, qt, a, b, c, d, co, marks)
                )
                added += 1
            conn.commit()
            if added:
                flash(f"{added} question(s) added successfully!", "success")
            else:
                flash("No valid questions were submitted. Please fill all fields.", "error")
            return redirect(url_for("add_questions", test_id=test_id))
        questions = conn.execute("SELECT * FROM questions WHERE test_id=? ORDER BY id", (test_id,)).fetchall()
    return render_template("add_questions.html", test=test, questions=questions)


@app.route("/teacher/questions/delete/<int:question_id>/<int:test_id>", methods=["POST"])
@teacher_login_required
def delete_question(question_id, test_id):
    with get_db() as conn:
        conn.execute("DELETE FROM questions WHERE id=?", (question_id,))
        conn.commit()
    flash("Question deleted.", "success")
    return redirect(url_for("add_questions", test_id=test_id))


@app.route("/teacher/tests/<int:test_id>/toggle", methods=["POST"])
@teacher_login_required
def toggle_test(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test:
            new_status = 0 if test["is_active"] == 1 else 1
            conn.execute("UPDATE tests SET is_active=? WHERE id=?", (new_status, test_id))
            conn.commit()
            if new_status == 1:
                create_notification(
                    title=f"Test active: {test['title']}",
                    message="A test is now available. Open your dashboard to attempt it.",
                    notif_type="test",
                    class_name=test["class_name"] or "",
                    link=url_for("student_dashboard"),
                )
    return redirect(url_for("manage_tests"))


@app.route("/teacher/tests/<int:test_id>/delete", methods=["POST"])
@teacher_login_required
def delete_test(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if not test:
            flash("Test not found.", "error")
            return redirect(url_for("manage_tests"))
        title = test["title"]
        # Cascade: answers -> results, questions, then test
        result_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM results WHERE test_id=?", (test_id,)
        ).fetchall()]
        for rid in result_ids:
            conn.execute("DELETE FROM answers WHERE result_id=?", (rid,))
        conn.execute("DELETE FROM results WHERE test_id=?", (test_id,))
        conn.execute("DELETE FROM questions WHERE test_id=?", (test_id,))
        conn.execute("DELETE FROM tests WHERE id=?", (test_id,))
        conn.commit()
        log_activity("teacher", "delete_test", f"id={test_id} title={title}")
    flash(f'Test "{title}" permanently deleted.', "success")
    return redirect(request.referrer or url_for("manage_tests"))


@app.route("/teacher/cleanup", methods=["GET"])
@teacher_login_required
def data_cleanup():
    with get_db() as conn:
        def cnt(sql):
            return conn.execute(sql).fetchone()["c"]
        counts = {
            "students": cnt("SELECT COUNT(*) AS c FROM students"),
            "tests": cnt("SELECT COUNT(*) AS c FROM tests"),
            "active_tests": cnt("SELECT COUNT(*) AS c FROM tests WHERE is_active=1"),
            "inactive_tests": cnt("SELECT COUNT(*) AS c FROM tests WHERE is_active=0"),
            "questions": cnt("SELECT COUNT(*) AS c FROM questions"),
            "results": cnt("SELECT COUNT(*) AS c FROM results"),
            "answers": cnt("SELECT COUNT(*) AS c FROM answers"),
            "notes": cnt("SELECT COUNT(*) AS c FROM notes"),
            "announcements": cnt("SELECT COUNT(*) AS c FROM announcements"),
            "contacts": cnt("SELECT COUNT(*) AS c FROM contact_requests"),
            "login_history": cnt("SELECT COUNT(*) AS c FROM login_history"),
            "otp": cnt("SELECT COUNT(*) AS c FROM otp_codes"),
            "activity": cnt("SELECT COUNT(*) AS c FROM activity_log"),
        }
        inactive = conn.execute(
            "SELECT * FROM tests WHERE is_active=0 ORDER BY created_at DESC"
        ).fetchall()
        inactive_tests = []
        for t in inactive:
            q_count = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            r_count = conn.execute(
                "SELECT COUNT(*) AS c FROM results WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            inactive_tests.append({
                "id": t["id"],
                "title": t["title"],
                "class_name": t["class_name"],
                "q_count": q_count,
                "r_count": r_count,
            })
    return render_template(
        "data_cleanup.html", counts=counts, inactive_tests=inactive_tests
    )


@app.route("/teacher/cleanup/action", methods=["POST"])
@teacher_login_required
def cleanup_action():
    action = request.form.get("action", "")
    with get_db() as conn:
        if action == "delete_inactive_tests":
            tests = conn.execute("SELECT id, title FROM tests WHERE is_active=0").fetchall()
            n = 0
            for t in tests:
                result_ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM results WHERE test_id=?", (t["id"],)
                ).fetchall()]
                for rid in result_ids:
                    conn.execute("DELETE FROM answers WHERE result_id=?", (rid,))
                conn.execute("DELETE FROM results WHERE test_id=?", (t["id"],))
                conn.execute("DELETE FROM questions WHERE test_id=?", (t["id"],))
                conn.execute("DELETE FROM tests WHERE id=?", (t["id"],))
                n += 1
            conn.commit()
            log_activity("teacher", "cleanup", f"deleted {n} inactive tests")
            flash(f"Deleted {n} inactive test(s) permanently.", "success")

        elif action == "clear_otps":
            n = conn.execute("SELECT COUNT(*) AS c FROM otp_codes").fetchone()["c"]
            conn.execute("DELETE FROM otp_codes")
            conn.commit()
            log_activity("teacher", "cleanup", f"cleared {n} otp codes")
            flash(f"Cleared {n} OTP code(s).", "success")

        elif action == "clear_old_logins":
            cutoff = (datetime.now() - timedelta(days=90)).isoformat()
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM login_history WHERE login_time < ?", (cutoff,)
            ).fetchone()["c"]
            conn.execute("DELETE FROM login_history WHERE login_time < ?", (cutoff,))
            conn.commit()
            log_activity("teacher", "cleanup", f"cleared {n} old logins")
            flash(f"Cleared {n} login history row(s) older than 90 days.", "success")

        elif action == "clear_old_activity":
            cutoff = (datetime.now() - timedelta(days=90)).isoformat()
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM activity_log WHERE created_at < ?", (cutoff,)
            ).fetchone()["c"]
            conn.execute("DELETE FROM activity_log WHERE created_at < ?", (cutoff,))
            conn.commit()
            log_activity("teacher", "cleanup", f"cleared {n} old activity rows")
            flash(f"Cleared {n} activity log row(s) older than 90 days.", "success")

        elif action == "clear_old_contacts":
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM contact_requests WHERE status IN ('replied','closed')"
            ).fetchone()["c"]
            conn.execute(
                "DELETE FROM contact_requests WHERE status IN ('replied','closed')"
            )
            conn.commit()
            log_activity("teacher", "cleanup", f"cleared {n} handled contacts")
            flash(f"Cleared {n} handled contact message(s).", "success")

        elif action == "vacuum":
            conn.commit()
            conn.execute("VACUUM")
            log_activity("teacher", "cleanup", "VACUUM")
            flash("Database optimized (VACUUM completed).", "success")

        else:
            flash("Unknown cleanup action.", "error")

    return redirect(url_for("data_cleanup"))


@app.route("/teacher/tests/<int:test_id>/results")
@teacher_login_required
def test_results_view(test_id):

    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        results = conn.execute(
            """SELECT results.*, students.name, students.class_name, students.roll, students.mobile
               FROM results JOIN students ON students.id = results.student_id
               WHERE results.test_id=? ORDER BY results.score DESC""",
            (test_id,)
        ).fetchall()
    return render_template("test_results_view.html", test=test, results=results)


@app.route("/teacher/tests/<int:test_id>/result/<int:result_id>")
@teacher_login_required
def teacher_student_result(test_id, result_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        result = conn.execute("SELECT * FROM results WHERE id=? AND test_id=?", (result_id, test_id)).fetchone()
        if test is None or result is None:
            abort(404)
        student = conn.execute("SELECT * FROM students WHERE id=?", (result["student_id"],)).fetchone()
        answers = conn.execute(
            """SELECT a.*, q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
                      q.correct_option, q.marks
               FROM answers a JOIN questions q ON q.id = a.question_id
               WHERE a.result_id=? ORDER BY q.id""",
            (result_id,)
        ).fetchall()
    pct = percentage(result["score"], result["total_marks"])
    return render_template(
        "teacher_student_result.html",
        test=test, student=student, result=result, answers=answers, pct=pct
    )


@app.route("/teacher/tests/<int:test_id>/result/<int:result_id>/pdf")
@teacher_login_required
def download_student_result_pdf(test_id, result_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        result = conn.execute("SELECT * FROM results WHERE id=? AND test_id=?", (result_id, test_id)).fetchone()
        if test is None or result is None:
            abort(404)
        student = conn.execute("SELECT * FROM students WHERE id=?", (result["student_id"],)).fetchone()
        answers = conn.execute(
            """SELECT a.*, q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
                      q.correct_option, q.marks
               FROM answers a JOIN questions q ON q.id = a.question_id
               WHERE a.result_id=? ORDER BY q.id""",
            (result_id,)
        ).fetchall()
    pdf_bytes = build_student_result_pdf(test, student, result, answers)
    if not pdf_bytes:
        flash("PDF generation is not available on this server.", "error")
        return redirect(url_for("teacher_student_result", test_id=test_id, result_id=result_id))
    safe_name = secure_filename(f"{student['name']}_{test['title']}_answers.pdf") or "student_answers.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'}
    )



@app.route("/teacher/announcements", methods=["GET", "POST"])
@teacher_login_required
def manage_announcements():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        class_name = request.form.get("class_name", "").strip()
        if title and content:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO announcements (title, content, class_name, created_at, is_active) VALUES (?,?,?,?,1)",
                    (title, content, class_name, datetime.now().isoformat())
                )
                conn.commit()
            create_notification(
                title=f"Notice: {title}",
                message=(content[:200] + ("…" if len(content) > 200 else "")),
                notif_type="notice",
                class_name=class_name or "",
                link=url_for("student_dashboard"),
            )
            flash("Announcement posted!", "success")
        else:
            flash("Title and content required.", "error")
        return redirect(url_for("manage_announcements"))
    with get_db() as conn:
        announcements = conn.execute("SELECT * FROM announcements ORDER BY created_at DESC").fetchall()
    return render_template("announcements.html", announcements=announcements)


@app.route("/teacher/announcements/<int:aid>/toggle", methods=["POST"])
@teacher_login_required
def toggle_announcement(aid):
    with get_db() as conn:
        row = conn.execute("SELECT is_active FROM announcements WHERE id=?", (aid,)).fetchone()
        if row:
            conn.execute("UPDATE announcements SET is_active=? WHERE id=?", (0 if row["is_active"] else 1, aid))
            conn.commit()
    return redirect(url_for("manage_announcements"))


@app.route("/teacher/announcements/<int:aid>/delete", methods=["POST"])
@teacher_login_required
def delete_announcement(aid):
    with get_db() as conn:
        conn.execute("DELETE FROM announcements WHERE id=?", (aid,))
        conn.commit()
    flash("Announcement deleted.", "success")
    return redirect(url_for("manage_announcements"))


@app.route("/teacher/contacts")
@teacher_login_required
def teacher_contacts():
    status = request.args.get("status", "all")
    with get_db() as conn:
        if status == "new":
            rows = conn.execute("SELECT * FROM contact_requests WHERE status='new' ORDER BY created_at DESC").fetchall()
        elif status == "replied":
            rows = conn.execute("SELECT * FROM contact_requests WHERE status='replied' ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM contact_requests ORDER BY created_at DESC").fetchall()
    return render_template("teacher_contacts.html", rows=rows, status=status)


@app.route("/teacher/contacts/<int:cid>/reply", methods=["POST"])
@teacher_login_required
def reply_contact(cid):
    reply = request.form.get("reply", "").strip()
    with get_db() as conn:
        conn.execute(
            """UPDATE contact_requests SET status='replied', teacher_reply=?, replied_at=? WHERE id=?""",
            (reply, datetime.now().isoformat(), cid)
        )
        conn.commit()
    log_activity("teacher", "reply_contact", f"id={cid}")
    flash("Reply saved.", "success")
    return redirect(url_for("teacher_contacts"))


@app.route("/teacher/contacts/<int:cid>/close", methods=["POST"])
@teacher_login_required
def close_contact(cid):
    with get_db() as conn:
        conn.execute("UPDATE contact_requests SET status='closed' WHERE id=?", (cid,))
        conn.commit()
    return redirect(url_for("teacher_contacts"))


@app.route("/teacher/activity")
@teacher_login_required
def teacher_activity():
    with get_db() as conn:
        logs = conn.execute("SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 100").fetchall()
    return render_template("teacher_activity.html", logs=logs)


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Something went wrong on our side."), 500


@app.route("/manifest.json")
def manifest():
    return send_from_directory(STATIC_FOLDER, "manifest.json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory(STATIC_FOLDER, "sw.js")


@app.errorhandler(413)
def too_large(e):
    flash("File is too large. Maximum size is 15 MB.", "error")
    return redirect(request.referrer or url_for("upload_note"))


@app.context_processor
def inject_notifications():
    """Provide unread notification count for student nav badge."""
    data = {"unread_notif_count": 0, "student_notifications": []}
    sid = session.get("student_id")
    if not sid:
        return data
    try:
        with get_db() as conn:
            student = conn.execute(
                "SELECT class_name FROM students WHERE id=?", (sid,)
            ).fetchone()
            class_name = student["class_name"] if student else ""
            rows = conn.execute(
                """SELECT n.* FROM notifications n
                   WHERE (n.class_name='' OR n.class_name IS NULL OR n.class_name=?)
                   AND n.id NOT IN (
                     SELECT notification_id FROM notification_reads WHERE student_id=?
                   )
                   ORDER BY n.created_at DESC LIMIT 20""",
                (class_name, sid)
            ).fetchall()
            data["student_notifications"] = rows
            data["unread_notif_count"] = len(rows)
    except Exception:
        pass
    return data


@app.route("/api/notifications")
@student_login_required
def api_notifications():
    sid = session["student_id"]
    with get_db() as conn:
        student = conn.execute("SELECT class_name FROM students WHERE id=?", (sid,)).fetchone()
        class_name = student["class_name"] if student else ""
        rows = conn.execute(
            """SELECT n.*,
                      CASE WHEN r.id IS NULL THEN 0 ELSE 1 END AS is_read
               FROM notifications n
               LEFT JOIN notification_reads r
                 ON r.notification_id = n.id AND r.student_id = ?
               WHERE (n.class_name='' OR n.class_name IS NULL OR n.class_name=?)
               ORDER BY n.created_at DESC LIMIT 30""",
            (sid, class_name)
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "title": r["title"],
            "message": r["message"],
            "type": r["notif_type"],
            "link": r["link"] or "",
            "created_at": r["created_at"],
            "is_read": bool(r["is_read"]),
        })
    return jsonify({"notifications": items})


@app.route("/api/notifications/mark-read", methods=["POST"])
@student_login_required
def api_mark_notifications_read():
    sid = session["student_id"]
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")  # list of ids, or None = all visible
    with get_db() as conn:
        student = conn.execute("SELECT class_name FROM students WHERE id=?", (sid,)).fetchone()
        class_name = student["class_name"] if student else ""
        if ids:
            target_ids = [int(i) for i in ids]
        else:
            target_ids = [
                r["id"] for r in conn.execute(
                    """SELECT id FROM notifications
                       WHERE class_name='' OR class_name IS NULL OR class_name=?""",
                    (class_name,)
                ).fetchall()
            ]
        now = datetime.now().isoformat()
        for nid in target_ids:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO notification_reads
                       (notification_id, student_id, read_at) VALUES (?,?,?)""",
                    (nid, sid, now)
                )
            except Exception:
                pass
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/push/public-key")
def api_push_public_key():
    """Public VAPID key the browser needs to create a push subscription."""
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@app.route("/api/push/subscribe", methods=["POST"])
@student_login_required
def api_push_subscribe():
    """
    Save (or refresh) a student's push subscription — called from the
    browser right after they tap "Allow" on the notification prompt.
    """
    sid = session["student_id"]
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint", "")
    keys = payload.get("keys", {}) or {}
    p256dh = keys.get("p256dh", "")
    auth = keys.get("auth", "")
    if not endpoint or not p256dh or not auth:
        return jsonify({"ok": False, "error": "Invalid subscription."}), 400
    with get_db() as conn:
        student = conn.execute("SELECT class_name FROM students WHERE id=?", (sid,)).fetchone()
        class_name = student["class_name"] if student else ""
        conn.execute(
            """INSERT INTO push_subscriptions (student_id, class_name, endpoint, p256dh, auth, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 student_id=excluded.student_id,
                 class_name=excluded.class_name,
                 p256dh=excluded.p256dh,
                 auth=excluded.auth""",
            (sid, class_name, endpoint, p256dh, auth, datetime.now().isoformat())
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
@student_login_required
def api_push_unsubscribe():
    """Remove a push subscription — called when a student turns notifications off."""
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint", "")
    if endpoint:
        with get_db() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
            conn.commit()
    return jsonify({"ok": True})


# Ensure tables exist on import (e.g. gunicorn / production)
init_db()
if not all(_teacher_creds()):
    logging.getLogger(__name__).warning(
        "TEACHER_USERNAME / TEACHER_PASSWORD are not set in .env - teacher login is disabled until they are.")

if __name__ == "__main__":
    app.run(debug=DEBUG_MODE, host="0.0.0.0", port=5000)
