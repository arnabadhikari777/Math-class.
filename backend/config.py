# -*- coding: utf-8 -*-
"""All settings and constants in ONE place.

Secrets come from the .env file (copy .env.example to .env). Nothing secret
is hard-coded here.
"""
import os
import re
from pathlib import Path

# Load .env file if present (for local development)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on real environment variables

# Project root (the folder that holds app.py, templates/, static/, uploads/, database.db)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "notes")
STATIC_FOLDER = os.path.join(BASE_DIR, "static")
ALLOWED_EXTENSIONS = {"pdf"}
# Image Library supports up to 10 x 5 MB files per upload batch.
# PDF notes retain their own 15 MB limit in upload_note().
MAX_CONTENT_LENGTH = 55 * 1024 * 1024

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

# JEE Main 2027 sessions shown in the countdown widgets (all times are IST, UTC+05:30).
# Edit the dates here if NTA changes them. `window_days` = how long the exam window
# stays "Live now" after the start time before the countdown is hidden.
JEE_SESSIONS = [
    {"id": "s1", "short": "Session 1", "label": "JEE Main 2027 · Session 1",
     "start": "2027-01-22T09:00:00+05:30", "window_days": 10},
    {"id": "s2", "short": "Session 2", "label": "JEE Main 2027 · Session 2",
     "start": "2027-04-02T09:00:00+05:30", "window_days": 10},
]

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

DEBUG_MODE = os.environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
# Redirect http -> https (localhost is always exempt). Set FORCE_HTTPS=0 to disable.
FORCE_HTTPS = os.environ.get("FORCE_HTTPS", "1").strip().lower() not in ("0", "false", "no")
