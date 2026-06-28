"""Truth-table tests for :mod:`shared.cycle.finalize` (Phase 6.6).

These cases pin down the semantics that Phase 13.x Hypothesis PBT
(Property 15 / 16 / 17) will later mechanise. Each function gets
explicit edge cases the property tests must hold over: empty inputs,
single-row inputs, mixed terminal/non-terminal collections, and the
type-error rejection paths.
"""

from __future__ import annotations

import pytest

from shared.cycle.finalize import (
    apply_timeout,
    compute_summary,
    count_pending_responses,
    is_cycle_completed,
    is_first_dispatch_incomplete,
)

# --- is_cycle_completed -------------------------------------------------


def test_is_cycle_completed_returns_true_for_empty_list() -> None:
    """Vacuous truth on empty input is the documented convention."""
    assert is_cycle_completed([]) is True


def test_is_cycle_completed_returns_true_when_all_responses_terminal() -> None:
    rows = [
        {"employeeId": "e1", "voiceStatus": "SAFE"},
        {"employeeId": "e2", "voiceStatus": "INJURED"},
        {"employeeId": "e3", "voiceStatus": "UNAVAILABLE"},
        {"employeeId": "e4", "voiceStatus": "UNREACHABLE"},
    ]
    assert is_cycle_completed(rows) is True


def test_is_cycle_completed_returns_false_when_one_pending() -> None:
    rows = [
        {"employeeId": "e1", "voiceStatus": "SAFE"},
        {"employeeId": "e2", "voiceStatus": "PENDING"},
    ]
    assert is_cycle_completed(rows) is False


def test_is_cycle_completed_returns_false_when_one_other() -> None:
    rows = [
        {"employeeId": "e1", "voiceStatus": "SAFE"},
        {"employeeId": "e2", "voiceStatus": "OTHER"},
    ]
    assert is_cycle_completed(rows) is False


def test_is_cycle_completed_treats_missing_voice_status_as_pending() -> None:
    """A row missing ``voiceStatus`` is treated as not-yet-terminal."""
    rows = [{"employeeId": "e1"}]
    assert is_cycle_completed(rows) is False


def test_is_cycle_completed_rejects_non_list_input() -> None:
    with pytest.raises(TypeError, match="responses must be a list"):
        is_cycle_completed("not-a-list")  # type: ignore[arg-type]


def test_is_cycle_completed_rejects_non_dict_items() -> None:
    with pytest.raises(TypeError, match=r"responses\[0\] must be a dict"):
        is_cycle_completed(["not-a-dict"])  # type: ignore[list-item]


# --- count_pending_responses -------------------------------------------


def test_count_pending_responses_returns_zero_for_empty_list() -> None:
    assert count_pending_responses([]) == 0


def test_count_pending_responses_counts_only_non_terminal() -> None:
    rows = [
        {"voiceStatus": "PENDING"},
        {"voiceStatus": "OTHER"},
        {"voiceStatus": "SAFE"},
        {"voiceStatus": "INJURED"},
        {"voiceStatus": "UNAVAILABLE"},
        {"voiceStatus": "UNREACHABLE"},
    ]
    assert count_pending_responses(rows) == 2  # PENDING + OTHER


def test_count_pending_responses_treats_missing_field_as_pending() -> None:
    rows = [{"voiceStatus": "SAFE"}, {}, {"unrelated": True}]
    assert count_pending_responses(rows) == 2


# --- compute_summary ---------------------------------------------------


def test_compute_summary_handles_empty_list() -> None:
    summary = compute_summary([])
    assert summary == {
        "targetTotal": 0,
        "dispatched": 0,
        "responded": 0,
        "unreachable": 0,
        "byStatus": {},
    }


