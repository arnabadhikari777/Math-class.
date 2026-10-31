# -*- coding: utf-8 -*-
"""Migration 012 - safe autosave (additive only, no row is changed or deleted).

  exam_attempts  + draft_meta  TEXT                (JSON {question_id: client_ms}: when each answer was chosen)
                 + draft_rev   INTEGER NOT NULL 0  (counts accepted autosaves, for diagnostics)

Why: the server draft is the single source of truth.  An autosave carries the time each answer was
chosen, so an older answer (a stale tab, a late retry, an old local copy) can never overwrite a newer one.
Existing drafts keep working: a missing draft_meta simply means "no timestamps yet".
"""


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    cols = _cols(conn, "exam_attempts")
    if cols and "draft_meta" not in cols:
        conn.execute("ALTER TABLE exam_attempts ADD COLUMN draft_meta TEXT")
    if cols and "draft_rev" not in cols:
        conn.execute("ALTER TABLE exam_attempts ADD COLUMN draft_rev INTEGER NOT NULL DEFAULT 0")
    return "exam_attempts.draft_meta / draft_rev (autosave ordering)"


def down(conn):
    raise RuntimeError("012 only adds columns; to undo, restore the pre-migration backup.")
