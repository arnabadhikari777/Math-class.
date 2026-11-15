# -*- coding: utf-8 -*-
"""Student AI assistant API (prefix /api/v1/ai).

Identity ALWAYS comes from the Math Class session. The browser sends only the
message text, an optional conversation id and an optional test id; the test id
is checked against the database and never trusted for results or answers.
"""
import time
from datetime import datetime

from flask import Blueprint, request, session

from backend.ai import context, provider
from backend.db import get_db
from backend.middleware.rate_limit import _bucket_ok
from backend.utils.api_response import fail, ok, page_meta, paging, student_api
from backend.services import doubt_service as ds
from backend.services import student_service as svc

bp = Blueprint("api_ai", __name__, url_prefix="/api/v1/ai")

MAX_MESSAGE_CHARS = 2000
MAX_BODY_BYTES = 20000
MAX_MESSAGES_PER_CONVERSATION = 200
HISTORY_TURNS = 20


def _now():
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _limit_ok(bucket, limit, window):
    """Same limiter as the rest of the site (backend/middleware/rate_limit.py)."""
    return _bucket_ok(bucket, limit, window)


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
    doubt_id = body.get("doubt_id")
    if doubt_id is not None and (not isinstance(doubt_id, int) or isinstance(doubt_id, bool)):
        return fail("invalid_param", "doubt_id must be a number.")

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
        doubt = None
        # ---- which saved doubt (if any) is this message about?  Always the student's OWN doubts. ----
        if doubt_id is not None:                                   # 1. explicit selection
            doubt = ds.get_own(conn, doubt_id, sid)
            if doubt is None:
                return fail("not_found", "Doubt not found.", 404)
        elif cid is not None:                                      # 2. conversation already belongs to a doubt
            r = conn.execute("SELECT doubt_id FROM ai_conversations WHERE id=? AND student_id=?", (cid, sid)).fetchone()
            if r and r["doubt_id"]:
                doubt = ds.get_own(conn, r["doubt_id"], sid)
        elif ds.looks_like_doubt_reference(text):                  # 3-6. search the student's own doubts
            res = ds.resolve(conn, sid, text)
            if res["status"] == "resolved":
                doubt = res["doubt"]
            elif res["status"] == "ambiguous":
                return ok({"needs_choice": True, "candidates": res["candidates"], "reason": "several"})
            else:
                cands = ds.picker_list(conn, sid, 6)
                if cands:      # unclear reference: never guess, let the student choose
                    return ok({"needs_choice": True, "candidates": cands, "reason": "unclear"})
        if doubt is not None:
            # a doubt always uses ITS OWN conversation; a different conversation id is ignored (no mixing)
            cid = ds.conversation_for(conn, doubt, _now())
            conn.commit()
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
            from backend.middleware.guards import _visible
            if t is not None and _visible(t["class_name"], student["class_name"]):
                test_ref = t["id"]  # reference only; correctness data still comes from results
        locked = context.active_test(conn, sid)
        system = context.build_system_prompt(conn, student, locked)
        if doubt is not None:
            system += ds.prompt_block(doubt)
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
        if doubt is not None:      # AI activity only touches the AI columns, never the teacher's
            ds.update_summary(conn, doubt, answer)
        conn.commit()
    by = _answered_by(used)
    return ok({"conversation_id": cid,
               "doubt": ({"id": doubt["id"], "subject": doubt["subject"]} if doubt is not None else None),
               "test_mode": bool(locked) or False,
               "fallback": bool(prefer and by and by["id"] != prefer),
               "message": {"id": reply_id, "role": "assistant", "content": answer, "created_at": now,
                           "answered_by": by}})


# ---------------------------------------------------------------- saved doubts inside the AI chat
@bp.get("/doubts")
@student_api
def my_doubts():
    """The student's OWN doubts, for the picker inside the AI chat (short previews only)."""
    with get_db() as conn:
        return ok(ds.picker_list(conn, session["student_id"], 20))


@bp.get("/doubts/<int:did>/messages")
@student_api
def doubt_messages(did):
    """Per-doubt AI history. 404 for a doubt that is not this student's."""
    sid = session["student_id"]
    with get_db() as conn:
        d = ds.get_own(conn, did, sid)
        if d is None:
            return fail("not_found", "Doubt not found.", 404)
        cid, rows = ds.ai_messages(conn, did, MAX_MESSAGES_PER_CONVERSATION)
    return ok({"conversation_id": cid, "doubt": {"id": d["id"], "subject": d["subject"]},
               "messages": [_msg(r) for r in rows]})
