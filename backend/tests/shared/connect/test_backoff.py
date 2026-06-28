"""Unit tests for the ``compute_backoff_delay`` pure function (Phase 6.2).

Property 24 (Hypothesis) will live in ``tests/shared/connect/
test_backoff_property24.py`` once Phase 13.24 is in scope. These tests
are the example-based companion: they pin specific cases and the
deterministic (no-jitter) shape of the function.
"""

from __future__ import annotations

import math

import pytest

from shared.connect.backoff import compute_backoff_delay

# --- Deterministic mode (jitter_ratio = 0.0) ---------------------------


class TestDeterministicGrowth:
    """With ``jitter_ratio = 0.0`` the function is purely deterministic."""

    def test_attempt_zero_returns_base(self) -> None:
        result = compute_backoff_delay(attempt=0, base_ms=200, max_ms=5000, jitter_ratio=0.0)
        assert math.isclose(result, 0.2)

    def test_attempt_one_doubles_base(self) -> None:
        result = compute_backoff_delay(attempt=1, base_ms=200, max_ms=5000, jitter_ratio=0.0)
        assert math.isclose(result, 0.4)

    def test_attempt_two_quadruples_base(self) -> None:
        result = compute_backoff_delay(attempt=2, base_ms=200, max_ms=5000, jitter_ratio=0.0)
        assert math.isclose(result, 0.8)

    def test_attempt_large_clamps_at_max(self) -> None:
        # 200 * 2 ** 20 vastly exceeds 5000ms; the clamp must engage.
        result = compute_backoff_delay(attempt=20, base_ms=200, max_ms=5000, jitter_ratio=0.0)
        assert math.isclose(result, 5.0)


# --- Random_fn injection -----------------------------------------------


class TestRandomFnInjection:
    """``random_fn`` is injected so tests can pin the jitter exactly."""

    def test_random_fn_at_zero_matches_deterministic(self) -> None:
        # random_fn always returns 0 → result equals the clamped base.
        result = compute_backoff_delay(
            attempt=1,
            base_ms=200,
            max_ms=5000,
            jitter_ratio=0.5,
            random_fn=lambda _a, _b: 0.0,
        )
        assert math.isclose(result, 0.4)

    def test_random_fn_at_positive_max(self) -> None:
        # random_fn returns +jitter_ratio → result = clamped * (1 + 0.5).
        result = compute_backoff_delay(
            attempt=2,
            base_ms=200,
            max_ms=5000,
            jitter_ratio=0.5,
            random_fn=lambda _a, b: b,
        )
        assert math.isclose(result, 0.8 * 1.5)

    def test_random_fn_at_negative_max(self) -> None:
        # random_fn returns -jitter_ratio → result = clamped * (1 - 0.5).
        result = compute_backoff_delay(
            attempt=2,
            base_ms=200,
            max_ms=5000,
            jitter_ratio=0.5,
            random_fn=lambda a, _b: a,
        )
        assert math.isclose(result, 0.8 * 0.5)

    def test_random_fn_receives_symmetric_bounds(self) -> None:
        """random_fn must be called with (-jitter_ratio, +jitter_ratio)."""
        seen_args: list[tuple[float, float]] = []

        def stub(a: float, b: float) -> float:
            seen_args.append((a, b))
            return 0.0

        compute_backoff_delay(
            attempt=0,
            base_ms=200,
            max_ms=5000,
            jitter_ratio=0.25,
            random_fn=stub,
        )
        assert seen_args == [(-0.25, 0.25)]


# --- Bounds & monotonicity ---------------------------------------------


class TestBoundsAndMonotonicity:
    """Range invariants useful for Property 24 sanity checks."""

    def test_result_within_clamped_window_for_any_attempt(self) -> None:
        # With jitter_ratio = 0.5, any single call must fall within
        # [clamped * 0.5, clamped * 1.5] seconds where
        # clamped_ms = min(base_ms * 2**attempt, max_ms).
        base_ms = 200
        max_ms = 5000
        jitter_ratio = 0.5
        for attempt in range(8):
            clamped_ms = min(base_ms * (2**attempt), max_ms)
            lo = clamped_ms * (1.0 - jitter_ratio) / 1000.0
            hi = clamped_ms * (1.0 + jitter_ratio) / 1000.0
            # Use random_fn = -jitter_ratio for low bound, +jitter_ratio for high bound.
            low_val = compute_backoff_delay(
                attempt=attempt,
                base_ms=base_ms,
                max_ms=max_ms,
                jitter_ratio=jitter_ratio,
                random_fn=lambda a, _b: a,
            )
            high_val = compute_backoff_delay(
                attempt=attempt,
                base_ms=base_ms,
                max_ms=max_ms,
                jitter_ratio=jitter_ratio,
                random_fn=lambda _a, b: b,
            )
            assert math.isclose(low_val, lo)
            assert math.isclose(high_val, hi)

    def test_deterministic_is_non_decreasing_until_clamp(self) -> None:
        prev = -1.0
        clamp_seconds = 5.0
        for attempt in range(6):
            val = compute_backoff_delay(
                attempt=attempt,
                base_ms=200,
                max_ms=5000,
                jitter_ratio=0.0,
            )
            # Either strictly larger than the previous one, or already at clamp.
            assert val >= prev or math.isclose(val, clamp_seconds)
            prev = val
        # After the clamp engages, value stays at exactly 5.0 seconds.
        assert math.isclose(prev, clamp_seconds)


# --- Validation --------------------------------------------------------


class TestValidation:
    """``compute_backoff_delay`` rejects out-of-range arguments loudly."""

    def test_negative_attempt_raises(self) -> None:
        with pytest.raises(ValueError, match="attempt"):
            compute_backoff_delay(attempt=-1)

    def test_zero_base_ms_raises(self) -> None:
        with pytest.raises(ValueError, match="base_ms"):
            compute_backoff_delay(attempt=0, base_ms=0)

    def test_max_ms_smaller_than_base_raises(self) -> None:
        with pytest.raises(ValueError, match="max_ms"):
            compute_backoff_delay(attempt=0, base_ms=500, max_ms=100)

    def test_jitter_ratio_one_raises(self) -> None:
        with pytest.raises(ValueError, match="jitter_ratio"):
            compute_backoff_delay(attempt=0, jitter_ratio=1.0)

    def test_jitter_ratio_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="jitter_ratio"):
            compute_backoff_delay(attempt=0, jitter_ratio=-0.1)
