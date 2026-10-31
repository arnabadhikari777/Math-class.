# -*- coding: utf-8 -*-
"""Student pages: dashboard, notes, profile, taking tests, results, leaderboard."""

from datetime import datetime
from werkzeug.utils import secure_filename

from flask import abort, flash, redirect, render_template, request, send_from_directory, session, url_for

from backend.config import PASS_PERCENTAGE, UPLOAD_FOLDER
from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import student_login_required
from backend.services.insights_service import build_result_insights, build_student_insights
from backend.utils.helpers import percentage


@app.route("/dashboard")
@student_login_required
def student_dashboard():
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (session["student_id"],)).fetchone()
        tests = conn.execute(
            """SELECT * FROM tests WHERE is_active=1
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
        attempted_test_ids = {r["test_id"] for r in my_results}
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
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=? AND is_active=1", (test_id,)).fetchone()
        if test is None:
            abort(404)
        already = conn.execute(
            "SELECT * FROM results WHERE test_id=? AND student_id=?",
            (test_id, session["student_id"])
        ).fetchone()
        if already:
            flash("You have already submitted this test.", "error")
            return redirect(url_for("student_dashboard"))
        questions = conn.execute("SELECT * FROM questions WHERE test_id=? ORDER BY id", (test_id,)).fetchall()
    if not questions:
        flash("No questions in this test yet.", "error")
        return redirect(url_for("student_dashboard"))
    return render_template("student/take_test.html", test=test, questions=questions)


@app.route("/test/<int:test_id>/submit", methods=["POST"])
@student_login_required
def submit_test(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        already = conn.execute(
            "SELECT * FROM results WHERE test_id=? AND student_id=?",
            (test_id, session["student_id"])
        ).fetchone()
        if already:
            flash("Already submitted.", "error")
            return redirect(url_for("student_dashboard"))
        questions = conn.execute("SELECT * FROM questions WHERE test_id=?", (test_id,)).fetchall()
        score = 0
        total_marks = 0
        answer_rows = []
        for q in questions:
            total_marks += q["marks"]
            selected = request.form.get(f"q_{q['id']}")
            is_correct = 1 if selected == q["correct_option"] else 0
            if is_correct:
                score += q["marks"]
            answer_rows.append((q["id"], selected, is_correct))
        cur = conn.execute(
            """INSERT INTO results (test_id, student_id, score, total_marks, submitted_at)
               VALUES (?,?,?,?,?)""",
            (test_id, session["student_id"], score, total_marks, datetime.now().isoformat())
        )
        result_id = cur.lastrowid
        for qid, selected, is_correct in answer_rows:
            conn.execute(
                "INSERT INTO answers (result_id, question_id, selected_option, is_correct) VALUES (?,?,?,?)",
                (result_id, qid, selected, is_correct)
            )
        conn.commit()
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
        if result is None:
            abort(404)
        answers = conn.execute(
            """SELECT a.*, q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
                      q.correct_option, q.marks
               FROM answers a JOIN questions q ON q.id = a.question_id
               WHERE a.result_id=? ORDER BY q.id""",
            (result["id"],)
        ).fetchall()
        insights = build_result_insights(conn, test, result, answers, {"id": session["student_id"]})
    pct = percentage(result["score"], result["total_marks"])
    passed = pct >= PASS_PERCENTAGE
    return render_template(
        "student/test_result.html", test=test, result=result, answers=answers,
        pct=pct, passed=passed, pass_pct=PASS_PERCENTAGE, insights=insights
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
               WHERE results.test_id=?
               ORDER BY results.score DESC, results.submitted_at ASC""",
            (test_id,)
        ).fetchall()
    return render_template("student/leaderboard.html", test=test, rows=rows)
