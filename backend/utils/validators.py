# -*- coding: utf-8 -*-
"""Small input validators: file type, mobile number, password strength."""

import re

from backend.config import ALLOWED_EXTENSIONS


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def normalize_mobile(m):

    if not m:
        return ""
    digits = "".join(c for c in m if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def validate_password_strength(password):
    """Returns an error message, or None when the password is acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 128:
        return "Password is too long (max 128 characters)."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain both letters and numbers."
    return None
