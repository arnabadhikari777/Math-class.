# -*- coding: utf-8 -*-
"""Creates the one Flask ``app`` object and applies its base configuration.

Every other module does ``from backend.core import app``.
"""
import os
import secrets
from datetime import timedelta

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from backend.config import (
    BASE_DIR, DEBUG_MODE, FORCE_HTTPS, MAX_CONTENT_LENGTH, PASS_PERCENTAGE,
    STATIC_FOLDER, UPLOAD_FOLDER,
)

# root_path = project root, so templates/ and static/ stay at the top level.
app = Flask(__name__, root_path=BASE_DIR)


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
# Trust the proxy's X-Forwarded-For / X-Forwarded-Proto (PythonAnywhere etc.)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config["SESSION_COOKIE_SECURE"] = FORCE_HTTPS and not DEBUG_MODE
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.config["PASS_PERCENTAGE"] = PASS_PERCENTAGE
# Keep users logged in across browser restarts / PWA launches (30 days)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
