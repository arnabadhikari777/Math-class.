# -*- coding: utf-8 -*-
"""Doubt Management: student doubts, teacher answers, notifications, AI context resolution.

Teacher answer and AI answer are stored in different places and never mix:
  * teacher answer  -> doubts.teacher_answer / teacher_answered_at
  * AI answers      -> ai_messages of the doubt's own ai_conversations row (doubt_id)
"""
import json
import re
from datetime import timedelta

from backend.services import image_store, notification_service
from backend.utils.timeutil import IST, now_ist, now_ist_iso, parse

MAX_SUBJECT, MAX_CHAPTER, MAX_TOPIC, MAX_QUESTION, MAX_ANSWER = 60, 120, 120, 4000, 8000
PER_PAGE = 12
SUBJECTS = ["Maths", "Physics", "Chemistry", "Biology", "English", "Other"]


class DoubtError(Exception):
    pass


def clean(text, limit):
    return " ".join(str(text or "").split())[:limit] if limit < 200 else str(text or "").strip()[:limit]


def validate_form(subject, chapter, topic, question):
    subject, chapter, topic = clean(subject, MAX_SUBJECT), clean(chapter, MAX_CHAPTER), clean(topic, MAX_TOPIC)
    question = str(question or "").strip()
    if not subject:
        raise DoubtError("Please choose or type a subject.")
    if len(question) < 5:
        raise DoubtError("Please write your question (at least a few words).")
    if len(question) > MAX_QUESTION:
        raise DoubtError("Your question is too long (maximum %d characters)." % MAX_QUESTION)
    return subject, chapter, topic, question


# ------------------------------------------------------------------ student side
def create_doubt(conn, student_id, subject, chapter, topic, question, image=None, token=""):
    """Insert one doubt. A repeated ``token`` returns the existing doubt (no duplicate)."""
    token = (token or "")[:64]
    if token:
        row = conn.execute("SELECT id FROM doubts WHERE student_id=? AND client_token=?", (student_id, token)).fetchone()
        if row:
            return row["id"], False
    img = image or {}
    meta = json.dumps({k: img.get(k) for k in ("width", "height", "bytes", "format")}) if img else None
    cur = conn.execute(
        """INSERT INTO doubts (student_id, subject, chapter, topic, question, image_url, image_public_id,
                               image_meta, client_token, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (student_id, subject, chapter, topic, question, img.get("url"), img.get("public_id"), meta,
         token or None, now_ist_iso()))
    return cur.lastrowid, True


def notify_teacher_new(conn, doubt_id, student_name, class_name, subject):
    """Saved in the DB (teacher bell). ref_key makes a repeated call a no-op (no duplicates)."""
    conn.execute(
        """INSERT OR IGNORE INTO teacher_notifications (title, message, notif_type, link, ref_key, created_at)
           VALUES (?,?,?,?,?,?)""",
        ("New doubt: %s" % subject, "%s (%s) asked a new doubt." % (student_name, class_name or "-"),
         "doubt", "/teacher/doubts/%d" % doubt_id, "doubt-new:%d" % doubt_id, now_ist_iso()))


def _status(row):
    return "answered" if row["teacher_answered_at"] else "waiting"


def card(row):
    d = dict(row)
    d["status"] = _status(row)
    d["ai_available"] = bool(row["ai_answered_at"])
    return d


def list_for_student(conn, sid, page=1, subject=""):
    where, args = "student_id=?", [sid]
    if subject:
        where += " AND subject=? COLLATE NOCASE"
        args.append(subject)
    total = conn.execute("SELECT COUNT(*) FROM doubts WHERE " + where, args).fetchone()[0]
    page = max(1, page)
    rows = conn.execute(
        "SELECT id, subject, chapter, topic, created_at, teacher_answered_at, ai_answered_at FROM doubts "
        "WHERE %s ORDER BY id DESC LIMIT ? OFFSET ?" % where, args + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    return [card(r) for r in rows], total


def get_own(conn, doubt_id, sid):
    """None unless this doubt belongs to this student (ownership is ALWAYS checked here)."""
    return conn.execute("SELECT * FROM doubts WHERE id=? AND student_id=?", (doubt_id, sid)).fetchone()


def ai_messages(conn, doubt_id, limit=60):
    conv = conn.execute("SELECT id FROM ai_conversations WHERE doubt_id=?", (doubt_id,)).fetchone()
    if not conv:
        return None, []
    rows = conn.execute("SELECT id, role, content, provider, created_at FROM ai_messages WHERE conversation_id=? "
                        "ORDER BY id ASC LIMIT ?", (conv["id"], limit)).fetchall()
    return conv["id"], rows


def delete_doubt(conn, doubt_id):
    """Removes the doubt, its AI history (FK cascade) and, best effort, its Cloudinary image."""
    row = conn.execute("SELECT image_public_id FROM doubts WHERE id=?", (doubt_id,)).fetchone()
    conn.execute("DELETE FROM doubts WHERE id=?", (doubt_id,))
    conn.execute("DELETE FROM teacher_notifications WHERE ref_key=?", ("doubt-new:%d" % doubt_id,))
    conn.execute("DELETE FROM notifications WHERE link=? AND notif_type='doubt'", ("/doubts/%d" % doubt_id,))
    return row["image_public_id"] if row else None


def destroy_image_quietly(public_id):
    if not public_id:
        return
    try:
        image_store.destroy(public_id)
    except Exception:
        pass   # an undeletable image never blocks deleting the doubt


# ------------------------------------------------------------------ teacher side
def teacher_counts(conn):
    r = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN teacher_answered_at IS NULL THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN teacher_answered_at IS NOT NULL THEN 1 ELSE 0 END) AS answered,
                  SUM(CASE WHEN teacher_seen=0 THEN 1 ELSE 0 END) AS new
           FROM doubts""").fetchone()
    return {"total": r["total"] or 0, "pending": r["pending"] or 0, "answered": r["answered"] or 0, "new": r["new"] or 0}


