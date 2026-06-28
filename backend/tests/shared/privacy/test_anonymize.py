"""Property-based & example tests for ``anonymize_employee_id`` (Task 15.12).

Validates: Requirements 15.5 / NFR3 / Property 20 extension (irreversible
anonymization of past Cycle Response rows on personal request).

Named properties (PBT main body):

    P1 (determinism / idempotency):
        For all (employee_id, salt) with both non-empty:
            anonymize(emp, salt) == anonymize(emp, salt)
        Same inputs yield the same output across repeated calls.

    P2 (collision-resistance under same salt):
        For all (emp_a, emp_b, salt) with emp_a != emp_b:
            anonymize(emp_a, salt) != anonymize(emp_b, salt)
        Different employee IDs under the same salt yield different
        anonymized IDs (within the 128-bit truncation; SHA-256 makes
        collisions astronomically unlikely at any human-plausible
        employee population size).

    P3 (salt-sensitivity):
        For all (employee_id, salt_a, salt_b) with salt_a != salt_b:
            anonymize(emp, salt_a) != anonymize(emp, salt_b)
        Different salts under the same employee_id yield different
        anonymized IDs.

    P4 (output shape):
        For all (employee_id, salt) with both non-empty:
            output.startswith("ANON_") AND len(output) == 5 + 32 = 37
            AND all chars after "ANON_" are lowercase hex digits.

    P5 (non-empty positive — symmetry pin, 第17原則 対称性推論):
        For non-empty (employee_id, salt) the function returns; the
        negative direction (empty input -> ValueError) is exercised in
        the example-test block below. P5 explicitly asserts the
        positive direction never raises so the negative direction's
        contract is meaningful.

Example tests (in the same file but outside PBT — TypeError / ValueError
boundaries that PBT cannot cleanly generate):

    * Empty employee_id -> ValueError.
    * Empty salt -> ValueError.
    * Non-str employee_id (int / None / bytes / list / dict) -> TypeError.
    * Non-str salt (same shapes) -> TypeError.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shared.privacy import ANONYMIZED_ID_PREFIX, anonymize_employee_id

# Hypothesis settings: the function is pure & cheap. 200 examples gives
# a healthy margin for shrinkage without slowing the suite. ``deadline``
# is disabled because Hypothesis sometimes triggers ``Flaky`` reports on
# the very first call due to JIT warm-up on this machine.
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

#: Output regex: "ANON_" followed by exactly 32 lowercase hex chars.
_OUTPUT_RE = re.compile(rf"^{re.escape(ANONYMIZED_ID_PREFIX)}[0-9a-f]{{32}}$")

#: Strategy: non-empty printable ASCII string up to 64 chars. The
#: production employee_id is a UUID v4 (36 chars), but the function's
#: contract permits any non-empty str so the strategy exercises wider
#: ground.
_non_empty_str: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=64,
)

# ---------------------------------------------------------------------------
# P1 — determinism / idempotency.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(employee_id=_non_empty_str, salt=_non_empty_str)
def test_p1_determinism(employee_id: str, salt: str) -> None:
    """Same inputs always yield the same output.

    Validates: Requirements 15.5 / Property 20 extension.
    """
    first = anonymize_employee_id(employee_id, salt)
    second = anonymize_employee_id(employee_id, salt)
    third = anonymize_employee_id(employee_id, salt)
    assert first == second == third


# ---------------------------------------------------------------------------
# P2 — collision-resistance under same salt.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(emp_a=_non_empty_str, emp_b=_non_empty_str, salt=_non_empty_str)
def test_p2_collision_resistance_same_salt(
    emp_a: str, emp_b: str, salt: str
) -> None:
    """Different employee IDs under same salt -> different outputs.

    SHA-256 truncated to 128 bits gives a birthday-collision threshold
    of ~2**64 inputs; finding a collision in a Hypothesis run is
    astronomically unlikely.

    Validates: Requirements 15.5 / Property 20 extension.
    """
    if emp_a == emp_b:
        # Same input is covered by P1; skip the trivial case.
        return
    assert anonymize_employee_id(emp_a, salt) != anonymize_employee_id(emp_b, salt)


# ---------------------------------------------------------------------------
# P3 — salt-sensitivity.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(employee_id=_non_empty_str, salt_a=_non_empty_str, salt_b=_non_empty_str)
def test_p3_salt_sensitivity(employee_id: str, salt_a: str, salt_b: str) -> None:
    """Different salts under same employee_id -> different outputs.

    Validates: Requirements 15.5 / Property 20 extension. Salt rotation
    must therefore be treated as an irreversible system-wide event
    (see anonymize.py module docstring).
    """
    if salt_a == salt_b:
        return
    assert (
        anonymize_employee_id(employee_id, salt_a)
        != anonymize_employee_id(employee_id, salt_b)
    )


# ---------------------------------------------------------------------------
# P4 — output shape.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(employee_id=_non_empty_str, salt=_non_empty_str)
def test_p4_output_shape(employee_id: str, salt: str) -> None:
    """Output is "ANON_" + 32 lowercase hex chars (total length 37).

    Validates: Requirements 15.5. The prefix is the public contract for
    "this row has been anonymized" — downstream code must be able to
    detect it without side-channel queries.
    """
    out = anonymize_employee_id(employee_id, salt)
    assert out.startswith(ANONYMIZED_ID_PREFIX), out
    assert len(out) == len(ANONYMIZED_ID_PREFIX) + 32, out
    assert _OUTPUT_RE.fullmatch(out) is not None, out


# ---------------------------------------------------------------------------
# P5 — positive symmetry pin (第17原則 対称性推論).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(employee_id=_non_empty_str, salt=_non_empty_str)
def test_p5_non_empty_positive_never_raises(employee_id: str, salt: str) -> None:
    """Non-empty (str, str) input never raises.

    Anchors the negative direction (example-tests below): they assert
    ValueError on empty / TypeError on non-str. P5 explicitly asserts
    that the positive direction is exception-free so the negative-side
    examples carry real signal.

    Validates: Requirements 15.5 (positive-side completeness).
    """
    # If anonymize_employee_id raises here, the test fails.
    out = anonymize_employee_id(employee_id, salt)
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# Example tests — TypeError / ValueError boundaries.
# ---------------------------------------------------------------------------


def test_empty_employee_id_raises_value_error() -> None:
    with pytest.raises(ValueError, match="employee_id"):
        anonymize_employee_id("", "non-empty-salt")


def test_empty_salt_raises_value_error() -> None:
    with pytest.raises(ValueError, match="salt"):
        anonymize_employee_id("emp-001", "")


@pytest.mark.parametrize(
    "bad_employee_id",
    [
        None,
        0,
        123,
        1.5,
        True,  # bool is int subclass but not str; isinstance(_, str) False
        b"bytes-employee-id",
        ["emp-001"],
        {"id": "emp-001"},
        ("emp-001",),
    ],
)
def test_non_str_employee_id_raises_type_error(bad_employee_id: Any) -> None:
    with pytest.raises(TypeError, match="employee_id must be str"):
        anonymize_employee_id(bad_employee_id, "non-empty-salt")


@pytest.mark.parametrize(
    "bad_salt",
    [
        None,
        0,
        123,
        1.5,
        True,
        b"bytes-salt",
        ["salt"],
        {"salt": "v"},
        ("salt",),
    ],
)
def test_non_str_salt_raises_type_error(bad_salt: Any) -> None:
    with pytest.raises(TypeError, match="salt must be str"):
        anonymize_employee_id("emp-001", bad_salt)


# ---------------------------------------------------------------------------
# Stability pin — a hand-computed digest catches accidental format changes.
# ---------------------------------------------------------------------------


def test_known_value_pin() -> None:
    """A stable known-input -> known-output pin.

    Computed independently of the implementation:
        sha256("the-system-salt:emp-001") =
            21fd923a48d4054ef9e4f43d22e7c08e... (first 32 chars)

    If the digest payload assembly changes (separator removal, encoding
    swap, prefix change, truncation length change), this test fails
    visibly rather than silently breaking previously-anonymized rows.
    """
    import hashlib

    expected_hash = hashlib.sha256(b"the-system-salt:emp-001").hexdigest()[:32]
    expected = f"{ANONYMIZED_ID_PREFIX}{expected_hash}"
    assert (
        anonymize_employee_id("emp-001", "the-system-salt") == expected
    ), "format drift — previously-anonymized rows would be orphaned"
