# -*- coding: utf-8 -*-
"""Teacher credentials are read from .env on every login attempt."""

import hmac
import os
from pathlib import Path

from backend.config import BASE_DIR


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
