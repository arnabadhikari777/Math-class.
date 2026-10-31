# -*- coding: utf-8 -*-
"""Teacher: upload and delete PDF notes."""

import os
from datetime import datetime
from werkzeug.utils import secure_filename

from flask import flash, redirect, render_template, request, url_for

from backend.config import UPLOAD_FOLDER
from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import teacher_login_required
from backend.services.activity_service import log_activity
from backend.services.notification_service import create_notification
from backend.utils.validators import allowed_file


@app.route("/teacher/notes/upload", methods=["GET", "POST"])
@teacher_login_required
def upload_note():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        subject = request.form.get("subject", "").strip()
        class_name_raw = request.form.get("class_name", "").strip()
        from backend.utils.classes import require_class
        try:
            class_name = require_class(class_name_raw, allow_empty=True)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("upload_note"))
        # Form field is named pdf_file (must match upload_note.html)
        file = request.files.get("pdf_file") or request.files.get("file")
        if not title:
            flash("Title is required.", "error")
            return redirect(url_for("upload_note"))
        if not file or file.filename == "":
            flash("Please choose a PDF file to upload.", "error")
            return redirect(url_for("upload_note"))
        if not allowed_file(file.filename):
            flash("Only PDF files are allowed.", "error")
            return redirect(url_for("upload_note"))
        # Keep the original PDF-note limit while allowing larger image batches.
        pdf_data = file.stream.read(15 * 1024 * 1024 + 1)
        if len(pdf_data) > 15 * 1024 * 1024:
            flash("PDF ফাইলের সাইজ সর্বোচ্চ 15 MB হতে পারবে।", "error")
            return redirect(url_for("upload_note"))
        file.stream.seek(0)
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        original = secure_filename(file.filename) or "note.pdf"
        if not original.lower().endswith(".pdf"):
            original = original + ".pdf"
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{timestamp}_{original}"
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        try:
            file.save(save_path)
        except Exception as e:
            flash(f"Could not save file: {e}", "error")
            return redirect(url_for("upload_note"))
        with get_db() as conn:
            conn.execute(
                "INSERT INTO notes (title, subject, class_name, filename, uploaded_at) VALUES (?,?,?,?,?)",
                (title, subject, class_name, filename, datetime.now().isoformat())
            )
            conn.commit()
        log_activity("teacher", "upload_note", title)
        create_notification(
            title=f"New note: {title}",
            message=f"A new note has been uploaded" + (f" for class {class_name}" if class_name else "") + ".",
            notif_type="note",
            class_name=class_name or "",
            link=url_for("notes_list"),
        )
        flash("Note uploaded successfully!", "success")
        return redirect(url_for("upload_note"))
    with get_db() as conn:
        notes = conn.execute("SELECT * FROM notes ORDER BY uploaded_at DESC").fetchall()
    return render_template("teacher/upload_note.html", notes=notes)


@app.route("/teacher/notes/<int:note_id>/delete", methods=["POST"])
@teacher_login_required
def delete_note(note_id):
    with get_db() as conn:
        note = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if note:
            path = os.path.join(UPLOAD_FOLDER, note["filename"])
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
            conn.commit()
            log_activity("teacher", "delete_note", note["title"])
            flash("Note deleted.", "success")
        else:
            flash("Note not found.", "error")
    return redirect(url_for("upload_note"))
