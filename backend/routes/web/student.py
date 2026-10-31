# -*- coding: utf-8 -*-
"""Student pages: dashboard, notes, profile, taking tests, results, leaderboard."""

import json
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
               JOIN tests t ON t.id = results.test_id JOIN tests ON tests.id = results.test_id
               WHERE results.student_id=? AND """ + exam_service.RELEASED_SQL + """
               ORDER BY results.submitted_at DESC""",
            (student["id"],)
        ).fetchall()
        # every submitted test (also those whose marks are not released yet) - they must show "Result", not "Start"
        all_done = {r[0] for r in conn.execute("SELECT test_id FROM results WHERE student_id=?", (student["id"],))}
        done_ids = {r[0] for r in conn.execute(
            "SELECT test_id FROM exam_attempts WHERE student_id=? AND status!='in_progress'", (student["id"],))}
        done_ids |= all_done
        attempted_test_ids = {t["id"] for t in tests if t["id"] in done_ids}
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
               JOIN tests t ON t.id = results.test_id JOIN tests ON tests.id = results.test_id
               WHERE results.student_id=? AND """ + exam_service.RELEASED_SQL + """
               ORDER BY results.submitted_at DESC""",
            (session["student_id"],)
        ).fetchall()
    return render_template("student/student_profile.html", student=student, history=history, results=results)


READY_TTL_SECONDS = 15 * 60   # how long "Continue to Confirmation" stays valid


def _ready_key(test_id):
    return f"exam_ready_{test_id}"


def _is_ready(test_id):
    ts = session.get(_ready_key(test_id))
    try:
        return ts is not None and (datetime.now() - datetime.fromisoformat(ts)).total_seconds() <= READY_TTL_SECONDS
    except (TypeError, ValueError):
        return False


def _impersonation_block():
    if session.get("impersonating"):
        # Starting a test is a database write - never allowed while a teacher views as a student.
        flash("A teacher viewing as a student cannot start a test.", "error")
        return redirect(url_for("student_dashboard"))
    return None


def _open_test(conn, test_id):
    """The published, active, non-archived test (class access is enforced by middleware/guards.py)."""
    test = conn.execute(
        "SELECT * FROM tests WHERE id=? AND is_active=1 AND is_archived=0 AND status='published'", (test_id,)).fetchone()
    if test is None:
        abort(404)
    return test


def _exam_facts(conn, test):
    qs = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(marks),0) AS m FROM questions WHERE test_id=?",
                      (test["id"],)).fetchone()
    return {"questions": qs["n"], "total_marks": qs["m"], "negative": float(test["negative_marks"] or 0)}


@app.route("/test/<int:test_id>")
@student_login_required
def take_test(test_id):
    """STEP 1 - examination information. Read-only: opening this page never starts an attempt or a timer."""
    blocked = _impersonation_block()
    if blocked:
        return blocked
    sid = session["student_id"]
    with get_db() as conn:
        test = _open_test(conn, test_id)
        if exam_service.get_running(conn, test_id, sid) is not None:
            return redirect(url_for("test_exam", test_id=test_id))     # resume the running attempt
        if exam_service.has_attempted(conn, test_id, sid):
            flash("You have already taken this test. Each test can be taken only once.", "error")
            return redirect(url_for("test_result", test_id=test_id)
                            if conn.execute("SELECT 1 FROM results WHERE test_id=? AND student_id=?",
                                            (test_id, sid)).fetchone() else url_for("student_dashboard"))
        facts = _exam_facts(conn, test)
    if facts["questions"] == 0:
        flash("No questions in this test yet.", "error")
        return redirect(url_for("student_dashboard"))
    return render_template("student/test_info.html", test=test, facts=facts)


@app.route("/test/<int:test_id>/continue", methods=["POST"])
@student_login_required
def test_continue(test_id):
    """'Continue to Confirmation' - unlocks the final confirmation screen (server-side, 15 min)."""
    blocked = _impersonation_block()
    if blocked:
        return blocked
    with get_db() as conn:
        _open_test(conn, test_id)
    session[_ready_key(test_id)] = datetime.now().isoformat()
    return redirect(url_for("test_confirm", test_id=test_id))


@app.route("/test/<int:test_id>/confirm")
@student_login_required
def test_confirm(test_id):
    """STEP 2 - final 'Are you ready?' screen. Still read-only."""
    blocked = _impersonation_block()
    if blocked:
        return blocked
    sid = session["student_id"]
    with get_db() as conn:
        test = _open_test(conn, test_id)
        if exam_service.get_running(conn, test_id, sid) is not None:
            return redirect(url_for("test_exam", test_id=test_id))
        if exam_service.has_attempted(conn, test_id, sid):
            return redirect(url_for("take_test", test_id=test_id))
        facts = _exam_facts(conn, test)
    if not _is_ready(test_id):
        return redirect(url_for("take_test", test_id=test_id))      # cannot skip the first screen
    return render_template("student/test_confirm.html", test=test, facts=facts)


@app.route("/test/<int:test_id>/start", methods=["POST"])
@student_login_required
def test_start(test_id):
    """The ONE place an attempt is created (inside one write transaction, exactly once)."""
    blocked = _impersonation_block()
    if blocked:
        return blocked
    sid = session["student_id"]
    with get_db() as conn:
        test = _open_test(conn, test_id)                      # 404 for draft / paused / archived / unknown tests
    if not _is_ready(test_id):
        flash("Please review the test information and confirm before starting.", "error")
        return redirect(url_for("take_test", test_id=test_id))
    session.pop(_ready_key(test_id), None)
    with get_db() as conn:
        attempt, state = exam_service.start_or_resume(conn, test, sid)
    if attempt is None:
        msgs = {"limit": "You have already taken this test. Each test can be taken only once.",
                "empty": "No questions in this test yet.",
                "expired": "Your previous attempt ran out of time and was closed."}
        flash(msgs.get(state, "This test cannot be started."), "error")
        return redirect(url_for("student_dashboard"))
    return redirect(url_for("test_exam", test_id=test_id))


