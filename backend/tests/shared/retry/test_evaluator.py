"""Unit tests for :mod:`shared.retry.evaluator` (Phase 6.5).

Truth-table coverage for ``should_retry``, ``compute_next_dispatch_at``,
``compute_retry_wait_seconds``, and ``derive_final_status``. Property
12 / Property 13 PBT entries arrive separately in Phase 13.12 / 13.13.
"""

from __future__ import annotations

import pytest

from shared.retry.evaluator import (
    VALID_VOICE_STATUS_VALUES,
    compute_next_dispatch_at,
    compute_retry_wait_seconds,
    derive_final_status,
    should_retry,
)

# --- should_retry: confirmed answers always return False ---------------


@pytest.mark.parametrize("status", ["SAFE", "INJURED", "UNAVAILABLE"])
def test_should_retry_confirmed_status_is_false_regardless_of_attempts(
    status: str,
) -> None:
    """Confirmed answers terminate the dispatch chain (Req 9.1)."""
    assert should_retry(status, "RECORDED", attempts=0, retry_count=5) is False
    assert should_retry(status, "RECORDED", attempts=4, retry_count=5) is False


def test_should_retry_unreachable_is_false_even_with_budget_remaining() -> None:
    """UNREACHABLE is terminal once written (Req 9.5)."""
    assert (
        should_retry("UNREACHABLE", None, attempts=0, retry_count=5) is False
    )


# --- should_retry: retryable statuses + budget arithmetic --------------


@pytest.mark.parametrize("status", ["PENDING", "OTHER"])
def test_should_retry_retryable_status_with_budget_remaining_is_true(
    status: str,
) -> None:
    """PENDING/OTHER with attempts < retryCount → another dispatch."""
    assert should_retry(status, "NO_ANSWER", attempts=0, retry_count=3) is True
    assert should_retry(status, "BUSY", attempts=2, retry_count=3) is True


@pytest.mark.parametrize("status", ["PENDING", "OTHER"])
def test_should_retry_retryable_status_at_budget_is_false(status: str) -> None:
    """attempts == retryCount → budget exhausted, no further dispatch."""
    assert should_retry(status, "NO_ANSWER", attempts=3, retry_count=3) is False


@pytest.mark.parametrize("status", ["PENDING", "OTHER"])
def test_should_retry_retryable_status_above_budget_is_false(
    status: str,
) -> None:
    """attempts > retryCount (defensive) still returns False."""
    assert (
        should_retry(status, "VOICEMAIL", attempts=4, retry_count=3) is False
    )


def test_should_retry_zero_budget_means_no_retry() -> None:
    """retry_count == 0 means one dispatch and finalize."""
    assert should_retry("PENDING", None, attempts=0, retry_count=0) is False
    assert should_retry("OTHER", "BUSY", attempts=0, retry_count=0) is False


def test_should_retry_boundary_one_less_than_budget_is_true() -> None:
    """attempts == retry_count - 1 is the last allowed retry."""
    assert should_retry("OTHER", "BUSY", attempts=4, retry_count=5) is True


# --- should_retry: input validation ------------------------------------


def test_should_retry_invalid_voice_status_raises_value_error() -> None:
    with pytest.raises(ValueError, match="voice_status must be one of"):
        should_retry("UNKNOWN", None, attempts=0, retry_count=3)


def test_should_retry_negative_attempts_raises_value_error() -> None:
    with pytest.raises(ValueError, match="attempts must be >= 0"):
        should_retry("PENDING", None, attempts=-1, retry_count=3)


def test_should_retry_negative_retry_count_raises_value_error() -> None:
    with pytest.raises(ValueError, match="retry_count must be >= 0"):
        should_retry("PENDING", None, attempts=0, retry_count=-1)


def test_should_retry_boolean_attempts_raises_value_error() -> None:
    """``isinstance(True, int) == True`` so guard explicitly."""
    with pytest.raises(ValueError, match="attempts must be int"):
        should_retry("PENDING", None, attempts=True, retry_count=3)  # type: ignore[arg-type]


# --- compute_next_dispatch_at: shape and arithmetic --------------------


def test_compute_next_dispatch_at_with_z_suffix_returns_z_suffix() -> None:
    """Z input → Z output, offset by interval_minutes."""
    result = compute_next_dispatch_at("2026-06-25T10:00:00Z", 5)
    assert result == "2026-06-25T10:05:00Z"


def test_compute_next_dispatch_at_with_plus_zero_suffix_normalizes_to_z() -> (
    None
):
    """``+00:00`` input is accepted and the output is normalized to Z."""
    result = compute_next_dispatch_at("2026-06-25T10:00:00+00:00", 15)
    assert result == "2026-06-25T10:15:00Z"


