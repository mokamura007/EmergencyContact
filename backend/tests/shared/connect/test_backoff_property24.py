"""Property 24 (Pure-function half) — compute_backoff_delay PBT (Phase 13.24).

Validates: Requirements 6.6 (再試行回数上限), 10.8 / 10.9 (録音 / Transcribe
    の再試行戦略 / バックオフ).

Target:
    ``shared.connect.backoff.compute_backoff_delay`` — pure helper
    that computes the **seconds** to wait before retry ``attempt``,
    given ``base_ms``, ``max_ms``, ``jitter_ratio`` and an injectable
    ``random_fn``. Reused by Phase 6.2 ConnectDispatcher, Phase 6.4
    TranscribeStarter, Phase 6.7 RecordingMetadataWriter and Phase 7.2
    RecordingRelocator (DRY).

Why this file is a *pure-function* half of Property 24:
    The handler-side integration half lives in
    ``tests/lambdas/test_retry_integration_property24.py`` and pins
    P1..P5 against the three boto3-mocked retry loops. Because every
    handler reuses this *same* backoff formula, pinning the formula's
    own invariants here means the handler tests can focus on the
    *control flow* (try-count, side-effect ordering) rather than on
    re-validating the math.

Named properties (3, complementing handler half):
    P1_pure (non_negative_for_all_valid_inputs):
        ``compute_backoff_delay(attempt, ...) >= 0`` for every valid
        argument tuple, regardless of the jittered draw. The clamp
        ``jitter_ratio < 1.0`` is what guarantees this — without it
        the multiplicative factor ``(1 + jitter)`` could go negative.

    P1_pure_bound (bounded_by_clamp_times_jitter_window):
        ``compute_backoff_delay(attempt, base, max_ms, jr)`` is in
        ``[clamped_ms * (1 - jr) / 1000, clamped_ms * (1 + jr) / 1000]``
        where ``clamped_ms = min(base * 2**attempt, max_ms)``. Pins the
        worst-case retry budget: the wait between retries never
        exceeds ``max_ms * (1 + jitter_ratio)`` seconds, so a 3-attempt
        loop with default ``max_ms=5000, jitter_ratio=0.5`` waits at
        most ``5 * 1.5 + 5 * 1.5 == 15`` seconds — well under any
        Lambda 15-minute timeout (Requirement 6.6).

    P6_determinism (deterministic_when_random_fn_injected):
        With a deterministic ``random_fn``, two back-to-back calls
        with identical arguments return identical values. This is the
        contract that lets every Phase 6.x handler test stub
        ``time.sleep`` and replay retry loops verbatim.
        第17原則 (対称性推論): ``random_fn`` is the only source of
        non-determinism; if injection works, removing the injection
        re-introduces non-determinism (covered by the example file's
        ``random_fn`` injection class).

Anchored examples (``@example`` pin, exercised in addition to random draws):
    - ``attempt=0`` (the first wait — base_ms domain).
    - ``attempt=20`` (deep in the clamp regime; raw_ms = base * 2**20).
    - ``jitter_ratio=0.0`` (no jitter — pure exponential growth).
    - ``jitter_ratio=0.999`` (maximum permitted ratio; just under 1.0).
    - ``base_ms == max_ms`` (clamp engages from attempt=0 onwards).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.connect.backoff import compute_backoff_delay

# Hypothesis settings: pure-function PBT runs quickly, so 200 examples
# per property gives strong shrink + counter-example coverage at low
# cost. ``HealthCheck.too_slow`` is suppressed defensively even though
# every example is < 1 ms.
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


# ---------------------------------------------------------------------------
# Strategies (local — strong contextual coupling to Property 24).
# ---------------------------------------------------------------------------

#: Retry index. Bounded at 30 so ``base_ms * 2 ** attempt`` stays within
#: float precision even at ``base_ms = 10_000``. Handlers cap retries at 3
#: in practice, but pinning the formula across a wider range protects
#: against future raises to the cap.
_attempt = st.integers(min_value=0, max_value=30)

#: Base wait in milliseconds. Lower bound is 1 (the function's domain
#: floor); upper bound 10_000 leaves room for the multiplicative clamp
#: + jitter at ``max_ms = 60_000`` without overflowing.
_base_ms = st.integers(min_value=1, max_value=10_000)

#: Maximum wait in milliseconds. Must be >= base_ms (formula precondition).
#: 60_000 ms == 1 minute, well above any production setting.
_max_ms = st.integers(min_value=1, max_value=60_000)

#: Jitter ratio in [0, 1). 0.0 means deterministic; 0.999 is the cap.
_jitter_ratio = st.floats(
    min_value=0.0,
    max_value=0.999,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _valid_args(
    draw: st.DrawFn,
) -> tuple[int, int, int, float]:
    """Draw ``(attempt, base_ms, max_ms, jitter_ratio)`` satisfying the
    precondition ``max_ms >= base_ms`` (which the function enforces via
    ``ValueError``)."""
    attempt = draw(_attempt)
    base = draw(_base_ms)
    # Force max_ms >= base_ms to land inside the function's valid domain.
    max_ms = draw(st.integers(min_value=base, max_value=60_000))
    jr = draw(_jitter_ratio)
    return attempt, base, max_ms, jr


def _make_random_stub(value: float) -> Callable[[float, float], float]:
    """Return a deterministic ``random_fn`` that always yields ``value``.

    The closure is the simplest possible substitution for
    ``random.uniform``; pinning ``value`` to ``-jitter_ratio`` /
    ``+jitter_ratio`` / ``0`` exercises the lower-bound / upper-bound
    / midpoint branches without invoking the global RNG.
    """

    def stub(a: float, b: float) -> float:
        # The function guarantees ``a == -jitter_ratio, b == jitter_ratio``.
        # Tests rely on this contract via ``test_property24_random_fn_
        # receives_symmetric_bounds`` in the example-based companion file.
        del a, b
        return value

    return stub


# ---------------------------------------------------------------------------
# P1_pure — non_negative_for_all_valid_inputs.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(args=(0, 200, 5_000, 0.5))  # task default values
@example(args=(20, 200, 5_000, 0.5))  # deep clamp regime
@example(args=(0, 200, 5_000, 0.0))  # no jitter
@example(args=(0, 200, 5_000, 0.999))  # max permitted jitter
@example(args=(0, 5_000, 5_000, 0.5))  # base == max (clamp from start)
@given(args=_valid_args())
def test_property24_pure_non_negative_for_all_valid_inputs(
    args: tuple[int, int, int, float],
) -> None:
    """``compute_backoff_delay(...) >= 0`` for every valid argument tuple.

    The function clamps ``jitter_ratio < 1.0`` (raises ValueError on
    ``1.0`` and ``-anything``), so the multiplicative factor
    ``(1 + jitter)`` is strictly positive for any draw from
    ``random_fn(-jitter_ratio, +jitter_ratio)``. Result == clamped_ms *
    positive / 1000 is therefore always >= 0.

    The property uses the default ``random_fn`` (true ``random.uniform``)
    so this also doubles as a smoke test for the production code path.

    Validates: Requirements 6.6 (再試行戦略の安全性 — sleep duration
        は常に非負).
    """
    attempt, base, max_ms, jr = args
    delay_s = compute_backoff_delay(
        attempt=attempt, base_ms=base, max_ms=max_ms, jitter_ratio=jr
    )
    assert delay_s >= 0.0, (
        f"compute_backoff_delay returned negative: {delay_s} for "
        f"attempt={attempt} base={base} max_ms={max_ms} jr={jr}"
    )


# ---------------------------------------------------------------------------
# P1_pure_bound — bounded_by_clamp_times_jitter_window.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(args=(0, 200, 5_000, 0.5))
@example(args=(20, 200, 5_000, 0.5))
@example(args=(0, 200, 5_000, 0.0))
@example(args=(0, 5_000, 5_000, 0.5))
@given(args=_valid_args())
def test_property24_pure_bounded_by_clamp_times_jitter_window(
    args: tuple[int, int, int, float],
) -> None:
    """Result lies in ``[clamped * (1 - jr), clamped * (1 + jr)] / 1000``.

    Probes both edges of the jitter window deterministically by
    injecting ``random_fn`` stubs that return ``-jr`` and ``+jr``. Pins
    the per-retry budget invariant: with ``max_ms=5000, jr=0.5``, no
    single wait can exceed ``5 * 1.5 = 7.5`` seconds, so 3 retries are
    bounded by ``22.5`` seconds total — well under any production
    timeout (Requirement 6.6).

    Validates: Requirements 6.6 (リトライ間 sleep の上限保証),
        10.8 (録音処理の再試行上限), 10.9 (Transcribe の再試行上限).
    """
    attempt, base, max_ms, jr = args
    raw_ms = base * (2**attempt)
    clamped_ms = min(raw_ms, max_ms)
    lo = clamped_ms * (1.0 - jr) / 1000.0
    hi = clamped_ms * (1.0 + jr) / 1000.0

    # Inject random_fn = -jitter_ratio for the low bound.
    low_val = compute_backoff_delay(
        attempt=attempt,
        base_ms=base,
        max_ms=max_ms,
        jitter_ratio=jr,
        random_fn=_make_random_stub(-jr),
    )
    # Inject random_fn = +jitter_ratio for the high bound.
    high_val = compute_backoff_delay(
        attempt=attempt,
        base_ms=base,
        max_ms=max_ms,
        jitter_ratio=jr,
        random_fn=_make_random_stub(jr),
    )

    # Floating-point equality is exact here because we control both
    # sides of the multiplication, but we still allow ``math.isclose``
    # tolerance to absorb any 1-ulp wobble in deeply-clamped regimes.
    assert math.isclose(low_val, lo, abs_tol=1e-12, rel_tol=1e-12), (
        f"low bound drift: got {low_val} expected {lo} for "
        f"attempt={attempt} base={base} max_ms={max_ms} jr={jr}"
    )
    assert math.isclose(high_val, hi, abs_tol=1e-12, rel_tol=1e-12), (
        f"high bound drift: got {high_val} expected {hi} for "
        f"attempt={attempt} base={base} max_ms={max_ms} jr={jr}"
    )

    # Symmetric check (對): a true random draw inside [-jr, +jr] must
    # land in [lo, hi]. The function's default uses ``random.uniform``,
    # so this is a Hypothesis-driven smoke test of the production
    # random_fn against the same bound.
    actual = compute_backoff_delay(
        attempt=attempt, base_ms=base, max_ms=max_ms, jitter_ratio=jr
    )
    assert lo - 1e-12 <= actual <= hi + 1e-12, (
        f"default random_fn result out of bounds: got {actual} "
        f"expected [{lo}, {hi}] for "
        f"attempt={attempt} base={base} max_ms={max_ms} jr={jr}"
    )


# ---------------------------------------------------------------------------
# P6_determinism — deterministic_when_random_fn_injected (第17原則).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(args=(0, 200, 5_000, 0.5), jitter_draw=0.0)
@example(args=(3, 200, 5_000, 0.5), jitter_draw=0.0)  # the f >= 3 boundary
@example(args=(20, 200, 5_000, 0.5), jitter_draw=0.0)
@given(args=_valid_args(), jitter_draw=st.floats(min_value=-0.999, max_value=0.999))
def test_property24_pure_deterministic_when_random_fn_injected(
    args: tuple[int, int, int, float],
    jitter_draw: float,
) -> None:
    """Two back-to-back calls with identical args + ``random_fn`` agree.

    With a deterministic ``random_fn`` that always returns the same
    value, the function is a pure mathematical mapping. Calling it
    twice with the same inputs must yield bit-identical outputs.

    第17原則 (対称性推論): the converse — without ``random_fn``
    injection, two calls *can* differ because ``random.uniform``
    is non-deterministic. That converse is pinned by the example-based
    companion in ``test_backoff.py``'s ``TestRandomFnInjection`` class
    (which the team relies on as documentation of the contract).

    The ``jitter_draw`` is clipped to ``[-jitter_ratio, +jitter_ratio]``
    before being fed to the stub so the test exercises the natural
    input range of ``random_fn``.

    Validates: Requirements 6.6 (再試行ロジックの決定論的検証可能性).
    """
    attempt, base, max_ms, jr = args
    # Clip to the natural range of ``random_fn``.
    clipped = max(-jr, min(jr, jitter_draw))
    stub = _make_random_stub(clipped)

    first = compute_backoff_delay(
        attempt=attempt,
        base_ms=base,
        max_ms=max_ms,
        jitter_ratio=jr,
        random_fn=stub,
    )
    second = compute_backoff_delay(
        attempt=attempt,
        base_ms=base,
        max_ms=max_ms,
        jitter_ratio=jr,
        random_fn=stub,
    )
    assert first == second, (
        f"determinism violated: first={first} second={second} for "
        f"attempt={attempt} base={base} max_ms={max_ms} jr={jr} "
        f"clipped_jitter={clipped}"
    )


# ---------------------------------------------------------------------------
# Independent oracle (第17原則 対称性推論).
#
# Re-derive the formula by hand and assert it agrees with the
# implementation for every drawn input. If a future change accidentally
# drops the ``/1000`` conversion or flips the sign of the jitter, the
# oracle will catch it without us having to write a dedicated unit
# test for every regression.
# ---------------------------------------------------------------------------


def _oracle(attempt: int, base_ms: int, max_ms: int, jitter_ratio: float, j: float) -> float:
    """Re-derive ``compute_backoff_delay`` from scratch.

    Pure restatement of the formula in the module docstring:
        result_seconds = min(base_ms * 2**attempt, max_ms) * (1 + j) / 1000
    where ``j`` is the value ``random_fn`` returned.
    """
    raw_ms = base_ms * (2**attempt)
    clamped_ms = min(raw_ms, max_ms)
    # ``jitter_ratio`` itself is unused in the formula — it bounds ``j``.
    del jitter_ratio
    return float(clamped_ms * (1.0 + j) / 1000.0)


@PBT_SETTINGS
@example(args=(0, 200, 5_000, 0.5), j_value=0.0)
@example(args=(20, 200, 5_000, 0.5), j_value=0.5)
@given(args=_valid_args(), j_value=st.floats(min_value=-0.999, max_value=0.999))
def test_property24_pure_matches_independent_oracle(
    args: tuple[int, int, int, float],
    j_value: float,
) -> None:
    """Implementation == oracle for every (args, injected jitter).

    The oracle is the formula transcribed independently from the
    docstring. Equality here means any future micro-optimisation of
    ``compute_backoff_delay`` that drops the ``/1000`` or flips a sign
    will be caught.

    Validates: Requirements 6.6 (実装と仕様式の同値性).
    """
    attempt, base, max_ms, jr = args
    # Clip the injected jitter to the function's natural range.
    clipped = max(-jr, min(jr, j_value))
    stub = _make_random_stub(clipped)

    actual = compute_backoff_delay(
        attempt=attempt,
        base_ms=base,
        max_ms=max_ms,
        jitter_ratio=jr,
        random_fn=stub,
    )
    expected = _oracle(attempt, base, max_ms, jr, clipped)
    assert math.isclose(actual, expected, abs_tol=1e-12, rel_tol=1e-12), (
        f"oracle disagreement: actual={actual} expected={expected} for "
        f"attempt={attempt} base={base} max_ms={max_ms} jr={jr} "
        f"clipped_jitter={clipped}"
    )
