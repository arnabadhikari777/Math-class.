# -*- coding: utf-8 -*-
"""Sends emails over SMTP (OTP mails, replies)."""

import logging
import smtplib
import ssl
import threading
from email.mime.text import MIMEText

from backend.config import EMAIL_ADDRESS, EMAIL_APP_PASSWORD, EMAIL_RE, EMAIL_SENDER_NAME, OTP_EXPIRY_MINUTES, SITE_NAME


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
