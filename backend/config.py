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

# --- Local development OTP mode ---------------------------------------------------------------------
# LOCAL_OTP_MODE=console  -> no email is sent; the OTP is printed in the terminal that runs Flask.
# Strictly for a developer's own computer. validate_local_otp_mode() runs when the app starts and
# REFUSES to start if the mode is switched on anywhere that is not a local debug run.
LOCAL_OTP_MODE = os.environ.get("LOCAL_OTP_MODE", "").strip().lower()
_PRODUCTION_ENV_HINTS = ("PYTHONANYWHERE_DOMAIN", "PYTHONANYWHERE_SITE", "DYNO", "RENDER", "K_SERVICE",
                         "WEBSITE_SITE_NAME", "VERCEL", "FLY_APP_NAME", "RAILWAY_ENVIRONMENT")


class ConfigurationError(RuntimeError):
    """The configuration is unsafe or invalid; the application must not start."""


def local_otp_console_enabled():
    return LOCAL_OTP_MODE == "console"


def validate_local_otp_mode(mode=None, debug=None, force_https=None, environ=None):
    """Raise ConfigurationError unless LOCAL_OTP_MODE is empty, or 'console' on a local debug run.
    (Arguments exist only so the tests can check every combination without touching the real environment.)"""
    mode = LOCAL_OTP_MODE if mode is None else mode
    debug = DEBUG_MODE if debug is None else debug
    force_https = FORCE_HTTPS if force_https is None else force_https
    environ = os.environ if environ is None else environ
    if mode in ("", "off", "0", "false", "no"):
        return
    if mode != "console":
        raise ConfigurationError("LOCAL_OTP_MODE must be empty or 'console' (got %r)." % mode)
    problems = []
    if not debug:
        problems.append("FLASK_DEBUG=1 is required")
    if force_https:
        problems.append("FORCE_HTTPS=0 is required (a real production site always forces HTTPS)")
    found = [k for k in _PRODUCTION_ENV_HINTS if environ.get(k)]
    if found:
        problems.append("this looks like a hosted/production environment (%s)" % ", ".join(found))
    if problems:
        raise ConfigurationError(
            "LOCAL_OTP_MODE=console is for local development only and was refused: " + "; ".join(problems) +
            ". Remove LOCAL_OTP_MODE from the environment/.env of this server.")
