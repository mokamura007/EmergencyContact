"""Property 2 — deleted-employee visibility PBT (Phase 13.2).

Validates: Requirements 13.5, 15.4

Contract (verbatim from shared/employee/visibility.py):
    is_visible(employee) is True
    iff
      (i)   employee is a dict, AND
      (ii)  employee.get("deleted") is not the literal True
            (False / None / absent all pass), AND
      (iii) employee.get("phoneNumber") is a non-empty str
            (None / "" / missing / non-str → not visible).

This file enforces the 9 cases enumerated in the task:
  (a) True : deleted=False + phoneNumber non-empty str → True.
  (b) True : `deleted` key absent + phoneNumber non-empty str → True.
  (c) False: deleted=True → always False, regardless of phoneNumber.
  (d) False: phoneNumber=None → False.
  (e) False: phoneNumber="" → False.
  (f) False: `phoneNumber` key absent → False.
  (g) False: phoneNumber is non-str (int / list / dict / ...) → False.
  (h) False: employee is non-dict (None / list / str / int / ...) → False.
  (i) Bidirectional: (deleted=True) OR (phoneNumber=None) → False
      (direct verification of the task Done-When "OR" condition).
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.employee.visibility import (
    DELETED_ATTR,
    PHONE_NUMBER_ATTR,
    is_visible,
)
from tests.strategies import e164_phone, non_string_value

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.large_base_example,
    ],
)


# ---------------------------------------------------------------------------
# Local strategies — employee-record shapes specific to visibility tests.
# Kept in-file because they are unlikely to be reused outside Phase 13.2.
# ---------------------------------------------------------------------------

#: Strategy for `employeeId` field: realistic UUID-shaped 1..40 char string.
_employee_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=40,
)

#: Strategy for `name` field: 1..100 char non-empty string.
_employee_name = st.text(min_size=1, max_size=100)

#: Strategy for arbitrary extra attributes (sub map of other DynamoDB-shaped
#: keys).  Used to ensure visibility judgement does not depend on the
#: presence of unrelated keys.
_extra_attrs = st.dictionaries(
    keys=st.text(min_size=1, max_size=10).filter(
        lambda s: s not in (DELETED_ATTR, PHONE_NUMBER_ATTR, "employeeId", "name")
    ),
    values=st.one_of(
        st.text(max_size=20),
        st.integers(),
        st.booleans(),
        st.none(),
    ),
    max_size=4,
)


@st.composite
def valid_employee_record(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a visible employee record: deleted=False + non-empty phoneNumber."""
    record: dict[str, Any] = {
        "employeeId": draw(_employee_id),
        "name": draw(_employee_name),
        PHONE_NUMBER_ATTR: draw(e164_phone),
        DELETED_ATTR: False,
    }
    record.update(draw(_extra_attrs))
    return record


