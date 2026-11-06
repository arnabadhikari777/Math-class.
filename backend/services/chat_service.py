# -*- coding: utf-8 -*-
"""Student-to-student chat: privacy, people, requests, messages, delivered/seen, online/last-seen, typing.

Plain sqlite3 like the rest of the project. Every function takes the CURRENT student's id as ``me``
and re-checks on the server that ``me`` belongs to the conversation: hiding a button is never the lock.

Message status (like WhatsApp):   sent  ->  delivered (the other student's app got it)  ->  seen (they opened it)
Presence:  "online" = the student's browser touched the site in the last ONLINE_SECONDS seconds.
Privacy:   PUBLIC (default) = anybody may start a chat.  PRIVATE = others must send a request first.
Notifications: go through the existing Math Class notification system (bell + phone push) and never
               contain the text of a message.
"""
import re
import sqlite3
from datetime import datetime, timezone

from backend.db.connection import escape_like, get_db
from backend.middleware.rate_limit import _bucket_ok
from backend.services.notification_service import mark_link_read, notify_student

ONLINE_SECONDS = 45          # heartbeat is ~20 s, so 45 s tolerates one missed beat
TYPING_SECONDS = 6
ACTIVE_SECONDS = 12          # "has this conversation open right now" (no push needed then)
MAX_BODY = 2000
PAGE = 40
PEOPLE_PER_PAGE = 20
_UUID_RE = re.compile(r"^[A-Za-z0-9\-]{8,36}$")
_ELIGIBLE = "s.is_verified=1 AND s.is_blocked=0 AND COALESCE(s.is_archived,0)=0"


class ChatError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------------ time helpers
def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now():
    """UTC, fixed format, so string comparison == time comparison."""
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")


def iso_z(value):
    return (value + "Z") if value else None


