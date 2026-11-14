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


def notify_student(student_id, title, message, link="", notif_type="chat", push=True):
    """A notification only ONE student sees (bell + optional phone push).

    One row per (student, link): a new message in the same chat refreshes that row and makes it
    unread again, so ten messages never become ten bell entries.
    """
    now = datetime.now().isoformat()
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM notifications WHERE student_id=? AND notif_type=? AND link=? ORDER BY id DESC LIMIT 1",
                (student_id, notif_type, link or "")).fetchone()
            if row:
                conn.execute("UPDATE notifications SET title=?, message=?, created_at=? WHERE id=?",
                             (title, message, now, row["id"]))
                conn.execute("DELETE FROM notification_reads WHERE notification_id=? AND student_id=?",
                             (row["id"], student_id))
            else:
                conn.execute(
                    """INSERT INTO notifications (title, message, notif_type, class_name, link, created_at, student_id)
                       VALUES (?,?,?,?,?,?,?)""",
                    (title, message, notif_type, "", link or "", now, student_id))
            conn.commit()
    except Exception as e:
        print(f"[notification] {e}")
    if push:
        send_push_to_class(title, message, link=link, student_id=student_id)


def mark_link_read(student_id, link):
    """Mark this student's notification(s) for ``link`` as read (opening the chat clears the bell)."""
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO notification_reads (notification_id, student_id, read_at)
                   SELECT id, ?, ? FROM notifications WHERE student_id=? AND link=?""",
                (student_id, datetime.now().isoformat(), student_id, link))
            conn.commit()
    except Exception as e:
        print(f"[notification] {e}")


# ---- what a student can see -------------------------------------------------------------------
# One definition used by the bell API, the page context and the unread counter.
#   params: (class_name, student_id, student_id)
VISIBLE_SQL = """((n.student_id IS NULL AND (n.class_name='' OR n.class_name IS NULL OR n.class_name=?))
                  OR n.student_id=?)
                 AND NOT EXISTS (SELECT 1 FROM notification_dismissals d
                                 WHERE d.notification_id=n.id AND d.student_id=?)"""


def student_class(conn, student_id):
    row = conn.execute("SELECT class_name FROM students WHERE id=?", (student_id,)).fetchone()
    return (row["class_name"] if row else "") or ""


def mark_all_read(conn, student_id):
    """Mark every notification this student can currently see as read. Returns how many were new."""
    cls = student_class(conn, student_id)
    cur = conn.execute(
        f"""INSERT OR IGNORE INTO notification_reads (notification_id, student_id, read_at)
            SELECT n.id, ?, ? FROM notifications n WHERE {VISIBLE_SQL}""",
        (student_id, datetime.now().isoformat(), cls, student_id, student_id))
    return cur.rowcount or 0


def clear_for_student(conn, student_id):
    """"All Clean": remove ONE student's notification history. Caller commits (single transaction).

    * personal rows (notifications.student_id = this student)  -> deleted
    * shared class rows                                        -> dismissed for this student only
    * announcements, other students' rows, teacher data        -> never touched
    """
    cls = student_class(conn, student_id)
    now = datetime.now().isoformat()
    shared = conn.execute(
        f"""INSERT OR IGNORE INTO notification_dismissals (notification_id, student_id, dismissed_at)
            SELECT n.id, ?, ? FROM notifications n
            WHERE n.student_id IS NULL AND (n.class_name='' OR n.class_name IS NULL OR n.class_name=?)""",
        (student_id, now, cls)).rowcount or 0
    conn.execute(
        """DELETE FROM notification_reads WHERE student_id=? AND notification_id IN
           (SELECT notification_id FROM notification_dismissals WHERE student_id=?)""",
        (student_id, student_id))
    personal = conn.execute("DELETE FROM notifications WHERE student_id=?", (student_id,)).rowcount or 0
    return {"personal_deleted": personal, "shared_dismissed": shared}
