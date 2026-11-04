# -*- coding: utf-8 -*-
"""ONE image service for the whole site (My Questions pages AND Test Workspace use it).

  * upload / validation / Cloudinary errors  -> backend.services.image_store (the single implementation;
    cloudinary_client.py is now only a thin compatibility wrapper around it)
  * usage checking                           -> usage(): live questions, bank questions, exam versions,
                                                attempt snapshots and stored answers - by image id AND by URL
  * safe delete                              -> safe_delete(): never deletes a used image; marks it
                                                pending_delete first, removes it from Cloudinary, then drops the
                                                row; any failure leaves a retry record, never a silent mismatch
  * reconciliation                           -> reconcile(): retries failed deletes, un-hides rows that became used

Existing Cloudinary URLs and bank_images ids are never changed.
"""
import logging
import sqlite3
from datetime import datetime

from backend.db.connection import get_db
from backend.services import deletion_queue
from backend.services import image_store as store

log = logging.getLogger(__name__)
ImageError = store.ImageStoreError


def _now():
    return datetime.now().isoformat()


# ------------------------------------------------------------------- usage
def usage(conn, image_id, url=None):
    """How many places still reference this image. Any non-zero count blocks deletion."""
    if url is None:
        row = conn.execute("SELECT url FROM bank_images WHERE id=?", (image_id,)).fetchone()
        url = row["url"] if row else None
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    u = url or "\x00none"
    out = {
        "test_questions": one("SELECT COUNT(*) FROM questions WHERE bank_image_id=? OR image_url=?", image_id, u),
        "bank_questions": one("SELECT COUNT(*) FROM bank_questions WHERE image_id=?", image_id),
        "exam_versions": one("SELECT COUNT(*) FROM exam_version_questions WHERE bank_image_id=? OR image_url=?", image_id, u),
        "attempt_snapshots": one("SELECT COUNT(*) FROM attempt_questions WHERE bank_image_id=? OR image_url=?", image_id, u),
        "stored_answers": one("SELECT COUNT(*) FROM answers WHERE bank_image_id=? OR image_url=?", image_id, u),
    }
    out["total"] = sum(out.values())
    return out


def usage_message(u):
    parts = []
    if u["test_questions"]:
        parts.append("%d test question(s)" % u["test_questions"])
    if u["bank_questions"]:
        parts.append("%d question(s) in My Questions" % u["bank_questions"])
    if u["exam_versions"] or u["attempt_snapshots"] or u["stored_answers"]:
        parts.append("past exam snapshots/results (%d reference(s))" % (
            u["exam_versions"] + u["attempt_snapshots"] + u["stored_answers"]))
    return "This image is used in " + " and ".join(parts) + ", so it was not deleted."


# ------------------------------------------------------------------ upload
def insert_image(conn, category_id, info, original_name):
    return conn.execute(
        """INSERT INTO bank_images (category_id, url, public_id, original_name, width, height, bytes, format,
                                    created_at, status)
           VALUES (?,?,?,?,?,?,?,?,?, 'active')""",
        (category_id, info["url"], info["public_id"], (original_name or "")[:150], info.get("width"),
         info.get("height"), info.get("bytes"), info.get("format"), _now())).lastrowid


def discard_orphan(public_id, reason="database insert failed after upload"):
    """The file reached Cloudinary but no database row exists: delete it, or queue the delete for retry."""
    try:
        store.destroy(public_id)
    except Exception as exc:  # noqa: BLE001
        with get_db() as conn:
            deletion_queue.enqueue(conn, "cloudinary", public_id, f"{reason}: {exc}")
            conn.commit()


def clean_category_name(raw):
    return " ".join(str(raw or "").split())[:60]


def find_category(conn, name):
    """The category with this name (case-insensitive), or None. Never creates anything."""
    name = clean_category_name(name)
    if not name:
        return None
    return conn.execute("SELECT * FROM bank_categories WHERE name=? COLLATE NOCASE", (name,)).fetchone()


def get_or_create_category(conn, name):
    """One category per name (case-insensitive): re-uses the existing one, never makes a duplicate.
    Does NOT commit - the caller commits it together with whatever needs the category."""
    row = find_category(conn, name)
    if row is not None:
        return row
    name = clean_category_name(name)
    try:
        conn.execute("INSERT INTO bank_categories (name, created_at) VALUES (?,?)", (name, _now()))
    except sqlite3.IntegrityError:           # another request created it a moment ago
        pass
    return find_category(conn, name)


