# -*- coding: utf-8 -*-
"""OTP create / verify for registration and password reset."""

import hashlib
import hmac
import secrets
import string
import threading
from datetime import datetime, timedelta

from backend.config import OTP_EXPIRY_MINUTES, OTP_MAX_ATTEMPTS, RESET_COOLDOWN_SECONDS
from backend.core import app
from backend.db.connection import get_db
from backend.config import local_otp_console_enabled
from backend.services import email_service


def generate_otp():
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _otp_hash(email, purpose, code):
    """OTPs are stored as an HMAC, never in plain text."""
    key = app.secret_key.encode("utf-8") if isinstance(app.secret_key, str) else app.secret_key
    return hmac.new(key, f"{email}|{purpose}|{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def create_and_send_otp(email, purpose="registration", background=False, cooldown=True):
    """
    Create OTP, invalidate older unused codes for same email+purpose,
    enforce a short resend cooldown, then send email.
    Returns (ok: bool, message: str, None) - the OTP is never returned, so it can never leak into a response
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

    if background and not local_otp_console_enabled():
        # Fire-and-forget (used by forgot-password so timing never leaks anything). The outcome is only logged.
        threading.Thread(target=email_service.send_otp_result, args=(email, otp_code, purpose), daemon=True).start()
        return True, "OTP sent.", None

    result = email_service.send_otp_result(email, otp_code, purpose)   # (console mode runs here, inside the request)
    if result.ok:
        if result.stage == "local_console":
            return True, "Local test mode: the OTP was not emailed. Check the Terminal where Flask is running.", None
        return True, "OTP sent to your email. Check inbox and spam folder.", None
    # The mail did not leave: this code is useless, so remove it (the cooldown must not punish a failed send).
    with get_db() as conn:
        conn.execute("DELETE FROM otp_codes WHERE email=? AND purpose=? AND otp_code=? AND is_used=0",
                     (email, purpose, _otp_hash(email, purpose, otp_code)))
        conn.commit()
    # The technical reason is in the server log (see email_service); the student gets a simple Bengali message.
    return False, result.user_message or "The email could not be sent. Please try again in a few minutes.", None


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
