"""Phone-number duplication detector for the Employee master.

Used by:
  * `employee_api/handler.py` (POST /employees, PUT /employees/{id}):
      reject inserts/updates whose phoneNumber collides with any existing row.
  * `csv_parser.parse_employee_csv` is *not* a caller here — CSV-internal
    duplicates are detected by the parser itself (Property 7).  This module
    handles the **table-vs-candidate** axis, which must additionally
    consider rows that have been *logically deleted* (Requirement 15.3 sets
    `phoneNumber = ""` to drop the row out of `PhoneNumberIndex`, but the
    `phoneNumber` attribute may still carry the original E.164 string for
    a brief audit-trail window before the empty-string sentinel is written).

Property 5 (target of Hypothesis PBT in Phase 13.5):
    For all `existing_phones` (Iterable[str]) and `new_phone` (object):
        find_duplicate_phone(existing_phones, new_phone) is True
        iff
          (i)  new_phone is a non-empty `str`, AND
          (ii) at least one element of `existing_phones` that is a `str`
               is exactly equal (case-sensitive) to new_phone.

Design notes:
    - The function is pure: no I/O, no globals, no logging.
    - `new_phone` must be a non-empty `str`.  Non-`str` (int / None / list /
      bool / ...) and empty string return False by contract.  Rationale:
      an *invalid* candidate is not, by definition, a duplicate of anything
      already valid in the table; the caller is responsible for separately
      rejecting invalid input with a 400-style error (see Property 3 /
      `is_valid_e164`).  Returning True here would conflate "invalid input"
      with "duplicate input", losing diagnostic clarity.
    - Non-`str` members of `existing_phones` are silently skipped.
      Rationale: DynamoDB's `phoneNumber` attribute is schema-typed `S`
      (string), so a non-`str` here is malformed data — but a malformed
      *existing* row should not be allowed to falsely match a candidate
      (which would incorrectly block insertion).  Filtering keeps the
      predicate honest under data corruption.
    - E.164 format validation is **not** performed.  Callers must validate
      format via `shared.employee.validate.is_valid_e164` *before* the
      duplication check.  This module's job is set membership, not syntax.
    - Comparison is **case-sensitive**.  E.164 strings are ASCII-digit-only
      so case is irrelevant in practice, but the contract is explicit to
      prevent future regressions (e.g. if a caller passes a `str` subclass
      with overridden `__eq__`).
    - Order-independence: the predicate is a function of the multiset of
      `existing_phones`, not its order.  The `any()` short-circuit may
      visit elements in iteration order, but the *truth value* of the
      result does not depend on order.
    - `existing_phones` is consumed once.  If the caller passes a generator,
      it will be exhausted; pass `list(...)` if you need to iterate later.

Logical-delete handling (Requirement 2.3 / 3.4 — task Done-When):
    The caller MUST pass *all* phoneNumber values from the Employee table,
    including those of logically-deleted rows whose `phoneNumber` attribute
    still carries the original E.164 string.  The function then returns
    True if the candidate matches any such value, which is the desired
    behaviour: re-registering a phone number that was logically deleted
    should be blocked by default (admins can manually un-delete instead).
    Rows whose `phoneNumber` is already `""` will be silently skipped via
    the non-empty-`str` gate on the candidate side and the
    `isinstance(phone, str) and phone == new_phone` gate on the row side
    (empty string never equals a non-empty `new_phone`).
"""

from __future__ import annotations

from typing import Iterable


def find_duplicate_phone(
    existing_phones: Iterable[str], new_phone: object
) -> bool:
    """Return True iff `new_phone` matches any element of `existing_phones`.

    Args:
        existing_phones: Iterable of E.164 strings representing the current
            phone numbers held in the Employee table — **including
            logically deleted rows** (the `phoneNumber` attribute may still
            hold a value for audit-trail purposes before the empty-string
            sentinel is written).  Non-`str` members are silently skipped.
        new_phone: Candidate string the caller wants to insert.  Expected
            to be an E.164-shaped `str`, but the function itself does
            not validate format — only the type and non-emptiness.

    Returns:
        True iff
            * `new_phone` is an instance of `str`, AND
            * `new_phone` is non-empty (i.e. `new_phone != ""`), AND
            * at least one element of `existing_phones` is a `str`
              exactly equal (case-sensitive) to `new_phone`.

        False in every other case (non-`str` / empty `new_phone`, no
        matching element, empty `existing_phones`, ...).
    """
    # Gate 1: candidate must be a non-empty str.  Covers:
    #   * new_phone is None / int / list / dict / bool / bytes / ...  → False
    #   * new_phone is ""                                              → False
    if not isinstance(new_phone, str) or new_phone == "":
        return False

    # Gate 2: any string element that exactly matches the candidate.
    # `isinstance(phone, str)` filter is defensive — DynamoDB schema
    # guarantees `phoneNumber` is `S` (string), but malformed data must
    # not falsely match.  Empty-string rows (logical-delete sentinel)
    # also flow through this filter harmlessly — `"" != new_phone` always
    # holds because Gate 1 already excluded `new_phone == ""`.
    for phone in existing_phones:
        if isinstance(phone, str) and phone == new_phone:
            return True
    return False
