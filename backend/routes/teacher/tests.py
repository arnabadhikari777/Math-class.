# -*- coding: utf-8 -*-
"""Teacher: create / toggle / delete tests and manage their questions."""

from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import teacher_login_required
from backend.services.activity_service import log_activity
from backend.services import publish_service
from backend.services.notification_service import create_notification


def _read_rules(form):
    """Validated exam rules from a form (timer, late rule, marking, result release). Bad values fall back to safe defaults."""
    def num(name, default, lo, hi, cast=int):
        try:
            v = cast(str(form.get(name, default)).strip() or default)
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))
    lp = form.get("late_policy", "auto_submit")
    rr = form.get("result_release", "immediate")
    return {
        "late_policy": lp if lp in ("auto_submit", "reject") else "auto_submit",
        "result_release": rr if rr in ("immediate", "manual") else "immediate",
        "grace_seconds": num("grace_seconds", 30, 0, 600),
        "negative_marks": num("negative_marks", 0, 0, 100, float),
    }


@app.route("/teacher/tests/<int:test_id>/settings", methods=["GET", "POST"])
@teacher_login_required
def test_settings(test_id):
    """Duration, late rule, grace, negative marking and result release - editable only while a draft.
    (A published test must be unpublished first; running attempts keep the rules they started with.)"""
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        if request.method == "POST":
            if not publish_service.is_editable(test):
                flash("This test is published. Unpublish it first, make your changes, then publish again.", "error")
                return redirect(url_for("test_review", test_id=test_id))
            rules = _read_rules(request.form)
            try:
                duration = int(request.form.get("duration_minutes", test["duration_minutes"]))
            except (TypeError, ValueError):
                duration = 0
            if not 1 <= duration <= 600:
                flash("Duration must be between 1 and 600 minutes.", "error")
                return redirect(url_for("test_settings", test_id=test_id))
            conn.execute(
                """UPDATE tests SET duration_minutes=?, late_policy=?, grace_seconds=?, negative_marks=?,
                   result_release=? WHERE id=?""",
                (duration, rules["late_policy"], rules["grace_seconds"], rules["negative_marks"],
                 rules["result_release"], test_id))
            conn.commit()
            log_activity("teacher", "test_settings", f"id={test_id} {rules} duration={duration}")
            flash("Test rules saved.", "success")
            return redirect(url_for("test_review", test_id=test_id))
    return render_template("teacher/test_settings.html", test=test)


@app.route("/teacher/tests")
@teacher_login_required
def manage_tests():
    with get_db() as conn:
        tests = conn.execute("SELECT * FROM tests WHERE is_archived=0 ORDER BY created_at DESC").fetchall()
        archived = conn.execute("SELECT * FROM tests WHERE is_archived=1 ORDER BY archived_at DESC").fetchall()
        tests_data = []
        for t in tests:
            q_count = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            r_count = conn.execute(
                "SELECT COUNT(*) AS c FROM results WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            tests_data.append({"test": t, "q_count": q_count, "r_count": r_count})
    return render_template("teacher/manage_tests.html", tests_data=tests_data, archived_tests=archived)


@app.route("/teacher/tests/create", methods=["GET", "POST"])
@teacher_login_required
def create_test():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        subject = request.form.get("subject", "").strip()
        class_name_raw = request.form.get("class_name", "").strip()
        duration = request.form.get("duration_minutes", "30").strip()
        if not title:
            flash("Test title is required.", "error")
            return redirect(url_for("create_test"))
        from backend.utils.classes import require_class
        try:
            class_name = require_class(class_name_raw, allow_empty=False)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("create_test"))
        try:
            duration = int(duration)
            if duration < 1:
                raise ValueError
        except ValueError:
            flash("Duration must be a positive number.", "error")
            return redirect(url_for("create_test"))
        rules = _read_rules(request.form)
        with get_db() as conn:
            # A new test is ALWAYS a draft: hidden from students (is_active=0) and no notification.
            cur = conn.execute(
                """INSERT INTO tests (title, subject, class_name, duration_minutes, is_active, created_at,
                                      max_attempts, result_policy, late_policy, grace_seconds, negative_marks,
                                      status, result_release)
                   VALUES (?,?,?,?,0,?,?,?,?,?,?,'draft',?)""",
                (title, subject, class_name, duration, datetime.now().isoformat(),
                 1, "best", rules["late_policy"], rules["grace_seconds"], rules["negative_marks"],
                 rules["result_release"])
            )
            test_id = cur.lastrowid
            conn.commit()
        log_activity("teacher", "create_test", f"id={test_id} status=draft")
        flash("Draft test created. Add your questions, then review and publish it. Students are not notified yet.",
              "success")
        return redirect(url_for("add_questions", test_id=test_id))
    return render_template("teacher/create_test.html")


