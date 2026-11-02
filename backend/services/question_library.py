# -*- coding: utf-8 -*-
"""My Questions (central library) <-> Test Workspace rules, in ONE place so every route enforces the same thing.

  * import_into_test()        copies saved questions into a test; refuses published tests; refuses duplicates
                              (by link AND by content) in the backend, not only in JavaScript.
  * save_to_my_questions()    copies a test question into the library; refuses duplicates.
  * ordered_questions()       the one ordering rule for a test's questions (sort_order, else the old id order).

A copy is independent: editing a test question never changes the library row, and vice versa.
"""
import sqlite3
from datetime import datetime

from backend.services import publish_service
from backend.utils.question_hash import content_hash

ORDER_SQL = "COALESCE(sort_order, id), id"


class PublishedTestError(Exception):
    """Raised when something tries to change the questions of a test that is not a draft."""


PUBLISHED_MESSAGE = ("এই Test এখন Published। প্রশ্ন যোগ বা পরিবর্তন করতে আগে Test-টি Unpublish করুন "
                     "(This test is published. Unpublish it to add or change questions.)")


def _now():
    return datetime.now().isoformat()


def ordered_questions(conn, test_id):
    return conn.execute(f"SELECT * FROM questions WHERE test_id=? ORDER BY {ORDER_SQL}", (test_id,)).fetchall()


def next_sort_order(conn, test_id):
    return conn.execute("SELECT COALESCE(MAX(COALESCE(sort_order, id)), 0) + 1 FROM questions WHERE test_id=?",
                        (test_id,)).fetchone()[0]


def require_editable(test):
    if not publish_service.is_editable(test):
        raise PublishedTestError(PUBLISHED_MESSAGE)


def bank_row_hash(row):
    return content_hash(row["question_text"], row["option_a"], row["option_b"], row["option_c"], row["option_d"],
                        row["correct_option"], row["image_id"])


def test_question_hash(row):
    return content_hash(row["question_text"], row["option_a"], row["option_b"], row["option_c"], row["option_d"],
                        row["correct_option"], row["bank_image_id"])


def find_bank_duplicate(conn, h, exclude_id=None):
    sql, args = "SELECT id FROM bank_questions WHERE content_hash=?", [h]
    if exclude_id:
        sql += " AND id<>?"
        args.append(exclude_id)
    row = conn.execute(sql + " ORDER BY id LIMIT 1", args).fetchone()
    return row["id"] if row else None


# ------------------------------------------------------------------ import
def import_into_test(conn, test, ids, default_marks=1, marks_by_id=None):
    """-> {"added": int, "skipped": [{"id", "reason"}]}. Raises PublishedTestError for a non-draft test.
    The caller commits. Original library rows are only read, never written."""
    require_editable(test)
    marks_by_id = marks_by_id or {}
    result = {"added": 0, "skipped": []}
    existing_links = {r[0] for r in conn.execute(
        "SELECT source_bank_id FROM questions WHERE test_id=? AND source_bank_id IS NOT NULL", (test["id"],))}
    existing_hashes = {test_question_hash(r) for r in conn.execute(
        "SELECT question_text, option_a, option_b, option_c, option_d, correct_option, bank_image_id "
        "FROM questions WHERE test_id=?", (test["id"],))}
    order = next_sort_order(conn, test["id"])
    for qid in ids:
        b = conn.execute("""SELECT b.*, im.url AS image_url FROM bank_questions b
                            LEFT JOIN bank_images im ON im.id = b.image_id WHERE b.id=?""", (qid,)).fetchone()
        if b is None:
            result["skipped"].append({"id": qid, "reason": "প্রশ্নটি My Questions-এ পাওয়া যায়নি।"})
            continue
        h = bank_row_hash(b)
        if qid in existing_links or h in existing_hashes:
            result["skipped"].append({"id": qid, "reason": "এই প্রশ্ন এই Test-এ আগেই আছে (duplicate)।"})
            continue
        try:
            conn.execute(
                """INSERT INTO questions (test_id, question_text, option_a, option_b, option_c, option_d,
                   correct_option, marks, image_url, bank_image_id, source_bank_id, sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (test["id"], b["question_text"], b["option_a"], b["option_b"], b["option_c"], b["option_d"],
                 b["correct_option"], marks_by_id.get(qid, default_marks), b["image_url"], b["image_id"], qid, order))
        except sqlite3.IntegrityError:  # the unique index caught a parallel import of the same question
            result["skipped"].append({"id": qid, "reason": "এই প্রশ্ন এই Test-এ আগেই আছে (duplicate)।"})
            continue
        existing_links.add(qid)
        existing_hashes.add(h)
        order += 1
        result["added"] += 1
    return result


# ----------------------------------------------------- test -> My Questions
def save_to_my_questions(conn, question_id):
    """-> (state, bank_id). state: 'saved' | 'exists' | 'missing' | 'no_image'. The caller commits."""
    q = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
    if q is None:
        return "missing", None
    image_id = q["bank_image_id"]
    if not image_id and q["image_url"]:
        r = conn.execute("SELECT id FROM bank_images WHERE url=? ORDER BY id LIMIT 1", (q["image_url"],)).fetchone()
        image_id = r["id"] if r else None
        if not image_id and not (q["question_text"] or "").strip():
            return "no_image", None   # an image-only question whose picture is not in the Image Library
    if q["source_bank_id"]:
        row = conn.execute("SELECT id FROM bank_questions WHERE id=?", (q["source_bank_id"],)).fetchone()
        if row:
            return "exists", row["id"]
    h = content_hash(q["question_text"], q["option_a"], q["option_b"], q["option_c"], q["option_d"],
                     q["correct_option"], image_id)
    dup = find_bank_duplicate(conn, h)
    if dup:
        return "exists", dup
    now = _now()
    bid = conn.execute(
        """INSERT INTO bank_questions (question_text, image_id, option_a, option_b, option_c, option_d,
           correct_option, created_at, updated_at, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (q["question_text"] or "", image_id, q["option_a"], q["option_b"], q["option_c"], q["option_d"],
         q["correct_option"], now, now, h)).lastrowid
    return "saved", bid


def ids_already_in_test(conn, test_id, bank_rows):
    """Which of these My Questions rows the test already has (by import link or identical content)."""
    links = {r[0] for r in conn.execute(
        "SELECT source_bank_id FROM questions WHERE test_id=? AND source_bank_id IS NOT NULL", (test_id,))}
    hashes = {test_question_hash(r) for r in conn.execute(
        "SELECT question_text, option_a, option_b, option_c, option_d, correct_option, bank_image_id "
        "FROM questions WHERE test_id=?", (test_id,))}
    return {r["id"] for r in bank_rows if r["id"] in links or bank_row_hash(r) in hashes}
