# -*- coding: utf-8 -*-
"""Student AI assistant API (prefix /api/v1/ai).

Identity ALWAYS comes from the Math Class session. The browser sends only the
message text, an optional conversation id and an optional test id; the test id
is checked against the database and never trusted for results or answers.
"""
import time
from datetime import datetime

from flask import Blueprint, request, session

from ..ai import context, provider
from ..db import get_db
from ..http import fail, ok, page_meta, paging, student_api
from ..services import student_service as svc

bp = Blueprint("api_ai", __name__, url_prefix="/api/v1/ai")

MAX_MESSAGE_CHARS = 2000
MAX_BODY_BYTES = 20000
MAX_MESSAGES_PER_CONVERSATION = 200
HISTORY_TURNS = 20


def _now():
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _limit_ok(bucket, limit, window):
    """Sliding window on the existing rate_limit_hits table (same as app.py)."""
    now = time.time()
    try:
        with get_db() as conn:
            used = conn.execute("SELECT COUNT(*) FROM rate_limit_hits WHERE bucket=? AND ts>?",
                                (bucket, now - window)).fetchone()[0]
            if used >= limit:
                return False
            conn.execute("INSERT INTO rate_limit_hits (bucket, ts) VALUES (?,?)", (bucket, now))
            conn.commit()
    except Exception:
        return True  # a DB hiccup must not lock everyone out
    return True


def _int_env(name, default):
    import os
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _answered_by(provider_name):
    """Student-facing alias only (never the real provider name)."""
    cid, label = provider.alias_of(provider_name)
    return {"id": cid, "label": label} if cid else None


def _msg(row):
    out = {"id": row["id"], "role": row["role"], "content": row["content"],
           "created_at": row["created_at"]}
    if row["role"] == "assistant":
        out["answered_by"] = _answered_by(row["provider"])
    return out


def _persona_prompt(base, provider_name):
    _cid, label = provider.alias_of(provider_name)
    if not label:
        return base
    return (base + f"\n\nYour name here is {label}. If asked who or what you are, say you are {label}, "
            "the class AI assistant. Do not name any AI company, model or provider.")


def _own_conversation(conn, cid, sid):
    """None unless the conversation exists AND belongs to this student."""
    return conn.execute("SELECT * FROM ai_conversations WHERE id=? AND student_id=?",
                        (cid, sid)).fetchone()


@bp.get("/status")
@student_api
def status():
    with get_db() as conn:
        locked = context.active_test(conn, session["student_id"])
    return ok({"enabled": provider.is_enabled(),
               "choices": provider.student_choices(),
               "test_mode": bool(locked) or False,
               "viewing_as_teacher": bool(session.get("impersonating"))})


