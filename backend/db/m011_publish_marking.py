# -*- coding: utf-8 -*-
"""Migration 011 - draft/publish workflow and marking breakdown (additive only, no row is deleted).

  tests           + status ('draft' | 'published'), published_at, notified_at,
                    result_release ('immediate' | 'manual'), results_released_at
  exam_attempts   + correct_count, wrong_count, unanswered_count, positive_marks, negative_marks_total
  results         + the same five columns (mirror of the attempt that is counted)

Existing data:
  * every existing test becomes 'published' (so nothing a student could see yesterday disappears;
    deactivated tests stay deactivated through the untouched is_active flag);
  * existing tests count as "already notified" (notified_at = created_at), so publishing or
    re-publishing an old test can never re-send a notification;
  * result_release defaults to 'immediate' - the behaviour students already have;
  * the breakdown columns are back-filled from the stored answers (read-only on answers).
"""


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add(conn, table, col, ddl):
    if _cols(conn, table) and col not in _cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


BREAKDOWN = (("correct_count", "INTEGER"), ("wrong_count", "INTEGER"), ("unanswered_count", "INTEGER"),
             ("positive_marks", "REAL"), ("negative_marks_total", "REAL"))


def up(conn):
    fresh = "status" not in _cols(conn, "tests")
    _add(conn, "tests", "status", "TEXT NOT NULL DEFAULT 'draft'")
    _add(conn, "tests", "published_at", "TEXT")
    _add(conn, "tests", "notified_at", "TEXT")
    _add(conn, "tests", "result_release", "TEXT NOT NULL DEFAULT 'immediate'")
    _add(conn, "tests", "results_released_at", "TEXT")
    if fresh:  # only on the first run: never overwrite a teacher's later draft/published choice
        conn.execute("""UPDATE tests SET status='published', published_at=created_at, notified_at=created_at""")
    for table in ("exam_attempts", "results"):
        for col, ddl in BREAKDOWN:
            _add(conn, table, col, ddl)
    # back-fill the attempts that already have stored answers
    conn.execute("""
        UPDATE exam_attempts SET
          correct_count    = (SELECT COUNT(*) FROM answers a WHERE a.attempt_id=exam_attempts.id AND a.is_correct=1),
          wrong_count      = (SELECT COUNT(*) FROM answers a WHERE a.attempt_id=exam_attempts.id
                              AND a.selected_option IS NOT NULL AND a.is_correct=0),
          unanswered_count = (SELECT COUNT(*) FROM answers a WHERE a.attempt_id=exam_attempts.id
                              AND a.selected_option IS NULL),
          positive_marks   = (SELECT COALESCE(SUM(a.marks_awarded),0) FROM answers a
                              WHERE a.attempt_id=exam_attempts.id AND a.marks_awarded>0),
          negative_marks_total = (SELECT COALESCE(-SUM(a.marks_awarded),0) FROM answers a
                              WHERE a.attempt_id=exam_attempts.id AND a.marks_awarded<0)
        WHERE status IN ('submitted','auto_submitted') AND correct_count IS NULL
          AND EXISTS (SELECT 1 FROM answers a WHERE a.attempt_id=exam_attempts.id)""")
    conn.execute("""
        UPDATE results SET
          correct_count = (SELECT e.correct_count FROM exam_attempts e WHERE e.id=results.attempt_id),
          wrong_count = (SELECT e.wrong_count FROM exam_attempts e WHERE e.id=results.attempt_id),
          unanswered_count = (SELECT e.unanswered_count FROM exam_attempts e WHERE e.id=results.attempt_id),
          positive_marks = (SELECT e.positive_marks FROM exam_attempts e WHERE e.id=results.attempt_id),
          negative_marks_total = (SELECT e.negative_marks_total FROM exam_attempts e WHERE e.id=results.attempt_id)
        WHERE attempt_id IS NOT NULL AND correct_count IS NULL""")
    return "draft/publish columns on tests; marking breakdown columns on exam_attempts/results"


def down(conn):
    raise RuntimeError("011 only adds columns and back-fills them; to undo, restore the pre-migration backup.")