def test_compute_next_dispatch_at_with_non_utc_offset_is_converted_to_z() -> (
    None
):
    """A +09:00 input must be converted to UTC and then offset."""
    # 10:00 JST == 01:00 UTC; +5 minutes → 01:05 UTC
    result = compute_next_dispatch_at("2026-06-25T10:00:00+09:00", 5)
    assert result == "2026-06-25T01:05:00Z"


def test_compute_next_dispatch_at_min_interval() -> None:
    """interval_minutes == 1 is the lower bound."""
    result = compute_next_dispatch_at("2026-06-25T10:00:00Z", 1)
    assert result == "2026-06-25T10:01:00Z"


def test_compute_next_dispatch_at_max_interval() -> None:
    """interval_minutes == 60 is the upper bound."""
    result = compute_next_dispatch_at("2026-06-25T10:00:00Z", 60)
    assert result == "2026-06-25T11:00:00Z"


def test_compute_next_dispatch_at_zero_interval_rejected() -> None:
    with pytest.raises(ValueError, match=r"interval_minutes must be in"):
        compute_next_dispatch_at("2026-06-25T10:00:00Z", 0)


def test_compute_next_dispatch_at_over_max_interval_rejected() -> None:
    with pytest.raises(ValueError, match=r"interval_minutes must be in"):
        compute_next_dispatch_at("2026-06-25T10:00:00Z", 61)


def test_compute_next_dispatch_at_bad_iso_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid ISO 8601 timestamp"):
        compute_next_dispatch_at("not-a-date", 5)


def test_compute_next_dispatch_at_naive_timestamp_rejected() -> None:
    """ISO 8601 without timezone offset is rejected (ambiguous)."""
    with pytest.raises(ValueError, match="must include a timezone offset"):
        compute_next_dispatch_at("2026-06-25T10:00:00", 5)


# --- compute_retry_wait_seconds ----------------------------------------


def test_compute_retry_wait_seconds_future_returns_positive_int() -> None:
    """now < next_dispatch_at → wait is positive seconds."""
    wait = compute_retry_wait_seconds(
        prev_end_at_iso="2026-06-25T10:00:00Z",
        interval_minutes=5,
        now_iso="2026-06-25T10:02:00Z",
    )
    # 10:05 - 10:02 = 3 minutes = 180 seconds.
    assert wait == 180


def test_compute_retry_wait_seconds_past_returns_zero() -> None:
    """If we are already past the dispatch instant, clamp to 0."""
    wait = compute_retry_wait_seconds(
        prev_end_at_iso="2026-06-25T10:00:00Z",
        interval_minutes=5,
        now_iso="2026-06-25T10:10:00Z",
    )
    assert wait == 0


def test_compute_retry_wait_seconds_now_equals_next_dispatch_is_zero() -> None:
    """Boundary: ``now`` exactly equals ``next_dispatch_at`` → 0."""
    wait = compute_retry_wait_seconds(
        prev_end_at_iso="2026-06-25T10:00:00Z",
        interval_minutes=5,
        now_iso="2026-06-25T10:05:00Z",
    )
    assert wait == 0


def test_compute_retry_wait_seconds_propagates_bad_input() -> None:
    """Invalid ``now_iso`` propagates as ``ValueError``."""
    with pytest.raises(ValueError, match="not a valid ISO 8601 timestamp"):
        compute_retry_wait_seconds(
            prev_end_at_iso="2026-06-25T10:00:00Z",
            interval_minutes=5,
            now_iso="garbage",
        )


# --- derive_final_status -----------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        ("SAFE", "SAFE"),
        ("INJURED", "INJURED"),
        ("UNAVAILABLE", "UNAVAILABLE"),
        ("UNREACHABLE", "UNREACHABLE"),
        ("PENDING", "UNREACHABLE"),
        ("OTHER", "UNREACHABLE"),
    ],
)
def test_derive_final_status_mapping(status: str, expected: str) -> None:
    """All six valid voice_status values map as documented."""
    assert derive_final_status(status) == expected


def test_derive_final_status_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match="voice_status must be one of"):
        derive_final_status("UNKNOWN")


# --- VALID_VOICE_STATUS_VALUES self-check ------------------------------


def test_valid_voice_status_values_set_is_exhaustive() -> None:
    """Guard against future drift: derive_final_status must accept all
    members and reject everything else."""
    for status in VALID_VOICE_STATUS_VALUES:
        # Must not raise.
        derive_final_status(status)
