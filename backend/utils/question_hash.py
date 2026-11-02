# -*- coding: utf-8 -*-
"""One definition of "the same question" for My Questions (no imports on purpose: migration 014 uses it too).

Two questions are the same when their text, four options, correct option and image are the same
(extra spaces and upper/lower case of the correct letter are ignored). Marks are NOT part of it:
marks belong to a test, not to the saved question.
"""
import hashlib


def _clean(value):
    return " ".join(str(value or "").split())


def content_hash(text, a, b, c, d, correct, image_id=None):
    parts = [_clean(text), _clean(a), _clean(b), _clean(c), _clean(d),
             _clean(correct).upper(), str(int(image_id)) if image_id else "0"]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
