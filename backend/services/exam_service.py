# -*- coding: utf-8 -*-
"""Exam engine: versions, per-attempt question snapshots, server-side timer,
one attempt per student, atomic submission and the counted result.

Rules (all enforced HERE, on the server - the browser clock is display only):
  * the first attempt that starts freezes the exam version it uses;
  * editing questions afterwards produces a NEW version for NEW attempts only;
  * every attempt copies its own question snapshot (attempt_questions) and its
    deadline is stored in the database when it starts;
  * a reload / reconnect resumes the same attempt with the server's remaining time;
  * a submission is accepted exactly once (conditional UPDATE inside one write
    transaction); a late one follows the test's late_policy.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from backend.db.m008_exam_system import question_hash

log = logging.getLogger(__name__)

POLICIES_RESULT = ("best", "latest")   # legacy only: kept so old multi-attempt history still resolves
POLICIES_LATE = ("auto_submit", "reject")
OPTIONS = ("A", "B", "C", "D")

# Final score = correct x marks - wrong x negative marks (unanswered = 0). By default a total below 0 is
# shown as it is, so the result page always adds up. Set MARKS_FLOOR_AT_ZERO=1 in .env to stop at 0 instead.
FLOOR_AT_ZERO = os.environ.get("MARKS_FLOOR_AT_ZERO", "0").strip().lower() in ("1", "true", "yes")

# SQL: a result is visible to its student when the test shows results immediately, or the teacher released them.
# Use it in any student-facing query that joins `tests t`.
RELEASED_SQL = "(COALESCE(t.result_release,'immediate')<>'manual' OR t.results_released_at IS NOT NULL)"


def results_visible(test):
    """True when students may see the marks of this test (teacher's result-publication setting)."""
    try:
        return (test["result_release"] or "immediate") != "manual" or bool(test["results_released_at"])
    except (KeyError, IndexError):
        return True


def money(x):
    """Marks rounded to 4 decimals (kills 0.30000000000000004) and never '-0.0'."""
    return round(float(x or 0), 4) + 0.0


def _now():
    return datetime.now()


def timer_enforced():
    """Kill-switch for emergencies only. Default is ON (the safe value)."""
    return os.environ.get("ENFORCE_TEST_TIMER", "1").strip().lower() not in ("0", "false", "no")


def _parse(ts):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- versions
def ensure_version(conn, test):
    """Return the exam_versions id new attempts must use (creating/refreshing it as needed)."""
    qs = conn.execute("SELECT * FROM questions WHERE test_id=? ORDER BY id", (test["id"],)).fetchall()
    h = question_hash(qs)
    neg = float(test["negative_marks"] or 0)
    latest = conn.execute(
        "SELECT * FROM exam_versions WHERE test_id=? ORDER BY version_no DESC LIMIT 1", (test["id"],)).fetchone()
    if latest and latest["content_hash"] == h and float(latest["negative_marks"]) == neg:
        return latest["id"]
    if latest and not latest["is_frozen"]:
        vid = latest["id"]  # nobody has started it yet: the teacher may still change it
        conn.execute("DELETE FROM exam_version_questions WHERE version_id=?", (vid,))
        conn.execute("UPDATE exam_versions SET content_hash=?, negative_marks=? WHERE id=?", (h, neg, vid))
    else:
        nxt = (latest["version_no"] + 1) if latest else 1
        vid = conn.execute(
            """INSERT INTO exam_versions (test_id, version_no, content_hash, negative_marks, is_frozen, created_at)
               VALUES (?,?,?,?,0,?)""", (test["id"], nxt, h, neg, _now().isoformat())).lastrowid
    for pos, q in enumerate(qs, 1):
        info = None
        if q["bank_image_id"]:
            img = conn.execute("SELECT public_id, width, height, bytes, format FROM bank_images WHERE id=?",
                               (q["bank_image_id"],)).fetchone()
            if img:
                info = json.dumps(dict(img))
        conn.execute(
            """INSERT INTO exam_version_questions
               (version_id, position, original_question_id, question_text, image_url, bank_image_id, image_info,
                option_a, option_b, option_c, option_d, correct_option, marks, negative_marks)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid, pos, q["id"], q["question_text"], q["image_url"], q["bank_image_id"], info,
             q["option_a"], q["option_b"], q["option_c"], q["option_d"], q["correct_option"],
             q["marks"] or 1, neg))
    return vid


# --------------------------------------------------------------- attempts
def attempts_used(conn, test_id, student_id):
    return conn.execute("SELECT COUNT(*) FROM exam_attempts WHERE test_id=? AND student_id=?",
                        (test_id, student_id)).fetchone()[0]


def attempts_left(conn, test, student_id):
    """One attempt per student per test (the old max_attempts setting is ignored; old rows are kept).
    Returns 1 while the student has never started the test, otherwise 0."""
    return max(0, 1 - attempts_used(conn, test["id"], student_id))


def has_attempted(conn, test_id, student_id):
    """True when the student already started this test in any way (running, submitted, auto-submitted, rejected)
    or has a stored result for it."""
    if attempts_used(conn, test_id, student_id):
        return True
    return conn.execute("SELECT 1 FROM results WHERE test_id=? AND student_id=?", (test_id, student_id)).fetchone() is not None


def get_running(conn, test_id, student_id):
    return conn.execute("SELECT * FROM exam_attempts WHERE test_id=? AND student_id=? AND status='in_progress'",
                        (test_id, student_id)).fetchone()


def remaining_seconds(attempt, now=None):
    now = now or _now()
    dl = _parse(attempt["deadline_at"])
    if dl is None:
        return 0
    return max(0, int((dl - now).total_seconds()))


def start_or_resume(conn, test, student_id):
    """-> (attempt_row | None, state)  state in 'started','resumed','limit','empty','expired'.
    The whole thing runs in one write transaction so two tabs cannot start two attempts."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        run = get_running(conn, test["id"], student_id)
        if run is not None:
            if _expired(run) and timer_enforced():
                _finalize_expired(conn, test, run)
                conn.execute("COMMIT")
                return None, "expired"
            conn.execute("COMMIT")
            return run, "resumed"
        if has_attempted(conn, test["id"], student_id):   # one attempt only - decided here, on the server
            conn.execute("COMMIT")
            return None, "limit"
        if conn.execute("SELECT COUNT(*) FROM questions WHERE test_id=?", (test["id"],)).fetchone()[0] == 0:
            conn.execute("COMMIT")
            return None, "empty"
        vid = ensure_version(conn, test)
        conn.execute("UPDATE exam_versions SET is_frozen=1, frozen_at=COALESCE(frozen_at,?) WHERE id=?",
                     (_now().isoformat(), vid))
        no = attempts_used(conn, test["id"], student_id) + 1
        now = _now()
        dur = int(test["duration_minutes"] or 0) * 60
        aid = conn.execute(
            """INSERT INTO exam_attempts (test_id, student_id, version_id, attempt_no, status, started_at,
                   deadline_at, duration_seconds, grace_seconds)
               VALUES (?,?,?,?,'in_progress',?,?,?,?)""",
            (test["id"], student_id, vid, no, now.isoformat(), (now + timedelta(seconds=dur)).isoformat(),
             dur, int(test["grace_seconds"] or 0))).lastrowid
        conn.execute(
            """INSERT INTO attempt_questions
               (attempt_id, position, version_id, original_question_id, question_text, image_url, bank_image_id,
                image_info, option_a, option_b, option_c, option_d, correct_option, marks, negative_marks, created_at)
               SELECT ?, position, version_id, original_question_id, question_text, image_url, bank_image_id,
                      image_info, option_a, option_b, option_c, option_d, correct_option, marks, negative_marks, ?
               FROM exam_version_questions WHERE version_id=? ORDER BY position""",
            (aid, now.isoformat(), vid))
        conn.execute("COMMIT")
        return conn.execute("SELECT * FROM exam_attempts WHERE id=?", (aid,)).fetchone(), "started"
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def resume_only(conn, test, student_id):
    """Open the question paper of an attempt that is ALREADY running. Never creates an attempt.
    -> (attempt | None, state)  state in 'resumed', 'none', 'expired'."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        run = get_running(conn, test["id"], student_id)
        if run is None:
            conn.execute("COMMIT")
            return None, "none"
        if _expired(run) and timer_enforced():
            _finalize_expired(conn, test, run)
            conn.execute("COMMIT")
            return None, "expired"
        conn.execute("COMMIT")
        return run, "resumed"
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def attempt_questions(conn, attempt_id):
    return conn.execute("SELECT * FROM attempt_questions WHERE attempt_id=? ORDER BY position",
                        (attempt_id,)).fetchall()


def _expired(attempt):
    """True once the deadline PLUS the teacher's grace window has passed."""
    dl = _parse(attempt["deadline_at"])
    if dl is None:
        return True
    return _now() > dl + timedelta(seconds=int(attempt["grace_seconds"] or 0))


# ----------------------------------------------------------------- drafts
def draft_answers(attempt):
    try:
        data = json.loads(attempt["draft_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def draft_meta(attempt):
    try:
        data = json.loads(attempt["draft_meta"] or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError, KeyError, IndexError):
        return {}


def _merge_delta(attempt, valid_ids, delta):
    """Merge a client delta into the server draft.  Each item is {"v": "A".."D" | null, "t": client_ms}
    (a bare "A" is accepted too and counts as 'chosen just now').  An item older than what the server
    already holds for that question is ignored, so a stale tab / late retry / old local copy can never
    overwrite a newer answer.  null clears the answer (kept as a timestamped tombstone).
    -> (draft, meta, changed)"""
    draft, meta = dict(draft_answers(attempt)), draft_meta(attempt)
    now_ms = int(_now().timestamp() * 1000)
    changed = False
    for k, item in (delta or {}).items():
        try:
            qid = int(str(k).replace("q_", ""))
        except ValueError:
            continue
        if qid not in valid_ids:
            continue
        v, t = (item.get("v"), item.get("t")) if isinstance(item, dict) else (item, None)
        try:
            t = int(t)
        except (TypeError, ValueError):
            t = now_ms
        t = min(t, now_ms + 60000)               # a wrong browser clock cannot lock a question forever
        key = str(qid)
        if t < int(meta.get(key, 0) or 0):
            continue
        if v in OPTIONS:
            draft[key] = v
        elif v in (None, ""):
            draft.pop(key, None)
        else:
            continue
        meta[key] = t
        changed = True
    return draft, meta, changed


def autosave(conn, test_id, student_id, delta):
    """Autosave / heartbeat.  One write transaction -> (state, answers, remaining_seconds)
    state: 'saved' | 'closed' (past the deadline) | 'none' (no running attempt: submitted elsewhere / never started).
    The returned answers are the AUTHORITATIVE server draft - the page re-renders from them."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        att = get_running(conn, test_id, student_id)
        if att is None:
            conn.execute("COMMIT")
            return "none", {}, 0
        dl = _parse(att["deadline_at"])
        if dl is None or _now() > dl:
            conn.execute("COMMIT")                   # past the deadline: no new answers via autosave
            return "closed", draft_answers(att), 0   # (the final submit still has the grace window)
        ids = {r[0] for r in conn.execute("SELECT id FROM attempt_questions WHERE attempt_id=?", (att["id"],))}
        draft, meta, changed = _merge_delta(att, ids, delta)
        if changed:
            conn.execute(
                """UPDATE exam_attempts SET draft_json=?, draft_meta=?, draft_saved_at=?, draft_rev=COALESCE(draft_rev,0)+1
                   WHERE id=? AND student_id=? AND status='in_progress'""",
                (json.dumps(draft), json.dumps(meta), _now().isoformat(), att["id"], student_id))
        conn.execute("COMMIT")
        return "saved", draft, remaining_seconds(att)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def save_draft(conn, attempt_id, student_id, answers):
    """Backwards-compatible wrapper (old payload {'q_12': 'A'}). True when the attempt was still open."""
    att = conn.execute("SELECT test_id FROM exam_attempts WHERE id=? AND student_id=?", (attempt_id, student_id)).fetchone()
    if att is None:
        return False
    state, _a, _r = autosave(conn, att["test_id"], student_id, answers)
    return state == "saved"


# ---------------------------------------------------------------- grading
def _grade(conn, attempt_id, chosen):
    """chosen = {attempt_question_id(int): 'A'..'D'}  ->  (score, total, rows, summary)
    The ONLY place marks are calculated.  It reads the correct option and the marks from the attempt's own
    frozen snapshot - never from the browser, never from the live questions table."""
    rows, total = [], 0.0
    n_ok = n_bad = n_none = 0
    pos = neg_total = 0.0
    for q in attempt_questions(conn, attempt_id):
        sel = chosen.get(q["id"])
        if sel not in OPTIONS:
            sel = None
        marks, neg = float(q["marks"]), float(q["negative_marks"] or 0)
        if sel is None:                       # unanswered: always exactly 0, never negative
            correct, awarded = 0, 0.0
            n_none += 1
        elif sel == q["correct_option"]:
            correct, awarded = 1, marks
            n_ok += 1
            pos += marks
        else:                                 # wrong answer: deducted ONCE, here
            correct, awarded = 0, -neg
            n_bad += 1
            neg_total += neg
        total += marks
        rows.append((q, sel, correct, money(awarded)))
    score = money(pos - neg_total)
    if FLOOR_AT_ZERO:
        score = max(0.0, score)
    summary = {"correct_count": n_ok, "wrong_count": n_bad, "unanswered_count": n_none,
               "positive_marks": money(pos), "negative_marks_total": money(neg_total)}
    return score, money(total), rows, summary


def breakdown_from_answers(answers):
    """Correct / wrong / unanswered and marks earned / deducted, straight from the stored answer rows
    (works for old results too: the rows are the frozen record of what happened)."""
    out = {"correct": 0, "wrong": 0, "unanswered": 0, "positive": 0.0, "negative": 0.0}
    for a in answers:
        if a["selected_option"] in (None, ""):
            out["unanswered"] += 1
        elif a["is_correct"]:
            out["correct"] += 1
        else:
            out["wrong"] += 1
        got = float(a["marks_awarded"] or 0)
        if got > 0:
            out["positive"] += got
        elif got < 0:
            out["negative"] += -got
    out["positive"], out["negative"] = money(out["positive"]), money(out["negative"])
    return out


def _chosen_from(draft, qids):
    """Final answers = the server draft (already merged with the page's last answers) for the attempt's questions."""
    chosen = {}
    for qid in qids:
        v = draft.get(str(qid))
        if v in OPTIONS:
            chosen[qid] = v
    return chosen


def _close(conn, attempt, test, chosen, status, source):
    """Atomically close an attempt. Returns result_id of the counted result, or None if lost the race."""
    score, total, rows, sm = _grade(conn, attempt["id"], chosen)
    now = _now().isoformat()
    cur = conn.execute(
        """UPDATE exam_attempts SET status=?, submitted_at=?, submit_source=?, score=?, total_marks=?,
               correct_count=?, wrong_count=?, unanswered_count=?, positive_marks=?, negative_marks_total=?
           WHERE id=? AND status='in_progress'""",
        (status, now, source, score, total, sm["correct_count"], sm["wrong_count"], sm["unanswered_count"],
         sm["positive_marks"], sm["negative_marks_total"], attempt["id"]))
    if cur.rowcount != 1:
        return None  # somebody else (double click / other tab / expiry sweep) already closed it
    for q, sel, correct, awarded in rows:
        conn.execute(
            """INSERT INTO answers (result_id, attempt_id, question_id, position, question_text, image_url,
                   bank_image_id, option_a, option_b, option_c, option_d, correct_option, marks, negative_marks,
                   selected_option, is_correct, marks_awarded)
               VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt["id"], q["original_question_id"], q["position"], q["question_text"], q["image_url"],
             q["bank_image_id"], q["option_a"], q["option_b"], q["option_c"], q["option_d"], q["correct_option"],
             q["marks"], q["negative_marks"], sel, correct, awarded))
    return refresh_counted_result(conn, test, attempt["student_id"])


def _finalize_expired(conn, test, attempt):
    """Called inside a transaction when an unsubmitted attempt is found past deadline+grace."""
    if test["late_policy"] == "reject":
        conn.execute("""UPDATE exam_attempts SET status='rejected', submitted_at=?, submit_source='server_expiry'
                        WHERE id=? AND status='in_progress'""", (_now().isoformat(), attempt["id"]))
        return None
    qids = [r["id"] for r in attempt_questions(conn, attempt["id"])]
    return _close(conn, attempt, test, _chosen_from(draft_answers(attempt), qids),
                  "auto_submitted", "server_expiry")


def submit(conn, test, student_id, form_answers):
    """-> (state, result_id)   state in 'ok','late_auto','rejected','already','none'.
    form_answers: the page's last answers, {qid: {"v","t"}} (or the legacy {qid: "A"}).  They are MERGED into the
    server draft inside this same transaction (newer wins per question), then the attempt is graded from the draft
    alone - so a last-second answer is never lost, a stale tab can never overwrite a newer answer, and the
    autosave can't race the submission (both are serialised by the write lock)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        att = get_running(conn, test["id"], student_id)
        if att is None:
            conn.execute("COMMIT")
            return "already", None
        qids = [r["id"] for r in attempt_questions(conn, att["id"])]
        if timer_enforced() and _expired(att):
            if test["late_policy"] == "reject":
                conn.execute("""UPDATE exam_attempts SET status='rejected', submitted_at=?,
                                submit_source='late_rejected' WHERE id=? AND status='in_progress'""",
                             (_now().isoformat(), att["id"]))
                conn.execute("COMMIT")
                return "rejected", None
            rid = _close(conn, att, test, _chosen_from(draft_answers(att), qids), "auto_submitted", "late_server_draft")
            conn.execute("COMMIT")
            return "late_auto", rid
        draft, _meta, _changed = _merge_delta(att, set(qids), form_answers)
        timed_out = _now() > (_parse(att["deadline_at"]) or _now())
        rid = _close(conn, att, test, _chosen_from(draft, qids), "submitted", "timer" if timed_out else "student")
        conn.execute("COMMIT")
        return ("ok" if rid else "already"), rid
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def sweep_expired(conn, test_id=None):
    """Close every running attempt that is past deadline+grace (safe to call any time)."""
    sql = "SELECT * FROM exam_attempts WHERE status='in_progress'"
    args = ()
    if test_id is not None:
        sql += " AND test_id=?"
        args = (test_id,)
    n = 0
    for att in conn.execute(sql, args).fetchall():
        if not _expired(att):
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            fresh = conn.execute("SELECT * FROM exam_attempts WHERE id=? AND status='in_progress'",
                                 (att["id"],)).fetchone()
            test = conn.execute("SELECT * FROM tests WHERE id=?", (att["test_id"],)).fetchone()
            if fresh is not None and test is not None:
                _finalize_expired(conn, test, fresh)
                n += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            log.exception("sweep_expired failed for attempt %s", att["id"])
    return n


# ----------------------------------------------------------- counted result
def refresh_counted_result(conn, test, student_id):
    """Pick the attempt that counts (best / latest) and mirror it into `results` + answers.result_id.
    Never touches attempts, answers' snapshots or other students. Returns results.id (or None)."""
    pol = test["result_policy"] if test["result_policy"] in POLICIES_RESULT else "best"
    done = conn.execute(
        """SELECT * FROM exam_attempts WHERE test_id=? AND student_id=? AND status IN ('submitted','auto_submitted')
           ORDER BY id""", (test["id"], student_id)).fetchall()
    if not done:
        return None
    if pol == "latest":
        pick = done[-1]
    else:
        pick = max(done, key=lambda a: (a["score"] or 0, -a["id"]))  # highest; earliest wins ties
    row = conn.execute("SELECT id FROM results WHERE test_id=? AND student_id=?",
                       (test["id"], student_id)).fetchone()
    if row is None:
        rid = conn.execute(
            """INSERT INTO results (test_id, student_id, score, total_marks, submitted_at, attempt_id,
                   correct_count, wrong_count, unanswered_count, positive_marks, negative_marks_total)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (test["id"], student_id, pick["score"], pick["total_marks"], pick["submitted_at"], pick["id"],
             pick["correct_count"], pick["wrong_count"], pick["unanswered_count"], pick["positive_marks"],
             pick["negative_marks_total"])).lastrowid
    else:
        rid = row["id"]
        conn.execute("""UPDATE results SET score=?, total_marks=?, submitted_at=?, attempt_id=?, correct_count=?,
                        wrong_count=?, unanswered_count=?, positive_marks=?, negative_marks_total=? WHERE id=?""",
                     (pick["score"], pick["total_marks"], pick["submitted_at"], pick["id"], pick["correct_count"],
                      pick["wrong_count"], pick["unanswered_count"], pick["positive_marks"],
                      pick["negative_marks_total"], rid))
    conn.execute("UPDATE exam_attempts SET is_counted=CASE WHEN id=? THEN 1 ELSE 0 END "
                 "WHERE test_id=? AND student_id=?", (pick["id"], test["id"], student_id))
    conn.execute("UPDATE answers SET result_id=NULL WHERE result_id=?", (rid,))
    conn.execute("UPDATE answers SET result_id=? WHERE attempt_id=?", (rid, pick["id"]))
    return rid


# ------------------------------------------------------------- read helpers
def result_answers(conn, result_id):
    """The answers of a counted result, straight from its frozen snapshot (never from `questions`)."""
    return conn.execute("SELECT * FROM answers WHERE result_id=? ORDER BY position, id", (result_id,)).fetchall()


def attempt_answers(conn, attempt_id):
    return conn.execute("SELECT * FROM answers WHERE attempt_id=? ORDER BY position, id", (attempt_id,)).fetchall()


def student_attempts(conn, test_id, student_id):
    return conn.execute("SELECT * FROM exam_attempts WHERE test_id=? AND student_id=? ORDER BY attempt_no",
                        (test_id, student_id)).fetchall()