@bp.get("/conversations")
@student_api
def conversations():
    page, per_page, offset = paging(20, 50)
    sid = session["student_id"]
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM ai_conversations WHERE student_id=?", (sid,)).fetchone()[0]
        rows = conn.execute(
            "SELECT id, title, test_id, created_at, updated_at FROM ai_conversations "
            "WHERE student_id=? ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            (sid, per_page, offset)).fetchall()
    return ok([dict(r) for r in rows], page_meta(page, per_page, total))


@bp.get("/conversations/<int:cid>/messages")
@student_api
def messages(cid):
    sid = session["student_id"]
    with get_db() as conn:
        if _own_conversation(conn, cid, sid) is None:
            return fail("not_found", "Conversation not found.", 404)
        rows = conn.execute(
            "SELECT id, role, content, provider, created_at FROM ai_messages WHERE conversation_id=? "
            "ORDER BY id ASC LIMIT ?", (cid, MAX_MESSAGES_PER_CONVERSATION)).fetchall()
    return ok([_msg(r) for r in rows])


@bp.post("/chat")
@student_api
def chat():
    sid = session["student_id"]
    if session.get("impersonating"):
        # a teacher 'viewing as' a student must not write chats under the student's name
        return fail("forbidden", "AI chat is not available while viewing as a student.", 403)
    if (request.content_length or 0) > MAX_BODY_BYTES:
        return fail("too_large", "Message is too large.", 413)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return fail("invalid_param", "Send JSON.")
    text = body.get("message")
    if not isinstance(text, str) or not text.strip():
        return fail("invalid_param", "Message cannot be empty.")
    text = text.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        return fail("invalid_param", f"Message is too long (max {MAX_MESSAGE_CHARS} characters).")
    cid = body.get("conversation_id")
    if cid is not None and (not isinstance(cid, int) or isinstance(cid, bool)):
        return fail("invalid_param", "conversation_id must be a number.")
    prefer = body.get("ai")
    if prefer is not None:
        if not isinstance(prefer, str) or prefer not in {c["id"] for c in provider.student_choices()}:
            return fail("invalid_param", "That AI is not available.")
    hint_test = body.get("test_id")
    if hint_test is not None and (not isinstance(hint_test, int) or isinstance(hint_test, bool)):
        return fail("invalid_param", "test_id must be a number.")

    if not provider.is_enabled():
        return fail("ai_not_configured", "AI assistant is not available right now.", 503)
    if not (_limit_ok(f"ai10:{sid}", _int_env("AI_RATE_PER_10MIN", 20), 600)
            and _limit_ok(f"aiday:{sid}", _int_env("AI_RATE_PER_DAY", 150), 86400)):
        return fail("rate_limited", "You are sending messages too fast. Please wait a little.", 429)

    # ---- read phase (connection closed before the slow provider call) ----
    with get_db() as conn:
        student = svc.get_student(conn, sid)
        if student is None or student["is_blocked"]:
            return fail("forbidden", "Account not available.", 403)
        conv = None
        if cid is not None:
            conv = _own_conversation(conn, cid, sid)
            if conv is None:
                return fail("not_found", "Conversation not found.", 404)
            n = conn.execute("SELECT COUNT(*) FROM ai_messages WHERE conversation_id=?", (cid,)).fetchone()[0]
            if n >= MAX_MESSAGES_PER_CONVERSATION:
                return fail("conversation_full", "This chat is full. Please start a new chat.", 409)
        test_ref = None
        if conv is None and hint_test is not None:
            t = conn.execute("SELECT id, class_name FROM tests WHERE id=?", (hint_test,)).fetchone()
            from ..guards import _visible
            if t is not None and _visible(t["class_name"], student["class_name"]):
                test_ref = t["id"]  # reference only; correctness data still comes from results
        locked = context.active_test(conn, sid)
        system = context.build_system_prompt(conn, student, locked)
        history = []
        if conv is not None:
            rows = conn.execute(
                "SELECT role, content FROM ai_messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                (cid, HISTORY_TURNS)).fetchall()
            history = [{"role": r["role"], "text": r["content"]} for r in reversed(rows)]
    turns = history + [{"role": "user", "text": text}]

    try:
        answer, used = provider.ask(lambda name: _persona_prompt(system, name), turns, prefer=prefer)
    except provider.AIError as exc:
        return fail(exc.code, exc.message, exc.status)

    # ---- write phase: both messages saved together, only after a real answer ----
    now = _now()
    with get_db() as conn:
        if conv is None:
            cur = conn.execute(
                "INSERT INTO ai_conversations (student_id, title, test_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?)", (sid, " ".join(text.split())[:60], test_ref, now, now))
            cid = cur.lastrowid
        else:
            conn.execute("UPDATE ai_conversations SET updated_at=? WHERE id=?", (now, cid))
        conn.execute("INSERT INTO ai_messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                     (cid, "user", text, now))
        cur = conn.execute(
            "INSERT INTO ai_messages (conversation_id, role, content, provider, created_at) VALUES (?,?,?,?,?)",
            (cid, "assistant", answer, used, now))
        reply_id = cur.lastrowid
        conn.commit()
    by = _answered_by(used)
    return ok({"conversation_id": cid,
               "test_mode": bool(locked) or False,
               "fallback": bool(prefer and by and by["id"] != prefer),
               "message": {"id": reply_id, "role": "assistant", "content": answer, "created_at": now,
                           "answered_by": by}})