def test_compute_summary_counts_dispatched_responded_unreachable() -> None:
    rows = [
        {"employeeId": "e1", "voiceStatus": "SAFE", "callAttempts": 1},
        {"employeeId": "e2", "voiceStatus": "INJURED", "callAttempts": 2},
        {"employeeId": "e3", "voiceStatus": "UNAVAILABLE", "callAttempts": 1},
        {"employeeId": "e4", "voiceStatus": "UNREACHABLE", "callAttempts": 4},
        {"employeeId": "e5", "voiceStatus": "OTHER", "callAttempts": 1},
        {"employeeId": "e6", "voiceStatus": "PENDING", "callAttempts": 0},
    ]
    summary = compute_summary(rows)
    assert summary["targetTotal"] == 6
    assert summary["dispatched"] == 5  # e1..e5
    assert summary["responded"] == 3  # SAFE + INJURED + UNAVAILABLE
    assert summary["unreachable"] == 1
    assert summary["byStatus"] == {
        "SAFE": 1,
        "INJURED": 1,
        "UNAVAILABLE": 1,
        "UNREACHABLE": 1,
        "OTHER": 1,
        "PENDING": 1,
    }


def test_compute_summary_defaults_missing_voice_status_to_pending() -> None:
    rows = [{"employeeId": "e1", "callAttempts": 1}]
    summary = compute_summary(rows)
    assert summary["byStatus"] == {"PENDING": 1}
    assert summary["responded"] == 0


def test_compute_summary_rejects_non_int_call_attempts() -> None:
    rows = [{"employeeId": "e1", "callAttempts": "1"}]
    with pytest.raises(TypeError, match="callAttempts must be int"):
        compute_summary(rows)


def test_compute_summary_rejects_bool_call_attempts() -> None:
    """``True`` is technically an int subclass but here it's a mistake."""
    rows = [{"employeeId": "e1", "callAttempts": True}]
    with pytest.raises(TypeError, match="callAttempts must be int"):
        compute_summary(rows)


# --- apply_timeout ------------------------------------------------------


def test_apply_timeout_empty_list_returns_empty() -> None:
    assert apply_timeout([]) == []


def test_apply_timeout_flips_pending_and_other_to_unreachable() -> None:
    rows = [
        {"employeeId": "e1", "voiceStatus": "PENDING"},
        {"employeeId": "e2", "voiceStatus": "OTHER"},
        {"employeeId": "e3", "voiceStatus": "SAFE"},
        {"employeeId": "e4", "voiceStatus": "INJURED"},
        {"employeeId": "e5", "voiceStatus": "UNAVAILABLE"},
        {"employeeId": "e6", "voiceStatus": "UNREACHABLE"},
    ]
    result = apply_timeout(rows)
    assert result == [("e1", "UNREACHABLE"), ("e2", "UNREACHABLE")]


def test_apply_timeout_preserves_input_order() -> None:
    rows = [
        {"employeeId": "z", "voiceStatus": "PENDING"},
        {"employeeId": "a", "voiceStatus": "PENDING"},
        {"employeeId": "m", "voiceStatus": "OTHER"},
    ]
    result = apply_timeout(rows)
    assert [eid for eid, _ in result] == ["z", "a", "m"]


def test_apply_timeout_rejects_row_in_rewrite_state_without_employee_id() -> None:
    rows = [{"voiceStatus": "PENDING"}]
    with pytest.raises(TypeError, match="must include a non-empty employeeId"):
        apply_timeout(rows)


def test_apply_timeout_tolerates_terminal_row_without_employee_id() -> None:
    """Terminal rows are filtered before the employee_id check."""
    rows = [{"voiceStatus": "SAFE"}, {"employeeId": "e1", "voiceStatus": "PENDING"}]
    assert apply_timeout(rows) == [("e1", "UNREACHABLE")]


# --- is_first_dispatch_incomplete --------------------------------------


def test_is_first_dispatch_incomplete_returns_false_for_empty_list() -> None:
    assert is_first_dispatch_incomplete([]) is False


def test_is_first_dispatch_incomplete_returns_true_when_zero_attempts_exists() -> (
    None
):
    rows = [
        {"employeeId": "e1", "callAttempts": 1},
        {"employeeId": "e2", "callAttempts": 0},
    ]
    assert is_first_dispatch_incomplete(rows) is True


