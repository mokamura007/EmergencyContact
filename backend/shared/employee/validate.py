"""Validation primitives for the Employee master.

E.164 spec (Requirement 2.7):
    A leading "+" immediately followed by 1 to 15 decimal digits.
    No country-code semantics; pure syntactic check.

Email spec (Requirement 2.1 revision, admin registration from SPA):
    RFC 5322 simplified pattern — non-empty local + "@" + non-empty
    domain + "." + non-empty TLD. Cognito's email-as-username feature
    enforces a similar shape upstream; this SPA-side / API-side check
    is the front-line filter to avoid unnecessary admin_create_user
    calls that would otherwise fail with InvalidParameterException.

Domestic JP format:
    A leading "0" immediately followed by 9 to 10 decimal digits
    (total 10-11 digits). Covers mobile (090/080/070 = 11 digits)
    and landline (03/06 etc. = 10 digits).
"""

from __future__ import annotations

import re

_E164_RE = re.compile(r"^\+\d{1,15}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_DOMESTIC_JP_RE = re.compile(r"^0\d{9,10}$")

MAX_NAME_LENGTH = 100


def is_valid_e164(phone: object) -> bool:
    """Return True iff `phone` is a string matching the E.164 pattern."""
    if not isinstance(phone, str):
        return False
    return bool(_E164_RE.match(phone))


def is_valid_domestic_jp(phone: object) -> bool:
    """Return True iff `phone` is a valid Japanese domestic phone number.

    Accepts: leading '0' followed by 9-10 digits (total 10-11 digits).
    """
    if not isinstance(phone, str):
        return False
    return bool(_DOMESTIC_JP_RE.match(phone))


def domestic_to_e164(phone: str) -> str:
    """Convert a Japanese domestic phone number to E.164 format.

    Replaces leading '0' with '+81'.
    If the input is not in domestic format, returns unchanged.
    """
    if _DOMESTIC_JP_RE.match(phone):
        return "+81" + phone[1:]
    return phone


def is_valid_name(name: object) -> bool:
    """Return True iff `name` is a non-empty string within size bounds."""
    if not isinstance(name, str):
        return False
    stripped = name.strip()
    return 0 < len(stripped) <= MAX_NAME_LENGTH


def is_valid_email(email: object) -> bool:
    """Return True iff `email` is a string matching the RFC 5322 simplified pattern.

    Consumed by:
      - ``employee_api._create_employee`` — pre-validation of ``adminEmail``
        before calling ``cognito-idp.admin_create_user``.
      - ``auth_failure_reporter.lambda_handler`` — shape check of the
        ``userIdentifier`` field before recording a lockout timestamp.
    """
    if not isinstance(email, str):
        return False
    return bool(_EMAIL_RE.match(email))
