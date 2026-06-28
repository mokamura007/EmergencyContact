"""Property 8 — account-lockout predicate PBT (Phase 13.8).

Validates: Requirements 1.6 (同一アカウントで認証 5 連続失敗 → 30 分ロック)

Contract (verbatim from shared/auth/lockout.py):
    is_locked(failed_ats, now_epoch_sec, threshold=5, window_sec=1800) is True
    iff
      (i)  len(failed_ats) >= threshold, AND
      (ii) every one of the last `threshold` chronological entries
           (i.e. sorted(failed_ats)[-threshold:]) is strictly greater
           than (now_epoch_sec - window_sec).

This file enforces:
  (a)  Lock fires when the tail-`threshold` window is fully recent.
  (b1) Fewer than `threshold` failures never lock (count-deficient).
  (b2) If any of the tail-`threshold` entries is at or before the cutoff,
       lock does not fire (window-deficient).
  (c)  Boundary on the strict inequality: t == cutoff → False,
       t == cutoff + 1 → True.
  (d)  Order-independence: the same multiset of timestamps yields the
       same verdict regardless of submission order.
  (e)  Parameterisation: the contract holds for non-default threshold /
       window_sec (e.g. threshold=3, window_sec=60).

Plus a small unit-test layer pinning the explicit edge cases enumerated
in the task: empty list, 1..4 entries, 5 entries at `now`, 5 at `now-1`,
5 with one at cutoff, 10 with newest 5 fresh, 10 with newest 4 fresh.
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.auth.lockout import DEFAULT_THRESHOLD, DEFAULT_WINDOW_SEC, is_locked
from tests.strategies import epoch_sec

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


# ---------------------------------------------------------------------------
# Local composite strategies — kept in-file because they are lockout-specific.
# ---------------------------------------------------------------------------


@st.composite
def _locked_history(draw: st.DrawFn) -> tuple[list[int], int]:
    """Generate (failed_ats, now) that MUST lock under the default contract.

    Construction:
      - now: any realistic epoch.
      - recent: 5..20 timestamps drawn from (now - 1800, now] (strictly
        greater than cutoff, ≤ now).
      - old: 0..10 additional timestamps from [0, now - 1800] (i.e. at
        or before cutoff).  Adding them stresses sort-then-tail behaviour:
        sorted-tail of 5 is fully inside `recent`, so lock still fires.
      - Final list is a permutation of (recent + old) to verify the
        function sorts internally.
    """
    now = draw(epoch_sec)
    cutoff = now - DEFAULT_WINDOW_SEC
    k = draw(st.integers(min_value=DEFAULT_THRESHOLD, max_value=20))
    recent = draw(
        st.lists(
            st.integers(min_value=cutoff + 1, max_value=now),
            min_size=k,
            max_size=k,
        )
    )
    old = draw(
        st.lists(
            st.integers(min_value=0, max_value=cutoff),
            min_size=0,
            max_size=10,
        )
    )
    combined = recent + old
    shuffled = draw(st.permutations(combined))
    return list(shuffled), now


@st.composite
def _short_history(draw: st.DrawFn) -> tuple[list[int], int]:
    """Generate (failed_ats, now) with strictly fewer than 5 timestamps."""
    now = draw(epoch_sec)
    history = draw(
        st.lists(
            st.integers(min_value=0, max_value=now),
            min_size=0,
            max_size=DEFAULT_THRESHOLD - 1,
        )
    )
    return history, now


@st.composite
def _window_deficient_history(draw: st.DrawFn) -> tuple[list[int], int]:
    """Generate (failed_ats, now) where sorted-tail-5 contains at least one
    old timestamp (≤ cutoff), so lock must NOT fire.

    Strategy: pick 5..15 timestamps all from [0, cutoff].  All of them
    are at or before cutoff, hence every position in sorted-tail-5 is
    ≤ cutoff and the contract returns False.  We optionally interleave
    0..3 fresh timestamps to keep `len(failed_ats) >= threshold` but
    still keep the sorted-tail-5 dominated by old entries when the
    fresh count is < 5.  (We cap fresh at 4 by design to guarantee at
    least one old entry survives in the tail of size 5.)
    """
    now = draw(epoch_sec)
    cutoff = now - DEFAULT_WINDOW_SEC
    old_count = draw(st.integers(min_value=DEFAULT_THRESHOLD, max_value=15))
    old = draw(
        st.lists(
            st.integers(min_value=0, max_value=cutoff),
            min_size=old_count,
            max_size=old_count,
        )
    )
    fresh_count = draw(st.integers(min_value=0, max_value=DEFAULT_THRESHOLD - 1))
    fresh = draw(
        st.lists(
            st.integers(min_value=cutoff + 1, max_value=now),
            min_size=fresh_count,
            max_size=fresh_count,
        )
    )
    combined = old + fresh
    shuffled = draw(st.permutations(combined))
    return list(shuffled), now


# ---------------------------------------------------------------------------
# Property 8 (a) — true case: tail-`threshold` fully inside the window.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(case=_locked_history())
def test_property8_locks_when_tail_within_window(case: tuple[list[int], int]) -> None:
    """is_locked is True when the tail-5 (sorted) is strictly newer than cutoff.

    Validates: Requirements 1.6
    """
    failed_ats, now = case
    assert is_locked(failed_ats, now) is True, (
        f"expected True for tail-in-window history "
        f"failed_ats={failed_ats!r} now={now} cutoff={now - DEFAULT_WINDOW_SEC}"
    )


# ---------------------------------------------------------------------------
# Property 8 (b1) — false case: count-deficient (< threshold entries).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(case=_short_history())
def test_property8_false_when_count_below_threshold(
    case: tuple[list[int], int],
) -> None:
    """is_locked is False whenever `len(failed_ats) < threshold`.

    Validates: Requirements 1.6
    """
    failed_ats, now = case
    assert is_locked(failed_ats, now) is False, (
        f"expected False for count-deficient history "
        f"failed_ats={failed_ats!r} (len={len(failed_ats)}) now={now}"
    )


# ---------------------------------------------------------------------------
# Property 8 (b2) — false case: sorted-tail contains ≥1 entry at or before cutoff.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(case=_window_deficient_history())
def test_property8_false_when_tail_contains_old_entry(
    case: tuple[list[int], int],
) -> None:
    """is_locked is False when any of the tail-5 entries is ≤ cutoff.

    Validates: Requirements 1.6
    """
    failed_ats, now = case
    cutoff = now - DEFAULT_WINDOW_SEC
    # Sanity invariant of the generator (helps debug if it ever drifts).
    tail = sorted(failed_ats)[-DEFAULT_THRESHOLD:]
    assert any(t <= cutoff for t in tail), (
        f"generator drift: tail {tail!r} unexpectedly all > cutoff={cutoff} "
        f"(failed_ats={failed_ats!r}, now={now})"
    )
    assert is_locked(failed_ats, now) is False, (
        f"expected False for tail-with-old-entry history "
        f"failed_ats={failed_ats!r} now={now} cutoff={cutoff}"
    )


# ---------------------------------------------------------------------------
# Property 8 (c) — boundary on the strict inequality.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(now=epoch_sec)
def test_property8_boundary_at_cutoff_is_false(now: int) -> None:
    """All 5 timestamps exactly at `now - window_sec` (cutoff) → False.

    The contract uses strict `>` against cutoff, so cutoff itself is
    NOT in the window.

    Validates: Requirements 1.6
    """
    cutoff = now - DEFAULT_WINDOW_SEC
    failed_ats = [cutoff] * DEFAULT_THRESHOLD
    assert is_locked(failed_ats, now) is False, (
        f"expected False at cutoff boundary now={now} cutoff={cutoff}"
    )


@PBT_SETTINGS
@given(now=epoch_sec)
def test_property8_boundary_just_after_cutoff_is_true(now: int) -> None:
    """All 5 timestamps at `now - window_sec + 1` (1 sec inside) → True.

    Validates: Requirements 1.6
    """
    cutoff = now - DEFAULT_WINDOW_SEC
    failed_ats = [cutoff + 1] * DEFAULT_THRESHOLD
    assert is_locked(failed_ats, now) is True, (
        f"expected True just inside cutoff now={now} cutoff={cutoff}"
    )


# ---------------------------------------------------------------------------
# Property 8 (d) — order-independence: same multiset → same verdict.
# ---------------------------------------------------------------------------


@st.composite
def _two_permutations_of_same_multiset(
    draw: st.DrawFn,
) -> tuple[list[int], list[int], int]:
    """Draw a single history H and two random permutations of it."""
    now = draw(epoch_sec)
    h = draw(
        st.lists(
            st.integers(min_value=0, max_value=now),
            min_size=0,
            max_size=20,
        )
    )
    p1 = draw(st.permutations(h))
    p2 = draw(st.permutations(h))
    return list(p1), list(p2), now


@PBT_SETTINGS
@given(case=_two_permutations_of_same_multiset())
def test_property8_order_independence(
    case: tuple[list[int], list[int], int],
) -> None:
    """is_locked output depends only on the multiset, not on input order.

    Validates: Requirements 1.6
    """
    p1, p2, now = case
    assert is_locked(p1, now) == is_locked(p2, now), (
        f"order dependence detected: p1={p1!r} p2={p2!r} now={now} "
        f"→ {is_locked(p1, now)} vs {is_locked(p2, now)}"
    )


# ---------------------------------------------------------------------------
# Property 8 (e) — non-default threshold / window_sec parameterisation.
# ---------------------------------------------------------------------------

# Custom epoch range for the parameterised variant: window_sec is smaller
# (60), so we only need now >= 60 to keep cutoff >= 0.  Reuse `epoch_sec`
# which already exceeds 60 by a wide margin.

_ALT_THRESHOLD = 3
_ALT_WINDOW_SEC = 60


@st.composite
def _locked_history_alt(draw: st.DrawFn) -> tuple[list[int], int]:
    """Same construction as `_locked_history` but for threshold=3, window=60."""
    now = draw(epoch_sec)
    cutoff = now - _ALT_WINDOW_SEC
    k = draw(st.integers(min_value=_ALT_THRESHOLD, max_value=10))
    recent = draw(
        st.lists(
            st.integers(min_value=cutoff + 1, max_value=now),
            min_size=k,
            max_size=k,
        )
    )
    old = draw(
        st.lists(
            st.integers(min_value=0, max_value=cutoff),
            min_size=0,
            max_size=5,
        )
    )
    combined = recent + old
    shuffled = draw(st.permutations(combined))
    return list(shuffled), now


@PBT_SETTINGS
@given(case=_locked_history_alt())
def test_property8_alt_params_locks_when_tail_within_window(
    case: tuple[list[int], int],
) -> None:
    """Same locking contract holds for (threshold=3, window_sec=60).

    Validates: Requirements 1.6
    """
    failed_ats, now = case
    assert (
        is_locked(failed_ats, now, threshold=_ALT_THRESHOLD, window_sec=_ALT_WINDOW_SEC)
        is True
    )


@PBT_SETTINGS
@given(now=epoch_sec, h=st.lists(st.integers(min_value=0), max_size=_ALT_THRESHOLD - 1))
def test_property8_alt_params_false_when_count_below_threshold(
    now: int, h: list[int]
) -> None:
    """(threshold=3, window_sec=60): fewer than 3 entries never lock.

    Validates: Requirements 1.6
    """
    # Clamp values to [0, now] so they are semantically valid timestamps.
    history = [min(t, now) for t in h]
    assert (
        is_locked(history, now, threshold=_ALT_THRESHOLD, window_sec=_ALT_WINDOW_SEC)
        is False
    )


@PBT_SETTINGS
@example(now=10_000_000)
@given(now=epoch_sec)
def test_property8_alt_params_boundary_at_cutoff_is_false(now: int) -> None:
    """(threshold=3, window_sec=60): 3 entries exactly at cutoff → False."""
    cutoff = now - _ALT_WINDOW_SEC
    failed_ats = [cutoff] * _ALT_THRESHOLD
    assert (
        is_locked(
            failed_ats, now, threshold=_ALT_THRESHOLD, window_sec=_ALT_WINDOW_SEC
        )
        is False
    )


@PBT_SETTINGS
@given(now=epoch_sec)
def test_property8_alt_params_boundary_just_after_cutoff_is_true(now: int) -> None:
    """(threshold=3, window_sec=60): 3 entries at cutoff+1 → True."""
    cutoff = now - _ALT_WINDOW_SEC
    failed_ats = [cutoff + 1] * _ALT_THRESHOLD
    assert (
        is_locked(
            failed_ats, now, threshold=_ALT_THRESHOLD, window_sec=_ALT_WINDOW_SEC
        )
        is True
    )


# ---------------------------------------------------------------------------
# Explicit unit examples (anchored boundary cases enumerated in the task).
# ---------------------------------------------------------------------------

_NOW = 1_700_000_000  # 2023-11-14T22:13:20Z — arbitrary, well above window.


def test_unit_empty_list_is_false() -> None:
    """Empty history never locks."""
    assert is_locked([], _NOW) is False


def test_unit_one_to_four_entries_is_false() -> None:
    """1..4 fresh entries never lock (count-deficient)."""
    for n in (1, 2, 3, 4):
        history = [_NOW] * n  # all maximally fresh
        assert is_locked(history, _NOW) is False, (
            f"expected False for {n} fresh entries (count-deficient)"
        )


def test_unit_five_entries_all_at_now_is_true() -> None:
    """Five entries all exactly at `now` → True (fresh edge)."""
    assert is_locked([_NOW] * 5, _NOW) is True


def test_unit_five_entries_all_at_now_minus_one_is_true() -> None:
    """Five entries all at `now - 1` (deeply inside window) → True."""
    assert is_locked([_NOW - 1] * 5, _NOW) is True


def test_unit_five_entries_one_exactly_at_cutoff_is_false() -> None:
    """One of five entries sits exactly at cutoff → False (strict `>`)."""
    cutoff = _NOW - DEFAULT_WINDOW_SEC
    failed_ats = [cutoff, _NOW - 1, _NOW - 2, _NOW - 3, _NOW - 4]
    assert is_locked(failed_ats, _NOW) is False


def test_unit_ten_entries_newest_five_in_window_is_true() -> None:
    """Ten entries; oldest five very old, newest five in window → True."""
    cutoff = _NOW - DEFAULT_WINDOW_SEC
    old = [cutoff - 100, cutoff - 200, cutoff - 300, cutoff - 400, cutoff - 500]
    fresh = [_NOW - 1, _NOW - 2, _NOW - 3, _NOW - 4, _NOW - 5]
    assert is_locked(old + fresh, _NOW) is True
    # Order independence anchor: shuffled input yields the same answer.
    shuffled = [old[2], fresh[1], old[0], fresh[4], old[4], fresh[0], old[1], fresh[3], old[3], fresh[2]]
    assert is_locked(shuffled, _NOW) is True


def test_unit_ten_entries_newest_four_in_window_is_false() -> None:
    """Ten entries; only newest four in window → False (5th tail entry old)."""
    cutoff = _NOW - DEFAULT_WINDOW_SEC
    old = [
        cutoff - 100,
        cutoff - 200,
        cutoff - 300,
        cutoff - 400,
        cutoff - 500,
        cutoff,  # ← will land in the sorted tail-5 since fresh count is 4.
    ]
    fresh = [_NOW - 1, _NOW - 2, _NOW - 3, _NOW - 4]
    assert is_locked(old + fresh, _NOW) is False
