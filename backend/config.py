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

# --- Web Push (VAPID) config ---
# These keys let the server send real push notifications to a student's phone/browser, even when the
# site/app is closed. There is NO built-in default key pair: put your own VAPID_PUBLIC_KEY /
# VAPID_PRIVATE_KEY in .env (generate once and keep them the same forever - changing them makes every
# subscribed browser resubscribe). The private key stays on the server and is never sent to the browser.
# When the keys are missing, push is switched off cleanly (see PUSH_ENABLED) instead of failing silently.
VAPID_PUBLIC_KEY = os.environ.get(
    "VAPID_PUBLIC_KEY",
    ""
)
VAPID_PRIVATE_KEY = os.environ.get(
    "VAPID_PRIVATE_KEY",
    ""
)
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", EMAIL_ADDRESS)
PUSH_ENABLED = bool(VAPID_PUBLIC_KEY.strip() and VAPID_PRIVATE_KEY.strip() and VAPID_CLAIMS_EMAIL.strip())

DEBUG_MODE = os.environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
# Redirect http -> https (localhost is always exempt). Set FORCE_HTTPS=0 to disable.
FORCE_HTTPS = os.environ.get("FORCE_HTTPS", "1").strip().lower() not in ("0", "false", "no")