def test_is_first_dispatch_incomplete_returns_true_when_attempts_missing() -> None:
    rows = [
        {"employeeId": "e1", "callAttempts": 1},
        {"employeeId": "e2"},  # missing → 0
    ]
    assert is_first_dispatch_incomplete(rows) is True


def test_is_first_dispatch_incomplete_returns_false_when_all_dispatched() -> None:
    rows = [
        {"employeeId": "e1", "callAttempts": 1},
        {"employeeId": "e2", "callAttempts": 3},
    ]
    assert is_first_dispatch_incomplete(rows) is False


def test_is_first_dispatch_incomplete_rejects_non_int_attempts() -> None:
    rows = [{"employeeId": "e1", "callAttempts": "0"}]
    with pytest.raises(TypeError, match="callAttempts must be int"):
        is_first_dispatch_incomplete(rows)


# --- Phase 17.2: Decimal coercion (boto3 DynamoDB read compatibility) -------
#
# boto3 returns DynamoDB ``Number`` (N) attributes as ``decimal.Decimal``.
# Pure helpers used by Lambda handlers must accept both ``int`` and
# ``Decimal`` for ``callAttempts``. These tests cover the
# ``_coerce_call_attempts`` helper introduced in Phase 17.2 and the
# downstream callers ``compute_summary`` / ``is_first_dispatch_incomplete``.


from decimal import Decimal  # noqa: E402  (intentionally late import, Phase 17.2)


def test_is_first_dispatch_incomplete_accepts_decimal_zero_as_incomplete() -> None:
    """``Decimal(0)`` must be treated identically to ``int(0)``."""
    rows = [
        {"employeeId": "e1", "callAttempts": Decimal(1)},
        {"employeeId": "e2", "callAttempts": Decimal(0)},
    ]
    assert is_first_dispatch_incomplete(rows) is True


def test_is_first_dispatch_incomplete_accepts_decimal_positive_as_complete() -> None:
    rows = [
        {"employeeId": "e1", "callAttempts": Decimal(1)},
        {"employeeId": "e2", "callAttempts": Decimal(3)},
    ]
    assert is_first_dispatch_incomplete(rows) is False


def test_compute_summary_accepts_decimal_call_attempts() -> None:
    """compute_summary must aggregate Decimal callAttempts as int."""
    rows = [
        {"employeeId": "e1", "callAttempts": Decimal(1), "voiceStatus": "SAFE"},
        {"employeeId": "e2", "callAttempts": Decimal(0), "voiceStatus": "PENDING"},
        {"employeeId": "e3", "callAttempts": Decimal(3), "voiceStatus": "UNREACHABLE"},
    ]
    out = compute_summary(rows)
    assert out["targetTotal"] == 3
    assert out["dispatched"] == 2  # e1 (=1) + e3 (=3), e2 (=0) excluded
    assert out["responded"] == 1  # SAFE
    assert out["unreachable"] == 1  # UNREACHABLE
    assert out["byStatus"] == {"SAFE": 1, "PENDING": 1, "UNREACHABLE": 1}


def test_compute_summary_rejects_bool_call_attempts() -> None:
    """bool is excluded explicitly even though it is an int subtype."""
    rows = [
        {"employeeId": "e1", "callAttempts": True, "voiceStatus": "SAFE"},
    ]
    with pytest.raises(TypeError, match="callAttempts must be int or Decimal"):
        compute_summary(rows)


def test_compute_summary_rejects_str_call_attempts() -> None:
    """str remains rejected (regression guard for Phase 16.5 G1 fix)."""
    rows = [
        {"employeeId": "e1", "callAttempts": "1", "voiceStatus": "SAFE"},
    ]
    with pytest.raises(TypeError, match="callAttempts must be int or Decimal"):
        compute_summary(rows)


def test_is_first_dispatch_incomplete_rejects_bool_call_attempts() -> None:
    """bool excluded for is_first_dispatch_incomplete as well."""
    rows = [{"employeeId": "e1", "callAttempts": False}]
    with pytest.raises(TypeError, match="callAttempts must be int or Decimal"):
        is_first_dispatch_incomplete(rows)
