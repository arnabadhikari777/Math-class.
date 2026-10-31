# -*- coding: utf-8 -*-
"""Teacher: test results, a student's answer sheet, result PDF."""

from werkzeug.utils import secure_filename

from flask import Response, abort, flash, redirect, render_template, url_for

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import teacher_login_required
from backend.services.pdf_service import build_student_result_pdf
from backend.utils.helpers import percentage


@app.route("/teacher/tests/<int:test_id>/results")
@teacher_login_required
def test_results_view(test_id):

    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        results = conn.execute(
            """SELECT results.*, students.name, students.class_name, students.roll, students.mobile
               FROM results JOIN students ON students.id = results.student_id
               WHERE results.test_id=? ORDER BY results.score DESC""",
            (test_id,)
        ).fetchall()
    return render_template("teacher/test_results_view.html", test=test, results=results)


@app.route("/teacher/tests/<int:test_id>/result/<int:result_id>")
@teacher_login_required
def teacher_student_result(test_id, result_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        result = conn.execute("SELECT * FROM results WHERE id=? AND test_id=?", (result_id, test_id)).fetchone()
        if test is None or result is None:
            abort(404)
        student = conn.execute("SELECT * FROM students WHERE id=?", (result["student_id"],)).fetchone()
        answers = conn.execute(
            """SELECT a.* FROM answers a WHERE a.result_id=? ORDER BY a.position, a.id""",
            (result_id,)
        ).fetchall()
    pct = percentage(result["score"], result["total_marks"])
    return render_template(
        "teacher/teacher_student_result.html",
        test=test, student=student, result=result, answers=answers, pct=pct
    )


@app.route("/teacher/tests/<int:test_id>/result/<int:result_id>/pdf")
@teacher_login_required
def download_student_result_pdf(test_id, result_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        result = conn.execute("SELECT * FROM results WHERE id=? AND test_id=?", (result_id, test_id)).fetchone()
        if test is None or result is None:
            abort(404)
        student = conn.execute("SELECT * FROM students WHERE id=?", (result["student_id"],)).fetchone()
        answers = conn.execute(
            """SELECT a.* FROM answers a WHERE a.result_id=? ORDER BY a.position, a.id""",
            (result_id,)
        ).fetchall()
    pdf_bytes = build_student_result_pdf(test, student, result, answers)
    if not pdf_bytes:
        flash("PDF generation is not available on this server.", "error")
        return redirect(url_for("teacher_student_result", test_id=test_id, result_id=result_id))
    safe_name = secure_filename(f"{student['name']}_{test['title']}_answers.pdf") or "student_answers.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'}
    )