def _age_seconds(value):
    try:
        return (_utcnow() - datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f")).total_seconds()
    except (TypeError, ValueError):
        return None


def is_online(last_seen):
    age = _age_seconds(last_seen)
    return age is not None and age <= ONLINE_SECONDS


# ------------------------------------------------------------------ profile / privacy / presence
def _upsert(conn, sid, **cols):
    keys = list(cols)
    conn.execute(
        "INSERT INTO chat_profiles (student_id, %s) VALUES (?, %s) ON CONFLICT(student_id) DO UPDATE SET %s"
        % (", ".join(keys), ", ".join("?" * len(keys)), ", ".join("%s=excluded.%s" % (k, k) for k in keys)),
        [sid] + [cols[k] for k in keys])


def touch(me):
    """Heartbeat: mark the student as online right now."""
    with get_db() as conn:
        _upsert(conn, me, last_seen_at=now())
        conn.commit()


def get_privacy(me):
    with get_db() as conn:
        row = conn.execute("SELECT is_public FROM chat_profiles WHERE student_id=?", (me,)).fetchone()
    return True if row is None else bool(row["is_public"])


def set_privacy(me, is_public):
    with get_db() as conn:
        _upsert(conn, me, is_public=1 if is_public else 0)
        conn.commit()


def _student(conn, sid):
    return conn.execute(
        "SELECT s.id, s.name, s.class_name, COALESCE(p.is_public,1) AS is_public, p.last_seen_at "
        "FROM students s LEFT JOIN chat_profiles p ON p.student_id=s.id "
        "WHERE s.id=? AND " + _ELIGIBLE, (sid,)).fetchone()


def _peer_card(row):
    return {"id": row["id"], "name": row["name"], "class_name": row["class_name"],
            "is_public": bool(row["is_public"])}


# ------------------------------------------------------------------ conversations
def _pair(a, b):
    return (a, b) if a < b else (b, a)


def _find(conn, a, b):
    low, high = _pair(a, b)
    return conn.execute("SELECT * FROM chat_conversations WHERE user_low=? AND user_high=?", (low, high)).fetchone()


def _get_or_create(conn, a, b):
    row = _find(conn, a, b)
    if row:
        return row["id"]
    low, high = _pair(a, b)
    stamp = now()
    try:
        cur = conn.execute("INSERT INTO chat_conversations (user_low, user_high, last_message_at, created_at) "
                           "VALUES (?,?,?,?)", (low, high, stamp, stamp))
        return cur.lastrowid
    except sqlite3.IntegrityError:           # two taps at once: the other one won, use its row
        return _find(conn, a, b)["id"]


def _member(conn, me, cid):
    row = conn.execute("SELECT * FROM chat_conversations WHERE id=?", (cid,)).fetchone()
    if row is None or me not in (row["user_low"], row["user_high"]):
        raise ChatError("not_found", "Conversation not found.", 404)
    return row


def _other(conv, me):
    return conv["user_high"] if me == conv["user_low"] else conv["user_low"]


def can_open(me, cid):
    with get_db() as conn:
        try:
            _member(conn, me, cid)
            return True
        except ChatError:
            return False


def start(me, other_id):
    """Open (or create) the chat with another student. Private accounts need a request first."""
    if me == other_id:
        raise ChatError("self", "You cannot chat with yourself.")
    with get_db() as conn:
        other = _student(conn, other_id)
        if other is None:
            raise ChatError("not_found", "Student not found.", 404)
        existing = _find(conn, me, other_id)
        if existing:
            return existing["id"]
        if not other["is_public"]:
            raise ChatError("needs_request", "This account is private. Send a chat request first.", 403)
        cid = _get_or_create(conn, me, other_id)
        conn.commit()
        return cid


# ------------------------------------------------------------------ requests (private accounts)
def _pending(conn, sender, receiver):
    return conn.execute("SELECT * FROM chat_requests WHERE sender_id=? AND receiver_id=? AND status='pending'",
                        (sender, receiver)).fetchone()


def send_request(me, other_id):
    if me == other_id:
        raise ChatError("self", "You cannot send a request to yourself.")
    with get_db() as conn:
        other, mine = _student(conn, other_id), _student(conn, me)
        if other is None or mine is None:
            raise ChatError("not_found", "Student not found.", 404)
        if _find(conn, me, other_id):
            raise ChatError("already_chatting", "You can already chat with this student.")
        if other["is_public"]:
            raise ChatError("use_message", "This account is public: just open the chat.")
        if _pending(conn, other_id, me):
            raise ChatError("they_sent", "This student already sent you a request. Accept it in Requests.")
        if _pending(conn, me, other_id):
            raise ChatError("already_pending", "Your request is already waiting.")
        try:
            conn.execute("INSERT INTO chat_requests (sender_id, receiver_id, created_at) VALUES (?,?,?)",
                         (me, other_id, now()))
        except sqlite3.IntegrityError:
            raise ChatError("already_pending", "Your request is already waiting.")
        conn.commit()
        sender_name = mine["name"]
    notify_student(other_id, "Chat request", "%s wants to chat with you." % sender_name, "/chat?tab=requests")


def answer_request(me, request_id, accept):
    """Only the receiver can answer, and only once (the UPDATE is atomic)."""
    with get_db() as conn:
        req = conn.execute("SELECT * FROM chat_requests WHERE id=? AND receiver_id=?", (request_id, me)).fetchone()
        if req is None:
            raise ChatError("not_found", "Request not found.", 404)
        changed = conn.execute(
            "UPDATE chat_requests SET status=?, responded_at=? WHERE id=? AND receiver_id=? AND status='pending'",
            ("accepted" if accept else "rejected", now(), request_id, me)).rowcount
        if not changed:
            raise ChatError("not_pending", "This request was already answered.")
        me_row = _student(conn, me)
        cid = _get_or_create(conn, req["sender_id"], me) if accept else None
        conn.commit()
        my_name = me_row["name"] if me_row else "A student"
    if accept:
        notify_student(req["sender_id"], "Request accepted", "%s accepted your chat request." % my_name, "/chat/%d" % cid)
    return cid


def incoming_requests(me):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT r.id, r.created_at, s.id AS sid, s.name, s.class_name FROM chat_requests r "
            "JOIN students s ON s.id=r.sender_id WHERE r.receiver_id=? AND r.status='pending' "
            "AND s.is_blocked=0 AND COALESCE(s.is_archived,0)=0 ORDER BY r.id DESC", (me,)).fetchall()
    return [{"id": r["id"], "student_id": r["sid"], "name": r["name"], "class_name": r["class_name"],
             "created_at": iso_z(r["created_at"])} for r in rows]


