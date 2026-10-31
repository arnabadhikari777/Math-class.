# -*- coding: utf-8 -*-
"""In-app notifications (bell icon) + push fan-out."""

from datetime import datetime

from backend.db.connection import get_db
from backend.services.push_service import send_push_to_class


def create_notification(title, message, notif_type="info", class_name="", link=""):
    """Insert an in-app notification for students (optionally filtered by class)."""
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO notifications (title, message, notif_type, class_name, link, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (title, message, notif_type, class_name or "", link or "", datetime.now().isoformat())
            )
            conn.commit()
    except Exception as e:
        print(f"[notification] {e}")
    # Also push a real phone/browser notification to whoever has allowed it.
    send_push_to_class(title, message, link=link, class_name=class_name)
