# -*- coding: utf-8 -*-
"""Insights for the student result-review page (the dashboards no longer compute analytics)."""

from datetime import datetime, timedelta

from backend.config import PASS_PERCENTAGE
from backend.core import app
from backend.utils.helpers import _parse_dt, percentage


def build_result_insights(conn, test, result, answers, student):
    """Context for one finished test: rank, percentile, class average, accuracy."""
    info = {"participants": 0, "rank": None, "percentile": None, "class_avg": None,
            "correct": 0, "wrong": 0, "accuracy": 0, "vs_avg": None, "wrong_numbers": [],
            "message": "", "kind": "info"}
    try:
        total_q = len(answers)
        correct = sum(1 for a in answers if a["is_correct"])
        wrong = sum(1 for a in answers if a["selected_option"] and not a["is_correct"])
        info["correct"], info["wrong"], info["unanswered"] = correct, wrong, total_q - correct - wrong
        info["accuracy"] = round(correct * 100 / total_q) if total_q else 0
        info["wrong_numbers"] = [i for i, a in enumerate(answers, 1) if a["selected_option"] and not a["is_correct"]][:30]

        rows = conn.execute(
            """SELECT student_id, score, total_marks, submitted_at FROM results
               WHERE test_id=? ORDER BY score DESC, submitted_at ASC""", (test["id"],)
        ).fetchall()
        n = len(rows)
        info["participants"] = n
        if n:
            info["class_avg"] = round(sum(percentage(r["score"], r["total_marks"]) for r in rows) / n, 1)
            for i, r in enumerate(rows, 1):
                if r["student_id"] == student["id"]:
                    info["rank"] = i
                    break
            lower = sum(1 for r in rows if r["score"] < result["score"])
            info["percentile"] = round(lower * 100 / n) if n > 1 else None
        pct = percentage(result["score"], result["total_marks"])
        if info["class_avg"] is not None and n > 1:
            info["vs_avg"] = round(pct - info["class_avg"], 1)

        prev = conn.execute(
            """SELECT r.score, r.total_marks FROM results r JOIN tests t ON t.id=r.test_id
               WHERE r.student_id=? AND r.test_id<>?
                 AND (COALESCE(t.result_release,'immediate')<>'manual' OR t.results_released_at IS NOT NULL)""",
            (student["id"], test["id"])
        ).fetchall()
        prev_avg = (sum(percentage(r["score"], r["total_marks"]) for r in prev) / len(prev)) if prev else None

        if pct >= 85:
            info["kind"], info["message"] = "good", "Outstanding. Your accuracy is exam-ready, so push for speed next."
        elif pct >= 60:
            info["kind"], info["message"] = "good", "Solid work. Fix the few mistakes below and you will cross 85%."
        elif pct >= PASS_PERCENTAGE:
            info["kind"], info["message"] = "info", "You passed. Review each wrong answer and note the concept behind it."
        else:
            info["kind"], info["message"] = "warn", "Do not worry. Study the explanations of the wrong answers, then retake similar questions."
        if prev_avg is not None:
            diff = round(pct - prev_avg, 1)
            if diff >= 5:
                info["message"] += " That is %.0f points above your usual average." % diff
            elif diff <= -5:
                info["message"] += " That is %.0f points below your usual average." % abs(diff)
    except Exception:
        app.logger.exception("build_result_insights failed")
    return info
