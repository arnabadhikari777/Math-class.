# -*- coding: utf-8 -*-
"""Migration 019 - forward-only safety net for the doubts schema.

Why: two files used to claim number 018 (m018_doubts.py = registered, m018_doubts_and_profile.py = never registered).
A database that ever received the second layout keeps a ``doubts`` table without ``question`` & co, because
CREATE TABLE IF NOT EXISTS never alters an existing table. 018 may be recorded as applied on such a database, so it
would never run again. This migration re-checks every expected column with PRAGMA table_info, adds what is missing,
maps legacy values (question_text, status, ai_has_answer, doubt_images) and re-creates missing indexes.

Additive and idempotent: on a database that already has the correct schema it changes nothing.
Never drops or rewrites existing rows. Any failure rolls the whole migration back (see migrations.run_pending).
"""
from backend.db import m018_doubts


def up(conn):
    exists = m018_doubts._table_exists(conn, "doubts")
    added = m018_doubts.repair_existing_doubts_table(conn)
    m018_doubts.up(conn)            # idempotent: creates anything still missing (tables, indexes, columns)
    bad = conn.execute("PRAGMA foreign_key_check(doubts)").fetchall()
    if bad:
        raise RuntimeError("doubts has %d foreign-key violation(s); migration rolled back: %s"
                           % (len(bad), [tuple(b) for b in bad[:5]]))
    if exists and added:
        return "doubts repaired: added columns " + ", ".join(added)
    return "doubts schema verified (no change needed)" if exists else "doubts schema created"


def down(conn):
    return "nothing to undo (019 only adds missing columns/indexes)"