@app.route("/teacher/tests/<int:test_id>/questions", methods=["GET", "POST"])
@teacher_login_required
def add_questions(test_id):
    """Kept as the stable endpoint name (templates/guards use it); the page is the Question Studio."""
    return redirect(url_for("studio.test_questions", test_id=test_id))


@app.route("/teacher/questions/delete/<int:question_id>/<int:test_id>", methods=["POST"])
@teacher_login_required
def delete_question(question_id, test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        if not publish_service.is_editable(test):
            flash("This test is published. Unpublish it before deleting questions.", "error")
            return redirect(url_for("add_questions", test_id=test_id))
        # scoped to the test: a question id from another test can never be removed through this URL
        conn.execute("DELETE FROM questions WHERE id=? AND test_id=?", (question_id, test_id))
        conn.commit()
    flash("Question deleted.", "success")
    return redirect(url_for("add_questions", test_id=test_id))


@app.route("/teacher/tests/<int:test_id>/toggle", methods=["POST"])
@teacher_login_required
def toggle_test(test_id):
    """Pause / resume a PUBLISHED test. Never notifies; a draft cannot be switched on this way."""
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        if test["status"] != "published":
            flash("Publish the test first - a draft cannot be activated.", "error")
        else:
            conn.execute("UPDATE tests SET is_active=? WHERE id=?", (0 if test["is_active"] == 1 else 1, test_id))
            conn.commit()
    return redirect(url_for("manage_tests"))


@app.route("/teacher/tests/<int:test_id>/review")
@teacher_login_required
def test_review(test_id):
    """Step 3: everything the teacher should check before publishing."""
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        questions = conn.execute("SELECT * FROM questions WHERE test_id=? ORDER BY id", (test_id,)).fetchall()
        issues = publish_service.problems(conn, test)
        total = publish_service.total_marks(conn, test_id)
    return render_template("teacher/test_review.html", test=test, questions=questions, issues=issues,
                           total_marks=total, editable=publish_service.is_editable(test))


@app.route("/teacher/tests/<int:test_id>/publish", methods=["POST"])
@teacher_login_required
def publish_test(test_id):
    """Step 4: the ONLY place the first student notification is sent (once per test, atomically)."""
    with get_db() as conn:
        state, detail = publish_service.publish(conn, test_id)
        notify = False
        if state == "published":
            notify = publish_service.claim_notification(conn, test_id)
        conn.commit()
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
    if state == "missing":
        abort(404)
    if state == "invalid":
        for msg in detail:
            flash(msg, "error")
        return redirect(url_for("test_review", test_id=test_id))
    if state == "already":
        flash("This test is already published.", "success")
        return redirect(url_for("manage_tests"))
    if notify:
        _notify_students(test)
    log_activity("teacher", "publish_test", f"id={test_id} notified={notify}")
    flash("Test published. Students have been notified." if notify else
          "Test published again. Students were not notified a second time.", "success")
    return redirect(url_for("manage_tests"))


@app.route("/teacher/tests/<int:test_id>/unpublish", methods=["POST"])
@teacher_login_required
def unpublish_test(test_id):
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM tests WHERE id=?", (test_id,)).fetchone() is None:
            abort(404)
        changed = publish_service.unpublish(conn, test_id)
        conn.commit()
    log_activity("teacher", "unpublish_test", f"id={test_id}")
    flash("Test moved back to draft. Students can no longer see it; no one was notified." if changed else
          "This test is already a draft.", "success")
    return redirect(url_for("test_review", test_id=test_id))


@app.route("/teacher/tests/<int:test_id>/notify", methods=["POST"])
@teacher_login_required
def renotify_test(test_id):
    """Explicit teacher action: send the 'new test' notification again for a published test."""
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        if test["status"] != "published" or not test["is_active"]:
            flash("Only a published, active test can be announced.", "error")
            return redirect(url_for("manage_tests"))
        publish_service.force_notification_stamp(conn, test_id)
        conn.commit()
    _notify_students(test)
    log_activity("teacher", "renotify_test", f"id={test_id}")
    flash("Students have been notified again.", "success")
    return redirect(url_for("manage_tests"))


def _notify_students(test):
    cls = test["class_name"] or ""
    create_notification(
        title=f"New test: {test['title']}",
        message="A new test is available" + (f" for class {cls}" if cls else "")
                + f". Duration: {test['duration_minutes']} minutes.",
        notif_type="test", class_name=cls, link=url_for("student_dashboard"))


def test_impact(conn, test_id):
    """What a permanent delete would remove (shown BEFORE the teacher confirms)."""
    one = lambda sql: conn.execute(sql, (test_id,)).fetchone()[0]
    return {
        "questions": one("SELECT COUNT(*) FROM questions WHERE test_id=?"),
        "attempts": one("SELECT COUNT(*) FROM exam_attempts WHERE test_id=?"),
        "results": one("SELECT COUNT(*) FROM results WHERE test_id=?"),
        "answers": one("SELECT COUNT(*) FROM answers WHERE attempt_id IN (SELECT id FROM exam_attempts WHERE test_id=?)"),
        "versions": one("SELECT COUNT(*) FROM exam_versions WHERE test_id=?"),
    }


def purge_test(conn, test_id):
    """Explicit, ordered permanent delete of one test and everything that hangs off it."""
    ids = "SELECT id FROM exam_attempts WHERE test_id=?"
    conn.execute(f"DELETE FROM answers WHERE attempt_id IN ({ids}) OR result_id IN (SELECT id FROM results WHERE test_id=?)",
                 (test_id, test_id))
    conn.execute(f"DELETE FROM attempt_questions WHERE attempt_id IN ({ids})", (test_id,))
    conn.execute("DELETE FROM results WHERE test_id=?", (test_id,))
    conn.execute("DELETE FROM exam_attempts WHERE test_id=?", (test_id,))
    conn.execute("DELETE FROM exam_version_questions WHERE version_id IN (SELECT id FROM exam_versions WHERE test_id=?)", (test_id,))
    conn.execute("DELETE FROM exam_versions WHERE test_id=?", (test_id,))
    conn.execute("DELETE FROM questions WHERE test_id=?", (test_id,))
    conn.execute("UPDATE ai_conversations SET test_id=NULL WHERE test_id=?", (test_id,))
    conn.execute("DELETE FROM tests WHERE id=?", (test_id,))


@app.route("/teacher/tests/<int:test_id>/archive", methods=["POST"])
@teacher_login_required
def archive_test(test_id):
    """Soft delete: the test disappears for students but every result, answer and snapshot is kept."""
    with get_db() as conn:
        conn.execute("UPDATE tests SET is_archived=1, is_active=0, archived_at=? WHERE id=?",
                     (datetime.now().isoformat(), test_id))
        conn.commit()
    log_activity("teacher", "archive_test", f"id={test_id}")
    flash("Test archived. Results and answers are kept; you can restore it any time.", "success")
    return redirect(url_for("manage_tests"))


@app.route("/teacher/tests/<int:test_id>/restore", methods=["POST"])
@teacher_login_required
def restore_test(test_id):
    with get_db() as conn:
        conn.execute("UPDATE tests SET is_archived=0, archived_at=NULL WHERE id=?", (test_id,))
        conn.commit()
    log_activity("teacher", "restore_test", f"id={test_id}")
    flash("Test restored (it is still inactive until you activate it).", "success")
    return redirect(url_for("manage_tests"))


@app.route("/teacher/tests/<int:test_id>/delete", methods=["GET", "POST"])
@teacher_login_required
def delete_test(test_id):
    """PERMANENT delete. GET shows exactly what will be lost; POST needs the test title typed in."""
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if not test:
            flash("Test not found.", "error")
            return redirect(url_for("manage_tests"))
        impact = test_impact(conn, test_id)
        if request.method == "GET":
            return render_template("teacher/confirm_delete.html", kind="test", name=test["title"], impact=impact,
                                   action=url_for("delete_test", test_id=test_id),
                                   back=url_for("manage_tests"), archive=url_for("archive_test", test_id=test_id))
        if request.form.get("confirm_name", "").strip() != test["title"]:
            flash("The name you typed does not match - nothing was deleted.", "error")
            return redirect(url_for("delete_test", test_id=test_id))
        title = test["title"]
        try:
            purge_test(conn, test_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        log_activity("teacher", "delete_test", f"id={test_id} title={title} impact={impact}")
    flash(f'Test "{title}" permanently deleted.', "success")
    return redirect(url_for("manage_tests"))
