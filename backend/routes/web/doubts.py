# -*- coding: utf-8 -*-
"""Doubt Management (student + teacher), student profile picture, teacher Student Login Activity.

Security rules used everywhere in this file:
  * a student only ever reads/changes rows WHERE student_id = session student (doubts.get_own);
  * every teacher route has @teacher_login_required; every POST is CSRF-protected by the global hook;
  * images go through the one existing Cloudinary implementation (services/image_store.py).
"""
import logging
import secrets
import sqlite3

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import student_login_required, teacher_login_required
from backend.middleware.rate_limit import rate_limit
from backend.services import doubt_service as ds
from backend.services import image_store, notification_service
from backend.services.activity_service import log_activity

log = logging.getLogger(__name__)


def _int(name, default=1, lo=1, hi=100000):
    try:
        return min(hi, max(lo, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


def _acting_student_blocked():
    """A teacher 'viewing as' a student must not write data under the student's name."""
    if session.get("impersonating"):
        flash("This action is not available while viewing as a student.", "error")
        return True
    return False


# =============================================================== student: doubts
def _wants_json():
    """The upgraded form submits with fetch() (so it can show errors inline and retry safely); plain browsers still POST normally."""
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _ref():
    return secrets.token_hex(3).upper()


@app.route("/doubts")
@student_login_required
def doubt_box():
    sid, page = session["student_id"], _int("page")
    subject = request.args.get("subject", "").strip()[:60]
    q = " ".join(request.args.get("q", "").split())[:80]
    status = request.args.get("status", "")
    status = status if status in ds.STATUS_FILTERS else ""
    items, total, subjects, load_error = [], 0, [], False
    try:
        with get_db() as conn:
            items, total = ds.list_for_student(conn, sid, page, subject, q, status)
            subjects = ds.student_subjects(conn, sid)
    except sqlite3.Error:
        ref = _ref()
        log.exception("doubt list failed ref=%s student=%s", ref, sid)
        load_error = ref
    pages = max(1, -(-total // ds.PER_PAGE))
    return render_template("student/doubts.html", items=items, total=total, page=page, pages=pages, subject=subject,
                           subjects=subjects, q=q, status=status, load_error=load_error,
                           filtered=bool(subject or q or status)), (500 if load_error else 200)


def _form_page(form, token, status, message=None, field=None, ref=None, retryable=True):
    """Re-show the form with everything the student typed (the picture itself cannot be re-filled by a browser - the
    message says so when it matters). JSON for fetch(), the normal page otherwise."""
    if _wants_json():
        return jsonify({"ok": False, "error": message, "field": field, "ref": ref, "retryable": retryable,
                        "token": token}), status
    if message:
        flash(message, "error")
    with get_db() as conn:
        subjects = ds.subject_suggestions(conn, session["student_id"])
    return render_template("student/doubt_form.html", f=form, token=token, subjects=subjects,
                           max_mb=image_store.max_mb(), field_error=field), status


@app.route("/doubts/new", methods=["GET", "POST"])
@student_login_required
@rate_limit("doubt_submit", 20, 3600)
def doubt_new():
    sid = session["student_id"]
    if request.method == "POST":
        if _acting_student_blocked():
            if _wants_json():
                return jsonify({"ok": False, "error": "This action is not available while viewing as a student.",
                                "retryable": False}), 403
            return redirect(url_for("doubt_box"))
        form = request.form
        token = ds.clean_token(form.get("token"))
        try:
            subject, chapter, topic, question = ds.validate_form(
                form.get("subject"), form.get("chapter"), form.get("topic"), form.get("question"))
        except ds.DoubtError as e:
            msg = str(e)
            return _form_page(form, token or secrets.token_hex(16), 400, msg,
                              field="subject" if "subject" in msg.lower() else "question", retryable=False)

        # repeated tap / reload / retry with the same token -> open the doubt that already exists (nothing is uploaded again)
        with get_db() as conn:
            old = ds.find_by_token(conn, sid, token)
        if old:
            return _done(old["id"], created=False)

        image = None
        f = request.files.get("image")
        if f and f.filename:
            data = f.read(image_store.max_bytes() + 1)
            try:
                image_store.validate(f.filename, data)
            except image_store.ImageStoreError as e:
                return _form_page(form, token or secrets.token_hex(16), 400, "Your doubt was not sent. " + str(e),
                                  field="image", retryable=False)
            try:
                image = image_store.upload(f.filename, data, "doubts")
            except image_store.ImageStoreError as e:       # Cloudinary / network / configuration problem
                log.warning("doubt image upload failed student=%s: %s", sid, e)
                return _form_page(form, token or secrets.token_hex(16), 502,
                                  "Your doubt was not sent because the picture could not be uploaded (%s). "
                                  "Your text is still here - try again, or send it without the picture." % e,
                                  field="image", retryable=True)

        try:
            with get_db() as conn:
                did, created, notified = ds.submit_doubt(conn, sid, subject, chapter, topic, question, image, token)
        except ds.DoubtError as e:
            _discard(image)
            return _form_page(form, token or secrets.token_hex(16), 403, str(e), retryable=False)
        except sqlite3.OperationalError as e:               # "database is locked" / busy: transient, safe to retry
            ref = _ref()
            log.error("doubt insert busy ref=%s student=%s: %s", ref, sid, e)
            _discard(image)                                  # no orphan picture on Cloudinary
            return _form_page(form, token or secrets.token_hex(16), 503,
                              "The server is busy right now, so your doubt was not saved. Please tap Send again "
                              "in a few seconds. (ref %s)" % ref, ref=ref, retryable=True)
        except Exception:                                    # noqa: BLE001 - logged with a reference, never shown as a traceback
            ref = _ref()
            log.exception("doubt insert failed ref=%s student=%s", ref, sid)
            _discard(image)
            return _form_page(form, token or secrets.token_hex(16), 500,
                              "Something went wrong on our side and your doubt was not saved. Your text is still "
                              "here - please try again. If it keeps happening, tell Sir this code: %s." % ref,
                              ref=ref, retryable=True)
        if not created:
            _discard(image)       # another request with the same token won the race: drop THIS request's picture, keep theirs
        if not notified:
            log.error("doubt #%s saved but Sir's notification failed", did)
        return _done(did, created=created)
    with get_db() as conn:
        subjects = ds.subject_suggestions(conn, sid)
    return render_template("student/doubt_form.html", f={}, token=secrets.token_hex(16), subjects=subjects,
                           max_mb=image_store.max_mb())


def _discard(image):
    if image:
        ds.destroy_image_quietly(image["public_id"], "doubt not saved")


def _done(doubt_id, created):
    url = url_for("doubt_view", doubt_id=doubt_id)
    msg = ("Your doubt was sent to Sir. You can ask the AI about it while you wait." if created
           else "This doubt was already sent - here it is.")
    if _wants_json():
        flash(msg, "success")      # shown on the doubt page after the browser navigates there
        return jsonify({"ok": True, "created": created, "redirect": url, "id": doubt_id})
    flash(msg, "success")
    return redirect(url)


@app.route("/doubts/<int:doubt_id>")
@student_login_required
def doubt_view(doubt_id):
    sid = session["student_id"]
    with get_db() as conn:
        d = ds.get_own(conn, doubt_id, sid)
        if d is None:
            abort(404)
        _cid, msgs = ds.ai_messages(conn, doubt_id)
    notification_service.mark_link_read(sid, "/doubts/%d" % doubt_id)
    return render_template("student/doubt_detail.html", d=d, status=ds.card(d)["status"], msgs=msgs)


@app.route("/doubts/<int:doubt_id>/delete", methods=["POST"])
@student_login_required
def doubt_delete(doubt_id):
    sid = session["student_id"]
    if _acting_student_blocked():
        return redirect(url_for("doubt_box"))
    with get_db() as conn:
        if ds.get_own(conn, doubt_id, sid) is None:
            abort(404)
        public_id = ds.delete_doubt(conn, doubt_id)
        conn.commit()
    ds.destroy_image_quietly(public_id)
    flash("Doubt deleted.", "success")
    return redirect(url_for("doubt_box"))


# =============================================================== student: profile picture
MAX_AVATAR_BYTES = 3 * 1024 * 1024


@app.route("/profile/avatar", methods=["POST"])
@student_login_required
@rate_limit("avatar", 10, 3600)
def avatar_upload():
    sid = session["student_id"]            # ONLY the logged-in student's own picture; no id is read from the form
    if _acting_student_blocked():
        return redirect(url_for("student_profile"))
    f = request.files.get("avatar")
    if not f or not f.filename:
        flash("Please choose a picture first.", "error")
        return redirect(url_for("student_profile"))
    data = f.read(MAX_AVATAR_BYTES + 1)
    try:
        if len(data) > MAX_AVATAR_BYTES:
            raise image_store.ImageStoreError("The picture must be smaller than 3 MB.")
        image_store.validate(f.filename, data)
        img = image_store.upload(f.filename, data, "avatars")
    except image_store.ImageStoreError as e:
        flash(str(e), "error")
        return redirect(url_for("student_profile"))
    try:
        with get_db() as conn:
            old = conn.execute("SELECT profile_image_public_id AS p FROM students WHERE id=?", (sid,)).fetchone()
            conn.execute("UPDATE students SET profile_image_url=?, profile_image_public_id=? WHERE id=?",
                         (img["url"], img["public_id"], sid))
            conn.commit()
    except Exception:
        ds.destroy_image_quietly(img["public_id"])
        flash("The picture could not be saved. Please try again.", "error")
        return redirect(url_for("student_profile"))
    if old and old["p"] and old["p"] != img["public_id"]:
        ds.destroy_image_quietly(old["p"])          # previous picture is removed only after the new one is saved
    session["student_avatar"] = img["url"]
    flash("Profile picture updated.", "success")
    return redirect(url_for("student_profile"))


@app.route("/profile/avatar/remove", methods=["POST"])
@student_login_required
def avatar_remove():
    sid = session["student_id"]
    if _acting_student_blocked():
        return redirect(url_for("student_profile"))
    with get_db() as conn:
        old = conn.execute("SELECT profile_image_public_id AS p FROM students WHERE id=?", (sid,)).fetchone()
        conn.execute("UPDATE students SET profile_image_url=NULL, profile_image_public_id=NULL WHERE id=?", (sid,))
        conn.commit()
    if old and old["p"]:
        ds.destroy_image_quietly(old["p"])
    session.pop("student_avatar", None)
    flash("Profile picture removed.", "success")
    return redirect(url_for("student_profile"))


# =============================================================== teacher: doubt management
@app.context_processor
def _teacher_doubt_badge():
    if not (session.get("is_teacher") and not session.get("student_id")):
        return {}
    try:
        with get_db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM teacher_notifications WHERE is_read=0").fetchone()[0]
        return {"teacher_unread": n}
    except Exception:
        return {"teacher_unread": 0}


@app.route("/teacher/doubts")
@teacher_login_required
def teacher_doubts():
    subject = request.args.get("subject", "").strip()[:60]
    class_name = request.args.get("class_name", "").strip()[:20]
    q = " ".join(request.args.get("q", "").split())[:80]
    status = request.args.get("status", "")
    status = status if status in ("pending", "answered") else ""
    page = _int("page")
    items, total, subjects, notes, load_error = [], 0, [], [], False
    counts = {"total": 0, "pending": 0, "answered": 0, "new": 0}
    try:
        with get_db() as conn:
            counts = ds.teacher_counts(conn)
            items, total = ds.teacher_list(conn, subject, class_name, status, page, q)
            subjects = [r[0] for r in conn.execute("SELECT DISTINCT subject FROM doubts ORDER BY subject COLLATE NOCASE")]
            notes = conn.execute("SELECT * FROM teacher_notifications ORDER BY id DESC LIMIT 8").fetchall()
    except sqlite3.Error:
        ref = _ref()
        log.exception("teacher doubt list failed ref=%s", ref)
        load_error = ref
    pages = max(1, -(-total // ds.PER_PAGE))
    return render_template("teacher/doubts.html", items=items, counts=counts, total=total, page=page, pages=pages,
                           subject=subject, class_name=class_name, status=status, subjects=subjects, notes=notes, q=q,
                           load_error=load_error, filtered=bool(subject or class_name or status or q)), (500 if load_error else 200)


def _teacher_reply(ok, message, status=200, category=None, **extra):
    """fetch() gets JSON (inline confirmation, no page reload); a plain form post gets the usual flash + redirect."""
    if _wants_json():
        return jsonify(dict(extra, ok=ok, message=message)), status
    flash(message, category or ("success" if ok else "error"))
    return None


@app.route("/teacher/doubts/<int:doubt_id>", methods=["GET", "POST"])
@teacher_login_required
def teacher_doubt_view(doubt_id):
    with get_db() as conn:
        d = conn.execute(
            """SELECT d.*, s.name AS student_name, s.class_name AS student_class
               FROM doubts d JOIN students s ON s.id=d.student_id WHERE d.id=?""", (doubt_id,)).fetchone()
        if d is None:
            abort(404)
    if request.method == "POST":
        back = redirect(url_for("teacher_doubt_view", doubt_id=doubt_id))
        try:
            with get_db() as conn:
                row, is_update = ds.save_teacher_answer(conn, doubt_id, request.form.get("answer"))
                conn.commit()
        except ds.DoubtError as e:
            return _teacher_reply(False, str(e), 400, field="answer") or back
        except sqlite3.Error:
            ref = _ref()
            log.exception("teacher answer save failed ref=%s doubt=%s", ref, doubt_id)
            return _teacher_reply(False, "The answer could not be saved (ref %s). Your text is still in the box - please try again." % ref,
                                  503, ref=ref) or back
        notified = ds.notify_student_answer(d["student_id"], doubt_id, d["subject"], is_update)
        log_activity("teacher", "doubt_answer", "doubt #%d" % doubt_id)
        if notified:
            msg = "Answer saved. The student has been notified."
        else:
            log.error("answer to doubt #%s saved but the student's notification failed", doubt_id)
            msg = "Answer saved, but the student's notification could not be created. The student can still see the answer in My Doubts."
        with get_db() as conn:
            fresh = conn.execute("SELECT teacher_answered_at, teacher_updated_at FROM doubts WHERE id=?", (doubt_id,)).fetchone()
        reply = _teacher_reply(True, msg, 200, category="success" if notified else "error", notified=bool(notified),
                               updated_at=fresh["teacher_updated_at"] if fresh else None, answered=True)
        return reply if reply is not None else back
    with get_db() as conn:
        conn.execute("UPDATE doubts SET teacher_seen=1 WHERE id=?", (doubt_id,))
        conn.execute("UPDATE teacher_notifications SET is_read=1 WHERE link=?", ("/teacher/doubts/%d" % doubt_id,))
        conn.commit()
        _cid, msgs = ds.ai_messages(conn, doubt_id)
        nav = ds.teacher_neighbors(conn, doubt_id)
    return render_template("teacher/doubt_detail.html", d=d, msgs=msgs, nav=nav)


@app.route("/teacher/doubts/<int:doubt_id>/delete", methods=["POST"])
@teacher_login_required
def teacher_doubt_delete(doubt_id):
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM doubts WHERE id=?", (doubt_id,)).fetchone() is None:
            abort(404)
        public_id = ds.delete_doubt(conn, doubt_id)
        conn.commit()
    ds.destroy_image_quietly(public_id)
    flash("Doubt deleted.", "success")
    return redirect(url_for("teacher_doubts"))


@app.route("/teacher/notifications/read-all", methods=["POST"])
@teacher_login_required
def teacher_notifications_read_all():
    with get_db() as conn:
        conn.execute("UPDATE teacher_notifications SET is_read=1 WHERE is_read=0")
        conn.commit()
    return redirect(request.referrer or url_for("teacher_doubts"))


# =============================================================== teacher: Student Login Activity
@app.route("/teacher/login-activity")
@teacher_login_required
def teacher_login_activity():
    q = request.args.get("q", "").strip()[:60]
    class_name = request.args.get("class_name", "").strip()[:20]
    page = _int("page")
    per = 30
    where, args = ["1=1"], []
    if q:
        where.append("(s.name LIKE ? OR s.username LIKE ?)")
        args += ["%" + q + "%", "%" + q + "%"]
    if class_name:
        where.append("s.class_name=?")
        args.append(class_name)
    w = " AND ".join(where)
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM login_history lh JOIN students s ON s.id=lh.student_id WHERE " + w,
                             args).fetchone()[0]
        rows = conn.execute(
            """SELECT lh.id, lh.login_time, lh.via_teacher, s.id AS student_id, s.name, s.class_name
               FROM login_history lh JOIN students s ON s.id=lh.student_id WHERE %s
               ORDER BY lh.id DESC LIMIT ? OFFSET ?""" % w, args + [per, (page - 1) * per]).fetchall()
        latest = conn.execute(
            """SELECT s.id AS student_id, s.name, s.class_name, MAX(lh.id) AS last_id
               FROM login_history lh JOIN students s ON s.id=lh.student_id WHERE %s AND lh.via_teacher=0
               GROUP BY s.id ORDER BY last_id DESC LIMIT 8""" % w, args).fetchall() if page == 1 else []
        latest = [dict(r, login_time=conn.execute("SELECT login_time FROM login_history WHERE id=?",
                                                  (r["last_id"],)).fetchone()[0]) for r in latest]
    pages = max(1, -(-total // per))
    return render_template("teacher/login_activity.html", rows=rows, latest=latest, q=q, class_name=class_name,
                           page=page, pages=pages, total=total)