def teacher_list(conn, subject="", class_name="", status="", page=1):
    where, args = ["1=1"], []
    if subject:
        where.append("d.subject=? COLLATE NOCASE"); args.append(subject)
    if class_name:
        where.append("s.class_name=?"); args.append(class_name)
    if status == "pending":
        where.append("d.teacher_answered_at IS NULL")
    elif status == "answered":
        where.append("d.teacher_answered_at IS NOT NULL")
    sql_where = " AND ".join(where)
    total = conn.execute("SELECT COUNT(*) FROM doubts d JOIN students s ON s.id=d.student_id WHERE " + sql_where,
                         args).fetchone()[0]
    rows = conn.execute(
        """SELECT d.id, d.subject, d.chapter, d.topic, d.created_at, d.teacher_answered_at, d.ai_answered_at,
                  d.teacher_seen, substr(d.question,1,140) AS preview, s.name AS student_name, s.class_name
           FROM doubts d JOIN students s ON s.id=d.student_id WHERE %s
           ORDER BY d.id DESC LIMIT ? OFFSET ?""" % sql_where, args + [PER_PAGE, (max(1, page) - 1) * PER_PAGE]).fetchall()
    return [card(r) | {"preview": r["preview"], "student_name": r["student_name"], "class_name": r["class_name"],
                       "teacher_seen": r["teacher_seen"]} for r in rows], total


def save_teacher_answer(conn, doubt_id, answer):
    """Stores the teacher's answer and notifies the student. Returns (doubt_row, is_update).
    Only this function can mark a doubt 'answered' - AI activity never touches these columns."""
    answer = str(answer or "").strip()
    if len(answer) < 2:
        raise DoubtError("Please write an answer first.")
    if len(answer) > MAX_ANSWER:
        raise DoubtError("The answer is too long (maximum %d characters)." % MAX_ANSWER)
    row = conn.execute("SELECT * FROM doubts WHERE id=?", (doubt_id,)).fetchone()
    if row is None:
        raise DoubtError("This doubt no longer exists.")
    now = now_ist_iso()
    is_update = bool(row["teacher_answered_at"])
    if is_update and (row["teacher_answer"] or "") == answer:
        return row, True          # nothing changed -> no second notification
    conn.execute("UPDATE doubts SET teacher_answer=?, teacher_answered_at=COALESCE(teacher_answered_at, ?), "
                 "teacher_updated_at=?, teacher_seen=1 WHERE id=?", (answer, now, now, doubt_id))
    return row, is_update


