# -*- coding: utf-8 -*-
"""Teacher: create / toggle / delete tests and manage their questions."""

from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import teacher_login_required
from backend.services.activity_service import log_activity
from backend.services.notification_service import create_notification


@app.route("/teacher/tests")
@teacher_login_required
def manage_tests():
    with get_db() as conn:
        tests = conn.execute("SELECT * FROM tests ORDER BY created_at DESC").fetchall()
        tests_data = []
        for t in tests:
            q_count = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            r_count = conn.execute(
                "SELECT COUNT(*) AS c FROM results WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            tests_data.append({"test": t, "q_count": q_count, "r_count": r_count})
    return render_template("teacher/manage_tests.html", tests_data=tests_data)


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
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO tests (title, subject, class_name, duration_minutes, is_active, created_at)
                   VALUES (?,?,?,?,1,?)""",
                (title, subject, class_name, duration, datetime.now().isoformat())
            )
            test_id = cur.lastrowid
            conn.commit()
        create_notification(
            title=f"New test: {title}",
            message=f"A new test is available" + (f" for class {class_name}" if class_name else "") + f". Duration: {duration} minutes.",
            notif_type="test",
            class_name=class_name or "",
            link=url_for("student_dashboard"),
        )
        flash("Test created! Now add questions.", "success")
        return redirect(url_for("add_questions", test_id=test_id))
    return render_template("teacher/create_test.html")


@app.route("/teacher/tests/<int:test_id>/questions", methods=["GET", "POST"])
@teacher_login_required
def add_questions(test_id):
    # নতুন Question Studio পেজ (ছবিসহ প্রশ্ন)। নিচের পুরনো কোড অক্ষত রাখা হয়েছে, কিন্তু আর চলে না।
    return redirect(url_for("studio.test_questions", test_id=test_id))
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)

        if request.method == "POST":
            q_texts = request.form.getlist("question_text[]")
            opt_a = request.form.getlist("option_a[]")
            opt_b = request.form.getlist("option_b[]")
            opt_c = request.form.getlist("option_c[]")
            opt_d = request.form.getlist("option_d[]")
            corrects = request.form.getlist("correct_option[]")
            marks_list = request.form.getlist("marks[]")
            image_ids = request.form.getlist("image_id[]")
            added, errors = 0, []

            for i, raw_text in enumerate(q_texts):
                qt = (raw_text or "").strip()
                a = (opt_a[i] if i < len(opt_a) else "").strip()
                b = (opt_b[i] if i < len(opt_b) else "").strip()
                c = (opt_c[i] if i < len(opt_c) else "").strip()
                d = (opt_d[i] if i < len(opt_d) else "").strip()
                co = (corrects[i] if i < len(corrects) else "").strip().upper()
                mk = (marks_list[i] if i < len(marks_list) else "1").strip()
                raw_image_id = image_ids[i] if i < len(image_ids) else ""
                try:
                    image_id = int(raw_image_id) if raw_image_id else None
                except (TypeError, ValueError):
                    image_id = None

                if not all([qt, a, b, c, d]) or co not in ("A", "B", "C", "D"):
                    continue

                image_url = None
                if image_id is not None:
                    image_row = conn.execute(
                        "SELECT url FROM bank_images WHERE id=?", (image_id,)
                    ).fetchone()
                    if image_row is None:
                        errors.append("একটি নির্বাচিত ছবি Library-তে পাওয়া যায়নি; সেই প্রশ্নটি যোগ হয়নি।")
                        continue
                    image_url = image_row["url"]

                try:
                    marks = max(1, min(100, int(mk)))
                except (TypeError, ValueError):
                    marks = 1

                conn.execute(
                    """INSERT INTO questions
                       (test_id, question_text, option_a, option_b, option_c, option_d,
                        correct_option, marks, image_url, bank_image_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (test_id, qt, a, b, c, d, co, marks, image_url, image_id)
                )
                added += 1

            conn.commit()
            if added:
                flash(f"{added} question(s) added successfully!", "success")
            for message in errors:
                flash(message, "error")
            if not added and not errors:
                flash("কোনো বৈধ প্রশ্ন যোগ হয়নি। প্রশ্ন ও চারটি Option পূরণ করুন।", "error")
            return redirect(url_for("add_questions", test_id=test_id))

        questions = conn.execute(
            "SELECT * FROM questions WHERE test_id=? ORDER BY id", (test_id,)
        ).fetchall()
        image_library = conn.execute(
            """SELECT i.id, i.url, i.original_name, c.name AS category_name
               FROM bank_images i JOIN bank_categories c ON c.id=i.category_id
               ORDER BY c.name COLLATE NOCASE, i.id DESC LIMIT 2000"""
        ).fetchall()

    return render_template(
        "teacher/add_questions.html", test=test, questions=questions,
        image_library=[dict(row) for row in image_library]
    )


@app.route("/teacher/questions/delete/<int:question_id>/<int:test_id>", methods=["POST"])
@teacher_login_required
def delete_question(question_id, test_id):
    with get_db() as conn:
        conn.execute("DELETE FROM questions WHERE id=?", (question_id,))
        conn.commit()
    flash("Question deleted.", "success")
    return redirect(url_for("add_questions", test_id=test_id))


@app.route("/teacher/tests/<int:test_id>/toggle", methods=["POST"])
@teacher_login_required
def toggle_test(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test:
            new_status = 0 if test["is_active"] == 1 else 1
            conn.execute("UPDATE tests SET is_active=? WHERE id=?", (new_status, test_id))
            conn.commit()
            if new_status == 1:
                create_notification(
                    title=f"Test active: {test['title']}",
                    message="A test is now available. Open your dashboard to attempt it.",
                    notif_type="test",
                    class_name=test["class_name"] or "",
                    link=url_for("student_dashboard"),
                )
    return redirect(url_for("manage_tests"))


@app.route("/teacher/tests/<int:test_id>/delete", methods=["POST"])
@teacher_login_required
def delete_test(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if not test:
            flash("Test not found.", "error")
            return redirect(url_for("manage_tests"))
        title = test["title"]
        # Cascade: answers -> results, questions, then test
        result_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM results WHERE test_id=?", (test_id,)
        ).fetchall()]
        for rid in result_ids:
            conn.execute("DELETE FROM answers WHERE result_id=?", (rid,))
        conn.execute("DELETE FROM results WHERE test_id=?", (test_id,))
        conn.execute("DELETE FROM questions WHERE test_id=?", (test_id,))
        conn.execute("DELETE FROM tests WHERE id=?", (test_id,))
        conn.commit()
        log_activity("teacher", "delete_test", f"id={test_id} title={title}")
    flash(f'Test "{title}" permanently deleted.', "success")
    return redirect(request.referrer or url_for("manage_tests"))
