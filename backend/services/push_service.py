# -*- coding: utf-8 -*-
"""Web Push (VAPID) delivery to students who allowed notifications."""

import json

from backend.config import VAPID_CLAIMS_EMAIL, VAPID_PRIVATE_KEY
from backend.db.connection import get_db


# Web Push (real phone/browser notifications for students who allow it)
try:
    from pywebpush import webpush, WebPushException
    WEBPUSH_OK = True
except ImportError:
    WEBPUSH_OK = False


def send_push_to_class(title, message, link="", class_name=""):
    """
    Actually push a phone/browser notification to every student who has
    allowed notifications (subscribed), optionally filtered to one class.
    Runs quietly in the background of a request — a failed/expired
    subscription is deleted so the list stays clean, but never blocks or
    breaks the calling request.
    """
    if not WEBPUSH_OK:
        print("[push] pywebpush not installed — run: pip install pywebpush")
        return
    try:
        with get_db() as conn:
            if class_name:
                rows = conn.execute(
                    "SELECT * FROM push_subscriptions WHERE class_name=? OR class_name=''",
                    (class_name,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()

        payload = json.dumps({
            "title": title,
            "body": message,
            "url": link or "/",
        })

        dead_endpoints = []
        for row in rows:
            subscription_info = {
                "endpoint": row["endpoint"],
                "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
            }
            try:
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": f"mailto:{VAPID_CLAIMS_EMAIL}"},
                )
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                # 404/410 = the browser unsubscribed or the subscription expired
                if status in (404, 410):
                    dead_endpoints.append(row["endpoint"])
                else:
                    print(f"[push] failed for one subscriber: {e}")
            except Exception as e:
                print(f"[push] unexpected error for one subscriber: {e}")

        if dead_endpoints:
            with get_db() as conn:
                conn.executemany(
                    "DELETE FROM push_subscriptions WHERE endpoint=?",
                    [(ep,) for ep in dead_endpoints]
                )
                conn.commit()
    except Exception as e:
        print(f"[push] send_push_to_class error: {e}")
