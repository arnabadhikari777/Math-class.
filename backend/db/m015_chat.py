# -*- coding: utf-8 -*-
"""Migration 015 - Student chat (additive only; no existing row is changed or removed).

New tables
  chat_profiles       one row per student who ever touched chat: privacy (default PUBLIC), last seen,
                      typing + "currently open conversation" markers. No row = public, never seen.
  chat_conversations  exactly one row per pair of students (user_low < user_high).
  chat_messages       text messages with sent / delivered / seen status (+ delete-for-everyone flag).
  chat_requests       "may I message you?" requests for PRIVATE accounts.
Existing table
  notifications.student_id  nullable. NULL = the old class-wide notices (unchanged). A number = a
                            notification only that student sees (used for chat messages / requests).
"""


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_profiles (
            student_id INTEGER PRIMARY KEY,
            is_public INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT,
            typing_conv_id INTEGER,
            typing_at TEXT,
            active_conv_id INTEGER,
            active_at TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_low INTEGER NOT NULL,
            user_high INTEGER NOT NULL,
            last_message_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (user_low, user_high),
            CHECK (user_low < user_high),
            FOREIGN KEY (user_low) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (user_high) REFERENCES students(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_low ON chat_conversations(user_low, last_message_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_high ON chat_conversations(user_high, last_message_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            client_uuid TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','delivered','seen')),
            delivered_at TEXT,
            seen_at TEXT,
            deleted_for_all INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (sender_id, client_uuid),
            FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES students(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg_conv ON chat_messages(conversation_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg_unread ON chat_messages(conversation_id, sender_id, status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
            created_at TEXT NOT NULL,
            responded_at TEXT,
            CHECK (sender_id != receiver_id),
            FOREIGN KEY (sender_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (receiver_id) REFERENCES students(id) ON DELETE CASCADE
        )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_request_pending "
                 "ON chat_requests(sender_id, receiver_id) WHERE status='pending'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_req_receiver ON chat_requests(receiver_id, status)")
    if "student_id" not in _cols(conn, "notifications"):
        conn.execute("ALTER TABLE notifications ADD COLUMN student_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_student ON notifications(student_id, created_at)")
    return "chat_profiles, chat_conversations, chat_messages, chat_requests, notifications.student_id"


def down(conn):
    for t in ("chat_requests", "chat_messages", "chat_conversations", "chat_profiles"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("DROP INDEX IF EXISTS idx_notifications_student")
    return "chat tables dropped (notifications.student_id column is kept, it is harmless)"