@app.route("/test/<int:test_id>/exam")
@student_login_required
def test_exam(test_id):
    """The question paper. Only opens an attempt that is already running (server deadline); never creates one."""
    blocked = _impersonation_block()
    if blocked:
        return blocked
    sid = session["student_id"]
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=? AND is_archived=0", (test_id,)).fetchone()
        if test is None:
            abort(404)
        attempt, state = exam_service.resume_only(conn, test, sid)
        if attempt is None:
            if state == "expired":
                flash("Your time ran out and the exam was submitted automatically.", "error")
                return redirect(url_for("test_result", test_id=test_id))
            return redirect(url_for("take_test", test_id=test_id))
        questions = exam_service.attempt_questions(conn, attempt["id"])
        draft = exam_service.draft_answers(attempt)
        remaining = exam_service.remaining_seconds(attempt)
    return render_template("student/take_test.html", test=test, questions=questions, attempt=attempt,
                           remaining=remaining, total_seconds=attempt["duration_seconds"], draft=draft)


@app.route("/test/<int:test_id>/save", methods=["POST"])
@student_login_required
def save_test_progress(test_id):
    """Autosave + heartbeat.  Body: {"answers": {"q_12": {"v": "A"|null, "t": client_ms}}} (may be empty).
    The reply carries the AUTHORITATIVE server answers and remaining time, so the page always re-syncs to the
    server (never the other way round).  A reload or a dropped connection never loses saved work, and the
    timer is not affected by this call."""
    if session.get("impersonating"):
        return jsonify({"ok": False, "error": "Not allowed while viewing as a student."}), 403
    payload = request.get_json(silent=True) or {}
    delta = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    with get_db() as conn:
        state, answers, remaining = exam_service.autosave(conn, test_id, session["student_id"], delta)
        finished = state == "none" and conn.execute(
            "SELECT 1 FROM results WHERE test_id=? AND student_id=?", (test_id, session["student_id"])).fetchone()
    if state == "none":
        # nothing to save into: the attempt was already submitted (this or another tab) or never started
        return jsonify({"ok": False, "closed": True, "state": "none", "remaining": 0,
                        "redirect": url_for("test_result", test_id=test_id) if finished else url_for("student_dashboard")}), 409
    return jsonify({"ok": state == "saved", "state": state, "closed": state == "closed", "remaining": remaining,
                    "answers": answers, "server_time": datetime.now().isoformat(timespec="seconds")})


def _submitted_answers(form):
    """The page's last answers.  New page: answers_json = {"q_<id>": {"v": "A", "t": ms}}.  Old/no-JS form: q_<id> fields."""
    raw = form.get("answers_json")
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data
    out = {}
    for key, val in form.items():
        if key.startswith("q_"):
            try:
                out[int(key[2:])] = val
            except ValueError:
                pass
    return out


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
        state, _rid = exam_service.submit(conn, test, session["student_id"], _submitted_answers(request.form))
        has_result = conn.execute("SELECT 1 FROM results WHERE test_id=? AND student_id=?",
                                  (test_id, session["student_id"])).fetchone() is not None
    if state == "already":
        # double click / second tab / refresh after submitting: never an error, never a second result
        if has_result:
            return redirect(url_for("test_result", test_id=test_id))
        flash("This test is not running.", "error")
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
    """The student's OWN result (looked up by the logged-in student id, never by a URL parameter)."""
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        result = conn.execute(
            "SELECT * FROM results WHERE test_id=? AND student_id=?",
            (test_id, session["student_id"])
        ).fetchone()
        if result is None or test is None:
            abort(404)
        if not exam_service.results_visible(test):
            # submitted, but the teacher has not released the marks yet: no score, no answers, no rank
            return render_template("student/test_result.html", test=test, result=result, pending=True,
                                   answers=[], pct=0, passed=False, pass_pct=PASS_PERCENTAGE,
                                   insights=None, bd=None)
        answers = exam_service.result_answers(conn, result["id"])
        insights = build_result_insights(conn, test, result, answers, {"id": session["student_id"]})
    bd = exam_service.breakdown_from_answers(answers) if answers else {
        "correct": result["correct_count"] or 0, "wrong": result["wrong_count"] or 0,
        "unanswered": result["unanswered_count"] or 0,
        "positive": result["positive_marks"] or 0, "negative": result["negative_marks_total"] or 0}
    pct = percentage(result["score"], result["total_marks"])
    passed = pct >= PASS_PERCENTAGE
    return render_template(
        "student/test_result.html", test=test, result=result, answers=answers, pending=False,
        pct=pct, passed=passed, pass_pct=PASS_PERCENTAGE, insights=insights, bd=bd,
    )


@app.route("/test/<int:test_id>/leaderboard")
@student_login_required
def test_leaderboard(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        if not exam_service.results_visible(test):
            flash("The leaderboard will be available after your teacher publishes the results.", "error")
            return redirect(url_for("test_result", test_id=test_id))
        rows = conn.execute(
            """SELECT results.score, results.total_marks, results.submitted_at,
                      students.name, students.roll, students.class_name
               FROM results JOIN students ON students.id = results.student_id
               WHERE results.test_id=? AND students.is_archived=0
               ORDER BY results.score DESC, results.submitted_at ASC""",
            (test_id,)
        ).fetchall()
    return render_template("student/leaderboard.html", test=test, rows=rows)