@st.composite
def valid_employee_record_no_deleted_key(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a visible record where the `deleted` key is *absent* entirely."""
    record: dict[str, Any] = {
        "employeeId": draw(_employee_id),
        "name": draw(_employee_name),
        PHONE_NUMBER_ATTR: draw(e164_phone),
    }
    extras = draw(_extra_attrs)
    # Defensive: ensure `_extra_attrs` did not synthesise a `deleted` key.
    extras.pop(DELETED_ATTR, None)
    record.update(extras)
    return record


@st.composite
def deleted_employee_record(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a deleted record (deleted=True), with arbitrary phoneNumber.

    The phoneNumber may be a valid E.164 (which DynamoDB never actually
    holds for deleted rows — the delete path sets it to "") or any other
    value, including the sentinel empty string.  Either way, the deleted
    flag alone must make `is_visible` False.
    """
    phone_strategy = st.one_of(
        e164_phone,        # phoneNumber is still a valid str
        st.just(""),       # phoneNumber NULL-ed via empty-string sentinel
        st.none(),         # phoneNumber set to None
    )
    record: dict[str, Any] = {
        "employeeId": draw(_employee_id),
        "name": draw(_employee_name),
        PHONE_NUMBER_ATTR: draw(phone_strategy),
        DELETED_ATTR: True,
    }
    record.update(draw(_extra_attrs))
    return record


@st.composite
def nulled_phone_employee_record(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a record with phoneNumber=None, deleted=False (Gate-2-only fail)."""
    record: dict[str, Any] = {
        "employeeId": draw(_employee_id),
        "name": draw(_employee_name),
        PHONE_NUMBER_ATTR: None,
        DELETED_ATTR: False,
    }
    record.update(draw(_extra_attrs))
    return record


@st.composite
def empty_phone_employee_record(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a record with phoneNumber="", deleted=False (Gate-2-only fail)."""
    record: dict[str, Any] = {
        "employeeId": draw(_employee_id),
        "name": draw(_employee_name),
        PHONE_NUMBER_ATTR: "",
        DELETED_ATTR: False,
    }
    record.update(draw(_extra_attrs))
    return record


@st.composite
def missing_phone_employee_record(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a record where the `phoneNumber` key is *absent* entirely."""
    record: dict[str, Any] = {
        "employeeId": draw(_employee_id),
        "name": draw(_employee_name),
        DELETED_ATTR: False,
    }
    extras = draw(_extra_attrs)
    extras.pop(PHONE_NUMBER_ATTR, None)
    record.update(extras)
    return record


#: Strategy for non-str phoneNumber values: int / float / bool / list / dict /
#: bytes / tuple / set / None.  None is acceptable here (Gate 2 rejects it).
_non_str_non_empty = non_string_value


@st.composite
def non_str_phone_employee_record(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a record where phoneNumber is non-str (covers Gate 2 type check)."""
    bogus_phone = draw(_non_str_non_empty)
    # Exclude the rare case where `non_string_value` yields a str subclass
    # (it does not today, but guard explicitly).
    if isinstance(bogus_phone, str):
        bogus_phone = 12345  # fallback to a clearly non-str value
    record: dict[str, Any] = {
        "employeeId": draw(_employee_id),
        "name": draw(_employee_name),
        PHONE_NUMBER_ATTR: bogus_phone,
        DELETED_ATTR: False,
    }
    record.update(draw(_extra_attrs))
    return record


# ---------------------------------------------------------------------------
# Property 2 (a) — True: deleted=False + phoneNumber non-empty str.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(
    employee={
        "employeeId": "emp-001",
        "name": "山田太郎",
        PHONE_NUMBER_ATTR: "+819012345678",
        DELETED_ATTR: False,
    }
)
@given(employee=valid_employee_record())
def test_property2_true_when_not_deleted_and_phone_present(
    employee: dict[str, Any],
) -> None:
    """deleted=False + non-empty phoneNumber → True.

    Validates: Requirements 13.5, 15.4
    """
    assert is_visible(employee) is True, (
        f"expected True for valid record {employee!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 (b) — True: `deleted` key absent + phoneNumber non-empty.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(
    employee={
        "employeeId": "emp-002",
        "name": "佐藤花子",
        PHONE_NUMBER_ATTR: "+819098765432",
    }
)
@given(employee=valid_employee_record_no_deleted_key())
def test_property2_true_when_deleted_key_absent(
    employee: dict[str, Any],
) -> None:
    """`deleted` key absent + non-empty phoneNumber → True.

    Absent `deleted` is treated as "not deleted" (the DynamoDB schema
    always writes `deleted=False` on insert, but defensive code must not
    hide a row just because the key is missing).

    Validates: Requirements 13.5, 15.4
    """
    assert DELETED_ATTR not in employee  # generator invariant
    assert is_visible(employee) is True, (
        f"expected True for deleted-absent record {employee!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 (c) — False: deleted=True → always False.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(
    employee={
        "employeeId": "emp-003",
        "name": "鈴木一郎",
        PHONE_NUMBER_ATTR: "+819011112222",
        DELETED_ATTR: True,
    }
)
@example(
    employee={
        "employeeId": "emp-004",
        "name": "高橋次郎",
        PHONE_NUMBER_ATTR: "",
        DELETED_ATTR: True,
    }
)
@given(employee=deleted_employee_record())
def test_property2_false_when_deleted(employee: dict[str, Any]) -> None:
    """deleted=True → False (regardless of phoneNumber).

    Validates: Requirement 15.4
    """
    assert employee[DELETED_ATTR] is True  # generator invariant
    assert is_visible(employee) is False, (
        f"expected False for deleted record {employee!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 (d) — False: phoneNumber=None.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(employee=nulled_phone_employee_record())
def test_property2_false_when_phone_is_none(employee: dict[str, Any]) -> None:
    """phoneNumber=None → False.

    Validates: Requirement 13.5
    """
    assert employee[PHONE_NUMBER_ATTR] is None  # generator invariant
    assert is_visible(employee) is False, (
        f"expected False for None-phone record {employee!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 (e) — False: phoneNumber="" (DynamoDB null-out sentinel).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(employee=empty_phone_employee_record())
def test_property2_false_when_phone_is_empty_str(
    employee: dict[str, Any],
) -> None:
    """phoneNumber="" → False (DynamoDB sentinel for logical-deleted rows).

    Validates: Requirement 13.5
    """
    assert employee[PHONE_NUMBER_ATTR] == ""  # generator invariant
    assert is_visible(employee) is False, (
        f"expected False for empty-phone record {employee!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 (f) — False: `phoneNumber` key absent entirely.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(employee=missing_phone_employee_record())
def test_property2_false_when_phone_key_absent(
    employee: dict[str, Any],
) -> None:
    """`phoneNumber` key absent from dict → False.

    Validates: Requirement 13.5
    """
    assert PHONE_NUMBER_ATTR not in employee  # generator invariant
    assert is_visible(employee) is False, (
        f"expected False for missing-phone record {employee!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 (g) — False: phoneNumber is non-str (int / list / dict / ...).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(employee=non_str_phone_employee_record())
def test_property2_false_when_phone_is_non_str(
    employee: dict[str, Any],
) -> None:
    """phoneNumber is non-str → False.

    Validates: Requirement 13.5
    """
    phone = employee[PHONE_NUMBER_ATTR]
    assert not isinstance(phone, str)  # generator invariant
    assert is_visible(employee) is False, (
        f"expected False for non-str-phone record {employee!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 (h) — False: employee is non-dict (None / list / str / ...).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(bogus=non_string_value)
def test_property2_false_when_employee_is_non_dict(bogus: object) -> None:
    """Any non-dict `employee` → False.

    Validates: Requirements 13.5, 15.4
    """
    # `non_string_value` does not emit dicts today, but guard defensively.
    if isinstance(bogus, dict):
        return
    assert is_visible(bogus) is False, (
        f"expected False for non-dict input {bogus!r}"
    )


def test_unit_none_employee_is_false() -> None:
    """None employee → False (anchor for case h)."""
    assert is_visible(None) is False


def test_unit_string_employee_is_false() -> None:
    """str employee → False (anchor for case h)."""
    assert is_visible("not-a-dict") is False


def test_unit_list_employee_is_false() -> None:
    """list employee → False (anchor for case h)."""
    assert is_visible([{"employeeId": "x"}]) is False


def test_unit_int_employee_is_false() -> None:
    """int employee → False (anchor for case h)."""
    assert is_visible(42) is False


def test_unit_bool_employee_is_false() -> None:
    """bool employee → False (bool is not a dict, anchor for case h)."""
    assert is_visible(True) is False
    assert is_visible(False) is False


# ---------------------------------------------------------------------------
# Property 2 (i) — Bidirectional: deleted=True OR phoneNumber=None → False.
#
# Directly verifies the task Done-When wording: "deleted=true OR
# phoneNumber=null → always False".
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(
    employee_id=_employee_id,
    name=_employee_name,
    deleted_flag=st.booleans(),
    phone_value=st.one_of(e164_phone, st.none()),
)
def test_property2_or_condition_blocks_visibility(
    employee_id: str,
    name: str,
    deleted_flag: bool,
    phone_value: str | None,
) -> None:
    """For any (deleted_flag, phone_value) combination:
    `(deleted_flag is True) OR (phone_value is None)` → `is_visible` False;
    otherwise → True.

    This is the direct PBT encoding of the Done-When clause
    "deleted=true または phoneNumber=null で常に false".

    Validates: Requirements 13.5, 15.4
    """
    employee = {
        "employeeId": employee_id,
        "name": name,
        PHONE_NUMBER_ATTR: phone_value,
        DELETED_ATTR: deleted_flag,
    }
    expected_invisible = (deleted_flag is True) or (phone_value is None)
    actual_visible = is_visible(employee)
    if expected_invisible:
        assert actual_visible is False, (
            f"expected False (OR-condition triggered) for {employee!r}"
        )
    else:
        assert actual_visible is True, (
            f"expected True (neither OR-clause triggered) for {employee!r}"
        )


# ---------------------------------------------------------------------------
# Additional defensive / cross-case anchors.
# ---------------------------------------------------------------------------


def test_unit_realistic_dynamodb_get_item_shape_visible() -> None:
    """Anchor: shape returned by DynamoDB GetItem for an active employee."""
    item = {
        "employeeId": "abc-123",
        "name": "山田太郎",
        PHONE_NUMBER_ATTR: "+819012345678",
        "role": "employee",
        DELETED_ATTR: False,
        "createdAt": "2026-06-25T00:00:00+00:00",
        "updatedAt": "2026-06-25T00:00:00+00:00",
    }
    assert is_visible(item) is True


def test_unit_realistic_dynamodb_get_item_shape_after_delete() -> None:
    """Anchor: shape after EMPLOYEE_DELETE (deleted=True + phoneNumber='')."""
    item = {
        "employeeId": "abc-123",
        "name": "山田太郎",
        PHONE_NUMBER_ATTR: "",
        "role": "employee",
        DELETED_ATTR: True,
        "createdAt": "2026-06-25T00:00:00+00:00",
        "updatedAt": "2026-06-25T01:00:00+00:00",
    }
    assert is_visible(item) is False


def test_unit_empty_dict_is_false() -> None:
    """Anchor: empty dict (missing both fields) → False."""
    assert is_visible({}) is False


def test_unit_deleted_false_explicit_with_valid_phone_is_true() -> None:
    """Anchor: deleted=False + valid phone → True."""
    assert is_visible(
        {PHONE_NUMBER_ATTR: "+819012345678", DELETED_ATTR: False}
    ) is True


def test_unit_deleted_none_is_treated_as_not_deleted() -> None:
    """Anchor: deleted=None is not literal True → falls through Gate 1."""
    assert is_visible(
        {PHONE_NUMBER_ATTR: "+819012345678", DELETED_ATTR: None}
    ) is True


def test_unit_deleted_non_bool_truthy_does_not_block() -> None:
    """Anchor: only literal `True` blocks visibility (per docstring).

    A malformed `deleted` value of, e.g., the string "true" or the int 1
    is NOT the literal True, so Gate 1 lets it pass.  Gate 2 still applies
    on phoneNumber.
    """
    assert is_visible(
        {PHONE_NUMBER_ATTR: "+819012345678", DELETED_ATTR: "true"}
    ) is True
    assert is_visible(
        {PHONE_NUMBER_ATTR: "+819012345678", DELETED_ATTR: 1}
    ) is True
