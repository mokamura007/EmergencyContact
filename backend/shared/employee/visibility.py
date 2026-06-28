"""Employee-visibility predicate for Requirement 13.5 / 15.4 / Property 2.

Requirement 13.5:
    THE Inbound_Handler SHALL Employee_Master を電話番号 (E.164) で照合し、
    deleted=true または phoneNumber=null の社員レコードを「未登録」として扱う。

Requirement 15.4:
    THE Admin_Console / API SHALL deleted=true の社員レコードを一覧 / 詳細
    取得対象から除外する（論理削除済データの参照拒否）。

Property 2 (target of Hypothesis PBT in Phase 13.2):
    For all `employee`:
        is_visible(employee) is True
        iff
          (i)   employee is a `dict`, AND
          (ii)  employee.get("deleted") is not True (False / absent → OK), AND
          (iii) employee.get("phoneNumber") is a non-empty `str`
                (None / empty / missing / non-str → not visible).

Design notes:
    - This module is pure: no I/O, no globals, no logging.
    - `deleted` is expected to be a `bool` (DynamoDB BOOL attribute), but
      this predicate treats *any value other than the literal `True`* as
      "not deleted" — i.e. absent key, `None`, `False`, `0`, "", `[]`,
      and even `1`/`"true"` (non-bool truthy values) all pass through the
      `deleted` gate.  Only the exact value `True` blocks visibility.
      Rationale: callers must never display a row whose `deleted` flag has
      been set to True, but a malformed `deleted` value is *not* a signal
      to hide the row; that would mask underlying data-corruption bugs
      behind a silent invisibility, which violates project principle 19(b)
      (no fallback behaviour).  Callers that need strict shape checking
      must do so separately.
    - `phoneNumber` must be a non-empty `str`.  The DynamoDB delete path
      sets `phoneNumber` to the empty string (`""`) to drop the row out
      of the `PhoneNumberIndex` GSI (Requirement 15.3 / Property 20), so
      an empty-string phoneNumber must be treated as "no phone" and the
      row hidden.  This predicate intentionally does *not* validate
      E.164 format — that is the role of `shared.employee.validate.is_valid_e164`.
    - Non-dict input (None, list, str, int, bool, ...) returns False.
      `bool` is a subclass of `int`, not of `dict`, so this is handled
      naturally by `isinstance(employee, dict)`.
"""

from __future__ import annotations

from typing import Any

#: Attribute name carrying the logical-delete flag in the Employee table.
DELETED_ATTR = "deleted"

#: Attribute name carrying the E.164 phone number in the Employee table.
PHONE_NUMBER_ATTR = "phoneNumber"


def is_visible(employee: Any) -> bool:
    """Return True iff `employee` is a record visible to the application layer.

    Args:
        employee: Employee record (expected `dict`, DynamoDB-shaped):
            ``{"employeeId": str, "name": str, "phoneNumber": str | "",
               "deleted": bool, ...}``.

    Returns:
        True iff all of the following hold:
          * `employee` is a `dict`,
          * `employee.get("deleted")` is not the literal `True`
            (False / absent / None all pass),
          * `employee.get("phoneNumber")` is a non-empty `str`
            (None / "" / missing / non-str → not visible).

        False in every other case (including non-dict input).
    """
    # Guard: must be a dict.  Note: `bool`/`int`/`str`/`list`/None all fail
    # `isinstance(..., dict)` and therefore correctly return False.
    if not isinstance(employee, dict):
        return False

    # Gate 1: `deleted` must not be the literal True.  Only `is True` blocks.
    # Absent key, None, False, and other values do NOT hide the row — only
    # the explicit True flag set by the delete handler does.
    if employee.get(DELETED_ATTR) is True:
        return False

    # Gate 2: `phoneNumber` must be a non-empty str.  Covers:
    #   * missing key      → `.get` returns None       → not isinstance(str)
    #   * value is None    → not isinstance(str)
    #   * value is int/list/dict/bool → not isinstance(str) (note: bool is
    #     not isinstance(str), so it is correctly rejected here too)
    #   * value is ""      → isinstance(str) but empty → rejected
    phone = employee.get(PHONE_NUMBER_ATTR)
    if not isinstance(phone, str) or phone == "":
        return False

    return True
