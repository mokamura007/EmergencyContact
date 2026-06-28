"""Validation primitives for the Employee master.

E.164 spec (Requirement 2.7):
    A leading "+" immediately followed by 1 to 15 decimal digits.
    No country-code semantics; pure syntactic check.
"""

from __future__ import annotations

import re

_E164_RE = re.compile(r"^\+\d{1,15}$")

MAX_NAME_LENGTH = 100


def is_valid_e164(phone: object) -> bool:
    """Return True iff `phone` is a string matching the E.164 pattern."""
    if not isinstance(phone, str):
        return False
    return bool(_E164_RE.match(phone))


def is_valid_name(name: object) -> bool:
    """Return True iff `name` is a non-empty string within size bounds."""
    if not isinstance(name, str):
        return False
    stripped = name.strip()
    return 0 < len(stripped) <= MAX_NAME_LENGTH
