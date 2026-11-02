# -*- coding: utf-8 -*-
"""Migration 014 - My Questions / Test Workspace (additive only; no row is changed or removed).

  questions.sort_order        nullable. NULL = "old order" (the question id). Used by Test Workspace reordering.
  questions.source_bank_id    nullable. Which My Questions row a test question was imported from.
  uq_questions_test_source    UNIQUE (test_id, source_bank_id) where source_bank_id is not NULL:
                              the DATABASE refuses the same saved question twice in one test.
  bank_questions.content_hash nullable. Fingerprint used to stop duplicate saves into My Questions
                              (not unique: old rows that are already duplicates must stay).

Existing questions keep their ids, images and order; nothing is backfilled except content_hash of saved questions.
"""
from backend.utils.question_hash import content_hash


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    q_cols, b_cols = _cols(conn, "questions"), _cols(conn, "bank_questions")
    if not q_cols or not b_cols:
        return "SKIP: questions / bank_questions missing (created by 007; will retry on next start)"
    if "sort_order" not in q_cols:
        conn.execute("ALTER TABLE questions ADD COLUMN sort_order INTEGER")
    if "source_bank_id" not in q_cols:
        conn.execute("ALTER TABLE questions ADD COLUMN source_bank_id INTEGER")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_questions_test_source "
                 "ON questions(test_id, source_bank_id) WHERE source_bank_id IS NOT NULL")
    if "content_hash" not in b_cols:
        conn.execute("ALTER TABLE bank_questions ADD COLUMN content_hash TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bank_questions_hash ON bank_questions(content_hash)")
    n = 0
    for r in conn.execute("SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option, image_id "
                          "FROM bank_questions WHERE content_hash IS NULL").fetchall():
        conn.execute("UPDATE bank_questions SET content_hash=? WHERE id=?",
                     (content_hash(r[1], r[2], r[3], r[4], r[5], r[6], r[7]), r[0]))
        n += 1
    return "questions.sort_order/source_bank_id, unique import index, bank_questions.content_hash (%d hashed)" % n


def down(conn):
    raise RuntimeError("014 only adds nullable columns and indexes; to undo, restore the pre-migration backup.")
