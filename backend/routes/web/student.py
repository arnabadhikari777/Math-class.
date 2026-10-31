# -*- coding: utf-8 -*-
"""Student pages: dashboard, notes, profile, taking tests, results, leaderboard."""

from datetime import datetime
from werkzeug.utils import secure_filename

from flask import abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from backend.config import PASS_PERCENTAGE, UPLOAD_FOLDER
from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import student_login_required
from backend.services import exam_service
from backend.services.insights_service import build_result_insights, build_student_insights
from backend.utils.helpers import percentage


@app.route("/dashboard")
@student_login_required
def student_dashboard():
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (session["student_id"],)).fetchone()
        tests = conn.execute(
            """SELECT * FROM tests WHERE is_active=1 AND is_archived=0
               AND (class_name=? OR class_name='' OR class_name IS NULL)
               ORDER BY created_at DESC""",
            (student["class_name"],)
        ).fetchall()
        my_results = conn.execute(
            """SELECT results.*, tests.title AS test_title FROM results
               JOIN tests ON tests.id = results.test_id
               WHERE results.student_id=? ORDER BY results.submitted_at DESC""",
            (student["id"],)
        ).fetchall()
        attempted_test_ids = {t["id"] for t in tests
                              if any(r["test_id"] == t["id"] for r in my_results)
                              and exam_service.attempts_left(conn, t, student["id"]) == 0}
        announcements = conn.execute(
            """SELECT * FROM announcements WHERE is_active=1
               AND (class_name=? OR class_name='' OR class_name IS NULL)
               ORDER BY created_at DESC LIMIT 5""",
            (student["class_name"],)
        ).fetchall()
        total_attempted = len(my_results)
        avg_pct = 0
        if total_attempted:
            avg_pct = round(sum(percentage(r["score"], r["total_marks"]) for r in my_results) / total_attempted, 1)
        insights = build_student_insights(conn, student, my_results, tests, attempted_test_ids)
    return render_template(
        "student/student_dashboard.html",
        student=student, tests=tests, my_results=my_results,
        attempted_test_ids=attempted_test_ids, announcements=announcements,
        total_attempted=total_attempted, avg_pct=avg_pct, insights=insights
    )


