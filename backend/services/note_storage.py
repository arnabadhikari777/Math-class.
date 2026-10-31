# -*- coding: utf-8 -*-
"""Safe PDF note storage: validation, unique names, atomic save, reference-checked delete."""
import hashlib
import logging
import os
import uuid

from werkzeug.utils import secure_filename

from backend.config import UPLOAD_FOLDER
from backend.services import deletion_queue

log = logging.getLogger(__name__)
MAX_PDF_BYTES = 15 * 1024 * 1024


class PdfError(ValueError):
    """Message is safe to show to the teacher."""


def validate_pdf(original_name, data):
    """Extension, size and real PDF signature (%PDF- within the first 1 KB)."""
    name = secure_filename(original_name or "") or ""
    if not name.lower().endswith(".pdf"):
        raise PdfError("Only PDF files are allowed.")
    if not data:
        raise PdfError("The file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfError("PDF ফাইলের সাইজ সর্বোচ্চ 15 MB হতে পারবে।")
    if b"%PDF-" not in data[:1024]:
        raise PdfError("This file is not a real PDF (missing PDF signature).")
    return name


def save_pdf(data, folder=None):
    """Write to a collision-proof name (uuid4). 'xb' refuses to overwrite; temp file + replace = atomic."""
    folder = folder or UPLOAD_FOLDER
    os.makedirs(folder, exist_ok=True)
    stored = uuid.uuid4().hex + ".pdf"
    final = os.path.join(folder, stored)
    tmp = final + ".part"
    with open(tmp, "xb") as fh:
        fh.write(data)
    os.replace(tmp, final)
    return stored, hashlib.sha256(data).hexdigest()


def remove_file(stored_name, folder=None):
    folder = os.path.abspath(folder or UPLOAD_FOLDER)
    if not stored_name or os.path.basename(stored_name) != stored_name:
        raise ValueError("refusing a path that is not a plain file name")
    path = os.path.join(folder, stored_name)
    if os.path.isfile(path):
        os.remove(path)


def other_references(conn, stored_name, exclude_note_id=None):
    sql, args = "SELECT COUNT(*) FROM notes WHERE filename=?", [stored_name]
    if exclude_note_id is not None:
        sql += " AND id<>?"
        args.append(exclude_note_id)
    return conn.execute(sql, args).fetchone()[0]


def delete_file_if_unreferenced(conn, stored_name):
    """Call AFTER the note row is gone and committed. Keeps the file while any other record uses it."""
    if other_references(conn, stored_name):
        return "kept (still used by another note)"
    try:
        remove_file(stored_name)
        return "deleted"
    except OSError as exc:
        deletion_queue.enqueue(conn, "file", stored_name, f"os error: {exc}")
        conn.commit()
        return "queued for retry"
