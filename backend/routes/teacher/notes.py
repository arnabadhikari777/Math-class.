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
from backend.services import note_storage
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
        data = file.stream.read(note_storage.MAX_PDF_BYTES + 1)
        try:
            original = note_storage.validate_pdf(file.filename, data)
        except note_storage.PdfError as e:
            flash(str(e), "error")
            return redirect(url_for("upload_note"))
        try:
            filename, digest = note_storage.save_pdf(data)
        except OSError as e:
            app.logger.exception("note upload could not save file")
            flash("Could not save the file on the server.", "error")
            return redirect(url_for("upload_note"))
        try:
            with get_db() as conn:
                conn.execute(
                    """INSERT INTO notes (title, subject, class_name, filename, uploaded_at,
                                          original_filename, file_sha256, file_size)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (title, subject, class_name, filename, datetime.now().isoformat(), original, digest, len(data))
                )
                conn.commit()
        except Exception:
            # the database refused the row: do not leave an orphan PDF behind
            try:
                note_storage.remove_file(filename)
            except OSError:
                app.logger.exception("could not clean up orphan upload %s", filename)
            app.logger.exception("note upload database failure")
            flash("Could not save the note. Nothing was stored; please try again.", "error")
            return redirect(url_for("upload_note"))
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
        if not note:
            flash("Note not found.", "error")
            return redirect(url_for("upload_note"))
        conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
        conn.commit()                                   # row first ...
        outcome = note_storage.delete_file_if_unreferenced(conn, note["filename"])   # ... then the file
    log_activity("teacher", "delete_note", f"{note['title']} file={note['filename']} {outcome}")
    flash("Note deleted." + (" (The PDF is still used by another note, so it was kept.)"
                             if outcome.startswith("kept") else ""), "success")
    return redirect(url_for("upload_note"))