@app.route("/notes")
@student_login_required
def notes_list():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (session["student_id"],)).fetchone()
        if q:
            notes = conn.execute(
                """SELECT * FROM notes WHERE (class_name=? OR class_name='' OR class_name IS NULL)
                   AND (title LIKE ? OR subject LIKE ?) ORDER BY uploaded_at DESC""",
                (student["class_name"], f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            notes = conn.execute(
                """SELECT * FROM notes WHERE class_name=? OR class_name='' OR class_name IS NULL
                   ORDER BY uploaded_at DESC""",
                (student["class_name"],)
            ).fetchall()
    return render_template("student/notes.html", notes=notes, q=q)


@app.route("/notes/download/<int:note_id>")
@student_login_required
def download_note(note_id):
    with get_db() as conn:
        note = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if note is None:
        abort(404)
    return send_from_directory(
        UPLOAD_FOLDER, note["filename"], as_attachment=True,
        download_name=secure_filename(note["title"]) + ".pdf"
    )


@app.route("/profile")
@student_login_required
def student_profile():
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (session["student_id"],)).fetchone()
        history = conn.execute(
            "SELECT * FROM login_history WHERE student_id=? ORDER BY login_time DESC LIMIT 20",
            (session["student_id"],)
        ).fetchall()
        results = conn.execute(
            """SELECT results.*, tests.title AS test_title FROM results
               JOIN tests ON tests.id = results.test_id
               WHERE results.student_id=? ORDER BY results.submitted_at DESC""",
            (session["student_id"],)
        ).fetchall()
    return render_template("student/student_profile.html", student=student, history=history, results=results)


@app.route("/test/<int:test_id>")
@student_login_required
def take_test(test_id):
    if session.get("impersonating"):
        # Opening a test STARTS a timed attempt (a database write) - never allowed while viewing as a student.
        flash("A teacher viewing as a student cannot start a test.", "error")
        return redirect(url_for("student_dashboard"))
    sid = session["student_id"]
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=? AND is_active=1 AND is_archived=0", (test_id,)).fetchone()
        if test is None:
            abort(404)
        attempt, state = exam_service.start_or_resume(conn, test, sid)
        if attempt is None:
            msgs = {"limit": "You have used all attempts for this test.",
                    "empty": "No questions in this test yet.",
                    "expired": "Your previous attempt ran out of time and was closed."}
            flash(msgs.get(state, "This test cannot be started."), "error")
            return redirect(url_for("student_dashboard"))
        questions = exam_service.attempt_questions(conn, attempt["id"])
        draft = exam_service.draft_answers(attempt)
        remaining = exam_service.remaining_seconds(attempt)
        left = exam_service.attempts_left(conn, test, sid)
    return render_template("student/take_test.html", test=test, questions=questions, attempt=attempt,
                           remaining=remaining, total_seconds=attempt["duration_seconds"], draft=draft,
                           attempts_left=left)


@app.route("/test/<int:test_id>/save", methods=["POST"])
@student_login_required
def save_test_progress(test_id):
    """Autosave of the chosen options. The server keeps them, so a reload or a dropped
    connection never loses work, and the timer is NOT affected by this call."""
    if session.get("impersonating"):
        return jsonify({"ok": False, "error": "Not allowed while viewing as a student."}), 403
    payload = request.get_json(silent=True) or {}
    with get_db() as conn:
        run = exam_service.get_running(conn, test_id, session["student_id"])
        if run is None:
            return jsonify({"ok": False, "error": "No running attempt."}), 409
        ok = exam_service.save_draft(conn, run["id"], session["student_id"], payload.get("answers") or {})
        conn.commit()
        return jsonify({"ok": bool(ok), "remaining": exam_service.remaining_seconds(run)})


@app.route("/test/<int:test_id>/submit", methods=["POST"])
@student_login_required
def submit_test(test_id):
    if session.get("impersonating"):
        flash("A teacher viewing as a student cannot submit a test.", "error")
        return redirect(url_for("student_dashboard"))
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        form = {}
        for key, val in request.form.items():
            if key.startswith("q_"):
                try:
                    form[int(key[2:])] = val
                except ValueError:
                    pass
        state, _rid = exam_service.submit(conn, test, session["student_id"], form)
    if state == "already":
        flash("Already submitted.", "error")
        return redirect(url_for("student_dashboard"))
    if state == "rejected":
        flash("Time was over, so this submission was not accepted.", "error")
        return redirect(url_for("student_dashboard"))
    if state == "late_auto":
        flash("Time was over - your last saved answers were submitted automatically.", "error")
    return redirect(url_for("test_result", test_id=test_id))


@app.route("/test/<int:test_id>/result")
@student_login_required
def test_result(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        result = conn.execute(
            "SELECT * FROM results WHERE test_id=? AND student_id=?",
            (test_id, session["student_id"])
        ).fetchone()
        if result is None or test is None:
            abort(404)
        answers = exam_service.result_answers(conn, result["id"])
        attempts = exam_service.student_attempts(conn, test_id, session["student_id"])
        insights = build_result_insights(conn, test, result, answers, {"id": session["student_id"]})
        left = exam_service.attempts_left(conn, test, session["student_id"])
    pct = percentage(result["score"], result["total_marks"])
    passed = pct >= PASS_PERCENTAGE
    return render_template(
        "student/test_result.html", test=test, result=result, answers=answers,
        pct=pct, passed=passed, pass_pct=PASS_PERCENTAGE, insights=insights,
        attempts=attempts, attempts_left=left
    )


@app.route("/test/<int:test_id>/leaderboard")
@student_login_required
def test_leaderboard(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        rows = conn.execute(
            """SELECT results.score, results.total_marks, results.submitted_at,
                      students.name, students.roll, students.class_name
               FROM results JOIN students ON students.id = results.student_id
               WHERE results.test_id=? AND students.is_archived=0
               ORDER BY results.score DESC, results.submitted_at ASC""",
            (test_id,)
        ).fetchall()
    return render_template("student/leaderboard.html", test=test, rows=rows)
