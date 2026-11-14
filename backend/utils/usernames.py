# -*- coding: utf-8 -*-
"""Unique student usernames (e.g. "Rahul Das" -> "rahuldas", then "rahuldas2", "rahuldas3" ...).

Rules
  * Built from the student's real name: Unicode-normalised, accents removed, Bengali letters
    transliterated with a small built-in table (no extra dependency), everything else dropped.
  * Result is lowercase ASCII letters/digits only, 3-30 characters.
  * Uniqueness is enforced by the database (unique NOCASE index, migration 016). The code below
    only picks a free candidate and retries if two registrations race for the same one.
  * A username is assigned ONCE (``username IS NULL`` guard) and never regenerated on login.
"""
import re
import sqlite3
import unicodedata

MAX_LEN = 30
MIN_LEN = 3
FALLBACK = "student"
_MAX_TRIES = 200

_BN = {
    # vowels
    "অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u", "ঋ": "ri", "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
    # vowel signs
    "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri", "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou",
    # consonants
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng", "চ": "ch", "ছ": "chh", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n", "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "f", "ব": "b", "ভ": "bh", "ম": "m", "য": "j", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh",
    "স": "s", "হ": "h", "ড়": "r", "ঢ়": "rh", "য়": "y", "ৎ": "t", "ং": "ng", "ঃ": "h",
    # signs that add no sound (chandrabindu, nukta, hasanta)
    "ঁ": "", "়": "", "্": "",
    # digits
    "০": "0", "১": "1", "২": "2", "৩": "3", "৪": "4", "৫": "5", "৬": "6", "৭": "7", "৮": "8", "৯": "9",
}


def slugify(name):
    """Name -> lowercase ASCII [a-z0-9] base (may be empty or short; ``base_username`` fixes that)."""
    text = unicodedata.normalize("NFC", str(name or ""))
    # NFC keeps these as base letter + nukta (they are composition exclusions), so join them first.
    text = text.replace("ড\u09bc", "ড়").replace("ঢ\u09bc", "ঢ়").replace("য\u09bc", "য়")
    text = "".join(_BN.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    return re.sub(r"[^a-z0-9]", "", text)


def base_username(name):
    base = slugify(name)
    if not base:
        return FALLBACK
    if len(base) < MIN_LEN:
        base = FALLBACK + base
    return base[:MAX_LEN - 4]          # leave room for a numeric suffix


def candidates(name):
    """Endless-ish stream: base, base2, base3, ... (suffix always fits in MAX_LEN)."""
    base = base_username(name)
    yield base
    for n in range(2, _MAX_TRIES + 2):
        yield "%s%d" % (base, n)


def is_taken(conn, username):
    return conn.execute("SELECT 1 FROM students WHERE username=? COLLATE NOCASE", (username,)).fetchone() is not None


def assign_username(conn, student_id, name):
    """Give ``student_id`` a unique username if it has none yet; return the username.

    Safe under concurrency: the unique index rejects a duplicate, we then try the next candidate.
    The caller owns the transaction/commit.
    """
    row = conn.execute("SELECT username FROM students WHERE id=?", (student_id,)).fetchone()
    if row is None:
        return None
    if row[0]:
        return row[0]
    for cand in candidates(name):
        if is_taken(conn, cand):
            continue
        try:
            conn.execute("SAVEPOINT uname")
            conn.execute("UPDATE students SET username=? WHERE id=? AND username IS NULL", (cand, student_id))
            conn.execute("RELEASE uname")
            return cand
        except sqlite3.IntegrityError:        # someone else took it between our check and the UPDATE
            conn.execute("ROLLBACK TO uname")
            conn.execute("RELEASE uname")
    # Deterministic last resort: the id is unique, so this cannot collide.
    cand = "%s%d" % (FALLBACK, student_id)
    conn.execute("UPDATE students SET username=? WHERE id=? AND username IS NULL", (cand, student_id))
    return cand