# ------------------------------------------------------------------ people
def list_people(me, q="", page=1):
    term = (q or "").strip()[:40]
    page = max(1, int(page or 1))
    sql = ("SELECT s.id, s.name, s.class_name, COALESCE(p.is_public,1) AS is_public FROM students s "
           "LEFT JOIN chat_profiles p ON p.student_id=s.id WHERE s.id!=? AND " + _ELIGIBLE)
    args = [me]
    if term:
        sql += " AND s.name LIKE ? ESCAPE '\\'"
        args.append("%" + escape_like(term) + "%")
    sql += " ORDER BY lower(s.name), s.id LIMIT ? OFFSET ?"
    args += [PEOPLE_PER_PAGE + 1, (page - 1) * PEOPLE_PER_PAGE]
    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        has_next = len(rows) > PEOPLE_PER_PAGE
        rows = rows[:PEOPLE_PER_PAGE]
        out = []
        for r in rows:
            card = _peer_card(r)
            conv = _find(conn, me, r["id"])
            card["conversation_id"] = conv["id"] if conv else None
            card["request_sent"] = bool(_pending(conn, me, r["id"]))
            card["request_received"] = bool(_pending(conn, r["id"], me))
            out.append(card)
    return out, has_next


# ------------------------------------------------------------------ messages
def _serialize(row, me):
    deleted = bool(row["deleted_for_all"])
    return {
        "id": row["id"], "mine": row["sender_id"] == me,
        "body": "" if deleted else row["body"], "deleted": deleted,
        "status": row["status"], "created_at": iso_z(row["created_at"]),
        "delivered_at": iso_z(row["delivered_at"]), "seen_at": iso_z(row["seen_at"]),
    }


def _peer_state(conn, peer_id, cid):
    p = conn.execute("SELECT last_seen_at, typing_conv_id, typing_at FROM chat_profiles WHERE student_id=?",
                     (peer_id,)).fetchone()
    last_seen = p["last_seen_at"] if p else None
    typing = bool(p and p["typing_conv_id"] == cid and (_age_seconds(p["typing_at"]) or 999) <= TYPING_SECONDS)
    return {"online": is_online(last_seen), "last_seen": iso_z(last_seen), "typing": typing}


def _mark_delivered(conn, me, cid=None):
    stamp = now()
    sql = ("UPDATE chat_messages SET status='delivered', delivered_at=?, updated_at=? "
           "WHERE status='sent' AND sender_id!=? AND ")
    if cid is None:
        sql += "conversation_id IN (SELECT id FROM chat_conversations WHERE user_low=? OR user_high=?)"
        conn.execute(sql, (stamp, stamp, me, me, me))
    else:
        conn.execute(sql + "conversation_id=?", (stamp, stamp, me, cid))


def history(me, cid, before=None, readonly=False):
    with get_db() as conn:
        conv = _member(conn, me, cid)
        peer = _student(conn, _other(conv, me))
        if not readonly:
            _mark_delivered(conn, me, cid)
            conn.commit()
        args = [cid]
        sql = "SELECT * FROM chat_messages WHERE conversation_id=?"
        if before:
            sql += " AND id<?"
            args.append(int(before))
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", args + [PAGE + 1]).fetchall()
        has_more = len(rows) > PAGE
        rows = list(reversed(rows[:PAGE]))
        return {"messages": [_serialize(r, me) for r in rows], "has_more": has_more,
                "server_time": iso_z(now()),
                "peer": dict(_peer_card(peer), **_peer_state(conn, peer["id"], cid)) if peer else None}


