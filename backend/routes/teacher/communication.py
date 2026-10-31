# -*- coding: utf-8 -*-
"""Teacher: announcements and contact requests."""

from datetime import datetime

from flask import flash, redirect, render_template, request, url_for

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import teacher_login_required
from backend.services.activity_service import log_activity
from backend.services.notification_service import create_notification


@app.route("/teacher/announcements", methods=["GET", "POST"])
@teacher_login_required
def manage_announcements():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        class_name_raw = request.form.get("class_name", "").strip()
        from backend.utils.classes import require_class
        try:
            class_name = require_class(class_name_raw, allow_empty=True)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("manage_announcements"))
        if title and content:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO announcements (title, content, class_name, created_at, is_active) VALUES (?,?,?,?,1)",
                    (title, content, class_name, datetime.now().isoformat())
                )
                conn.commit()
            create_notification(
                title=f"Notice: {title}",
                message=(content[:200] + ("…" if len(content) > 200 else "")),
                notif_type="notice",
                class_name=class_name or "",
                link=url_for("student_dashboard"),
            )
            flash("Announcement posted!", "success")
        else:
            flash("Title and content required.", "error")
        return redirect(url_for("manage_announcements"))
    with get_db() as conn:
        announcements = conn.execute("SELECT * FROM announcements ORDER BY created_at DESC").fetchall()
    return render_template("teacher/announcements.html", announcements=announcements)


@app.route("/teacher/announcements/<int:aid>/toggle", methods=["POST"])
@teacher_login_required
def toggle_announcement(aid):
    with get_db() as conn:
        row = conn.execute("SELECT is_active FROM announcements WHERE id=?", (aid,)).fetchone()
        if row:
            conn.execute("UPDATE announcements SET is_active=? WHERE id=?", (0 if row["is_active"] else 1, aid))
            conn.commit()
    return redirect(url_for("manage_announcements"))


@app.route("/teacher/announcements/<int:aid>/delete", methods=["POST"])
@teacher_login_required
def delete_announcement(aid):
    with get_db() as conn:
        conn.execute("DELETE FROM announcements WHERE id=?", (aid,))
        conn.commit()
    flash("Announcement deleted.", "success")
    return redirect(url_for("manage_announcements"))


@app.route("/teacher/contacts")
@teacher_login_required
def teacher_contacts():
    status = request.args.get("status", "all")
    with get_db() as conn:
        if status == "new":
            rows = conn.execute("SELECT * FROM contact_requests WHERE status='new' ORDER BY created_at DESC").fetchall()
        elif status == "replied":
            rows = conn.execute("SELECT * FROM contact_requests WHERE status='replied' ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM contact_requests ORDER BY created_at DESC").fetchall()
    return render_template("teacher/teacher_contacts.html", rows=rows, status=status)


@app.route("/teacher/contacts/<int:cid>/reply", methods=["POST"])
@teacher_login_required
def reply_contact(cid):
    reply = request.form.get("reply", "").strip()
    with get_db() as conn:
        conn.execute(
            """UPDATE contact_requests SET status='replied', teacher_reply=?, replied_at=? WHERE id=?""",
            (reply, datetime.now().isoformat(), cid)
        )
        conn.commit()
    log_activity("teacher", "reply_contact", f"id={cid}")
    flash("Reply saved.", "success")
    return redirect(url_for("teacher_contacts"))


@app.route("/teacher/contacts/<int:cid>/close", methods=["POST"])
@teacher_login_required
def close_contact(cid):
    with get_db() as conn:
        conn.execute("UPDATE contact_requests SET status='closed' WHERE id=?", (cid,))
        conn.commit()
    return redirect(url_for("teacher_contacts"))
