# -*- coding: utf-8 -*-
"""Chat pages (/chat, /chat/<id>), the privacy Settings page (/settings) and the JSON API (/api/chat/...).

All the real work is in backend/services/chat_service.py. A teacher who is "viewing as" a student
gets a READ-ONLY chat: nothing is marked delivered/seen, no heartbeat, nothing can be sent.
"""
import time

from flask import abort, flash, redirect, render_template, request, session, url_for

from backend.core import app
from backend.middleware.auth import student_login_required
from backend.services import chat_service as chat
from backend.utils.api_response import fail, ok, student_api

HEARTBEAT_EVERY = 20   # seconds between "I am online" database writes per student


def _readonly():
    return bool(session.get("impersonating"))


def _wrap(fn):
    """Run a service call and turn ChatError into the project's JSON error envelope."""
    try:
        return fn()
    except chat.ChatError as exc:
        return fail(exc.code, exc.message, exc.status)


@app.before_request
def chat_heartbeat():
    """Every page / poll of a logged-in student counts as 'online' (one cheap write per ~20 s)."""
    sid = session.get("student_id")
    if not sid or _readonly() or request.endpoint in (None, "static", "manifest", "service_worker"):
        return None
    last = session.get("_chat_ls") or 0
    if time.time() - last < HEARTBEAT_EVERY:
        return None
    session["_chat_ls"] = time.time()
    try:
        chat.touch(sid)
    except Exception:            # presence must never break a page
        app.logger.exception("chat heartbeat failed")
    return None


# ------------------------------------------------------------------ pages
@app.route("/chat")
@student_login_required
def chat_home():
    return render_template("student/chat.html", conv_id=None, readonly=_readonly(),
                           tab=request.args.get("tab", "chats"))


@app.route("/chat/<int:cid>")
@student_login_required
def chat_room(cid):
    if not chat.can_open(session["student_id"], cid):
        abort(404)
    return render_template("student/chat.html", conv_id=cid, readonly=_readonly(), tab="chats")


@app.route("/settings")
@student_login_required
def student_settings():
    return render_template("student/settings.html", chat_public=chat.get_privacy(session["student_id"]),
                           readonly=_readonly())


@app.route("/settings/chat-privacy", methods=["POST"])
@student_login_required
def student_settings_chat_privacy():
    choice = request.form.get("privacy", "")
    if choice not in ("public", "private"):
        flash("Please choose Public or Private.", "error")
    else:
        chat.set_privacy(session["student_id"], choice == "public")
        flash("Your chat account is now %s." % choice, "success")
    return redirect(url_for("student_settings"))


# ------------------------------------------------------------------ JSON API
@app.route("/api/chat/summary")
@student_api
def api_chat_summary():
    return ok(chat.summary(session["student_id"], _readonly()))


@app.route("/api/chat/chats")
@student_api
def api_chat_chats():
    me = session["student_id"]
    items = chat.chat_list(me, _readonly())
    return ok({"chats": items, "requests": chat.incoming_requests(me), "privacy_public": chat.get_privacy(me),
               "readonly": _readonly()})


@app.route("/api/chat/people")
@student_api
def api_chat_people():
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    people, has_next = chat.list_people(session["student_id"], request.args.get("q", ""), page)
    return ok({"people": people, "has_next": has_next})


def _write_guard():
    return fail("readonly", "Read-only while viewing as a student.", 403) if _readonly() else None


@app.route("/api/chat/start", methods=["POST"])
@student_api
def api_chat_start():
    blocked = _write_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    return _wrap(lambda: ok({"conversation_id": chat.start(session["student_id"], int(data.get("student_id") or 0))}))


@app.route("/api/chat/requests", methods=["POST"])
@student_api
def api_chat_request_send():
    blocked = _write_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}

    def run():
        chat.send_request(session["student_id"], int(data.get("student_id") or 0))
        return ok({"sent": True}, status=201)
    return _wrap(run)


@app.route("/api/chat/requests/<int:request_id>/<action>", methods=["POST"])
@student_api
def api_chat_request_answer(request_id, action):
    blocked = _write_guard()
    if blocked:
        return blocked
    if action not in ("accept", "reject"):
        return fail("bad_request", "Unknown action.", 400)
    return _wrap(lambda: ok({"conversation_id": chat.answer_request(session["student_id"], request_id, action == "accept")}))


def _int(name, default=0):
    try:
        return max(0, int(request.args.get(name) or default))
    except (TypeError, ValueError):
        return default


@app.route("/api/chat/c/<int:cid>/messages", methods=["GET", "POST"])
@student_api
def api_chat_messages(cid):
    me = session["student_id"]
    if request.method == "GET":
        return _wrap(lambda: ok(chat.history(me, cid, _int("before") or None, _readonly())))
    blocked = _write_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}

    def run():
        msg, created = chat.send_text(me, cid, str(data.get("uuid", "")), str(data.get("body", "")))
        return ok({"message": msg, "created": created}, status=201 if created else 200)
    return _wrap(run)


@app.route("/api/chat/c/<int:cid>/poll")
@student_api
def api_chat_poll(cid):
    return _wrap(lambda: ok(chat.poll(session["student_id"], cid, _int("after"), request.args.get("since"), _readonly())))


@app.route("/api/chat/c/<int:cid>/seen", methods=["POST"])
@student_api
def api_chat_seen(cid):
    if _readonly():
        return ok({"updated": 0})
    data = request.get_json(silent=True) or {}
    try:
        upto = int(data.get("upto") or 0) or None
    except (TypeError, ValueError):
        upto = None
    return _wrap(lambda: ok({"updated": chat.mark_seen(session["student_id"], cid, upto)}))


@app.route("/api/chat/c/<int:cid>/typing", methods=["POST"])
@student_api
def api_chat_typing(cid):
    if _readonly():
        return ok({})
    data = request.get_json(silent=True) or {}

    def run():
        chat.set_typing(session["student_id"], cid, bool(data.get("typing")))
        return ok({})
    return _wrap(run)


@app.route("/api/chat/messages/<int:message_id>/delete", methods=["POST"])
@student_api
def api_chat_delete(message_id):
    blocked = _write_guard()
    if blocked:
        return blocked

    def run():
        chat.delete_for_everyone(session["student_id"], message_id)
        return ok({"id": message_id})
    return _wrap(run)
