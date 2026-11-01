# -*- coding: utf-8 -*-
"""Migration 013 - real logout (additive: one new table, nothing existing is touched).

  revoked_sessions(jti, revoked_at, expires_at)
Every new login gets a random session id (jti). Logout writes it here, so a copy of the old cookie stops
working immediately (a cookie-only session could not be cancelled on the server before). Rows are deleted
automatically once the cookie would have expired anyway. Sessions created before this migration have no jti
and keep working until they expire - nobody is logged out by the upgrade.
"""


def up(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS revoked_sessions (
        jti TEXT PRIMARY KEY, revoked_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")
    return "revoked_sessions table"


def down(conn):
    raise RuntimeError("013 only adds a table; to undo, restore the pre-migration backup.")
