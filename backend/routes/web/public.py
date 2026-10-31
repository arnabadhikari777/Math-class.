# -*- coding: utf-8 -*-
"""Public pages: home, app download, contact form."""

from datetime import datetime

from flask import flash, redirect, render_template, request, session, url_for

from backend.config import SITE_NAME
from backend.core import app
from backend.db.connection import get_db
from backend.middleware.rate_limit import rate_limit
from backend.services.activity_service import log_activity
from backend.utils.validators import normalize_mobile


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
        "public/index.html", announcements=announcements, site_name=SITE_NAME,
        total_students=total_students, total_tests=total_tests, total_notes=total_notes
    )


@app.route("/download")
def download_app():
    return render_template("public/download.html", site_name=SITE_NAME)


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
    return render_template("public/contact.html", site_name=SITE_NAME)
