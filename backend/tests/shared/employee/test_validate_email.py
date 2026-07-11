"""Email validator PBT + example anchors (admin registration from SPA).

Validates:
    - Requirement 2.1 (revised) — admin registration from SPA UI: the
      ``adminEmail`` field, present only when ``isAdmin=true``, must
      match the RFC 5322 simplified shape before being sent to
      ``cognito-idp.admin_create_user``.
    - DRY: also underwrites ``auth_failure_reporter.lambda_handler``
      which now consumes the same ``is_valid_email`` (previously had
      an inline copy of the regex).

Contract (verbatim from shared/employee/validate.py):
    ``is_valid_email(email)`` returns True iff:
      (i)  ``email`` is an instance of ``str``, AND
      (ii) ``email`` matches ``^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$``.

Semantically that means:
    - Non-empty local part with no whitespace or ``@``.
    - Single ``@`` separator.
    - Non-empty domain (no whitespace or ``@``).
    - At least one ``.`` inside the domain.
    - Non-empty TLD (no whitespace or ``@``) after the last ``.``.

This test file enforces both directions:
  (a) every simplified-shape email is accepted    → True
  (b1) non-string values are rejected             → False
  (b2) strings without any ``@`` are rejected     → False
  (b3) strings without any ``.`` after ``@`` are  → False
  (b4) empty local / domain / TLD are rejected    → False
  (b5) leading / trailing / embedded whitespace   → False
  (b6) multiple ``@`` are rejected                → False
  (c)  representative shapes (unit anchors)
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.employee.validate import is_valid_email
from tests.strategies import non_string_value

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


# ---------------------------------------------------------------------------
# Strategy: printable ASCII bytes with whitespace and "@" removed.
# ---------------------------------------------------------------------------


# Characters allowed inside local / domain segments per our simplified regex:
# any codepoint in [0x21, 0x7E] except " ", "@", ".", '\t', '\n', '\r'.
# We further exclude "." explicitly for segment strategies so that the outer
# composite has full control over dot placement.
_SEGMENT_CHAR = st.characters(
    min_codepoint=0x21,
    max_codepoint=0x7E,
    blacklist_characters=" @.\t\n\r",
)

_segment = st.text(alphabet=_SEGMENT_CHAR, min_size=1, max_size=32)


@st.composite
def _simplified_email(draw: st.DrawFn) -> str:
    """Draw a string that matches the RFC 5322 simplified pattern.

    Shape: ``<local>@<domain>.<tld>`` where each of local / domain / tld
    is a non-empty string from the printable-ASCII alphabet excluding
    whitespace / ``@`` / ``.``.

    This deliberately over-restricts the alphabet vs the regex (which
    permits ``.`` inside local and domain) — the goal here is to
    generate obviously-valid emails, not to explore edge cases like
    ``a.b@c.d.e``. Those edges are covered by the example anchors below.
    """
    local = draw(_segment)
    domain = draw(_segment)
    tld = draw(_segment)
    return f"{local}@{domain}.{tld}"


# ---------------------------------------------------------------------------
# Strategy: arbitrary strings guaranteed NOT to match the simplified regex.
# We construct these by generating printable-ASCII strings with no "@",
# so the regex fails at the first ``@`` capture group.
# ---------------------------------------------------------------------------


_no_at_string = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E, blacklist_characters="@"),
    min_size=0,
    max_size=40,
)


# ---------------------------------------------------------------------------
# Property (a) — every simplified-shape email is accepted.
# ---------------------------------------------------------------------------


@example(s="a@b.c")  # smallest realistic email
@example(s="admin@example.com")  # realistic corporate admin
@example(s="user.name@example.co.jp")  # multi-dot domain (regex allows this)
@example(s="a+b@example.com")  # "+" tag (regex allows)
@example(s="a_b@example.com")  # underscore (regex allows)
@example(s="1234567890@example.com")  # digit-only local
@PBT_SETTINGS
@given(s=_simplified_email())
def test_email_true_for_simplified_shape(s: str) -> None:
    """is_valid_email(s) is True for every simplified-shape email.

    Validates: Requirement 2.1 (revised)
    """
    assert is_valid_email(s) is True, f"expected True for simplified email s={s!r}"


# ---------------------------------------------------------------------------
# Property (b1) — non-string values are rejected.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(v=non_string_value)
def test_email_false_for_non_string(v: object) -> None:
    """is_valid_email(v) is False for any non-str value.

    Validates: Requirement 2.1 (revised)
    """
    assert is_valid_email(v) is False, f"expected False for non-str v={v!r}"


# ---------------------------------------------------------------------------
# Property (b2) — strings without any "@" are rejected.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(s=_no_at_string)
def test_email_false_for_no_at(s: str) -> None:
    """is_valid_email(s) is False when the string contains no ``@``.

    Validates: Requirement 2.1 (revised)
    """
    assert is_valid_email(s) is False, f"expected False for no-@ string s={s!r}"


# ---------------------------------------------------------------------------
# Property (b3-b6) — explicit unit anchors for shape violations.
# ---------------------------------------------------------------------------


def test_email_false_for_empty_string() -> None:
    """Empty string is rejected."""
    assert is_valid_email("") is False


def test_email_false_for_missing_dot_in_domain() -> None:
    """``local@domain`` without a dot in the domain is rejected."""
    assert is_valid_email("abc@def") is False


def test_email_false_for_empty_local() -> None:
    """``@domain.tld`` with an empty local part is rejected."""
    assert is_valid_email("@example.com") is False


def test_email_false_for_empty_domain_before_dot() -> None:
    """``local@.tld`` with an empty domain segment before the dot is rejected."""
    assert is_valid_email("abc@.com") is False


def test_email_false_for_empty_tld_after_dot() -> None:
    """``local@domain.`` with an empty TLD after the dot is rejected."""
    assert is_valid_email("abc@def.") is False


def test_email_false_for_leading_whitespace() -> None:
    """A leading space disqualifies the string."""
    assert is_valid_email(" abc@example.com") is False


def test_email_false_for_trailing_whitespace() -> None:
    """A trailing space disqualifies the string."""
    assert is_valid_email("abc@example.com ") is False


def test_email_false_for_embedded_whitespace_in_local() -> None:
    """Whitespace inside the local part disqualifies the string."""
    assert is_valid_email("ab c@example.com") is False


def test_email_false_for_embedded_whitespace_in_domain() -> None:
    """Whitespace inside the domain disqualifies the string."""
    assert is_valid_email("abc@ex ample.com") is False


def test_email_false_for_multiple_at_signs() -> None:
    """Multiple ``@`` characters disqualify the string.

    The regex ``[^\\s@]+`` disallows ``@`` inside either segment, so any
    string with two or more ``@`` cannot fully match.
    """
    assert is_valid_email("a@b@example.com") is False


def test_email_false_for_at_only() -> None:
    """A bare ``@`` with no surrounding characters is rejected."""
    assert is_valid_email("@") is False


def test_email_false_for_embedded_tab() -> None:
    """Tab characters inside local / domain segments are rejected.

    Note on trailing newline (Python ``re.match`` quirk):
        The pattern is ``re.match(r"^...$", s)`` — Python's ``$`` by
        default matches the position immediately before a trailing
        ``\\n``. So ``"abc@example.com\\n"`` is (surprisingly) accepted
        by this simplified regex. This is the historical behaviour of
        the ``_EMAIL_RE`` inherited from ``auth_failure_reporter``;
        downstream Cognito validation catches such payloads. Tightening
        the regex (``\\A...\\Z``) is deferred to a separate task.
    """
    assert is_valid_email("abc\t@example.com") is False
    assert is_valid_email("abc@ex\tample.com") is False


# ---------------------------------------------------------------------------
# Property (c) — realistic example anchors on the True side.
# ---------------------------------------------------------------------------


def test_email_true_for_realistic_examples() -> None:
    """Representative realistic emails must all match."""
    for s in (
        "admin@example.com",
        "integration-test-admin@example.com",
        "tomita@g-wise.co.jp",
        "user.name+tag@sub.example.co.jp",
    ):
        assert is_valid_email(s) is True, f"expected True for s={s!r}"