def notify_student_answer(student_id, doubt_id, subject, is_update):
    # notify_student keeps ONE row per (student, link): an edited answer refreshes it instead of duplicating.
    notification_service.notify_student(
        student_id, "Teacher %s your doubt" % ("updated the answer to" if is_update else "answered"),
        "Your %s doubt has a teacher's answer." % subject, link="/doubts/%d" % doubt_id, notif_type="doubt")


# ------------------------------------------------------------------ AI: conversation + context
def conversation_for(conn, doubt, now):
    """The ONE ai_conversations row of this doubt (created on first use)."""
    row = conn.execute("SELECT * FROM ai_conversations WHERE doubt_id=?", (doubt["id"],)).fetchone()
    if row:
        return row["id"]
    title = ("Doubt #%d · %s" % (doubt["id"], doubt["subject"]))[:60]
    cur = conn.execute("INSERT INTO ai_conversations (student_id, title, test_id, created_at, updated_at, doubt_id) "
                       "VALUES (?,?,?,?,?,?)", (doubt["student_id"], title, None, now, now, doubt["id"]))
    return cur.lastrowid


def prompt_block(doubt):
    """Only THIS doubt is described to the AI - never other doubts or their histories."""
    lines = ["", "The student is discussing ONE saved doubt. Help with this doubt only.",
             "Doubt ID: %d" % doubt["id"], "Subject: %s" % doubt["subject"]]
    if doubt["chapter"]:
        lines.append("Chapter: %s" % doubt["chapter"])
    if doubt["topic"]:
        lines.append("Topic: %s" % doubt["topic"])
    lines.append("Original question:\n%s" % doubt["question"])
    if doubt["image_url"]:
        lines.append("The student attached an image of the question: %s (you may not be able to see it; "
                     "if the text is not enough, ask the student to type what the image shows)." % doubt["image_url"])
    if doubt["ai_summary"]:
        lines.append("Summary of your earlier discussion about this doubt:\n%s" % doubt["ai_summary"])
    lines.append("You are the AI assistant, not the teacher. Never say the teacher answered or approved anything.")
    return "\n".join(lines)


def update_summary(conn, doubt, answer):
    """Short rolling summary (no extra AI call): question + start of the latest answer."""
    q = " ".join(doubt["question"].split())[:200]
    a = " ".join(str(answer).split())[:500]
    conn.execute("UPDATE doubts SET ai_summary=?, ai_answered_at=? WHERE id=?",
                 ("Question: %s | Latest AI answer: %s" % (q, a), now_ist_iso(), doubt["id"]))


# ------------------------------------------------------------------ AI: which doubt does the student mean?
_REF = re.compile(r"\bdoubts?\b|\b(my|that|this|the)\s+(\w+\s+){0,2}(previous|earlier|last|old|older|first|second)\s+"
                  r"(\w+\s+){0,2}question|\b(previous|earlier)\s+(\w+\s+){0,2}question|\bquestion\s+i\s+asked\b", re.I)
_SUBJ_ALIASES = {"math": "maths", "mathematics": "maths", "maths": "maths", "phy": "physics", "physics": "physics",
                 "chem": "chemistry", "chemistry": "chemistry", "bio": "biology", "biology": "biology",
                 "english": "english"}


def looks_like_doubt_reference(text):
    return bool(_REF.search(text or ""))


def _norm_subject(s):
    s = (s or "").strip().lower()
    return _SUBJ_ALIASES.get(s, s)


