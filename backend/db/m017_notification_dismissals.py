# -*- coding: utf-8 -*-
"""Migration 017 - per-student notification dismissals (additive, new table only).

Class-wide notifications (notifications.student_id IS NULL) are shared by every student of the
class, so "All Clean" must never delete them. Instead one row here says "this student dismissed
this notification"; it is hidden for that student only. Rows vanish automatically (ON DELETE
CASCADE) when the notification or the student is deleted.
"""


def up(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_dismissals (
            notification_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            dismissed_at TEXT NOT NULL,
            PRIMARY KEY (notification_id, student_id),
            FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_dismiss_student ON notification_dismissals(student_id)")
    return "notification_dismissals"


def down(conn):
    conn.execute("DROP TABLE IF EXISTS notification_dismissals")
    return "notification_dismissals dropped"
