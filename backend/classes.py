# -*- coding: utf-8 -*-
"""Canonical class values and validation for Arnab Math Class.

Single source of truth for allowed class_name values used in registration,
teacher student management, tests, notes, and announcements.

Canonical storage values (always uppercase ASCII, no superscript):
  SIX, SEVEN, EIGHT, NINE, TEN, 11TH, 12TH, OTHER

Display labels use Unicode superscript for 11ᵗʰ / 12ᵗʰ only.
Empty string / None means "all classes" where the form allows it.
"""

from __future__ import annotations

# Ordered list of canonical values stored in the database.
CANONICAL_CLASSES: tuple[str, ...] = (
    "SIX",
    "SEVEN",
    "EIGHT",
    "NINE",
    "TEN",
    "11TH",
    "12TH",
    "OTHER",
)

# value -> display label (HTML-safe plain text; templates may use |safe if needed)
DISPLAY_LABELS: dict[str, str] = {
    "SIX": "SIX",
    "SEVEN": "SEVEN",
    "EIGHT": "EIGHT",
    "NINE": "NINE",
    "TEN": "TEN",
    "11TH": "11ᵗʰ",
    "12TH": "12ᵗʰ",
    "OTHER": "OTHER",
}

# Map legacy free-text / old-dropdown values onto canonical ones.
# Matching is case-insensitive on the stripped input.
_LEGACY_MAP: dict[str, str] = {
    "six": "SIX",
    "class six": "SIX",
    "class 6": "SIX",
    "6": "SIX",
    "seven": "SEVEN",
    "class seven": "SEVEN",
    "class 7": "SEVEN",
    "7": "SEVEN",
    "eight": "EIGHT",
    "class eight": "EIGHT",
    "class 8": "EIGHT",
    "8": "EIGHT",
    "nine": "NINE",
    "class nine": "NINE",
    "class 9": "NINE",
    "9": "NINE",
    "ten": "TEN",
    "class ten": "TEN",
    "class 10": "TEN",
    "10": "TEN",
    "ssc": "TEN",
    "ssc batch": "TEN",
    "ssc batch.": "TEN",
    "11": "11TH",
    "11th": "11TH",
    "11ᵗʰ": "11TH",
    "class 11": "11TH",
    "hsc": "12TH",
    "hsc batch": "12TH",
    "12": "12TH",
    "12th": "12TH",
    "12ᵗʰ": "12TH",
    "class 12": "12TH",
    "other": "OTHER",
}


def normalize_class(value: str | None, *, allow_empty: bool = False) -> str | None:
    """Return canonical class value, or None if empty and allowed, or None if invalid.

    When allow_empty is True, blank input becomes '' (all-classes).
    When allow_empty is False, blank input is invalid (returns None).
    """
    if value is None:
        return "" if allow_empty else None
    raw = str(value).strip()
    if not raw:
        return "" if allow_empty else None
    upper = raw.upper()
    if upper in CANONICAL_CLASSES:
        return upper
    # Try legacy map (lowercase key)
    mapped = _LEGACY_MAP.get(raw.lower())
    if mapped:
        return mapped
    # Already-canonical with different spacing / case already handled above
    return None


def is_valid_class(value: str | None, *, allow_empty: bool = False) -> bool:
    """True if value is an allowed class (or empty when allow_empty)."""
    if value is None or str(value).strip() == "":
        return allow_empty
    return normalize_class(value, allow_empty=False) is not None


def require_class(value: str | None, *, allow_empty: bool = False) -> str:
    """Normalize and return canonical value, or raise ValueError."""
    result = normalize_class(value, allow_empty=allow_empty)
    if result is None:
        raise ValueError("Invalid class. Please select a valid class from the list.")
    return result


def class_options(*, include_all: bool = False, all_label: str = "Available for all classes") -> list[dict]:
    """Options for HTML <select>: list of {value, label}.

    When include_all is True, prepends an empty-value option for 'all classes'.
    """
    opts: list[dict] = []
    if include_all:
        opts.append({"value": "", "label": all_label})
    for v in CANONICAL_CLASSES:
        opts.append({"value": v, "label": DISPLAY_LABELS[v]})
    return opts


def display_class(value: str | None) -> str:
    """Human-readable label for a stored class_name (legacy or canonical)."""
    if not value:
        return "All"
    norm = normalize_class(value, allow_empty=False)
    if norm:
        return DISPLAY_LABELS.get(norm, norm)
    return str(value)


def classes_equal(a: str | None, b: str | None) -> bool:
    """True if two class values refer to the same class (after normalization).

    Empty / None on either side means unrestricted (matches anything) only when
    used for item visibility — call sites should still treat empty item class
    as visible to all. This helper compares student-to-item identity.
    """
    na = normalize_class(a, allow_empty=True) or ""
    nb = normalize_class(b, allow_empty=True) or ""
    if not na or not nb:
        return na == nb
    return na == nb


# Jinja-friendly list of (value, label) for templates that loop without the helper.
CLASS_CHOICES = [(v, DISPLAY_LABELS[v]) for v in CANONICAL_CLASSES]
