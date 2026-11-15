# -*- coding: utf-8 -*-
"""Doubt Management (student + teacher), student profile picture, teacher Student Login Activity.

Security rules used everywhere in this file:
  * a student only ever reads/changes rows WHERE student_id = session student (doubts.get_own);
  * every teacher route has @teacher_login_required; every POST is CSRF-protected by the global hook;
  * images go through the one existing Cloudinary implementation (services/image_store.py).
"""
import secrets

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import student_login_required, teacher_login_required
from backend.middleware.rate_limit import rate_limit
from backend.services import doubt_service as ds
from backend.services import image_store, notification_service
from backend.services.activity_service import log_activity


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
@app.route("/doubts")
@student_login_required
def doubt_box():
    sid, page, subject = session["student_id"], _int("page"), request.args.get("subject", "").strip()[:60]
    with get_db() as conn:
        items, total = ds.list_for_student(conn, sid, page, subject)
        subjects = [r[0] for r in conn.execute(
            "SELECT DISTINCT subject FROM doubts WHERE student_id=? ORDER BY subject COLLATE NOCASE", (sid,))]
    pages = max(1, -(-total // ds.PER_PAGE))
    return render_template("student/doubts.html", items=items, total=total, page=page, pages=pages,
                           subject=subject, subjects=subjects)


@app.route("/doubts/new", methods=["GET", "POST"])
@student_login_required
@rate_limit("doubt_submit", 20, 3600)
def doubt_new():
    sid = session["student_id"]
    if request.method == "POST":
        if _acting_student_blocked():
            return redirect(url_for("doubt_box"))
        form = request.form
        try:
            subject, chapter, topic, question = ds.validate_form(
                form.get("subject"), form.get("chapter"), form.get("topic"), form.get("question"))
        except ds.DoubtError as e:
            flash(str(e), "error")
            return render_template("student/doubt_form.html", f=form, token=form.get("token") or secrets.token_hex(16),
                                   subjects=ds.SUBJECTS), 400
        token = (form.get("token") or "")[:64]
        with get_db() as conn:     # repeated tap / reload with the same token -> open the existing doubt
            old = conn.execute("SELECT id FROM doubts WHERE student_id=? AND client_token=?", (sid, token)).fetchone() if token else None
        if old:
            return redirect(url_for("doubt_view", doubt_id=old["id"]))
        image = None
        f = request.files.get("image")
        if f and f.filename:
            data = f.read(image_store.max_bytes() + 1)
            try:
                image_store.validate(f.filename, data)
                image = image_store.upload(f.filename, data, "doubts")
            except image_store.ImageStoreError as e:
                flash("Your doubt was not sent. " + str(e), "error")
                return render_template("student/doubt_form.html", f=form, token=token or secrets.token_hex(16),
                                       subjects=ds.SUBJECTS), 400
        try:
            with get_db() as conn:
                student = conn.execute("SELECT name, class_name FROM students WHERE id=?", (sid,)).fetchone()
                did, created = ds.create_doubt(conn, sid, subject, chapter, topic, question, image, token)
                if created:
                    ds.notify_teacher_new(conn, did, student["name"], student["class_name"], subject)
                conn.commit()
        except Exception:
            if image:
                ds.destroy_image_quietly(image["public_id"])   # do not leave an orphan image on Cloudinary
            app.logger.exception("doubt insert failed")
            flash("Something went wrong and your doubt was not saved. Please try again.", "error")
            return render_template("student/doubt_form.html", f=form, token=token or secrets.token_hex(16),
                                   subjects=ds.SUBJECTS), 500
        flash("Your doubt was sent to Sir. You can ask the AI about it while you wait.", "success")
        return redirect(url_for("doubt_view", doubt_id=did))
    return render_template("student/doubt_form.html", f={}, token=secrets.token_hex(16), subjects=ds.SUBJECTS)


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
    status = request.args.get("status", "")
    status = status if status in ("pending", "answered") else ""
    page = _int("page")
    with get_db() as conn:
        counts = ds.teacher_counts(conn)
        items, total = ds.teacher_list(conn, subject, class_name, status, page)
        subjects = [r[0] for r in conn.execute("SELECT DISTINCT subject FROM doubts ORDER BY subject COLLATE NOCASE")]
        notes = conn.execute("SELECT * FROM teacher_notifications ORDER BY id DESC LIMIT 8").fetchall()
    pages = max(1, -(-total // ds.PER_PAGE))
    return render_template("teacher/doubts.html", items=items, counts=counts, total=total, page=page, pages=pages,
                           subject=subject, class_name=class_name, status=status, subjects=subjects, notes=notes)


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
            try:
                row, is_update = ds.save_teacher_answer(conn, doubt_id, request.form.get("answer"))
                conn.commit()
            except ds.DoubtError as e:
                flash(str(e), "error")
                return redirect(url_for("teacher_doubt_view", doubt_id=doubt_id))
            ds.notify_student_answer(d["student_id"], doubt_id, d["subject"], is_update)
            log_activity("teacher", "doubt_answer", "doubt #%d" % doubt_id)
            flash("Answer saved. The student has been notified.", "success")
            return redirect(url_for("teacher_doubt_view", doubt_id=doubt_id))
        conn.execute("UPDATE doubts SET teacher_seen=1 WHERE id=?", (doubt_id,))
        conn.execute("UPDATE teacher_notifications SET is_read=1 WHERE link=?", ("/teacher/doubts/%d" % doubt_id,))
        conn.commit()
        _cid, msgs = ds.ai_messages(conn, doubt_id)
    return render_template("teacher/doubt_detail.html", d=d, msgs=msgs)


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
