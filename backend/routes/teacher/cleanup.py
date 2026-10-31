# -*- coding: utf-8 -*-
"""Teacher: data cleanup tools."""

from datetime import datetime, timedelta

from flask import flash, redirect, render_template, request, url_for

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import teacher_login_required
from backend.services.activity_service import log_activity


@app.route("/teacher/cleanup", methods=["GET"])
@teacher_login_required
def data_cleanup():
    with get_db() as conn:
        def cnt(sql):
            return conn.execute(sql).fetchone()["c"]
        counts = {
            "students": cnt("SELECT COUNT(*) AS c FROM students"),
            "tests": cnt("SELECT COUNT(*) AS c FROM tests"),
            "active_tests": cnt("SELECT COUNT(*) AS c FROM tests WHERE is_active=1"),
            "inactive_tests": cnt("SELECT COUNT(*) AS c FROM tests WHERE is_active=0 AND is_archived=0"),
            "questions": cnt("SELECT COUNT(*) AS c FROM questions"),
            "results": cnt("SELECT COUNT(*) AS c FROM results"),
            "answers": cnt("SELECT COUNT(*) AS c FROM answers"),
            "attempts": cnt("SELECT COUNT(*) AS c FROM exam_attempts"),
            "notes": cnt("SELECT COUNT(*) AS c FROM notes"),
            "announcements": cnt("SELECT COUNT(*) AS c FROM announcements"),
            "contacts": cnt("SELECT COUNT(*) AS c FROM contact_requests"),
            "login_history": cnt("SELECT COUNT(*) AS c FROM login_history"),
            "otp": cnt("SELECT COUNT(*) AS c FROM otp_codes"),
            "activity": cnt("SELECT COUNT(*) AS c FROM activity_log"),
        }
        inactive = conn.execute(
            "SELECT * FROM tests WHERE is_active=0 AND is_archived=0 ORDER BY created_at DESC"
        ).fetchall()
        inactive_tests = []
        for t in inactive:
            q_count = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            r_count = conn.execute(
                "SELECT COUNT(*) AS c FROM results WHERE test_id=?", (t["id"],)
            ).fetchone()["c"]
            inactive_tests.append({
                "id": t["id"],
                "title": t["title"],
                "class_name": t["class_name"],
                "q_count": q_count,
                "r_count": r_count,
            })
    return render_template(
        "teacher/data_cleanup.html", counts=counts, inactive_tests=inactive_tests
    )


@app.route("/teacher/cleanup/action", methods=["POST"])
@teacher_login_required
def cleanup_action():
    action = request.form.get("action", "")
    with get_db() as conn:
        if action in ("delete_inactive_tests", "archive_inactive_tests"):
            # Never a hard delete from a bulk button: inactive tests are ARCHIVED, so every result,
            # answer and exam snapshot survives. Permanent delete is per test, with a typed confirmation.
            now = datetime.now().isoformat()
            n = conn.execute(
                "UPDATE tests SET is_archived=1, archived_at=? WHERE is_active=0 AND is_archived=0", (now,)).rowcount
            conn.commit()
            log_activity("teacher", "cleanup", f"archived {n} inactive tests")
            flash(f"Archived {n} inactive test(s). Results are kept; restore or delete them one by one "
                  f"from All Tests.", "success")

        elif action == "clear_otps":
            n = conn.execute("SELECT COUNT(*) AS c FROM otp_codes").fetchone()["c"]
            conn.execute("DELETE FROM otp_codes")
            conn.commit()
            log_activity("teacher", "cleanup", f"cleared {n} otp codes")
            flash(f"Cleared {n} OTP code(s).", "success")

        elif action == "clear_old_logins":
            cutoff = (datetime.now() - timedelta(days=90)).isoformat()
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM login_history WHERE login_time < ?", (cutoff,)
            ).fetchone()["c"]
            conn.execute("DELETE FROM login_history WHERE login_time < ?", (cutoff,))
            conn.commit()
            log_activity("teacher", "cleanup", f"cleared {n} old logins")
            flash(f"Cleared {n} login history row(s) older than 90 days.", "success")

        elif action == "clear_old_activity":
            cutoff = (datetime.now() - timedelta(days=90)).isoformat()
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM activity_log WHERE created_at < ?", (cutoff,)
            ).fetchone()["c"]
            conn.execute("DELETE FROM activity_log WHERE created_at < ?", (cutoff,))
            conn.commit()
            log_activity("teacher", "cleanup", f"cleared {n} old activity rows")
            flash(f"Cleared {n} activity log row(s) older than 90 days.", "success")

        elif action == "clear_old_contacts":
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM contact_requests WHERE status IN ('replied','closed')"
            ).fetchone()["c"]
            conn.execute(
                "DELETE FROM contact_requests WHERE status IN ('replied','closed')"
            )
            conn.commit()
            log_activity("teacher", "cleanup", f"cleared {n} handled contacts")
            flash(f"Cleared {n} handled contact message(s).", "success")

        elif action == "vacuum":
            conn.commit()
            conn.execute("VACUUM")
            log_activity("teacher", "cleanup", "VACUUM")
            flash("Database optimized (VACUUM completed).", "success")

        else:
            flash("Unknown cleanup action.", "error")

    return redirect(url_for("data_cleanup"))
