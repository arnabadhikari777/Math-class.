# -*- coding: utf-8 -*-
"""Web Push (VAPID) delivery to students who allowed notifications."""

import json
import logging

from backend.config import PUSH_ENABLED, VAPID_CLAIMS_EMAIL, VAPID_PRIVATE_KEY
from datetime import datetime, timedelta

from backend.db.connection import get_db
from backend.services import push_validation

MAX_FAILURES = 5
log = logging.getLogger(__name__)


# Web Push (real phone/browser notifications for students who allow it)
try:
    from pywebpush import webpush, WebPushException
    WEBPUSH_OK = True
except ImportError:
    WEBPUSH_OK = False


def send_push_to_class(title, message, link="", class_name="", student_id=None):
    """
    Actually push a phone/browser notification to every student who has
    allowed notifications (subscribed), optionally filtered to one class, or - with
    ``student_id`` - sent only to that one student's devices (used for chat messages).
    Runs quietly in the background of a request — a failed/expired
    subscription is deleted so the list stays clean, but never blocks or
    breaks the calling request.
    """
    if not PUSH_ENABLED:
        log.info("push skipped: VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_CLAIMS_EMAIL are not all set in .env")
        return
    if not WEBPUSH_OK:
        log.warning("push skipped: pywebpush is not installed (pip install pywebpush)")
        return
    try:
        with get_db() as conn:
            if student_id:
                rows = conn.execute("SELECT * FROM push_subscriptions WHERE student_id=?",
                                    (int(student_id),)).fetchall()
            elif class_name:
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
        ok_endpoints, failed_endpoints = [], []
        now_iso = datetime.now().isoformat()
        for row in rows:
            valid, _err = push_validation.validate_endpoint(row["endpoint"])
            if not valid:                       # never call a host that is not a real push service
                dead_endpoints.append(row["endpoint"])
                continue
            if row["expires_at"] and row["expires_at"] < now_iso:
                dead_endpoints.append(row["endpoint"])
                continue
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
                ok_endpoints.append(row["endpoint"])
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                # 404/410 = the browser unsubscribed or the subscription expired
                if status in (404, 410):
                    dead_endpoints.append(row["endpoint"])
                else:
                    failed_endpoints.append(row["endpoint"])
                    log.warning("push: failed for one subscriber: %s", e)
            except Exception as e:
                log.warning("push: unexpected error for one subscriber: %s", e)

        if ok_endpoints or failed_endpoints:
            with get_db() as conn:
                conn.executemany("UPDATE push_subscriptions SET fail_count=0, last_success_at=? WHERE endpoint=?",
                                 [(now_iso, ep) for ep in ok_endpoints])
                conn.executemany("UPDATE push_subscriptions SET fail_count=fail_count+1, last_failure_at=? "
                                 "WHERE endpoint=?", [(now_iso, ep) for ep in failed_endpoints])
                # subscriptions that keep failing are expired: remove them so the list stays clean
                conn.execute("DELETE FROM push_subscriptions WHERE fail_count>=?", (MAX_FAILURES,))
                conn.commit()
        if dead_endpoints:
            with get_db() as conn:
                conn.executemany(
                    "DELETE FROM push_subscriptions WHERE endpoint=?",
                    [(ep,) for ep in dead_endpoints]
                )
                conn.commit()
    except Exception as e:
        log.error("push: send_push_to_class error: %s", e)
