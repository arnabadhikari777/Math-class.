# -*- coding: utf-8 -*-
"""Builds the insight cards shown on student / result / teacher pages."""

from datetime import datetime, timedelta

from backend.config import PASS_PERCENTAGE
from backend.core import app
from backend.utils.helpers import _parse_dt, _streak_days, percentage
from backend.utils.jee import _next_jee


def build_student_insights(conn, student, my_results, tests, attempted_test_ids):
    """Pure read-only analytics for the student dashboard. Never raises."""
    ins = {
        "pcts": [], "avg": 0, "best": 0, "last": None, "trend": None, "delta": 0,
        "streak": 0, "week_done": 0, "week_goal": 3, "subjects": [], "weakest": None,
        "rank": None, "class_size": 0, "pending": 0, "wrong_total": 0,
        "xp": 0, "level": 1, "level_pct": 0, "xp_to_next": 50,
        "tips": [], "next_jee": None,
    }
    try:
        rows = list(my_results)
        chrono = sorted(rows, key=lambda r: r["submitted_at"])
        pcts = [percentage(r["score"], r["total_marks"]) for r in chrono]
        ins["pcts"] = pcts[-10:]
        if pcts:
            ins["avg"] = round(sum(pcts) / len(pcts), 1)
            ins["best"] = max(pcts)
            ins["last"] = pcts[-1]
        if len(pcts) >= 4:
            half = min(3, len(pcts) // 2)
            recent, before = pcts[-half:], pcts[-2 * half:-half]
            delta = round(sum(recent) / len(recent) - sum(before) / len(before), 1)
            ins["delta"] = delta
            ins["trend"] = "up" if delta >= 3 else "down" if delta <= -3 else "flat"
        ins["streak"] = _streak_days(rows)
        week_ago = datetime.now() - timedelta(days=7)
        ins["week_done"] = sum(1 for r in rows if (_parse_dt(r["submitted_at"]) or datetime.min) >= week_ago)
        ins["pending"] = sum(1 for t in tests if t["id"] not in attempted_test_ids)

        # subject strength (average % per subject + question accuracy)
        subj = {}
        for r in conn.execute(
            """SELECT COALESCE(NULLIF(TRIM(t.subject),''),'General') AS subject,
                      r.score, r.total_marks
               FROM results r JOIN tests t ON t.id = r.test_id
               WHERE r.student_id=?""", (student["id"],)
        ).fetchall():
            d = subj.setdefault(r["subject"], {"name": r["subject"], "sum": 0.0, "n": 0})
            d["sum"] += percentage(r["score"], r["total_marks"])
            d["n"] += 1
        acc = {}
        for r in conn.execute(
            """SELECT COALESCE(NULLIF(TRIM(t.subject),''),'General') AS subject,
                      SUM(a.is_correct) AS ok, COUNT(*) AS total
               FROM answers a
               JOIN results r ON r.id = a.result_id
               JOIN tests t ON t.id = r.test_id
               WHERE r.student_id=? GROUP BY subject""", (student["id"],)
        ).fetchall():
            acc[r["subject"]] = (r["ok"] or 0, r["total"] or 0)
            ins["wrong_total"] += (r["total"] or 0) - (r["ok"] or 0)
        subjects = []
        for d in subj.values():
            ok, total = acc.get(d["name"], (0, 0))
            subjects.append({
                "name": d["name"], "avg": round(d["sum"] / d["n"], 1), "tests": d["n"],
                "accuracy": round(ok * 100 / total) if total else None,
            })
        subjects.sort(key=lambda x: x["avg"])
        ins["subjects"] = subjects
        if len(subjects) >= 2:
            ins["weakest"] = subjects[0]

        # class rank by average percentage
        cls = conn.execute(
            """SELECT r.student_id, AVG(r.score * 100.0 / r.total_marks) AS a
               FROM results r JOIN students s ON s.id = r.student_id
               WHERE s.class_name=? AND r.total_marks > 0 AND s.is_verified=1
               GROUP BY r.student_id ORDER BY a DESC""", (student["class_name"],)
        ).fetchall()
        ins["class_size"] = len(cls)
        for i, row in enumerate(cls, 1):
            if row["student_id"] == student["id"]:
                ins["rank"] = i
                break

        # XP / level (just for motivation)
        correct = sum(r["score"] for r in rows)
        xp = int(correct * 10 + len(rows) * 20)
        level = 1
        while xp >= 50 * level * level:
            level += 1
        floor_xp = 50 * (level - 1) ** 2
        ceil_xp = 50 * level * level
        ins["xp"], ins["level"] = xp, level
        ins["level_pct"] = int((xp - floor_xp) * 100 / max(1, ceil_xp - floor_xp))
        ins["xp_to_next"] = ceil_xp - xp

        # JEE-aware coaching
        nxt = _next_jee()
        ins["next_jee"] = nxt
        tips = []
        if not rows:
            tips.append(("info", "rocket", "Take your first test",
                         "One test unlocks your personal analytics: strengths, weak areas and rank."))
        else:
            if ins["trend"] == "up":
                tips.append(("good", "arrow-trend-up", "You are improving",
                             "Your last scores are %.0f points higher than before. Keep this rhythm." % abs(ins["delta"])))
            elif ins["trend"] == "down":
                tips.append(("warn", "arrow-trend-down", "Scores slipped a little",
                             "Recent tests are %.0f points lower. Re-read your wrong answers before the next test." % abs(ins["delta"])))
            if ins["weakest"] and ins["weakest"]["avg"] < 60:
                w = ins["weakest"]
                tips.append(("warn", "bullseye", "Focus on " + w["name"],
                             "Your average there is %.0f%%, the lowest of your subjects. Practise 10 questions today." % w["avg"]))
            if ins["wrong_total"] >= 5:
                tips.append(("info", "brain", "Mistake bank: %d" % ins["wrong_total"],
                             "Open your latest result and redo every question you got wrong. This is the fastest way to jump marks."))
            if ins["streak"] >= 3:
                tips.append(("good", "fire", "%d-day streak" % ins["streak"], "Do not break the chain. One short test today keeps it alive."))
            elif ins["streak"] == 0 and rows:
                tips.append(("info", "clock", "Start a streak", "Attempt a test today and build a daily habit."))
            if ins["pending"]:
                tips.append(("info", "pen-to-square", "%d test%s waiting" % (ins["pending"], "" if ins["pending"] == 1 else "s"),
                             "New tests are ready for your class. Start with the oldest one."))
        if nxt:
            d = nxt["days_left"]
            if d <= 14:
                tips.append(("warn", "hourglass-half", "%d days to %s" % (d, nxt["short"]),
                             "Time for full-length mocks, error-log revision and 7+ hours of sleep."))
            elif d <= 60:
                tips.append(("info", "hourglass-half", "%d days to %s" % (d, nxt["short"]),
                             "Aim for %d timed tests every week and revise formulae daily." % ins["week_goal"]))
        ins["tips"] = [dict(kind=k, icon=i, title=t, text=x, jee=(i == "hourglass-half")) for k, i, t, x in tips[:5]]
    except Exception:
        app.logger.exception("build_student_insights failed")
    return ins


def build_result_insights(conn, test, result, answers, student):
    """Context for one finished test: rank, percentile, class average, accuracy."""
    info = {"participants": 0, "rank": None, "percentile": None, "class_avg": None,
            "correct": 0, "wrong": 0, "accuracy": 0, "vs_avg": None, "wrong_numbers": [],
            "message": "", "kind": "info"}
    try:
        total_q = len(answers)
        correct = sum(1 for a in answers if a["is_correct"])
        info["correct"], info["wrong"] = correct, total_q - correct
        info["accuracy"] = round(correct * 100 / total_q) if total_q else 0
        info["wrong_numbers"] = [i for i, a in enumerate(answers, 1) if not a["is_correct"]][:30]

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
            "SELECT score, total_marks FROM results WHERE student_id=? AND test_id<>?",
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


def build_teacher_insights(conn):
    """Read-only analytics for the teacher dashboard. Never raises."""
    t = {"avg_pct": 0, "active_7d": 0, "subs_7d": 0, "daily": [], "at_risk": [],
         "top": [], "hard_questions": [], "test_avgs": []}
    try:
        today = datetime.now().date()
        days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        counts = {d: 0 for d in days}
        for r in conn.execute("SELECT submitted_at FROM results").fetchall():
            d = _parse_dt(r["submitted_at"])
            if d and d.date() in counts:
                counts[d.date()] += 1
        t["daily"] = [{"label": d.strftime("%a"), "n": counts[d]} for d in days]
        t["subs_7d"] = sum(counts.values())
        mx = max([x["n"] for x in t["daily"]] + [1])
        for x in t["daily"]:
            x["h"] = max(6, int(x["n"] * 100 / mx)) if x["n"] else 6

        since = (datetime.now() - timedelta(days=7)).isoformat()
        t["active_7d"] = conn.execute(
            "SELECT COUNT(DISTINCT student_id) AS c FROM login_history WHERE login_time>=?", (since,)
        ).fetchone()["c"]

        per = conn.execute(
            """SELECT s.id, s.name, s.class_name, COUNT(r.id) AS n,
                      AVG(r.score * 100.0 / r.total_marks) AS a, MAX(r.submitted_at) AS last_at
               FROM students s JOIN results r ON r.student_id = s.id
               WHERE s.is_verified=1 AND r.total_marks>0 GROUP BY s.id"""
        ).fetchall()
        if per:
            t["avg_pct"] = round(sum(p["a"] for p in per) / len(per), 1)
        cutoff = datetime.now() - timedelta(days=14)
        risk = []
        for p in per:
            last = _parse_dt(p["last_at"])
            reasons = []
            if p["a"] < PASS_PERCENTAGE:
                reasons.append("low average")
            if last and last < cutoff:
                reasons.append("inactive 14d+")
            if reasons:
                risk.append({"id": p["id"], "name": p["name"], "class_name": p["class_name"],
                             "avg": round(p["a"], 1), "reason": ", ".join(reasons)})
        risk.sort(key=lambda x: x["avg"])
        t["at_risk"] = risk[:6]
        top = sorted(per, key=lambda p: (-p["a"], -p["n"]))[:5]
        t["top"] = [{"name": p["name"], "class_name": p["class_name"], "avg": round(p["a"], 1), "n": p["n"]} for p in top]

        hard = conn.execute(
            """SELECT q.id, q.question_text, te.title AS test_title, te.id AS test_id,
                      COUNT(a.id) AS total, SUM(a.is_correct) AS ok
               FROM answers a JOIN questions q ON q.id = a.question_id
               JOIN tests te ON te.id = q.test_id
               GROUP BY q.id HAVING total >= 2
               ORDER BY (ok * 1.0 / total) ASC, total DESC LIMIT 5"""
        ).fetchall()
        t["hard_questions"] = [{
            "text": (h["question_text"] or "")[:90], "test": h["test_title"], "test_id": h["test_id"],
            "acc": round((h["ok"] or 0) * 100 / h["total"]), "n": h["total"]} for h in hard]

        tavgs = conn.execute(
            """SELECT te.id, te.title, COUNT(r.id) AS n, AVG(r.score * 100.0 / r.total_marks) AS a
               FROM tests te JOIN results r ON r.test_id = te.id
               WHERE r.total_marks>0 GROUP BY te.id ORDER BY te.created_at DESC LIMIT 6"""
        ).fetchall()
        t["test_avgs"] = [{"id": x["id"], "title": x["title"], "n": x["n"], "avg": round(x["a"], 1)} for x in tavgs]
    except Exception:
        app.logger.exception("build_teacher_insights failed")
    return t
