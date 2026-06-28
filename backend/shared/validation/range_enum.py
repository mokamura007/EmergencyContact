"""Numeric-range / string-enum validators for Requirements 4.6, 4.7, 4.10,
17.2, 17.4 / Property 4.

Property 4 (target of Hypothesis PBT in Phase 13.4):

    in_range(value, low, high) is True
    iff
        (i)   value is an int and NOT a bool, AND
        (ii)  low <= value <= high (inclusive on both ends), AND
        (iii) low <= high (invalid range arg returns False, never raises).

    in_enum(value, valid) is True
    iff
        (i)   value is a str, AND
        (ii)  `valid` is a non-empty iterable of str, AND
        (iii) value == v for some v in valid (case-sensitive, exact match).

Design notes:
  - `in_range` deliberately rejects `bool` even though `bool` is a subclass
    of `int` in Python.  Without this guard, `True` would pass `0 <= True <= 5`
    (since `True == 1`).  Property 4's intent (validate numeric configuration
    such as Retry_Count) does not consider `True` / `False` valid Retry_Count
    inputs — they must be rejected at the type boundary so they cannot leak
    into DynamoDB writes as integers.
  - `in_range` also rejects `float`.  Retry_Count / Retry_Interval are
    declared as `Number` with `MinValue` / `MaxValue` integer constraints in
    CloudFormation Parameters (Phase 1.2), and DynamoDB stores them as
    Number-typed integers.  Accepting `1.0` here would mask configuration
    drift where a value was inadvertently serialised as a JSON float.
  - `in_range` returns False (never raises) for `low > high`.  This keeps
    the function total — callers that pass a misconfigured range get a
    deterministic False rather than a `ValueError`, which is the consistent
    "reject" answer for Property 4 inputs.  Per project principle 19(b),
    this is NOT a fallback masking an error: the contract for Property 4 is
    "reject everything that fails the predicate", and `low > high` is a
    failed predicate (no value can satisfy `low <= v <= high` when
    `low > high`).
  - `in_enum` is case-sensitive.  Requirement 4.10 (mode: `ALL` /
    `UNREACHABLE_ONLY`) and Requirement 17.4 (Voice_Status: `PENDING` /
    `SAFE` / `INJURED` / `UNAVAILABLE` / `OTHER`) treat these as exact
    string identifiers — they are stored and compared as-is in DynamoDB
    and CloudFormation Parameters.
  - Empty `valid` returns False (no value can match an empty set).  This
    is the symmetric counterpart of the "low > high" branch in `in_range`.

Allowed-value constants exposed for downstream callers (handlers /
validators) that want to spell the same set the PBT is using:

    RETRY_COUNT_LOW = 0
    RETRY_COUNT_HIGH = 5          # Requirement 4.6
    RETRY_INTERVAL_LOW = 1
    RETRY_INTERVAL_HIGH = 60      # Requirement 4.7 (minutes)
    ENV_VALUES = frozenset({"dev", "stg", "prod"})            # Req 17.2
    MODE_VALUES = frozenset({"ALL", "UNREACHABLE_ONLY"})       # Req 4.10
    VOICE_STATUS_VALUES = frozenset({                          # Req 17.4
        "PENDING", "SAFE", "INJURED", "UNAVAILABLE", "OTHER",
    })
"""

from __future__ import annotations

from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Allowed-value constants (DRY: re-used by handlers + tests).
# ---------------------------------------------------------------------------

#: Retry_Count inclusive range (Requirement 4.6).
RETRY_COUNT_LOW: int = 0
RETRY_COUNT_HIGH: int = 5

#: Retry_Interval inclusive range, in minutes (Requirement 4.7).
RETRY_INTERVAL_LOW: int = 1
RETRY_INTERVAL_HIGH: int = 60

#: Deployment environment identifiers (Requirement 17.2).
ENV_VALUES: frozenset[str] = frozenset({"dev", "stg", "prod"})

#: Cycle dispatch mode (Requirement 4.10).
MODE_VALUES: frozenset[str] = frozenset({"ALL", "UNREACHABLE_ONLY"})

#: Voice_Status enumeration (Requirement 17.4).
VOICE_STATUS_VALUES: frozenset[str] = frozenset(
    {"PENDING", "SAFE", "INJURED", "UNAVAILABLE", "OTHER"}
)


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def in_range(value: Any, low: int, high: int) -> bool:
    """Return True iff `value` is an int (not bool, not float) within
    the inclusive range `[low, high]`.

    Args:
        value: Candidate to validate.  Anything that is not strictly `int`
            (i.e. `bool`, `float`, `str`, `None`, `list`, `dict`, ...) is
            rejected.
        low: Inclusive lower bound.
        high: Inclusive upper bound.

    Returns:
        True iff all of the following hold:
          * `type(value) is int` (bool is rejected even though it is an
            int subclass),
          * `low <= high` (invalid range arg → False, never raises),
          * `low <= value <= high`.

        False in every other case.
    """
    # Guard 1: reject bool BEFORE the isinstance(int) check, because
    # `isinstance(True, int)` is True in Python.
    if isinstance(value, bool):
        return False

    # Guard 2: reject everything that is not strictly int.
    if type(value) is not int:
        return False

    # Guard 3: reject misconfigured ranges (low > high → no value satisfies).
    if low > high:
        return False

    # Inclusive bounds.
    return low <= value <= high


def in_enum(value: Any, valid: Iterable[str]) -> bool:
    """Return True iff `value` is a str exactly matching one of `valid`.

    Args:
        value: Candidate to validate.  Must be a `str` (not bytes, not int,
            not None, ...).
        valid: Iterable of permitted string values.  Empty iterables are
            valid input (the function returns False for any `value`).
            Case-sensitive: "DEV" does NOT match "dev".

    Returns:
        True iff `value` is a `str` and equals (`==`) some element of
        `valid`.  False in every other case, including:
          * `value` is not a str (None, int, bool, float, bytes, ...),
          * `valid` is empty,
          * `value` is not present in `valid` (case mismatch counts as
            "not present" because `==` is case-sensitive on str).
    """
    if not isinstance(value, str):
        return False

    # Convert to a tuple once so we can both iterate and detect emptiness
    # without consuming a generator twice.  `frozenset` is preferable for
    # O(1) lookup, but the caller may pass a list / set / frozenset / tuple
    # / generator; we accept all by materialising to tuple.
    materialised = tuple(valid)
    if not materialised:
        return False

    return value in materialised