def poll(me, cid, after=0, since=None, readonly=False):
    """New messages after id ``after`` + status/delete changes since ``since`` (server time of the last poll)."""
    stamp = now()
    with get_db() as conn:
        conv = _member(conn, me, cid)
        if not readonly:
            _mark_delivered(conn, me, cid)
            _upsert(conn, me, last_seen_at=stamp, active_conv_id=cid, active_at=stamp)
            conn.commit()
        new = conn.execute("SELECT * FROM chat_messages WHERE conversation_id=? AND id>? ORDER BY id LIMIT 100",
                           (cid, int(after or 0))).fetchall()
        updates = []
        if since:
            since_db = since.rstrip("Z")
            updates = conn.execute(
                "SELECT * FROM chat_messages WHERE conversation_id=? AND id<=? AND updated_at>?",
                (cid, int(after or 0), since_db)).fetchall()
        return {"messages": [_serialize(r, me) for r in new], "updates": [_serialize(r, me) for r in updates],
                "server_time": iso_z(stamp), "peer": _peer_state(conn, _other(conv, me), cid)}


def mark_seen(me, cid, upto=None):
    stamp = now()
    with get_db() as conn:
        _member(conn, me, cid)
        sql = ("UPDATE chat_messages SET status='seen', seen_at=?, delivered_at=COALESCE(delivered_at,?), updated_at=? "
               "WHERE conversation_id=? AND sender_id!=? AND status!='seen'")
        args = [stamp, stamp, stamp, cid, me]
        if upto:
            sql += " AND id<=?"
            args.append(int(upto))
        changed = conn.execute(sql, args).rowcount
        conn.commit()
    mark_link_read(me, "/chat/%d" % cid)
    return changed


def set_typing(me, cid, is_typing):
    with get_db() as conn:
        _member(conn, me, cid)
        if is_typing:
            _upsert(conn, me, typing_conv_id=cid, typing_at=now())
        else:
            _upsert(conn, me, typing_conv_id=None, typing_at=None)
        conn.commit()


def send_text(me, cid, client_uuid, body):
    body = (body or "").replace("\r\n", "\n").strip()
    if not body:
        raise ChatError("empty", "Write something first.")
    if len(body) > MAX_BODY:
        raise ChatError("too_long", "Message is too long (max %d characters)." % MAX_BODY)
    if not _UUID_RE.match(client_uuid or ""):
        raise ChatError("bad_request", "Invalid message id.")
    if not _bucket_ok("chatmsg:%s" % me, 60, 60, fail_open=True):
        raise ChatError("rate_limited", "You are sending too fast. Wait a moment.", 429)
    stamp = now()
    with get_db() as conn:
        conv = _member(conn, me, cid)
        peer_id = _other(conv, me)
        peer, mine = _student(conn, peer_id), _student(conn, me)
        if peer is None or mine is None:
            raise ChatError("unavailable", "This student is not available for chat right now.", 403)
        dup = conn.execute("SELECT * FROM chat_messages WHERE sender_id=? AND client_uuid=?", (me, client_uuid)).fetchone()
        if dup:                                           # a retry / double tap: same message, no duplicate row
            return _serialize(dup, me), False
        cur = conn.execute(
            "INSERT INTO chat_messages (conversation_id, sender_id, body, client_uuid, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)", (cid, me, body, client_uuid, stamp, stamp))
        conn.execute("UPDATE chat_conversations SET last_message_at=? WHERE id=?", (stamp, cid))
        _upsert(conn, me, typing_conv_id=None, typing_at=None, last_seen_at=stamp)
        row = conn.execute("SELECT * FROM chat_messages WHERE id=?", (cur.lastrowid,)).fetchone()
        unread = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE conversation_id=? AND sender_id=? "
                              "AND status!='seen' AND deleted_for_all=0", (cid, me)).fetchone()[0]
        pp = conn.execute("SELECT active_conv_id, active_at FROM chat_profiles WHERE student_id=?", (peer_id,)).fetchone()
        conn.commit()
        looking = bool(pp and pp["active_conv_id"] == cid and (_age_seconds(pp["active_at"]) or 999) <= ACTIVE_SECONDS)
        sender_name = mine["name"]
    # Bell entry always; phone push only when the other student is not looking at this chat right now.
    msg = "Sent you a message." if unread <= 1 else "Sent you %d new messages." % unread
    notify_student(peer_id, "New message from %s" % sender_name, msg, "/chat/%d" % cid, push=not looking)
    return _serialize(row, me), True


