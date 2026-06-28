"""One-way employee-ID anonymization (Task 15.12).

Background
----------
The privacy operations runbook (``docs/operations/privacy.md`` §6.3) lists
"anonymization of past Cycle Response rows" as a step required only when
a personal-request (e.g., GDPR Right to Erasure) is received. The system
keeps past Response rows under the *original* ``employeeId`` for audit
and operational reasons; on a personal request, every Response row for
the departed employee must have its ``employeeId`` replaced with an
irreversibly-hashed value.

Design contract
---------------
:func:`anonymize_employee_id` is a *pure* function — same inputs always
yield the same output, no I/O, no globals.

Output format::

    "ANON_" + sha256(f"{salt}:{employee_id}").hexdigest()[:32]

Where:

* ``ANON_`` is a stable prefix that lets downstream code (audit logs,
  SPA visualisations) identify anonymized rows without consulting any
  side channel.
* The SHA-256 digest is truncated to the first 32 hex characters
  (= 128 bits of entropy), which is far above the birthday-collision
  threshold for any plausible employee population (Property 20 ext.).

Inputs
------
``employee_id``: non-empty ``str``. Empty / non-``str`` input raises
``ValueError`` / ``TypeError`` respectively — there is no fallback per
project principle 19(b).

``salt``: non-empty ``str`` — the system-wide secret. Operationally the
salt is injected into the EmployeeApi Lambda via the
``EMPLOYEE_ANONYMIZE_SALT`` environment variable (CFn Parameter
``EmployeeAnonymizeSalt`` with ``NoEcho: true``). Different salts produce
different anonymized IDs for the same ``employee_id`` (Property 20 ext.).

The salt MUST be treated as a long-lived secret. Rotating the salt
breaks the link between previously-anonymized rows and newly-anonymized
rows — by design — and is therefore an irreversible system-wide event.
"""

from __future__ import annotations

import hashlib

#: Stable prefix marking anonymized ``employeeId`` values. Downstream
#: code can use ``str.startswith(ANONYMIZED_ID_PREFIX)`` to detect a
#: scrubbed row without consulting a side channel.
ANONYMIZED_ID_PREFIX: str = "ANON_"

#: Number of hex characters retained from the SHA-256 digest (= 128
#: bits of entropy after truncation). Chosen to balance row-size
#: footprint against the birthday-collision threshold for plausible
#: employee populations.
_HASH_HEX_LENGTH: int = 32


def anonymize_employee_id(employee_id: str, salt: str) -> str:
    """Return the irreversible anonymized form of an employee ID.

    Args:
        employee_id: The original employee identifier (UUID v4 string
            in production). Must be a non-empty ``str``.
        salt: System-wide secret salt. Must be a non-empty ``str``.
            See module docstring for operational handling.

    Returns:
        A string of the form ``"ANON_" + <32 hex chars>``. The same
        ``(employee_id, salt)`` pair always yields the same output
        (determinism). Different salts produce different outputs for
        the same ``employee_id``.

    Raises:
        TypeError: If ``employee_id`` or ``salt`` is not ``str``.
        ValueError: If either argument is an empty ``str``.
    """
    if not isinstance(employee_id, str):
        raise TypeError(
            f"employee_id must be str, got {type(employee_id).__name__}"
        )
    if not isinstance(salt, str):
        raise TypeError(f"salt must be str, got {type(salt).__name__}")
    if employee_id == "":
        raise ValueError("employee_id must be non-empty")
    if salt == "":
        raise ValueError("salt must be non-empty")

    # Encode salt and employee_id with a `:` separator so that the
    # concatenation is unambiguous (e.g., (salt="ab", emp="cd") and
    # (salt="a", emp="bcd") produce distinct digests).
    payload = f"{salt}:{employee_id}".encode("utf-8")
    digest_hex = hashlib.sha256(payload).hexdigest()
    return ANONYMIZED_ID_PREFIX + digest_hex[:_HASH_HEX_LENGTH]
