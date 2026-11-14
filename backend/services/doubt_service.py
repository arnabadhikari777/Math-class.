# -*- coding: utf-8 -*-
"""Doubt Management service — student submissions, teacher answers, AI history isolation."""

from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


def _now():
    try:
        if ZoneInfo:
            return datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")
    except Exception:
        pass
    return datetime.now().isoformat(timespec="seconds")


def create_doubt(conn, student_id, subject, question_text, chapter=None, topic=None):
    """Insert a new doubt. Returns doubt id."""
    now = _now()
    cur = conn.execute(
        """INSERT INTO doubts
           (student_id, subject, chapter, topic, question_text, status,
            ai_has_answer, created_at, updated_at)
           VALUES (?,?,?,?,?,'pending',0,?,?)""",
        (student_id, subject.strip(), (chapter or "").strip() or None,
         (topic or "").strip() or None, question_text.strip(), now, now),
    )
    return cur.lastrowid


def attach_image(conn, doubt_id, info, original_name=None):
    """Store Cloudinary metadata for a doubt image."""
    now = _now()
    conn.execute(
        """INSERT INTO doubt_images
           (doubt_id, url, public_id, original_name, width, height, bytes, format, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (doubt_id, info["url"], info.get("public_id"), (original_name or "")[:150],
         info.get("width"), info.get("height"), info.get("bytes"), info.get("format"), now),
    )


def list_student_doubts(conn, student_id, limit=50, offset=0):
    return conn.execute(
        """SELECT d.*,
                  (SELECT url FROM doubt_images di WHERE di.doubt_id=d.id LIMIT 1) AS first_image_url
           FROM doubts d
           WHERE d.student_id=?
           ORDER BY d.created_at DESC
           LIMIT ? OFFSET ?""",
        (student_id, limit, offset),
    ).fetchall()


def get_doubt_for_student(conn, doubt_id, student_id):
    row = conn.execute(
        "SELECT * FROM doubts WHERE id=? AND student_id=?", (doubt_id, student_id)
    ).fetchone()
    return row


def get_doubt_images(conn, doubt_id):
    return conn.execute(
        "SELECT * FROM doubt_images WHERE doubt_id=? ORDER BY id", (doubt_id,)
    ).fetchall()


def teacher_list_doubts(conn, status=None, subject=None, class_name=None, limit=50, offset=0):
    where = ["1=1"]
    params = []
    if status == "pending":
        where.append("d.status='pending'")
    elif status == "answered":
        where.append("d.status='answered'")
    if subject:
        where.append("d.subject=?")
        params.append(subject)
    if class_name:
        where.append("s.class_name=?")
        params.append(class_name)
    sql = f"""
        SELECT d.*, s.name AS student_name, s.class_name, s.username
        FROM doubts d
        JOIN students s ON s.id = d.student_id
        WHERE {' AND '.join(where)}
        ORDER BY d.created_at DESC
        LIMIT ? OFFSET ?
    """
    return conn.execute(sql, params + [limit, offset]).fetchall()


def teacher_counts(conn):
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
             SUM(CASE WHEN status='answered' THEN 1 ELSE 0 END) AS answered,
             COUNT(*) AS total
           FROM doubts"""
    ).fetchone()
    return {
        "pending": row["pending"] or 0,
        "answered": row["answered"] or 0,
        "total": row["total"] or 0,
        "new": row["pending"] or 0,
    }


def set_teacher_answer(conn, doubt_id, answer_text, teacher_label="Teacher"):
    now = _now()
    conn.execute(
        """UPDATE doubts SET
             teacher_answer=?, teacher_answered_at=?, teacher_answered_by=?,
             status='answered', updated_at=?
           WHERE id=?""",
        (answer_text.strip(), now, teacher_label, now, doubt_id),
    )


def mark_ai_answered(conn, doubt_id, summary=None):
    now = _now()
    conn.execute(
        """UPDATE doubts SET ai_has_answer=1, ai_summary=COALESCE(?, ai_summary), updated_at=?
           WHERE id=?""",
        (summary, now, doubt_id),
    )


def get_or_create_ai_conversation(conn, doubt_id, student_id):
    row = conn.execute(
        "SELECT * FROM doubt_ai_conversations WHERE doubt_id=?", (doubt_id,)
    ).fetchone()
    if row:
        return row["id"]
    now = _now()
    cur = conn.execute(
        """INSERT INTO doubt_ai_conversations
           (doubt_id, student_id, message_count, created_at, updated_at)
           VALUES (?,?,0,?,?)""",
        (doubt_id, student_id, now, now),
    )
    return cur.lastrowid


def append_ai_message(conn, conversation_id, role, content):
    now = _now()
    conn.execute(
        """INSERT INTO doubt_ai_messages (conversation_id, role, content, created_at)
           VALUES (?,?,?,?)""",
        (conversation_id, role, content, now),
    )
    conn.execute(
        """UPDATE doubt_ai_conversations
           SET message_count = message_count + 1, last_message_at=?, updated_at=?
           WHERE id=?""",
        (now, now, conversation_id),
    )


def get_ai_messages(conn, conversation_id, limit=40):
    return conn.execute(
        """SELECT role, content, created_at FROM doubt_ai_messages
           WHERE conversation_id=? ORDER BY id DESC LIMIT ?""",
        (conversation_id, limit),
    ).fetchall()[::-1]  # chronological