def summary_row(r):
    return {"id": r["id"], "subject": r["subject"], "chapter": r["chapter"], "topic": r["topic"],
            "preview": " ".join((r["question"] or "").split())[:90], "created_at": r["created_at"]}


def resolve(conn, sid, message, selected_id=None, conversation_id=None):
    """Context resolution in the required order. ALWAYS limited to this student's own doubts.

    Returns {"status": "resolved", "doubt": row} | {"status": "ambiguous", "candidates": [...]}
            | {"status": "none"}     (the AI never guesses a doubt)
    """
    if selected_id:                                                   # 1. explicit selection
        row = get_own(conn, selected_id, sid)
        return {"status": "resolved", "doubt": row} if row else {"status": "none"}
    if conversation_id:                                               # 2. conversation already belongs to a doubt
        r = conn.execute("SELECT doubt_id FROM ai_conversations WHERE id=? AND student_id=?",
                         (conversation_id, sid)).fetchone()
        if r and r["doubt_id"]:
            row = get_own(conn, r["doubt_id"], sid)
            if row:
                return {"status": "resolved", "doubt": row}
    rows = conn.execute("SELECT id, subject, chapter, topic, question, created_at FROM doubts "
                        "WHERE student_id=? ORDER BY id DESC LIMIT 200", (sid,)).fetchall()
    if not rows:
        return {"status": "none"}
    text = " ".join((message or "").lower().split())
    words = set(re.findall(r"[a-z0-9\u0980-\u09ff]{3,}", text))
    narrowed, pool = False, list(rows)
    wanted = {v for k, v in _SUBJ_ALIASES.items() if re.search(r"\b%s\b" % re.escape(k), text)}   # 3. subject
    if wanted:
        sub = [r for r in pool if _norm_subject(r["subject"]) in wanted]
        if sub:
            pool, narrowed = sub, True
    sel = [r for r in pool if (r["chapter"] and r["chapter"].lower() in text) or (r["topic"] and r["topic"].lower() in text)]
    if sel:                                                           # chapter / topic named in the message
        pool, narrowed = sel, True
    if "yesterday" in text or "today" in text or "last week" in text:  # 4. older question by date
        today = now_ist().date()
        if "yesterday" in text:
            lo, hi = today - timedelta(days=1), today - timedelta(days=1)
        elif "today" in text:
            lo = hi = today
        else:
            lo, hi = today - timedelta(days=14), today - timedelta(days=1)
        def in_range(r):
            dt = parse(r["created_at"])
            if dt is None:
                return False
            d = (dt.astimezone(IST) if dt.tzinfo else dt).date()
            return lo <= d <= hi
        dated = [r for r in pool if in_range(r)]
        if not dated:
            return {"status": "none"}
        pool, narrowed = dated, True
    if len(pool) > 1 and narrowed:                                     # words of the question itself
        scored = [(len(words & set(re.findall(r"[a-z0-9\u0980-\u09ff]{4,}", (r["question"] or "").lower()))), r) for r in pool]
        best = max(s for s, _ in scored)
        if best >= 2:
            top = [r for s, r in scored if s == best]
            if len(top) == 1:
                pool = top
    if len(pool) == 1 and (narrowed or len(rows) == 1):
        return {"status": "resolved", "doubt": get_own(conn, pool[0]["id"], sid)}
    if len(pool) > 1:                                                  # 5. several match -> student chooses
        return {"status": "ambiguous", "candidates": [summary_row(r) for r in pool[:6]]}
    return {"status": "none"}                                          # 6. unclear -> never guess


def picker_list(conn, sid, limit=15):
    rows = conn.execute(
        """SELECT d.id, d.subject, d.chapter, d.topic, d.question, d.created_at, d.ai_answered_at
           FROM doubts d WHERE d.student_id=? ORDER BY d.id DESC LIMIT ?""", (sid, limit)).fetchall()
    return [summary_row(r) | {"ai_available": bool(r["ai_answered_at"])} for r in rows]
