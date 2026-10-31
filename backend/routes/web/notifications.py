# -*- coding: utf-8 -*-
"""Notification bell + Web Push subscription endpoints (/api/notifications, /api/push)."""

from datetime import datetime

from flask import jsonify, request, session

from backend.config import VAPID_PUBLIC_KEY
from backend.core import app
from backend.services import push_validation
from backend.db.connection import get_db
from backend.middleware.auth import student_login_required


@app.route("/api/notifications")
@student_login_required
def api_notifications():
    sid = session["student_id"]
    with get_db() as conn:
        student = conn.execute("SELECT class_name FROM students WHERE id=?", (sid,)).fetchone()
        class_name = student["class_name"] if student else ""
        rows = conn.execute(
            """SELECT n.*,
                      CASE WHEN r.id IS NULL THEN 0 ELSE 1 END AS is_read
               FROM notifications n
               LEFT JOIN notification_reads r
                 ON r.notification_id = n.id AND r.student_id = ?
               WHERE (n.class_name='' OR n.class_name IS NULL OR n.class_name=?)
               ORDER BY n.created_at DESC LIMIT 30""",
            (sid, class_name)
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "title": r["title"],
            "message": r["message"],
            "type": r["notif_type"],
            "link": r["link"] or "",
            "created_at": r["created_at"],
            "is_read": bool(r["is_read"]),
        })
    return jsonify({"notifications": items})


@app.route("/api/notifications/mark-read", methods=["POST"])
@student_login_required
def api_mark_notifications_read():
    sid = session["student_id"]
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")  # list of ids, or None = all visible
    with get_db() as conn:
        student = conn.execute("SELECT class_name FROM students WHERE id=?", (sid,)).fetchone()
        class_name = student["class_name"] if student else ""
        if ids:
            target_ids = [int(i) for i in ids]
        else:
            target_ids = [
                r["id"] for r in conn.execute(
                    """SELECT id FROM notifications
                       WHERE class_name='' OR class_name IS NULL OR class_name=?""",
                    (class_name,)
                ).fetchall()
            ]
        now = datetime.now().isoformat()
        for nid in target_ids:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO notification_reads
                       (notification_id, student_id, read_at) VALUES (?,?,?)""",
                    (nid, sid, now)
                )
            except Exception:
                pass
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/push/public-key")
def api_push_public_key():
    """Public VAPID key the browser needs to create a push subscription."""
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@app.route("/api/push/subscribe", methods=["POST"])
@student_login_required
def api_push_subscribe():
    """Save (or refresh) the signed-in student's push subscription after they tap "Allow"."""
    sid = session["student_id"]
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint", "")
    keys = payload.get("keys", {}) or {}
    p256dh, auth = keys.get("p256dh", ""), keys.get("auth", "")
    ok, err = push_validation.validate_endpoint(endpoint)
    if ok:
        ok, err = push_validation.validate_keys(p256dh, auth)
    if not ok:
        return jsonify({"ok": False, "error": err or "Invalid subscription."}), 400
    now = datetime.now().isoformat()
    with get_db() as conn:
        student = conn.execute("SELECT class_name FROM students WHERE id=?", (sid,)).fetchone()
        class_name = student["class_name"] if student else ""
        existing = conn.execute("SELECT student_id, p256dh, auth FROM push_subscriptions WHERE endpoint=?",
                                (endpoint,)).fetchone()
        if existing and existing["student_id"] != sid and (existing["p256dh"], existing["auth"]) != (p256dh, auth):
            # Same endpoint but different keys, owned by someone else: refuse to overwrite it.
            return jsonify({"ok": False, "error": "This device is registered to another account."}), 409
        # Same device + same keys, another account signed in -> the device simply changed hands.
        conn.execute(
            """INSERT INTO push_subscriptions (student_id, class_name, endpoint, p256dh, auth, created_at, host,
                                               fail_count)
               VALUES (?,?,?,?,?,?,?,0)
               ON CONFLICT(endpoint) DO UPDATE SET
                 student_id=excluded.student_id, class_name=excluded.class_name,
                 p256dh=excluded.p256dh, auth=excluded.auth, host=excluded.host, fail_count=0""",
            (sid, class_name, endpoint, p256dh, auth, now, push_validation.endpoint_host(endpoint)))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
@student_login_required
def api_push_unsubscribe():
    """Remove the signed-in student's OWN subscription (endpoint AND student id must match)."""
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint", "")
    if isinstance(endpoint, str) and 0 < len(endpoint) <= push_validation.MAX_ENDPOINT:
        with get_db() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND student_id=?",
                         (endpoint, session["student_id"]))
            conn.commit()
    return jsonify({"ok": True})   # same answer whether or not it existed: reveals nothing about other users
