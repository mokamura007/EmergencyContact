"""Property 23 — 90-day expiry predicate PBT (Phase 13.23).

Validates: Requirements 10.7 (録音 / Transcript 90 日保持), 12.2, 12.3
(presigned URL 発行ポリシー / 15 分 TTL 不変条件).

Contract (verbatim from shared/recording/expiry.py):
    For all reference timestamps t_ref and current timestamps t_now,
        can_issue_url(t_ref, t_now, max_days=90) is True
        iff
        (t_now - t_ref) <= 90 days.

    Additionally, compute_expiry(t_now) returns the maximum-acceptable
    reference time: any older value yields can_issue_url == False.

This file enforces:
  (a)  Within-window pair → True (random ref/now in [0, 90d] delta).
  (b)  Beyond-window pair → False (random ref/now with delta > 90d).
  (c)  Boundary: delta == 90 days exactly → True (inclusive `<=`).
  (d)  Boundary: delta == 90 days + 1 second → False.
  (e)  Parameterisation: same a/b/c/d for max_days=30 and max_days=7.
  (f)  compute_expiry consistency: compute_expiry(now) + 90d == parse(now)
       AND can_issue_url(compute_expiry(now).isoformat(), now) is True.
  (g)  15-minute TTL invariant: PRESIGNED_URL_TTL_SECONDS == 900 (regression
       detector — prevents silent policy drift).
  (h)  Format equivalence: `Z` and `+00:00` suffixes produce identical
       verdicts for the same epoch.

Plus a small unit-test layer pinning the edge cases enumerated in the task:
  - ref == now (delta == 0) → True
  - ref in the future (negative delta) → True (contract is `<=`)
  - max_days == 0 boundary
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.recording.expiry import (
    PRESIGNED_URL_TTL_SECONDS,
    _parse_iso,
    can_issue_url,
    compute_expiry,
)
from tests.strategies import (
    DEFAULT_MAX_DAYS,
    SECONDS_PER_DAY,
    _epoch_to_iso,
    epoch_sec,
    iso_pair_over_days,
    iso_pair_within_days,
)

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


# ---------------------------------------------------------------------------
# (a) Within-window pair → True (default max_days=90).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(pair=iso_pair_within_days())
def test_property23_within_90days_returns_true(pair: tuple[str, str]) -> None:
    """can_issue_url is True for any (ref, now) with delta in [0, 90 days].

    Validates: Requirements 10.7
    """
    ref_iso, now_iso = pair
    assert can_issue_url(ref_iso, now_iso) is True, (
        f"expected True for within-window pair ref={ref_iso!r} now={now_iso!r} "
        f"delta_sec={(_parse_iso(now_iso) - _parse_iso(ref_iso)).total_seconds()}"
    )


# ---------------------------------------------------------------------------
# (b) Beyond-window pair → False (default max_days=90).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(pair=iso_pair_over_days())
def test_property23_over_90days_returns_false(pair: tuple[str, str]) -> None:
    """can_issue_url is False for any (ref, now) with delta strictly > 90 days.

    Validates: Requirements 10.7
    """
    ref_iso, now_iso = pair
    delta = _parse_iso(now_iso) - _parse_iso(ref_iso)
    # Sanity invariant of the generator.
    assert delta > timedelta(days=DEFAULT_MAX_DAYS), (
        f"generator drift: delta={delta} not strictly > 90d "
        f"(ref={ref_iso!r}, now={now_iso!r})"
    )
    assert can_issue_url(ref_iso, now_iso) is False, (
        f"expected False for beyond-window pair ref={ref_iso!r} now={now_iso!r} "
        f"delta={delta}"
    )


# ---------------------------------------------------------------------------
# (c) Boundary: delta == 90 days exactly → True (inclusive `<=`).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(now_sec=1_700_000_000)
@given(
    now_sec=st.integers(
        min_value=DEFAULT_MAX_DAYS * SECONDS_PER_DAY, max_value=2**31 - 1
    )
)
def test_property23_boundary_exact_90days_is_true(now_sec: int) -> None:
    """delta == exactly 90 days → True (inclusive upper bound).

    The local `st.integers` strategy bounds `now_sec` so that `ref_sec` is
    non-negative even at the 90-day floor.

    Validates: Requirements 10.7
    """
    ref_sec = now_sec - DEFAULT_MAX_DAYS * SECONDS_PER_DAY
    ref_iso = _epoch_to_iso(ref_sec, use_z_suffix=False)
    now_iso = _epoch_to_iso(now_sec, use_z_suffix=False)
    assert can_issue_url(ref_iso, now_iso) is True, (
        f"expected True at exact 90d boundary ref={ref_iso!r} now={now_iso!r}"
    )


# ---------------------------------------------------------------------------
# (d) Boundary: delta == 90 days + 1 second → False.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(now_sec=1_700_000_000)
@given(
    now_sec=st.integers(
        min_value=DEFAULT_MAX_DAYS * SECONDS_PER_DAY + 1, max_value=2**31 - 1
    )
)
def test_property23_boundary_just_past_90days_is_false(now_sec: int) -> None:
    """delta == 90 days + 1 second → False (one second past boundary).

    Validates: Requirements 10.7
    """
    ref_sec = now_sec - DEFAULT_MAX_DAYS * SECONDS_PER_DAY - 1
    ref_iso = _epoch_to_iso(ref_sec, use_z_suffix=False)
    now_iso = _epoch_to_iso(now_sec, use_z_suffix=False)
    assert can_issue_url(ref_iso, now_iso) is False, (
        f"expected False at 90d+1s ref={ref_iso!r} now={now_iso!r}"
    )


# ---------------------------------------------------------------------------
# (e) Parameterisation: same contract holds for max_days=30 and max_days=7.
# ---------------------------------------------------------------------------

_ALT_MAX_DAYS_30 = 30
_ALT_MAX_DAYS_7 = 7


@PBT_SETTINGS
@given(pair=iso_pair_within_days(max_days=_ALT_MAX_DAYS_30))
def test_property23_alt_max_days_30_within_returns_true(
    pair: tuple[str, str],
) -> None:
    """max_days=30: within-window pair → True."""
    ref_iso, now_iso = pair
    assert can_issue_url(ref_iso, now_iso, max_days=_ALT_MAX_DAYS_30) is True


@PBT_SETTINGS
@given(pair=iso_pair_over_days(max_days=_ALT_MAX_DAYS_30))
def test_property23_alt_max_days_30_over_returns_false(
    pair: tuple[str, str],
) -> None:
    """max_days=30: beyond-window pair → False."""
    ref_iso, now_iso = pair
    assert can_issue_url(ref_iso, now_iso, max_days=_ALT_MAX_DAYS_30) is False


@PBT_SETTINGS
@given(
    now_sec=st.integers(
        min_value=_ALT_MAX_DAYS_30 * SECONDS_PER_DAY, max_value=2**31 - 1
    )
)
def test_property23_alt_max_days_30_boundary_exact_is_true(now_sec: int) -> None:
    """max_days=30: exact 30-day boundary → True."""
    ref_sec = now_sec - _ALT_MAX_DAYS_30 * SECONDS_PER_DAY
    ref_iso = _epoch_to_iso(ref_sec, use_z_suffix=False)
    now_iso = _epoch_to_iso(now_sec, use_z_suffix=False)
    assert can_issue_url(ref_iso, now_iso, max_days=_ALT_MAX_DAYS_30) is True


@PBT_SETTINGS
@given(
    now_sec=st.integers(
        min_value=_ALT_MAX_DAYS_30 * SECONDS_PER_DAY + 1, max_value=2**31 - 1
    )
)
def test_property23_alt_max_days_30_boundary_just_past_is_false(
    now_sec: int,
) -> None:
    """max_days=30: 30-day + 1 second → False."""
    ref_sec = now_sec - _ALT_MAX_DAYS_30 * SECONDS_PER_DAY - 1
    ref_iso = _epoch_to_iso(ref_sec, use_z_suffix=False)
    now_iso = _epoch_to_iso(now_sec, use_z_suffix=False)
    assert can_issue_url(ref_iso, now_iso, max_days=_ALT_MAX_DAYS_30) is False


@PBT_SETTINGS
@given(pair=iso_pair_within_days(max_days=_ALT_MAX_DAYS_7))
def test_property23_alt_max_days_7_within_returns_true(
    pair: tuple[str, str],
) -> None:
    """max_days=7: within-window pair → True (smaller window stress)."""
    ref_iso, now_iso = pair
    assert can_issue_url(ref_iso, now_iso, max_days=_ALT_MAX_DAYS_7) is True


@PBT_SETTINGS
@given(pair=iso_pair_over_days(max_days=_ALT_MAX_DAYS_7))
def test_property23_alt_max_days_7_over_returns_false(
    pair: tuple[str, str],
) -> None:
    """max_days=7: beyond-window pair → False."""
    ref_iso, now_iso = pair
    assert can_issue_url(ref_iso, now_iso, max_days=_ALT_MAX_DAYS_7) is False


# ---------------------------------------------------------------------------
# (f) compute_expiry consistency.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(now_sec=1_700_000_000, use_z=False)
@example(now_sec=1_700_000_000, use_z=True)
@given(
    now_sec=st.integers(
        min_value=DEFAULT_MAX_DAYS * SECONDS_PER_DAY, max_value=2**31 - 1
    ),
    use_z=st.booleans(),
)
def test_property23_compute_expiry_plus_window_equals_now(
    now_sec: int, use_z: bool
) -> None:
    """compute_expiry(t_now) + max_days == parse(t_now).

    Validates: Requirements 10.7 (compute_expiry returns the earliest
    reference time still eligible — its offset from now equals max_days).
    """
    now_iso = _epoch_to_iso(now_sec, use_z_suffix=use_z)
    expiry = compute_expiry(now_iso)
    assert expiry + timedelta(days=DEFAULT_MAX_DAYS) == _parse_iso(now_iso), (
        f"compute_expiry drift: expiry={expiry} now={now_iso!r} "
        f"diff={(_parse_iso(now_iso) - expiry).total_seconds()}s"
    )


@PBT_SETTINGS
@example(now_sec=1_700_000_000, use_z=False)
@example(now_sec=1_700_000_000, use_z=True)
@given(
    now_sec=st.integers(
        min_value=DEFAULT_MAX_DAYS * SECONDS_PER_DAY, max_value=2**31 - 1
    ),
    use_z=st.booleans(),
)
def test_property23_compute_expiry_boundary_is_eligible(
    now_sec: int, use_z: bool
) -> None:
    """can_issue_url(compute_expiry(now).isoformat(), now) is True.

    The expiry boundary is the *earliest accepted* reference, so the
    inclusive `<=` contract must accept it.
    """
    now_iso = _epoch_to_iso(now_sec, use_z_suffix=use_z)
    expiry = compute_expiry(now_iso)
    # `datetime.isoformat()` from an aware UTC datetime produces `+00:00`,
    # which `_parse_iso` accepts directly.
    assert can_issue_url(expiry.isoformat(), now_iso) is True, (
        f"compute_expiry boundary not eligible: expiry={expiry.isoformat()!r} "
        f"now={now_iso!r}"
    )


# ---------------------------------------------------------------------------
# (g) 15-minute TTL invariant: regression detector.
# ---------------------------------------------------------------------------


def test_property23_presigned_url_ttl_is_exactly_900_seconds() -> None:
    """PRESIGNED_URL_TTL_SECONDS must remain exactly 15 minutes (= 900 s).

    Validates: Requirements 12.2, 12.3 (URL 有効期限が常に 15 分以内).
    Any silent change to this constant breaks the safety invariant
    documented in design.md for recording / transcript presigned URLs.
    """
    assert PRESIGNED_URL_TTL_SECONDS == 15 * 60
    assert PRESIGNED_URL_TTL_SECONDS == 900


# ---------------------------------------------------------------------------
# (h) Format equivalence: `Z` and `+00:00` produce identical verdicts.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(ref_sec=epoch_sec, now_sec=epoch_sec)
def test_property23_z_and_plus_zero_suffix_are_equivalent(
    ref_sec: int, now_sec: int
) -> None:
    """Same epoch in `Z` form vs `+00:00` form yields the same verdict.

    Validates: Requirements 10.7 (format-tolerant ISO 8601 parsing).
    """
    ref_z = _epoch_to_iso(ref_sec, use_z_suffix=True)
    ref_plus = _epoch_to_iso(ref_sec, use_z_suffix=False)
    now_z = _epoch_to_iso(now_sec, use_z_suffix=True)
    now_plus = _epoch_to_iso(now_sec, use_z_suffix=False)
    # All four (ref-suffix × now-suffix) combinations must agree.
    verdict_zz = can_issue_url(ref_z, now_z)
    verdict_zp = can_issue_url(ref_z, now_plus)
    verdict_pz = can_issue_url(ref_plus, now_z)
    verdict_pp = can_issue_url(ref_plus, now_plus)
    assert verdict_zz == verdict_zp == verdict_pz == verdict_pp, (
        f"format equivalence broken: ZZ={verdict_zz} ZP={verdict_zp} "
        f"PZ={verdict_pz} PP={verdict_pp} (ref_sec={ref_sec}, now_sec={now_sec})"
    )


# ---------------------------------------------------------------------------
# Explicit unit examples (anchored boundary cases enumerated in the task).
# ---------------------------------------------------------------------------

# Arbitrary anchor: 2023-11-14T22:13:20Z.  Well above the 90-day floor so
# negative `ref` never appears.
_NOW_SEC = 1_700_000_000
_NOW_ISO_Z = _epoch_to_iso(_NOW_SEC, use_z_suffix=True)
_NOW_ISO_PLUS = _epoch_to_iso(_NOW_SEC, use_z_suffix=False)


def test_unit_ref_equals_now_is_true() -> None:
    """delta == 0 → True (ref exactly at now)."""
    assert can_issue_url(_NOW_ISO_Z, _NOW_ISO_Z) is True
    assert can_issue_url(_NOW_ISO_PLUS, _NOW_ISO_PLUS) is True


def test_unit_ref_in_future_is_true() -> None:
    """Negative delta (ref later than now) → True under `<=` contract.

    Although clock-skew of this magnitude is unrealistic, the contract is
    one-sided (`(now - ref) <= max_days`); negative values trivially
    satisfy it.  Documenting the asymmetry here prevents accidental
    introduction of a lower-bound check.
    """
    future_sec = _NOW_SEC + 3600  # 1 hour in the future
    future_iso = _epoch_to_iso(future_sec, use_z_suffix=False)
    assert can_issue_url(future_iso, _NOW_ISO_Z) is True


def test_unit_max_days_zero_same_instant_is_true() -> None:
    """max_days=0 + delta=0 → True (inclusive `<=` at the boundary)."""
    assert can_issue_url(_NOW_ISO_Z, _NOW_ISO_Z, max_days=0) is True


def test_unit_max_days_zero_one_second_apart_is_false() -> None:
    """max_days=0 + delta=1s → False (the only acceptable delta is 0)."""
    one_sec_earlier = _epoch_to_iso(_NOW_SEC - 1, use_z_suffix=False)
    assert can_issue_url(one_sec_earlier, _NOW_ISO_Z, max_days=0) is False


def test_unit_90days_minus_one_second_is_true() -> None:
    """delta == 90 days - 1 second → True (just inside the window)."""
    ref_sec = _NOW_SEC - DEFAULT_MAX_DAYS * SECONDS_PER_DAY + 1
    ref_iso = _epoch_to_iso(ref_sec, use_z_suffix=False)
    assert can_issue_url(ref_iso, _NOW_ISO_Z) is True


def test_unit_compute_expiry_returns_utc_datetime() -> None:
    """compute_expiry returns a tz-aware UTC datetime (not naive)."""
    expiry = compute_expiry(_NOW_ISO_Z)
    assert isinstance(expiry, datetime)
    assert expiry.tzinfo is not None
    assert expiry.utcoffset() == timedelta(0)


def test_unit_compute_expiry_subtracts_exactly_90_days() -> None:
    """compute_expiry(now) == now - 90 days (default max_days)."""
    expiry = compute_expiry(_NOW_ISO_Z)
    expected = datetime.fromtimestamp(
        _NOW_SEC - DEFAULT_MAX_DAYS * SECONDS_PER_DAY, tz=timezone.utc
    )
    assert expiry == expected
