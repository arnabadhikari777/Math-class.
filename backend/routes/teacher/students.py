# -*- coding: utf-8 -*-
"""Teacher: admit / edit / verify / block / delete students, history, CSV export."""

import csv
import io
from datetime import datetime
from werkzeug.security import generate_password_hash

from flask import Response, abort, flash, redirect, render_template, request, session, url_for

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import teacher_login_required
from backend.services.activity_service import log_activity
from backend.utils.helpers import csv_safe, safe_back
from backend.utils.validators import normalize_mobile, validate_password_strength


@app.route("/teacher/students")
@teacher_login_required
def students_list():
    q = request.args.get("q", "").strip()
    class_filter = request.args.get("class", "").strip()
    status = request.args.get("status", "all")
    with get_db() as conn:
        sql = "SELECT * FROM students WHERE is_archived=?"
        params = [1 if status == "archived" else 0]
        if q:
            sql += " AND (name LIKE ? OR email LIKE ? OR mobile LIKE ? OR roll LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like, like])
        if class_filter:
            sql += " AND class_name=?"
            params.append(class_filter)
        if status == "verified":
            sql += " AND is_verified=1 AND is_blocked=0"
        elif status == "pending":
            sql += " AND is_verified=0"
        elif status == "blocked":
            sql += " AND is_blocked=1"
        sql += " ORDER BY class_name, name"
        students = conn.execute(sql, params).fetchall()
        classes = [r[0] for r in conn.execute("SELECT DISTINCT class_name FROM students ORDER BY class_name").fetchall()]
    return render_template(
        "teacher/students_list.html", students=students, q=q, class_filter=class_filter,
        status=status, classes=classes
    )


@app.route("/teacher/students/admit", methods=["GET", "POST"])
@teacher_login_required
def admit_student():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        class_name_raw = request.form.get("class_name", "").strip()
        roll = request.form.get("roll", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = normalize_mobile(request.form.get("mobile", ""))
        password = request.form.get("password", "") or "student123"
        if not name or not class_name_raw or not email:
            flash("Name, class and email are required.", "error")
            return redirect(url_for("admit_student"))
        from backend.utils.classes import require_class
        try:
            class_name = require_class(class_name_raw, allow_empty=False)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("admit_student"))
        if not mobile or len(mobile) < 10:
            flash("Valid 10-digit mobile is mandatory.", "error")
            return redirect(url_for("admit_student"))
        pw_err = validate_password_strength(password)
        if pw_err:
            flash(pw_err, "error")
            return redirect(url_for("admit_student"))
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM students WHERE email=?", (email,)).fetchone()
            if existing:
                flash("Email already registered.", "error")
                return redirect(url_for("admit_student"))
            conn.execute(
                """INSERT INTO students
                   (name, class_name, roll, email, mobile, password_hash,
                    is_verified, registered_at, admitted_by_teacher)
                   VALUES (?,?,?,?,?,?,1,?,1)""",
                (name, class_name, roll, email, mobile,
                 generate_password_hash(password), datetime.now().isoformat())
            )
            conn.commit()
        log_activity("teacher", "admit_student", f"{name} <{email}>")
        flash(f"Student {name} admitted and verified. Default password: {password}", "success")
        return redirect(url_for("students_list"))
    return render_template("teacher/admit_student.html")


@app.route("/teacher/students/<int:student_id>/edit", methods=["GET", "POST"])
@teacher_login_required
def edit_student(student_id):
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if not student:
            abort(404)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            class_name_raw = request.form.get("class_name", "").strip()
            roll = request.form.get("roll", "").strip()
            mobile = request.form.get("mobile", "").strip()
            notes_internal = request.form.get("notes_internal", "").strip()
            if not name:
                flash("Name is required.", "error")
                return redirect(url_for("edit_student", student_id=student_id))
            if not class_name_raw:
                flash("Class is required.", "error")
                return redirect(url_for("edit_student", student_id=student_id))
            from backend.utils.classes import require_class
            try:
                class_name = require_class(class_name_raw, allow_empty=False)
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("edit_student", student_id=student_id))
            conn.execute(
                """UPDATE students SET name=?, class_name=?, roll=?, mobile=?, notes_internal=?
                   WHERE id=?""",
                (name, class_name, roll or None, mobile or None, notes_internal or None, student_id)
            )
            conn.commit()
            log_activity("teacher", "edit_student", f"id={student_id} name={name}")
            flash(f"Student details updated: {name}", "success")
            return redirect(url_for("students_list"))
    return render_template("teacher/edit_student.html", student=student)