def delete_for_everyone(me, message_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM chat_messages WHERE id=?", (message_id,)).fetchone()
        if row is None:
            raise ChatError("not_found", "Message not found.", 404)
        _member(conn, me, row["conversation_id"])
        if row["sender_id"] != me:
            raise ChatError("forbidden", "You can only delete your own messages.", 403)
        conn.execute("UPDATE chat_messages SET deleted_for_all=1, body='', updated_at=? WHERE id=?", (now(), message_id))
        conn.commit()


# ------------------------------------------------------------------ lists & counters
def chat_list(me, readonly=False):
    with get_db() as conn:
        if not readonly:
            _mark_delivered(conn, me)        # the app is open, so everything waiting has "arrived"
            _upsert(conn, me, last_seen_at=now())
            conn.commit()
        convs = conn.execute(
            "SELECT * FROM chat_conversations WHERE user_low=? OR user_high=? ORDER BY last_message_at DESC LIMIT 200",
            (me, me)).fetchall()
        ids = [c["id"] for c in convs]
        marks = ",".join("?" * len(ids)) or "NULL"
        unread = {r[0]: r[1] for r in conn.execute(
            "SELECT conversation_id, COUNT(*) FROM chat_messages WHERE sender_id!=? AND status!='seen' "
            "AND deleted_for_all=0 AND conversation_id IN (%s) GROUP BY conversation_id" % marks, [me] + ids)}
        last = {}
        for r in conn.execute(
                "SELECT m.* FROM chat_messages m WHERE m.id IN (SELECT MAX(id) FROM chat_messages "
                "WHERE conversation_id IN (%s) GROUP BY conversation_id)" % marks, ids):
            last[r["conversation_id"]] = r
        items = []
        for c in convs:
            peer = _student(conn, _other(c, me))
            if peer is None:
                continue
            m = last.get(c["id"])
            items.append({
                "id": c["id"], "peer": _peer_card(peer), "unread": unread.get(c["id"], 0),
                "last_at": iso_z(m["created_at"] if m else c["last_message_at"]),
                "last_text": ("" if m is None else ("This message was deleted" if m["deleted_for_all"] else m["body"][:80])),
                "last_mine": bool(m and m["sender_id"] == me),
                "last_status": m["status"] if m else None,
                "online": is_online(peer["last_seen_at"]),
            })
    return items


def summary(me, readonly=False):
    """Tiny counter for the nav badge. Calling it also marks incoming messages as delivered."""
    with get_db() as conn:
        if not readonly:
            _mark_delivered(conn, me)
            conn.commit()
        unread = conn.execute(
            "SELECT COUNT(*) FROM chat_messages m JOIN chat_conversations c ON c.id=m.conversation_id "
            "WHERE (c.user_low=? OR c.user_high=?) AND m.sender_id!=? AND m.status!='seen' AND m.deleted_for_all=0",
            (me, me, me)).fetchone()[0]
        reqs = conn.execute("SELECT COUNT(*) FROM chat_requests WHERE receiver_id=? AND status='pending'",
                            (me,)).fetchone()[0]
    return {"messages": unread, "requests": reqs}
