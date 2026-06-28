"""Property 22 — phone-number masking PBT (Phase 13.22).

Validates Requirements 16.4
Validates: Requirements 16.4 (audit log phone-number masking)

Contract (verbatim from shared/audit/mask.py docstring):
    For all E.164-shaped s = "+" + d_1 d_2 ... d_n (n >= 1):
    (a) output starts with "+"
    (b) for n >= 4, the last 4 chars match s[-4:]
    (c) middle chars (between "+" and the last 4 digits) are "*"
    (d) output length == input length
    (e) digits of the original (except the last 4 when n >= 4) are absent
        from the output.

Boundary behaviour (also from the source):
    - E.164 with body length <= 4: returned unchanged (no info-leak room
      to mask; preserved as-is).
    - Non-E.164 (no leading "+"): best-effort: "*" * (len-4) + s[-4:]
      when len > 4, otherwise unchanged.
    - Empty string: returned unchanged.

This test file enforces all of the above with Hypothesis (min 100 cases
per property via settings) and a small set of explicit boundary examples.
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.audit.mask import mask_phone
from tests.strategies import (
    e164_phone_maskable,
    e164_phone_short,
    non_e164_string,
)

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# Property 22 — core: E.164 with body length >= 5 (i.e. mask actually fires)
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(s=e164_phone_maskable)
def test_property22_core_all_five_conditions(s: str) -> None:
    """All five Property-22 conditions hold for maskable E.164 inputs.

    Validates: Requirements 16.4
    """
    out = mask_phone(s)

    # (a) output starts with "+"
    assert out.startswith("+"), f"(a) failed: out={out!r} for s={s!r}"

    # (b) last 4 chars are preserved
    assert out[-4:] == s[-4:], f"(b) failed: out={out!r} for s={s!r}"

    # (c) middle chars (between "+" and last 4) are all "*"
    middle = out[1:-4]
    assert all(c == "*" for c in middle), (
        f"(c) failed: middle={middle!r} contains non-* for s={s!r}"
    )

    # (d) length preserved
    assert len(out) == len(s), (
        f"(d) failed: len(out)={len(out)} != len(s)={len(s)} for s={s!r}"
    )

    # (e) digits of the original (except the trailing 4) are absent from out
    body = s[1:]
    hidden_digits = body[:-4]  # all but the last 4
    for d in hidden_digits:
        # Every hidden digit position in `out` (positions 1..-5) must be '*'.
        # Equivalently: the masked region must not echo any hidden digit.
        # Concrete check: no digit char appears anywhere in the middle slice.
        assert d not in out[1:-4], (
            f"(e) failed: hidden digit {d!r} leaked into out={out!r} "
            f"for s={s!r}"
        )


# Concrete examples covering boundary lengths inside the "maskable" range.
@example(s="+12345")  # body length 5 — smallest masking case
@example(s="+99999")  # body length 5 — all same digit (stress on (e))
@example(s="+" + "9" * 15)  # max E.164 length (16 chars total)
@example(s="+81901234567")  # realistic Japanese mobile
@PBT_SETTINGS
@given(s=e164_phone_maskable)
def test_property22_core_with_examples(s: str) -> None:
    """Same as the core property but with @example anchors for visibility.

    Validates: Requirements 16.4
    """
    out = mask_phone(s)
    assert out.startswith("+")
    assert out[-4:] == s[-4:]
    assert all(c == "*" for c in out[1:-4])
    assert len(out) == len(s)


# ---------------------------------------------------------------------------
# Boundary 1 — E.164 with body length 1..4: returned unchanged by contract.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(s=e164_phone_short)
def test_property22_short_e164_returned_unchanged(s: str) -> None:
    """Short E.164 (body <= 4) is returned unchanged.

    The source docstring states "Too short to mask without leaking
    everything; keep as-is."

    Validates: Requirements 16.4
    """
    out = mask_phone(s)
    assert out == s, f"short E.164 must be unchanged: in={s!r} out={out!r}"
    # Length still preserved (trivially) and still starts with "+".
    assert out.startswith("+")
    assert len(out) == len(s)


# ---------------------------------------------------------------------------
# Boundary 2 — non-E.164 best-effort masking.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(s=non_e164_string)
def test_property22_non_e164_best_effort(s: str) -> None:
    """Non-E.164 input: len<=4 unchanged, otherwise "*"*(n-4)+s[-4:].

    Validates: Requirements 16.4
    """
    out = mask_phone(s)

    # Length is always preserved.
    assert len(out) == len(s)

    if not s:
        assert out == s
        return

    if len(s) <= 4:
        assert out == s, f"non-E.164 short must be unchanged: in={s!r} out={out!r}"
        return

    # len(s) > 4 branch: all-but-last-4 are "*", last 4 preserved.
    assert out[-4:] == s[-4:]
    assert all(c == "*" for c in out[:-4]), (
        f"non-E.164 prefix must be all *: out={out!r} for s={s!r}"
    )


# ---------------------------------------------------------------------------
# Explicit unit examples (anchored boundary cases).
# ---------------------------------------------------------------------------


def test_empty_string_returned_as_is() -> None:
    """Empty string is returned unchanged."""
    assert mask_phone("") == ""


def test_plus_only_returned_unchanged() -> None:
    """Single '+' (E.164 body length 0) is unchanged (n<=4 branch)."""
    assert mask_phone("+") == "+"


def test_e164_body_1() -> None:
    """+1 (body length 1) is unchanged."""
    assert mask_phone("+1") == "+1"


def test_e164_body_3() -> None:
    """+123 (body length 3) is unchanged."""
    assert mask_phone("+123") == "+123"


def test_e164_body_4_boundary_unchanged() -> None:
    """+1234 (body length 4) is unchanged — boundary of the short branch."""
    assert mask_phone("+1234") == "+1234"


def test_e164_body_5_first_masked() -> None:
    """+12345 (body length 5) becomes +*2345 — boundary where mask fires."""
    assert mask_phone("+12345") == "+*2345"


def test_e164_max_length() -> None:
    """Max E.164 (+ + 15 digits): 11 middle chars become '*', tail preserved."""
    s = "+" + "1" * 11 + "2345"
    expected = "+" + "*" * 11 + "2345"
    assert mask_phone(s) == expected
    assert len(mask_phone(s)) == len(s)


def test_non_e164_short_unchanged() -> None:
    """Non-E.164, len<=4: unchanged."""
    assert mask_phone("abc") == "abc"
    assert mask_phone("1234") == "1234"


def test_non_e164_long_masked() -> None:
    """Non-E.164, len>4: all-but-last-4 become '*'."""
    assert mask_phone("hello1234") == "*****1234"


# ---------------------------------------------------------------------------
# Cross-property invariant: output length == input length, for ANY string.
# (A strict form of condition (d) lifted to the whole input domain.)
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(s=st.text(min_size=0, max_size=30))
def test_property22_length_invariant_holds_universally(s: str) -> None:
    """Condition (d) holds for *every* input, not just E.164.

    Validates: Requirements 16.4
    """
    assert len(mask_phone(s)) == len(s)