@app.route("/teacher/students/<int:student_id>/verify", methods=["POST"])
@teacher_login_required
def verify_student(student_id):
    with get_db() as conn:
        conn.execute("UPDATE students SET is_verified=1, admitted_by_teacher=1 WHERE id=?", (student_id,))
        conn.commit()
        s = conn.execute("SELECT name FROM students WHERE id=?", (student_id,)).fetchone()
    log_activity("teacher", "verify_student", f"id={student_id} {s['name'] if s else ''}")
    flash("Student verified without OTP.", "success")
    return redirect(safe_back("students_list"))


def student_impact(conn, student_id):
    """Everything a permanent delete of this student would erase (shown before confirming)."""
    one = lambda sql: conn.execute(sql, (student_id,)).fetchone()[0]
    return {
        "exam attempts": one("SELECT COUNT(*) FROM exam_attempts WHERE student_id=?"),
        "results": one("SELECT COUNT(*) FROM results WHERE student_id=?"),
        "answers": one("SELECT COUNT(*) FROM answers WHERE attempt_id IN (SELECT id FROM exam_attempts WHERE student_id=?)"),
        "attendance records": one("SELECT COUNT(*) FROM attendance WHERE student_id=?"),
        "AI conversations": one("SELECT COUNT(*) FROM ai_conversations WHERE student_id=?"),
        "AI messages": one("SELECT COUNT(*) FROM ai_messages WHERE conversation_id IN (SELECT id FROM ai_conversations WHERE student_id=?)"),
        "login history rows": one("SELECT COUNT(*) FROM login_history WHERE student_id=?"),
    }


def purge_student(conn, student_id):
    """Explicit ordered permanent delete (the foreign keys are RESTRICT, so nothing cascades by accident)."""
    att = "SELECT id FROM exam_attempts WHERE student_id=?"
    conn.execute(f"DELETE FROM answers WHERE attempt_id IN ({att}) OR result_id IN (SELECT id FROM results WHERE student_id=?)",
                 (student_id, student_id))
    conn.execute(f"DELETE FROM attempt_questions WHERE attempt_id IN ({att})", (student_id,))
    conn.execute("DELETE FROM results WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM exam_attempts WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM ai_messages WHERE conversation_id IN (SELECT id FROM ai_conversations WHERE student_id=?)", (student_id,))
    conn.execute("DELETE FROM ai_conversations WHERE student_id=?", (student_id,))
    for table in ("attendance", "login_history", "notification_reads", "push_subscriptions"):
        conn.execute(f"DELETE FROM {table} WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM students WHERE id=?", (student_id,))


@app.route("/teacher/students/<int:student_id>/archive", methods=["POST"])
@teacher_login_required
def archive_student(student_id):
    """Archive = hide + lock the account. All results, attempts, attendance and AI history stay."""
    with get_db() as conn:
        s = conn.execute("SELECT name, email FROM students WHERE id=?", (student_id,)).fetchone()
        if not s:
            flash("Student not found.", "error")
            return redirect(url_for("students_list"))
        conn.execute(
            """UPDATE students SET is_archived=1, archived_at=?, archive_reason=?,
                      session_version=COALESCE(session_version,0)+1 WHERE id=?""",
            (datetime.now().isoformat(), (request.form.get("reason") or "")[:200] or None, student_id))
        conn.execute("DELETE FROM push_subscriptions WHERE student_id=?", (student_id,))
        conn.commit()
    log_activity("teacher", "archive_student", f"{s['name']} <{s['email']}> id={student_id}")
    flash(f"{s['name']} archived. History is kept; restore any time from the Archived filter.", "success")
    return redirect(url_for("students_list"))


@app.route("/teacher/students/<int:student_id>/restore", methods=["POST"])
@teacher_login_required
def restore_student(student_id):
    with get_db() as conn:
        s = conn.execute("SELECT name FROM students WHERE id=?", (student_id,)).fetchone()
        if s:
            conn.execute("UPDATE students SET is_archived=0, archived_at=NULL, archive_reason=NULL WHERE id=?",
                         (student_id,))
            conn.commit()
            log_activity("teacher", "restore_student", f"id={student_id} {s['name']}")
            flash(f"{s['name']} restored.", "success")
    return redirect(url_for("students_list"))


