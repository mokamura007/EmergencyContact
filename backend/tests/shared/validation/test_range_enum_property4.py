"""Property 4 — numeric-range / string-enum validator PBT (Phase 13.4).

Validates: Requirements 4.6, 4.7, 4.10, 17.2, 17.4

Contract (verbatim from shared/validation/range_enum.py):

    in_range(value, low, high) is True
    iff
        (i)   value is an int and NOT a bool, AND
        (ii)  low <= value <= high (inclusive on both ends), AND
        (iii) low <= high (invalid range arg returns False, never raises).

    in_enum(value, valid) is True
    iff
        (i)   value is a str, AND
        (ii)  `valid` is a non-empty iterable of str, AND
        (iii) value == v for some v in valid (case-sensitive).

This file enforces the cases enumerated in the task:

  in_range:
    (a) low <= value <= high (any valid int) → True
    (b) value < low OR value > high → False
    (c) Boundary: value == low → True, value == low - 1 → False,
                  value == high → True, value == high + 1 → False
    (d) Non-int (str / float / None / list / bool / ...) → False
    (e) low > high (invalid range) → False (no raise)

  in_enum:
    (a) value ∈ valid (exact match) → True
    (b) value ∉ valid (incl. case differences) → False
    (c) Non-str value → False
    (d) Empty `valid` → always False
    (e) Concrete allowed-sets for Retry_Count / Retry_Interval / env /
        mode / Voice_Status verified with representative pos / neg anchors.
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.validation.range_enum import (
    ENV_VALUES,
    MODE_VALUES,
    RETRY_COUNT_HIGH,
    RETRY_COUNT_LOW,
    RETRY_INTERVAL_HIGH,
    RETRY_INTERVAL_LOW,
    VOICE_STATUS_VALUES,
    in_enum,
    in_range,
)
from tests.strategies import non_string_value

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
    ],
)


# ---------------------------------------------------------------------------
# Local strategies — range-arg pairs and enum-value pairs.
# ---------------------------------------------------------------------------

#: Strategy generating valid (low, high) pairs with low <= high.  Bounds are
#: capped at +/- 10_000 to keep generation cheap; this comfortably covers
#: every range used by Property 4 (Retry_Count [0,5] / Retry_Interval [1,60]
#: / etc.) as well as future Number-typed parameters.
@st.composite
def _valid_range(draw: st.DrawFn) -> tuple[int, int]:
    low = draw(st.integers(min_value=-10_000, max_value=10_000))
    high = draw(st.integers(min_value=low, max_value=low + 20_000))
    return (low, high)


@st.composite
def _value_within(draw: st.DrawFn) -> tuple[int, int, int]:
    """Draw (value, low, high) with low <= value <= high."""
    low, high = draw(_valid_range())
    value = draw(st.integers(min_value=low, max_value=high))
    return (value, low, high)


@st.composite
def _value_below(draw: st.DrawFn) -> tuple[int, int, int]:
    """Draw (value, low, high) with value < low and low <= high."""
    low, high = draw(_valid_range())
    value = draw(st.integers(min_value=low - 10_000, max_value=low - 1))
    return (value, low, high)


@st.composite
def _value_above(draw: st.DrawFn) -> tuple[int, int, int]:
    """Draw (value, low, high) with value > high and low <= high."""
    low, high = draw(_valid_range())
    value = draw(st.integers(min_value=high + 1, max_value=high + 10_000))
    return (value, low, high)


@st.composite
def _invalid_range(draw: st.DrawFn) -> tuple[int, int]:
    """Draw (low, high) with low > high (invalid range arg)."""
    high = draw(st.integers(min_value=-10_000, max_value=10_000))
    low = draw(st.integers(min_value=high + 1, max_value=high + 10_000))
    return (low, high)


#: Strategy generating ALL the named Property-4 ranges as (low, high) pairs.
_named_ranges = st.sampled_from(
    [
        (RETRY_COUNT_LOW, RETRY_COUNT_HIGH),
        (RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH),
    ]
)


# Float strategy that excludes integer-valued floats only when needed; here
# we want to assert "any float, even 1.0, is rejected", so we keep all.
_any_float = st.floats(allow_nan=True, allow_infinity=True, width=64)


# Enum-membership strategies.
@st.composite
def _enum_pair(draw: st.DrawFn) -> tuple[str, frozenset[str]]:
    """Draw (value, valid) where value ∈ valid (positive case)."""
    valid = draw(
        st.sets(
            st.text(min_size=1, max_size=10),
            min_size=1,
            max_size=8,
        )
    )
    value = draw(st.sampled_from(sorted(valid)))
    return (value, frozenset(valid))


@st.composite
def _enum_pair_negative(draw: st.DrawFn) -> tuple[str, frozenset[str]]:
    """Draw (value, valid) where value ∉ valid (negative case)."""
    valid = draw(
        st.sets(
            st.text(min_size=1, max_size=10),
            min_size=1,
            max_size=8,
        )
    )
    # Generate a value NOT in valid.  Use a length one greater than every
    # element to guarantee uniqueness (cheap and avoids rejection loops).
    max_len = max(len(s) for s in valid)
    value = "z" * (max_len + 1)
    assert value not in valid
    return (value, frozenset(valid))


@st.composite
def _case_mismatch_pair(draw: st.DrawFn) -> tuple[str, frozenset[str]]:
    """Draw (value, valid) where value differs from a member of valid only
    by ASCII case (e.g. "DEV" vs "dev").
    """
    # Force ASCII letters so swapcase yields a guaranteed different string.
    base = draw(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
            min_size=1,
            max_size=10,
        )
    )
    flipped = base.swapcase()
    assert flipped != base, f"swapcase produced identical string: {base!r}"
    # `valid` contains the original-case `base` only — value (flipped) must
    # not match.
    return (flipped, frozenset({base}))


# ---------------------------------------------------------------------------
# Property 4 in_range (a) — True for low <= value <= high.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=(0, RETRY_COUNT_LOW, RETRY_COUNT_HIGH))
@example(case=(5, RETRY_COUNT_LOW, RETRY_COUNT_HIGH))
@example(case=(1, RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH))
@example(case=(60, RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH))
@given(case=_value_within())
def test_property4_in_range_true_when_within_inclusive_bounds(
    case: tuple[int, int, int],
) -> None:
    """low <= value <= high → True.

    Validates: Requirements 4.6, 4.7
    """
    value, low, high = case
    assert in_range(value, low, high) is True, (
        f"expected True for value={value} low={low} high={high}"
    )


# ---------------------------------------------------------------------------
# Property 4 in_range (b) — False for value < low OR value > high.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=(-1, RETRY_COUNT_LOW, RETRY_COUNT_HIGH))
@example(case=(0, RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH))  # 0 < 1
@given(case=_value_below())
def test_property4_in_range_false_when_below_low(
    case: tuple[int, int, int],
) -> None:
    """value < low → False.

    Validates: Requirements 4.6, 4.7
    """
    value, low, high = case
    assert in_range(value, low, high) is False, (
        f"expected False for value={value} low={low} high={high}"
    )


@PBT_SETTINGS
@example(case=(6, RETRY_COUNT_LOW, RETRY_COUNT_HIGH))
@example(case=(61, RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH))
@given(case=_value_above())
def test_property4_in_range_false_when_above_high(
    case: tuple[int, int, int],
) -> None:
    """value > high → False.

    Validates: Requirements 4.6, 4.7
    """
    value, low, high = case
    assert in_range(value, low, high) is False, (
        f"expected False for value={value} low={low} high={high}"
    )


# ---------------------------------------------------------------------------
# Property 4 in_range (c) — Boundary cases (low / low-1 / high / high+1).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(rng=_named_ranges)
def test_property4_in_range_boundary_low_inclusive(rng: tuple[int, int]) -> None:
    """value == low → True; value == low - 1 → False.

    Validates: Requirements 4.6, 4.7
    """
    low, high = rng
    assert in_range(low, low, high) is True, f"low={low} should be True at boundary"
    assert in_range(low - 1, low, high) is False, (
        f"low-1={low - 1} should be False just below boundary"
    )


@PBT_SETTINGS
@given(rng=_named_ranges)
def test_property4_in_range_boundary_high_inclusive(rng: tuple[int, int]) -> None:
    """value == high → True; value == high + 1 → False.

    Validates: Requirements 4.6, 4.7
    """
    low, high = rng
    assert in_range(high, low, high) is True, (
        f"high={high} should be True at boundary"
    )
    assert in_range(high + 1, low, high) is False, (
        f"high+1={high + 1} should be False just above boundary"
    )


# ---------------------------------------------------------------------------
# Property 4 in_range (d) — Non-int values rejected.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(bogus=non_string_value)
def test_property4_in_range_false_for_non_int(bogus: object) -> None:
    """Any value that is not strictly `int` → False.

    Validates: Requirements 4.6, 4.7 (type safety)
    """
    # `non_string_value` includes int, so skip the rare int draw (we want
    # to assert the negative branch only).  `bool` IS-A int per the
    # contract but must be rejected; do not skip bool.
    if type(bogus) is int:
        return
    assert in_range(bogus, 0, 100) is False, (
        f"expected False for non-int bogus={bogus!r} (type={type(bogus).__name__})"
    )


@PBT_SETTINGS
@given(b=st.booleans())
def test_property4_in_range_false_for_bool(b: bool) -> None:
    """`True` and `False` are rejected even though bool ⊂ int.

    Validates: Requirements 4.6 (Retry_Count is numeric, not boolean)
    """
    assert in_range(b, 0, 5) is False, f"expected False for bool {b!r}"


@PBT_SETTINGS
@given(f=_any_float)
def test_property4_in_range_false_for_float(f: float) -> None:
    """Floats (incl. integer-valued like 1.0) are rejected.

    Validates: Requirements 4.6 (Number-typed integer parameters)
    """
    assert in_range(f, 0, 100) is False, f"expected False for float {f!r}"


# ---------------------------------------------------------------------------
# Property 4 in_range (e) — low > high (invalid range arg) → False, no raise.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(rng=_invalid_range(), value=st.integers(min_value=-20_000, max_value=20_000))
def test_property4_in_range_invalid_range_returns_false(
    rng: tuple[int, int], value: int
) -> None:
    """low > high → False for every value (no exception raised).

    Validates: principle 19(b) (no raise, no fallback — predicate is total)
    """
    low, high = rng
    assert low > high  # generator invariant
    # No try/except: any raise here would surface as a Hypothesis failure
    # (which is the desired regression signal).
    assert in_range(value, low, high) is False, (
        f"expected False for invalid range low={low} high={high} value={value}"
    )


# ---------------------------------------------------------------------------
# Property 4 in_enum (a) — True when value ∈ valid (exact match).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=("dev", ENV_VALUES))
@example(case=("ALL", MODE_VALUES))
@example(case=("PENDING", VOICE_STATUS_VALUES))
@given(case=_enum_pair())
def test_property4_in_enum_true_on_exact_match(
    case: tuple[str, frozenset[str]],
) -> None:
    """value ∈ valid (case-sensitive exact match) → True.

    Validates: Requirements 4.10, 17.2, 17.4
    """
    value, valid = case
    assert in_enum(value, valid) is True, (
        f"expected True for value={value!r} valid={sorted(valid)!r}"
    )


# ---------------------------------------------------------------------------
# Property 4 in_enum (b) — False when value ∉ valid, including case mismatch.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=("DEV", ENV_VALUES))  # case mismatch
@example(case=("all", MODE_VALUES))  # case mismatch
@example(case=("pending", VOICE_STATUS_VALUES))  # case mismatch
@given(case=_enum_pair_negative())
def test_property4_in_enum_false_when_not_in_valid(
    case: tuple[str, frozenset[str]],
) -> None:
    """value ∉ valid → False.

    Validates: Requirements 4.10, 17.2, 17.4
    """
    value, valid = case
    assert in_enum(value, valid) is False, (
        f"expected False for value={value!r} valid={sorted(valid)!r}"
    )


@PBT_SETTINGS
@given(case=_case_mismatch_pair())
def test_property4_in_enum_case_sensitive(
    case: tuple[str, frozenset[str]],
) -> None:
    """ASCII case differences count as mismatch → False.

    Validates: Requirements 17.2 (`dev` ≠ `DEV`), 17.4
    """
    value, valid = case
    assert in_enum(value, valid) is False, (
        f"expected False for case-mismatch value={value!r} valid={sorted(valid)!r}"
    )


# ---------------------------------------------------------------------------
# Property 4 in_enum (c) — Non-str value rejected.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(bogus=non_string_value)
def test_property4_in_enum_false_for_non_str(bogus: object) -> None:
    """Non-str value → False.

    Validates: Requirements 4.10, 17.2, 17.4 (type safety)
    """
    if isinstance(bogus, str):
        return  # non_string_value does not emit str today; guard anyway.
    assert in_enum(bogus, ENV_VALUES) is False, (
        f"expected False for non-str bogus={bogus!r} (type={type(bogus).__name__})"
    )


# ---------------------------------------------------------------------------
# Property 4 in_enum (d) — Empty `valid` always returns False.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(value=st.text(min_size=0, max_size=20))
def test_property4_in_enum_false_for_empty_valid(value: str) -> None:
    """Empty `valid` iterable → False for every value.

    Validates: Requirements 4.10, 17.2, 17.4 (empty allowed-set rejects all)
    """
    assert in_enum(value, frozenset()) is False
    assert in_enum(value, []) is False
    assert in_enum(value, ()) is False
    assert in_enum(value, set()) is False


# ---------------------------------------------------------------------------
# Property 4 in_enum (e) — Named Property-4 sets (env / mode / Voice_Status).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(value=st.sampled_from(sorted(ENV_VALUES)))
def test_property4_in_enum_env_positive(value: str) -> None:
    """Every member of ENV_VALUES is accepted (Requirement 17.2)."""
    assert in_enum(value, ENV_VALUES) is True


@PBT_SETTINGS
@given(value=st.sampled_from(sorted(MODE_VALUES)))
def test_property4_in_enum_mode_positive(value: str) -> None:
    """Every member of MODE_VALUES is accepted (Requirement 4.10)."""
    assert in_enum(value, MODE_VALUES) is True


@PBT_SETTINGS
@given(value=st.sampled_from(sorted(VOICE_STATUS_VALUES)))
def test_property4_in_enum_voice_status_positive(value: str) -> None:
    """Every member of VOICE_STATUS_VALUES is accepted (Requirement 17.4)."""
    assert in_enum(value, VOICE_STATUS_VALUES) is True


# ---------------------------------------------------------------------------
# Unit anchors — explicit boundary / type-rejection / case-sensitivity.
# ---------------------------------------------------------------------------


def test_unit_in_range_retry_count_low_inclusive() -> None:
    """Retry_Count = 0 is accepted (Requirement 4.6 lower bound)."""
    assert in_range(0, RETRY_COUNT_LOW, RETRY_COUNT_HIGH) is True


def test_unit_in_range_retry_count_high_inclusive() -> None:
    """Retry_Count = 5 is accepted (Requirement 4.6 upper bound)."""
    assert in_range(5, RETRY_COUNT_LOW, RETRY_COUNT_HIGH) is True


def test_unit_in_range_retry_count_below_rejected() -> None:
    """Retry_Count = -1 is rejected."""
    assert in_range(-1, RETRY_COUNT_LOW, RETRY_COUNT_HIGH) is False


def test_unit_in_range_retry_count_above_rejected() -> None:
    """Retry_Count = 6 is rejected."""
    assert in_range(6, RETRY_COUNT_LOW, RETRY_COUNT_HIGH) is False


def test_unit_in_range_retry_interval_low_inclusive() -> None:
    """Retry_Interval = 1 minute is accepted (Requirement 4.7 lower)."""
    assert in_range(1, RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH) is True


def test_unit_in_range_retry_interval_high_inclusive() -> None:
    """Retry_Interval = 60 minutes is accepted (Requirement 4.7 upper)."""
    assert in_range(60, RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH) is True


def test_unit_in_range_retry_interval_zero_rejected() -> None:
    """Retry_Interval = 0 is rejected (lower bound is 1)."""
    assert in_range(0, RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH) is False


def test_unit_in_range_retry_interval_above_rejected() -> None:
    """Retry_Interval = 61 is rejected."""
    assert in_range(61, RETRY_INTERVAL_LOW, RETRY_INTERVAL_HIGH) is False


def test_unit_in_range_bool_true_rejected() -> None:
    """`True` is rejected even though `0 <= True <= 5` would otherwise pass."""
    assert in_range(True, 0, 5) is False


def test_unit_in_range_bool_false_rejected() -> None:
    """`False` is rejected even though `0 <= False <= 5` would otherwise pass."""
    assert in_range(False, 0, 5) is False


def test_unit_in_range_float_integer_value_rejected() -> None:
    """`1.0` is rejected: floats are not int-typed."""
    assert in_range(1.0, 0, 5) is False


def test_unit_in_range_float_fractional_rejected() -> None:
    """`2.5` is rejected."""
    assert in_range(2.5, 0, 5) is False


def test_unit_in_range_str_rejected() -> None:
    """`"3"` is rejected: str is not int."""
    assert in_range("3", 0, 5) is False


def test_unit_in_range_none_rejected() -> None:
    """`None` is rejected."""
    assert in_range(None, 0, 5) is False


def test_unit_in_range_invalid_range_returns_false() -> None:
    """`low > high` returns False without raising."""
    assert in_range(3, 10, 0) is False
    assert in_range(0, 1, 0) is False


def test_unit_in_enum_env_dev_accepted() -> None:
    """`"dev"` ∈ ENV_VALUES → True (Requirement 17.2)."""
    assert in_enum("dev", ENV_VALUES) is True


def test_unit_in_enum_env_uppercase_rejected() -> None:
    """`"DEV"` is rejected: case-sensitive (Requirement 17.2)."""
    assert in_enum("DEV", ENV_VALUES) is False


def test_unit_in_enum_env_unknown_rejected() -> None:
    """`"local"` is rejected: not a configured environment."""
    assert in_enum("local", ENV_VALUES) is False


def test_unit_in_enum_mode_all_accepted() -> None:
    """`"ALL"` is accepted (Requirement 4.10)."""
    assert in_enum("ALL", MODE_VALUES) is True


def test_unit_in_enum_mode_unreachable_only_accepted() -> None:
    """`"UNREACHABLE_ONLY"` is accepted (Requirement 4.10)."""
    assert in_enum("UNREACHABLE_ONLY", MODE_VALUES) is True


def test_unit_in_enum_mode_lowercase_rejected() -> None:
    """`"all"` is rejected: case-sensitive (Requirement 4.10)."""
    assert in_enum("all", MODE_VALUES) is False


def test_unit_in_enum_voice_status_all_members_accepted() -> None:
    """Every Voice_Status enum value is accepted (Requirement 17.4)."""
    for status in ("PENDING", "SAFE", "INJURED", "UNAVAILABLE", "OTHER"):
        assert in_enum(status, VOICE_STATUS_VALUES) is True, (
            f"{status!r} should be accepted"
        )


def test_unit_in_enum_voice_status_unknown_rejected() -> None:
    """Unknown Voice_Status values are rejected (Requirement 17.4)."""
    for status in ("UNKNOWN", "Safe", "safe", "DEAD", ""):
        assert in_enum(status, VOICE_STATUS_VALUES) is False, (
            f"{status!r} should be rejected"
        )


def test_unit_in_enum_non_str_int_rejected() -> None:
    """int `5` is rejected even if `valid` contains the string `"5"`."""
    assert in_enum(5, {"5"}) is False


def test_unit_in_enum_non_str_bool_rejected() -> None:
    """`True` is rejected even though `bool` has a str repr."""
    assert in_enum(True, {"True"}) is False


def test_unit_in_enum_none_rejected() -> None:
    """`None` is rejected."""
    assert in_enum(None, ENV_VALUES) is False


def test_unit_in_enum_empty_valid_set_rejects_all() -> None:
    """Empty `valid` set rejects every str."""
    assert in_enum("dev", frozenset()) is False
    assert in_enum("", frozenset()) is False
    assert in_enum("anything", []) is False


def test_unit_in_enum_empty_string_value_handled_correctly() -> None:
    """Empty string is accepted iff `""` is explicitly in `valid`."""
    assert in_enum("", {""}) is True
    assert in_enum("", ENV_VALUES) is False