def upload_and_register(filename, data, category):
    """Validate -> Cloudinary -> bank_images row. Returns the stored info dict (+ id, category_id, category_name).

    ``category`` is a dict-like with ``name`` and either an existing ``id`` or id=None ("create it by this name").
    A category that does not exist yet is created only AFTER Cloudinary accepted the file, in the same
    transaction as the image row - so a failed upload leaves no empty category and no orphan row, and a
    failed database insert removes the file from Cloudinary again (or queues the retry). Raises ImageError."""
    info = store.upload(filename, data, category=category["name"])
    try:
        with get_db() as conn:
            cid = category["id"]
            name = category["name"]
            if not cid:
                row = get_or_create_category(conn, name)
                cid, name = row["id"], row["name"]
            info["id"] = insert_image(conn, cid, info, filename)
            info["category_id"], info["category_name"] = cid, name
            conn.commit()
    except Exception:
        log.exception("image row insert failed for %s", info.get("public_id"))
        discard_orphan(info["public_id"])
        raise ImageError("'%s': could not be saved to the database." % filename)
    return info


# ------------------------------------------------------------------ delete
def safe_delete(image_id):
    """-> (state, message) with state in 'deleted' | 'blocked' | 'queued' | 'missing'."""
    with get_db() as conn:
        img = conn.execute("SELECT * FROM bank_images WHERE id=?", (image_id,)).fetchone()
        if img is None:
            return "missing", "Image not found."
        u = usage(conn, image_id, img["url"])
        if u["total"]:
            return "blocked", usage_message(u)
        # hide it first (so nobody picks it while the remote delete runs); the row stays until Cloudinary agrees
        conn.execute("UPDATE bank_images SET status='pending_delete', status_changed_at=? WHERE id=? AND status='active'",
                     (_now(), image_id))
        conn.commit()
    try:
        store.destroy(img["public_id"])
    except Exception as exc:  # noqa: BLE001 - recorded for retry; DB and Cloudinary stay consistent
        with get_db() as conn:
            deletion_queue.enqueue(conn, "cloudinary", img["public_id"], str(exc))
            conn.commit()
        return "queued", "Could not delete from Cloudinary right now; the image has been hidden and deletion will be retried automatically."
    with get_db() as conn:
        u = usage(conn, image_id, img["url"])           # re-check: someone may have picked it meanwhile
        if u["total"]:
            conn.execute("UPDATE bank_images SET status='active', status_changed_at=? WHERE id=?", (_now(), image_id))
            conn.commit()
            return "blocked", usage_message(u)
        conn.execute("DELETE FROM bank_images WHERE id=?", (image_id,))
        conn.commit()
    return "deleted", "Image deleted."


# ---------------------------------------------------------- reconciliation
def _cloudinary_handler(public_id):
    with get_db() as conn:
        img = conn.execute("SELECT * FROM bank_images WHERE public_id=?", (public_id,)).fetchone()
        if img is not None:
            u = usage(conn, img["id"], img["url"])
            if u["total"]:                               # became used again: keep it, un-hide it
                conn.execute("UPDATE bank_images SET status='active', status_changed_at=? WHERE id=?",
                             (_now(), img["id"]))
                conn.commit()
                return
    store.destroy(public_id)                             # raises on failure -> stays queued
    with get_db() as conn:
        conn.execute("DELETE FROM bank_images WHERE public_id=? AND status='pending_delete'", (public_id,))
        conn.commit()


def reconcile():
    """Retry queued deletes and report DB rows hidden for deletion that have no queue entry."""
    from backend.services import note_storage
    with get_db() as conn:
        for r in conn.execute("SELECT public_id FROM bank_images WHERE status='pending_delete'").fetchall():
            deletion_queue.enqueue(conn, "cloudinary", r["public_id"], "reconcile: pending_delete without queue entry")
        conn.commit()
        stats = deletion_queue.run_pending(conn, {
            "cloudinary": _cloudinary_handler,
            "file": lambda ref: None if note_storage.other_references(conn, ref) else note_storage.remove_file(ref),
        })
    return stats


if __name__ == "__main__":   # python -m backend.services.image_service  (cron / PythonAnywhere scheduled task)
    import backend.bootstrap  # noqa: F401  (loads .env and the app)
    print(reconcile())
