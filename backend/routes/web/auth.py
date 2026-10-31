# -*- coding: utf-8 -*-
"""Login, logout, registration, OTP verification and password reset (students + teacher)."""

import hashlib
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash

from flask import flash, redirect, render_template, request, session, url_for

from backend.config import EMAIL_RE, EMAIL_SENDER_NAME, RESET_COOLDOWN_SECONDS, RESET_EMAIL_PER_HOUR, SITE_NAME
from backend.core import app
from backend.db.connection import get_db
from backend.middleware.rate_limit import _bucket_ok, rate_limit
from backend.services.activity_service import log_activity
from backend.services.email_service import _send_email_async
from backend.services.otp_service import create_and_send_otp, verify_otp_code
from backend.services.teacher_auth_service import check_teacher_credentials
from backend.utils.validators import normalize_mobile, validate_password_strength


@app.route("/register", methods=["GET", "POST"])
@rate_limit("register", 10, 3600)
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        class_name_raw = request.form.get("class_name", "").strip()
        roll = request.form.get("roll", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = normalize_mobile(request.form.get("mobile", ""))
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not name or not class_name_raw or not email or not password:
            flash("Please fill all required fields.", "error")
            return redirect(url_for("register"))
        from backend.utils.classes import require_class
        try:
            class_name = require_class(class_name_raw, allow_empty=False)
        except ValueError as e:
            flash(str(e), "error")
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
    return render_template("auth/register.html")


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
    return render_template("auth/verify_otp.html", email=email)


@app.route("/resend-otp", methods=["POST"])
@rate_limit("resend_otp", 5, 3600)
def resend_otp():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("register"))
    ok, msg, _ = create_and_send_otp(email, purpose="registration")
    flash(msg, "success" if ok else "error")
    return redirect(url_for("verify_otp"))


# Forgot password (email OTP only)
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
    return render_template("auth/forgot_password.html", email=session.get("reset_email", ""))


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
    return render_template("auth/reset_password.html", email=session.get("reset_email", ""))


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
        if student["is_archived"]:
            flash("This account is archived. Contact Sir to have it restored.", "error")
            return redirect(url_for("login"))
        if student["is_verified"] == 0:
            session["pending_email"] = email
            ok, msg, _ = create_and_send_otp(email, purpose="registration")
            flash(
                "Account not verified. " + msg,
                "success" if ok else "error"
            )
            return redirect(url_for("verify_otp"))

        # Fresh session on every login (prevents session fixation): nothing from the
        # anonymous / previous session - including any teacher flag - survives.
        session.clear()
        session.permanent = True
        session["student_id"] = student["id"]
        session["student_name"] = student["name"]
        session["student_class"] = student["class_name"]
        session["sv"] = student["session_version"] or 0
        ip_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO login_history (student_id, login_time, ip_address, via_teacher) VALUES (?,?,?,0)",
                (student["id"], datetime.now().isoformat(), ip_addr)
            )
            conn.commit()
        flash(f"Welcome back, {student['name']}!", "success")
        return redirect(url_for("student_dashboard"))
    return render_template("auth/login.html")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """POST-only state change (CSRF-protected). A GET (old bookmark / link prefetch) changes nothing."""
    if request.method != "POST":
        return redirect(url_for("student_dashboard") if session.get("student_id") else url_for("index"))
    if session.get("impersonating"):
        return end_impersonation_response()
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("index"))


def end_impersonation_response():
    """Teacher leaves 'view as student': audit row closed, session rebuilt from scratch as teacher."""
    from backend.services import impersonation_service
    impersonation_service.end(session.get("impersonation_log_id"), "exit")
    session.clear()
    session.permanent = True
    session["is_teacher"] = True
    flash("Returned to teacher session.", "success")
    return redirect(url_for("teacher_dashboard"))


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
    return render_template("auth/teacher_login.html")


@app.route("/teacher/logout", methods=["GET", "POST"])
def teacher_logout():
    if request.method != "POST":
        return redirect(url_for("teacher_dashboard") if session.get("is_teacher") else url_for("index"))
    session.clear()
    return redirect(url_for("index"))