@app.route("/teacher/students/<int:student_id>/delete", methods=["GET", "POST"])
@teacher_login_required
def delete_student(student_id):
    """PERMANENT delete: only for an archived student, after showing the impact and typing the email."""
    with get_db() as conn:
        s = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if not s:
            flash("Student not found.", "error")
            return redirect(url_for("students_list"))
        if not s["is_archived"]:
            flash("Archive the student first. Only an archived student can be deleted permanently.", "error")
            return redirect(url_for("students_list"))
        impact = student_impact(conn, student_id)
        if request.method == "GET":
            return render_template("teacher/confirm_delete.html", kind="student", name=s["email"], impact=impact,
                                   action=url_for("delete_student", student_id=student_id),
                                   back=url_for("students_list"), archive=None)
        if request.form.get("confirm_name", "").strip().lower() != s["email"].lower():
            flash("The email you typed does not match - nothing was deleted.", "error")
            return redirect(url_for("delete_student", student_id=student_id))
        try:
            purge_student(conn, student_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        log_activity("teacher", "delete_student_permanent", f"{s['name']} <{s['email']}> impact={impact}")
    flash(f"Student {s['name']} permanently deleted.", "success")
    return redirect(url_for("students_list"))


@app.route("/teacher/students/<int:student_id>/block", methods=["POST"])
@teacher_login_required
def block_student(student_id):
    with get_db() as conn:
        row = conn.execute("SELECT is_blocked, name FROM students WHERE id=?", (student_id,)).fetchone()
        if row:
            new_val = 0 if row["is_blocked"] else 1
            conn.execute("UPDATE students SET is_blocked=? WHERE id=?", (new_val, student_id))
            conn.commit()
            log_activity("teacher", "block_toggle", f"{row['name']} -> {new_val}")
            flash("Student blocked." if new_val else "Student unblocked.", "success")
    return redirect(safe_back("students_list"))


@app.route("/teacher/students/<int:student_id>/login-as", methods=["POST"])
@teacher_login_required
def login_as_student(student_id):
    from backend.services import impersonation_service
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=? AND is_verified=1 AND is_archived=0",
                               (student_id,)).fetchone()
    if not student:
        flash("Student not found, archived, or not verified.", "error")
        return redirect(url_for("students_list"))
    if student["is_blocked"]:
        flash("Cannot login as a blocked student.", "error")
        return redirect(url_for("students_list"))
    ip_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
    log_id = impersonation_service.start(student["id"], ip_addr)
    # Brand-new session (no fixation): it is a STUDENT session that merely remembers it is a teacher view.
    # is_teacher is deliberately NOT set, so teacher pages / APIs reject it.
    session.clear()
    session.permanent = True
    session["student_id"] = student["id"]
    session["student_name"] = student["name"]
    session["student_class"] = student["class_name"]
    session["sv"] = student["session_version"] or 0
    session["impersonating"] = True
    session["impersonation_log_id"] = log_id
    session["impersonation_started"] = datetime.now().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO login_history (student_id, login_time, ip_address, via_teacher) VALUES (?,?,?,1)",
            (student["id"], datetime.now().isoformat(), ip_addr)
        )
        conn.commit()
    log_activity("teacher", "impersonate", f"{student['name']} (id={student_id}) log={log_id}")
    flash(f"You are viewing as {student['name']} (read-only). Use 'Exit to Teacher' to go back.", "success")
    return redirect(url_for("student_dashboard"))


@app.route("/teacher/student/<int:student_id>/history")
@teacher_login_required
def student_history(student_id):
    with get_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if not student:
            abort(404)
        history = conn.execute(
            "SELECT * FROM login_history WHERE student_id=? ORDER BY login_time DESC", (student_id,)
        ).fetchall()
        results = conn.execute(
            """SELECT results.*, tests.title AS test_title FROM results
               JOIN tests ON tests.id = results.test_id
               WHERE results.student_id=? ORDER BY results.submitted_at DESC""",
            (student_id,)
        ).fetchall()
    return render_template("teacher/student_history.html", student=student, history=history, results=results)


@app.route("/teacher/students/export")
@teacher_login_required
def export_students():
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, name, class_name, roll, email, mobile, is_verified, is_blocked, registered_at
               FROM students ORDER BY class_name, name"""
        ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "name", "class", "roll", "email", "mobile", "verified", "blocked", "registered_at"])
    for r in rows:
        writer.writerow([csv_safe(v) for v in (r["id"], r["name"], r["class_name"], r["roll"], r["email"],
                                               r["mobile"], r["is_verified"], r["is_blocked"], r["registered_at"])])
    log_activity("teacher", "export_students", f"count={len(rows)}")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=students_export.csv"}
    )
