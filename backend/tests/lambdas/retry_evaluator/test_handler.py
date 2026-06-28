"""Unit tests for RetryEvaluator Lambda handler (Phase 6.5).

The handler is a thin wrapper over :mod:`shared.retry.evaluator`; the
exhaustive truth-table tests live there. Here we focus on:

* Event-shape validation (required keys, primitive types).
* The two output branches (retry vs. finalize) wire the pure-function
  results into the documented response shape.
* The clock injection point is honored (``datetime.now`` is patched so
  ``retryWaitSeconds`` is deterministic).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from lambdas.retry_evaluator import handler as h


def _fixed_now(monkeypatch: pytest.MonkeyPatch, iso: str) -> None:
    """Patch ``datetime.now`` in the handler to return ``iso``."""

    target = datetime.fromisoformat(iso.replace("Z", "+00:00"))

    class _FrozenDateTime(datetime):  # noqa: D401 - test helper
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return target.astimezone(tz) if tz else target

    monkeypatch.setattr(h, "datetime", _FrozenDateTime)


# --- Happy paths -------------------------------------------------------


def test_handler_returns_retry_true_for_other_with_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OTHER + attempts < retryCount → retry payload."""
    _fixed_now(monkeypatch, "2026-06-25T10:02:00Z")
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "OTHER",
        "callResultCode": "NO_ANSWER",
        "attempts": 1,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    result = h.lambda_handler(event, None)
    assert result == {
        "retry": True,
        # next dispatch = 10:00 + 5min = 10:05; now=10:02 → 180s wait.
        "retryWaitSeconds": 180,
        "nextDispatchAt": "2026-06-25T10:05:00Z",
        "finalStatus": None,
    }


def test_handler_returns_retry_true_for_pending_with_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PENDING + attempts < retryCount → retry payload."""
    _fixed_now(monkeypatch, "2026-06-25T10:00:00Z")
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "PENDING",
        "callResultCode": None,
        "attempts": 0,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    result = h.lambda_handler(event, None)
    assert result["retry"] is True
    assert result["finalStatus"] is None
    # now == prev → full interval seconds.
    assert result["retryWaitSeconds"] == 300
    assert result["nextDispatchAt"] == "2026-06-25T10:05:00Z"


def test_handler_returns_retry_false_for_safe() -> None:
    """SAFE → finalize, no clock dependency."""
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "SAFE",
        "callResultCode": "RECORDED",
        "attempts": 1,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    result = h.lambda_handler(event, None)
    assert result == {
        "retry": False,
        "retryWaitSeconds": 0,
        "nextDispatchAt": None,
        "finalStatus": "SAFE",
    }


def test_handler_returns_retry_false_with_unreachable_for_exhausted_other() -> (
    None
):
    """OTHER + attempts == retryCount → finalize as UNREACHABLE."""
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "OTHER",
        "callResultCode": "BUSY",
        "attempts": 3,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    result = h.lambda_handler(event, None)
    assert result["retry"] is False
    assert result["finalStatus"] == "UNREACHABLE"
    assert result["nextDispatchAt"] is None
    assert result["retryWaitSeconds"] == 0


def test_handler_clamps_wait_to_zero_when_interval_already_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """now > next_dispatch_at → wait clamped at 0."""
    _fixed_now(monkeypatch, "2026-06-25T10:10:00Z")
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "PENDING",
        "callResultCode": None,
        "attempts": 0,
        "retryCount": 1,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    result = h.lambda_handler(event, None)
    assert result["retry"] is True
    assert result["retryWaitSeconds"] == 0


def test_handler_accepts_null_call_result_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initial dispatch has no callResultCode yet — must be accepted."""
    _fixed_now(monkeypatch, "2026-06-25T10:00:00Z")
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "PENDING",
        "callResultCode": None,
        "attempts": 0,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    result = h.lambda_handler(event, None)
    assert result["retry"] is True


# --- Input validation --------------------------------------------------


def test_handler_rejects_non_dict_event() -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        h.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]


def test_handler_rejects_event_missing_required_keys() -> None:
    with pytest.raises(ValueError, match="event missing required keys"):
        h.lambda_handler({"cycleId": "c1"}, None)


def test_handler_rejects_empty_cycle_id() -> None:
    event = {
        "cycleId": "",
        "employeeId": "emp-1",
        "voiceStatus": "PENDING",
        "callResultCode": None,
        "attempts": 0,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    with pytest.raises(ValueError, match="cycleId must be a non-empty string"):
        h.lambda_handler(event, None)


def test_handler_rejects_non_string_call_result_code() -> None:
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "PENDING",
        "callResultCode": 123,  # invalid
        "attempts": 0,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    with pytest.raises(
        ValueError, match="callResultCode must be a string or null"
    ):
        h.lambda_handler(event, None)


def test_handler_propagates_invalid_voice_status_from_pure_function() -> None:
    """Bad voiceStatus surfaces via ``should_retry``'s ValueError."""
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "UNKNOWN",
        "callResultCode": None,
        "attempts": 0,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    with pytest.raises(ValueError, match="voice_status must be one of"):
        h.lambda_handler(event, None)


def test_handler_propagates_bad_prev_end_at_from_pure_function() -> None:
    """Bad prevEndAt surfaces via ``compute_next_dispatch_at``'s ValueError."""
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "OTHER",
        "callResultCode": "BUSY",
        "attempts": 0,
        "retryCount": 3,
        "retryIntervalMinutes": 5,
        "prevEndAt": "not-iso",
    }
    with pytest.raises(ValueError, match="not a valid ISO 8601 timestamp"):
        h.lambda_handler(event, None)


def test_handler_propagates_invalid_interval_from_pure_function() -> None:
    """Out-of-range retryIntervalMinutes propagates as ValueError."""
    event = {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "voiceStatus": "OTHER",
        "callResultCode": "BUSY",
        "attempts": 0,
        "retryCount": 3,
        "retryIntervalMinutes": 0,
        "prevEndAt": "2026-06-25T10:00:00Z",
    }
    with pytest.raises(ValueError, match="interval_minutes must be in"):
        h.lambda_handler(event, None)
