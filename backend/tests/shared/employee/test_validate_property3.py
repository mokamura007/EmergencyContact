"""Property 3 — E.164 phone-number validator PBT (Phase 13.3).

Validates: Requirements 2.7 (社員マスタ画面の電話番号バリデーション)
Validates: Requirements 3.4 (CSV インポートの電話番号形式チェック)

Contract (verbatim from shared/employee/validate.py):
    A leading "+" immediately followed by 1 to 15 decimal digits.
    No country-code semantics; pure syntactic check.

    is_valid_e164(phone) returns True iff:
      (i)  phone is an instance of `str`, AND
      (ii) phone matches the regex `^\\+\\d{1,15}$`.

This test file enforces both directions:
  (a) every E.164 string is accepted          → True
  (b1) non-string values are rejected         → False
  (b2) strings not starting with "+" rejected → False
  (b3) strings with body length > 15 rejected → False
  (b4) "+" followed by any non-digit rejected → False
  (b5) "" and "+" rejected (unit anchors)     → False
  (c)  body length 1 and 15 are boundary True (unit + @example anchors)

A small tagged-on section also exercises `is_valid_name` for parity, kept
intentionally minimal so the file remains focused on Property 3.
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.employee.validate import MAX_NAME_LENGTH, is_valid_e164, is_valid_name
from tests.strategies import (
    e164_phone,
    non_e164_string,
    non_string_value,
    plus_non_digit_body,
    plus_too_many_digits,
)

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


# ---------------------------------------------------------------------------
# Property 3 (a) — true case: every E.164 string is accepted.
# ---------------------------------------------------------------------------


@example(s="+0")  # body length 1 — smallest valid E.164
@example(s="+9")  # body length 1 — other extreme of single-digit body
@example(s="+" + "1" * 15)  # body length 15 — maximum valid E.164
@example(s="+" + "9" * 15)  # body length 15 — alt max
@example(s="+81901234567")  # realistic Japanese mobile
@PBT_SETTINGS
@given(s=e164_phone)
def test_property3_true_for_valid_e164(s: str) -> None:
    """is_valid_e164(s) is True for every "+" + 1..15 digits string.

    Validates: Requirements 2.7
    """
    assert is_valid_e164(s) is True, f"expected True for valid E.164 s={s!r}"


# ---------------------------------------------------------------------------
# Property 3 (b1) — non-string values are rejected.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(v=non_string_value)
def test_property3_false_for_non_string(v: object) -> None:
    """is_valid_e164(v) is False for any non-str value.

    Validates: Requirements 2.7
    """
    assert is_valid_e164(v) is False, f"expected False for non-str v={v!r}"


# ---------------------------------------------------------------------------
# Property 3 (b2) — strings not starting with "+" are rejected.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(s=non_e164_string)
def test_property3_false_for_no_leading_plus(s: str) -> None:
    """is_valid_e164(s) is False for any string not starting with "+".

    Validates: Requirements 2.7, 3.4
    """
    assert is_valid_e164(s) is False, (
        f"expected False for string without leading '+': s={s!r}"
    )


# ---------------------------------------------------------------------------
# Property 3 (b3) — body length > 15 is rejected (digits-only beyond max).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(s=plus_too_many_digits)
def test_property3_false_for_too_many_digits(s: str) -> None:
    """is_valid_e164(s) is False when body length exceeds 15 digits.

    Validates: Requirements 2.7
    """
    assert is_valid_e164(s) is False, (
        f"expected False for over-length E.164 body: s={s!r} (len body={len(s) - 1})"
    )


# ---------------------------------------------------------------------------
# Property 3 (b4) — "+" followed by any non-digit ASCII character is rejected.
# ---------------------------------------------------------------------------


@example(s="+abc")
@example(s="+ 123")
@example(s="+1-2-3")
@example(s="+12.34")
@example(s="+1 2 3")
@example(s="+a")  # single non-digit after "+"
@PBT_SETTINGS
@given(s=plus_non_digit_body)
def test_property3_false_for_non_digit_after_plus(s: str) -> None:
    """is_valid_e164(s) is False when "+" is followed by any non-digit.

    Validates: Requirements 2.7, 3.4
    """
    assert is_valid_e164(s) is False, (
        f"expected False for non-digit char in body: s={s!r}"
    )


# ---------------------------------------------------------------------------
# Property 3 (b5) — explicit unit anchors for empty / "+" only.
# ---------------------------------------------------------------------------


def test_property3_false_for_empty_string() -> None:
    """Empty string is rejected (no "+", no body)."""
    assert is_valid_e164("") is False


def test_property3_false_for_plus_only() -> None:
    """A bare "+" with no body is rejected (body length 0)."""
    assert is_valid_e164("+") is False


def test_property3_false_for_double_plus() -> None:
    """A leading "++" violates the single-"+" prefix rule."""
    assert is_valid_e164("++81901234567") is False


def test_property3_false_for_plus_with_leading_space() -> None:
    """A leading space disqualifies the string from being E.164."""
    assert is_valid_e164(" +81901234567") is False


# ---------------------------------------------------------------------------
# Property 3 (c) — explicit boundary anchors on the True side.
# ---------------------------------------------------------------------------


def test_property3_true_boundary_body_length_1() -> None:
    """Body length 1 ("+0") is the smallest valid E.164."""
    assert is_valid_e164("+0") is True


def test_property3_true_boundary_body_length_15() -> None:
    """Body length 15 is the maximum valid E.164."""
    assert is_valid_e164("+" + "1" * 15) is True


def test_property3_false_boundary_body_length_16() -> None:
    """Body length 16 is one over the maximum — must be rejected."""
    assert is_valid_e164("+" + "1" * 16) is False


# ---------------------------------------------------------------------------
# Optional tag-on — is_valid_name minimal PBT (Requirement 2.1: 氏名必須 +
# 1〜128 文字).  Kept narrow so Property 3 remains the focus of the file.
# Note: validate.py uses MAX_NAME_LENGTH = 100 (stricter than the 128
# documented in the requirement); we test against the code-of-truth value.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(v=non_string_value)
def test_is_valid_name_false_for_non_string(v: object) -> None:
    """is_valid_name(v) is False for any non-str value."""
    assert is_valid_name(v) is False, f"expected False for non-str v={v!r}"


@PBT_SETTINGS
@given(
    s=st.text(
        alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
        min_size=1,
        max_size=MAX_NAME_LENGTH,
    )
)
def test_is_valid_name_true_for_in_range_non_whitespace_strings(s: str) -> None:
    """Non-empty, non-whitespace strings within [1, MAX_NAME_LENGTH] are valid.

    Note: validate.py strips whitespace before length check.  We restrict the
    alphabet to printable non-space ASCII so that `strip()` is a no-op and
    1..MAX_NAME_LENGTH chars always remain.
    """
    assert is_valid_name(s) is True, f"expected True for in-range name s={s!r}"


@PBT_SETTINGS
@given(
    s=st.text(
        alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
        min_size=MAX_NAME_LENGTH + 1,
        max_size=MAX_NAME_LENGTH + 50,
    )
)
def test_is_valid_name_false_for_over_max_length(s: str) -> None:
    """Strings strictly longer than MAX_NAME_LENGTH (after strip) are rejected."""
    assert is_valid_name(s) is False, f"expected False for over-max name s={s!r}"


def test_is_valid_name_false_for_empty_string() -> None:
    """Empty string is rejected."""
    assert is_valid_name("") is False


def test_is_valid_name_false_for_whitespace_only() -> None:
    """All-whitespace string is rejected (strip-then-length == 0)."""
    assert is_valid_name("   ") is False
