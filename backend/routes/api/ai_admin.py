# -*- coding: utf-8 -*-
"""Sir/Admin monitoring of student AI chats.

Every route requires a real teacher session (server-side). A teacher who is
'viewing as' a student is refused until they return to the teacher session.
Only name / class / roll are shown for a student (no email, mobile or notes).
"""
from functools import wraps

from flask import Blueprint, redirect, render_template, request, session, url_for

from backend.ai import provider as ai_provider
from backend.db import escape_like, get_db
from backend.utils.api_response import fail, ok, page_meta, paging, teacher_api

bp = Blueprint("api_ai_admin", __name__)


def _teacher_page(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("is_teacher") or session.get("impersonating"):
            return redirect(url_for("teacher_login"))
        return f(*a, **kw)
    return wrapper


@bp.get("/teacher/ai-chats")
@_teacher_page
def page():
    return render_template("teacher/teacher_ai_chats.html")


def _student(row):
    return {"id": row["student_id"], "name": row["name"],
            "class_name": row["class_name"], "roll": row["roll"]}


@bp.post("/api/v1/teacher/ai/test")
@teacher_api
def test_connections():
    """Sir-only: ask every configured AI one tiny question and report which work and why one fails.
    Real provider names and error reasons are shown here only (never to students); keys are never shown."""
    return ok({"results": ai_provider.diagnose()})


@bp.get("/api/v1/teacher/ai/students")
@teacher_api
def students():
    page_, per_page, offset = paging(20, 100)
    q = request.args.get("q", "").strip()[:60]
    only = request.args.get("with_chats", "1") != "0"
    where, params = "WHERE 1=1", []
    if q:
        like = "%" + escape_like(q) + "%"
        where += " AND (s.name LIKE ? ESCAPE '\\' OR s.roll LIKE ? ESCAPE '\\')"
        params += [like, like]
    having = "HAVING conversations > 0" if only else ""
    base = f"""FROM students s LEFT JOIN ai_conversations c ON c.student_id = s.id
               {where} GROUP BY s.id {having}"""
    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM (SELECT s.id, COUNT(c.id) AS conversations {base})",
                             params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT s.id AS student_id, s.name, s.class_name, s.roll,
                       COUNT(c.id) AS conversations, MAX(c.updated_at) AS last_activity
                {base} ORDER BY last_activity DESC, s.name LIMIT ? OFFSET ?""",
            params + [per_page, offset]).fetchall()
    data = [dict(_student(r), conversations=r["conversations"], last_activity=r["last_activity"]) for r in rows]
    return ok(data, page_meta(page_, per_page, total))


@bp.get("/api/v1/teacher/ai/students/<int:sid>/conversations")
@teacher_api
def conversations(sid):
    page_, per_page, offset = paging(20, 100)
    with get_db() as conn:
        st = conn.execute("SELECT id AS student_id, name, class_name, roll FROM students WHERE id=?",
                          (sid,)).fetchone()
        if st is None:
            return fail("not_found", "Student not found.", 404)
        total = conn.execute("SELECT COUNT(*) FROM ai_conversations WHERE student_id=?", (sid,)).fetchone()[0]
        rows = conn.execute(
            """SELECT c.id, c.title, c.test_id, t.title AS test_title, c.created_at, c.updated_at,
                      (SELECT COUNT(*) FROM ai_messages m WHERE m.conversation_id=c.id) AS message_count
               FROM ai_conversations c LEFT JOIN tests t ON t.id=c.test_id
               WHERE c.student_id=? ORDER BY c.updated_at DESC, c.id DESC LIMIT ? OFFSET ?""",
            (sid, per_page, offset)).fetchall()
    return ok({"student": _student(st), "conversations": [dict(r) for r in rows]},
              page_meta(page_, per_page, total))


@bp.get("/api/v1/teacher/ai/conversations/<int:cid>/messages")
@teacher_api
def messages(cid):
    page_, per_page, offset = paging(50, 200)
    with get_db() as conn:
        conv = conn.execute(
            """SELECT c.id, c.title, c.test_id, c.created_at, c.updated_at,
                      s.id AS student_id, s.name, s.class_name, s.roll
               FROM ai_conversations c JOIN students s ON s.id=c.student_id WHERE c.id=?""",
            (cid,)).fetchone()
        if conv is None:
            return fail("not_found", "Conversation not found.", 404)
        test = None
        if conv["test_id"]:
            t = conn.execute("SELECT id, title, subject FROM tests WHERE id=?", (conv["test_id"],)).fetchone()
            if t:
                test = dict(t)
                r = conn.execute("SELECT score, total_marks, submitted_at FROM results "
                                 "WHERE test_id=? AND student_id=?", (t["id"], conv["student_id"])).fetchone()
                test["result"] = dict(r) if r else None
        total = conn.execute("SELECT COUNT(*) FROM ai_messages WHERE conversation_id=?", (cid,)).fetchone()[0]
        rows = conn.execute(
            "SELECT id, role, content, provider, created_at FROM ai_messages "
            "WHERE conversation_id=? ORDER BY id ASC LIMIT ? OFFSET ?", (cid, per_page, offset)).fetchall()
    return ok({"conversation": {"id": conv["id"], "title": conv["title"],
                                "created_at": conv["created_at"], "updated_at": conv["updated_at"]},
               "student": _student(conv), "test": test,
               "messages": [dict(r, provider_label=ai_provider.real_label(r["provider"]) if r["provider"] else None)
                            for r in rows]},
              page_meta(page_, per_page, total))


@bp.get("/api/v1/teacher/ai/search")
@teacher_api
def search():
    q = request.args.get("q", "").strip()[:80]
    if len(q) < 2:
        return fail("invalid_param", "Type at least 2 characters.")
    page_, per_page, offset = paging(20, 100)
    like = "%" + escape_like(q) + "%"
    base = """FROM ai_messages m JOIN ai_conversations c ON c.id=m.conversation_id
              JOIN students s ON s.id=c.student_id WHERE m.content LIKE ? ESCAPE '\\'"""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) " + base, (like,)).fetchone()[0]
        rows = conn.execute(
            "SELECT m.id, m.conversation_id, m.role, m.content, m.created_at, "
            "s.id AS student_id, s.name, s.class_name, s.roll " + base +
            " ORDER BY m.id DESC LIMIT ? OFFSET ?", (like, per_page, offset)).fetchall()
    data = []
    for r in rows:
        i = r["content"].lower().find(q.lower())
        start = max(0, i - 60)
        data.append({"message_id": r["id"], "conversation_id": r["conversation_id"], "role": r["role"],
                     "snippet": r["content"][start:start + 200], "created_at": r["created_at"],
                     "student": _student(r)})
    return ok(data, page_meta(page_, per_page, total))
