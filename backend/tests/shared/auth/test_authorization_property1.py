"""Property 1 — administrator role authorisation PBT (Phase 13.1).

Validates: Requirements 1.3, 1.4, 1.9

Contract (verbatim from shared/auth/authorization.py):
    is_authorized(claims, required_group) is True
    iff
      (i)   claims is a dict, AND
      (ii)  required_group is a non-empty str, AND
      (iii) claims["cognito:groups"] parses to a non-empty list of strings
            (either a list[str], or a whitespace-separated str), AND
      (iv)  that list contains `required_group` (case-sensitive, exact
            match after whitespace trimming).

This file enforces the 10 cases enumerated in the task:
  (a) True : claims["cognito:groups"] is `[required_group]` (list form).
  (b) True : claims["cognito:groups"] is `required_group` itself (single str).
  (c) True : claims["cognito:groups"] is `"X required_group Y"` (multi str).
  (d) True : list contains required_group plus other groups.
  (e) False: cognito:groups is empty list / empty str.
  (f) False: cognito:groups contains only groups other than required_group.
  (g) False: claims lacks the `cognito:groups` key.
  (h) False: claims is None / non-dict (int / list / str / bool / ...).
  (i) False: required_group is None / non-str / empty str.
  (j) False: case mismatch (e.g. "administrator" vs "Administrator").
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.auth.authorization import (
    ADMINISTRATOR_GROUP,
    COGNITO_GROUPS_CLAIM,
    is_authorized,
)
from tests.strategies import non_string_value

# Hypothesis settings: at least 100 runs per property (task requirement).
#
# `HealthCheck.large_base_example` is suppressed because composite strategies
# that constrain `candidate != required_group` via a rejection filter (e.g.
# `_other_group`) yield a relatively large base example.  This is purely a
# generator-overhead warning, not a correctness signal.
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.large_base_example,
    ],
)


# ---------------------------------------------------------------------------
# Local strategies — group-name-related, kept in-file because they are
# specific to authorisation tests (re-use across other Phase 13 PBT files is
# unlikely).
# ---------------------------------------------------------------------------

#: Alphabet for group names: ASCII letters, digits, and underscore.  Mirrors
#: Cognito's documented group-name character class (letters / digits / "_" /
#: "-" / "+" / "=" / "," / "." / "@") but restricted to a conservative subset
#: that avoids any whitespace ambiguity in the "X Admin Y" split-string form.
_GROUP_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)

#: Strategy generating valid Cognito group names (1..30 chars from the
#: alphabet above).  Length cap 30 keeps generated test data small.
group_name = st.text(alphabet=_GROUP_ALPHABET, min_size=1, max_size=30)


@st.composite
def _other_group(draw: st.DrawFn, target: str) -> str:
    """Draw a group name that differs from `target` (case-sensitive)."""
    while True:
        candidate = draw(group_name)
        if candidate != target:
            return candidate


@st.composite
def _list_of_others(draw: st.DrawFn, target: str) -> list[str]:
    """Draw a non-empty list of group names, none equal to `target`."""
    n = draw(st.integers(min_value=1, max_value=5))
    return [draw(_other_group(target)) for _ in range(n)]


@st.composite
def _claims_with_required_in_list(
    draw: st.DrawFn,
) -> tuple[dict, str]:
    """Draw (claims, required) where claims[cognito:groups] is a list[str]
    containing required (and possibly other groups, all distinct from required).
    """
    required = draw(group_name)
    others = draw(st.lists(_other_group(required), min_size=0, max_size=4))
    insert_at = draw(st.integers(min_value=0, max_value=len(others)))
    groups = list(others)
    groups.insert(insert_at, required)
    return ({COGNITO_GROUPS_CLAIM: groups}, required)


@st.composite
def _claims_with_required_as_single_str(
    draw: st.DrawFn,
) -> tuple[dict, str]:
    """Draw (claims, required) where claims[cognito:groups] is required itself."""
    required = draw(group_name)
    return ({COGNITO_GROUPS_CLAIM: required}, required)


@st.composite
def _claims_with_required_in_space_separated_str(
    draw: st.DrawFn,
) -> tuple[dict, str]:
    """Draw (claims, required) where claims[cognito:groups] is a whitespace-
    separated string containing required surrounded by zero or more other
    groups.  Also injects extra whitespace (leading / trailing / multi-space)
    to verify trimming.
    """
    required = draw(group_name)
    prefix = draw(st.lists(_other_group(required), min_size=0, max_size=3))
    suffix = draw(st.lists(_other_group(required), min_size=0, max_size=3))
    parts = prefix + [required] + suffix
    # Vary the separator between parts: 1..3 spaces, plus optional leading
    # and trailing whitespace.
    sep_lens = [draw(st.integers(min_value=1, max_value=3)) for _ in parts[:-1]]
    body = parts[0]
    for sep_len, part in zip(sep_lens, parts[1:]):
        body += " " * sep_len + part
    leading = " " * draw(st.integers(min_value=0, max_value=2))
    trailing = " " * draw(st.integers(min_value=0, max_value=2))
    return ({COGNITO_GROUPS_CLAIM: leading + body + trailing}, required)


@st.composite
def _claims_with_others_only_list(
    draw: st.DrawFn,
) -> tuple[dict, str]:
    """Draw (claims, required) where claims[cognito:groups] is a list of group
    names NONE of which equals required.
    """
    required = draw(group_name)
    others = draw(_list_of_others(required))
    return ({COGNITO_GROUPS_CLAIM: others}, required)


@st.composite
def _claims_with_others_only_str(
    draw: st.DrawFn,
) -> tuple[dict, str]:
    """Same as `_claims_with_others_only_list` but as a space-separated str."""
    required = draw(group_name)
    others = draw(_list_of_others(required))
    return ({COGNITO_GROUPS_CLAIM: " ".join(others)}, required)


@st.composite
def _claims_missing_groups_key(
    draw: st.DrawFn,
) -> tuple[dict, str]:
    """Draw (claims, required) where claims is a dict that lacks the
    `cognito:groups` key.  Other arbitrary keys may be present.
    """
    required = draw(group_name)
    # Keys that are NOT the groups claim.
    other_keys = draw(
        st.lists(
            st.text(min_size=1, max_size=10).filter(
                lambda s: s != COGNITO_GROUPS_CLAIM
            ),
            min_size=0,
            max_size=3,
            unique=True,
        )
    )
    claims = {k: draw(st.text(max_size=10)) for k in other_keys}
    return (claims, required)


# Cases swap a non-Cognito-shaped letter so they differ from target by case.
@st.composite
def _case_mismatched_pair(draw: st.DrawFn) -> tuple[dict, str]:
    """Draw (claims, required) where the group in claims differs from required
    by ASCII case only.  Both strings have at least one ASCII letter to
    guarantee a case flip is possible.
    """
    # Force at least one ASCII letter in `required` so the case flip is
    # guaranteed to produce a different string.
    required = draw(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
            min_size=1,
            max_size=20,
        )
    )
    # Build a flipped version: swap the case of every ASCII letter.  The
    # resulting string is guaranteed to differ from `required` in at least
    # one character, since `required` is purely ASCII letters.
    flipped = required.swapcase()
    # Sanity check (would only fire on Hypothesis drift):
    assert flipped != required, (
        f"swapcase produced identical string for {required!r}"
    )
    use_list = draw(st.booleans())
    if use_list:
        return ({COGNITO_GROUPS_CLAIM: [flipped]}, required)
    return ({COGNITO_GROUPS_CLAIM: flipped}, required)


# ---------------------------------------------------------------------------
# Property 1 (a) — True: list form containing required_group.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=({COGNITO_GROUPS_CLAIM: [ADMINISTRATOR_GROUP]}, ADMINISTRATOR_GROUP))
@given(case=_claims_with_required_in_list())
def test_property1_true_when_list_contains_required(
    case: tuple[dict, str],
) -> None:
    """List form containing required → True.

    Validates: Requirements 1.3, 1.9
    """
    claims, required = case
    assert is_authorized(claims, required) is True, (
        f"expected True for list-containing-required claims={claims!r} "
        f"required={required!r}"
    )


# ---------------------------------------------------------------------------
# Property 1 (b) — True: single-string claim equals required_group.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=({COGNITO_GROUPS_CLAIM: ADMINISTRATOR_GROUP}, ADMINISTRATOR_GROUP))
@given(case=_claims_with_required_as_single_str())
def test_property1_true_when_single_string_equals_required(
    case: tuple[dict, str],
) -> None:
    """`cognito:groups == required_group` (single str) → True.

    Validates: Requirements 1.3, 1.9
    """
    claims, required = case
    assert is_authorized(claims, required) is True, (
        f"expected True for single-string claims={claims!r} "
        f"required={required!r}"
    )


# ---------------------------------------------------------------------------
# Property 1 (c) — True: whitespace-separated string includes required_group.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(
    case=(
        {COGNITO_GROUPS_CLAIM: f"X {ADMINISTRATOR_GROUP} Y"},
        ADMINISTRATOR_GROUP,
    )
)
@example(
    case=(
        {COGNITO_GROUPS_CLAIM: f"  {ADMINISTRATOR_GROUP}  "},
        ADMINISTRATOR_GROUP,
    )
)
@given(case=_claims_with_required_in_space_separated_str())
def test_property1_true_when_space_separated_string_includes_required(
    case: tuple[dict, str],
) -> None:
    """Whitespace-separated `cognito:groups` containing required → True.

    Validates: Requirements 1.3, 1.9
    """
    claims, required = case
    assert is_authorized(claims, required) is True, (
        f"expected True for space-separated claims={claims!r} "
        f"required={required!r}"
    )


# ---------------------------------------------------------------------------
# Property 1 (d) — True: list contains required plus other groups.
#
# Covered by (a): `_claims_with_required_in_list` generates 0..4 OTHER groups
# plus required (insert at random index), so this is the same property.
# Add a dedicated unit anchor to pin the behaviour.
# ---------------------------------------------------------------------------


def test_unit_list_with_required_among_others_is_true() -> None:
    """List with required + multiple others → True (anchor for case d)."""
    claims = {COGNITO_GROUPS_CLAIM: ["OtherA", ADMINISTRATOR_GROUP, "OtherB"]}
    assert is_authorized(claims, ADMINISTRATOR_GROUP) is True


# ---------------------------------------------------------------------------
# Property 1 (e) — False: cognito:groups is empty list / empty str.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(required=group_name)
def test_property1_false_when_groups_is_empty_list(required: str) -> None:
    """Empty list `[]` for `cognito:groups` → False.

    Validates: Requirement 1.4
    """
    claims = {COGNITO_GROUPS_CLAIM: []}
    assert is_authorized(claims, required) is False


@PBT_SETTINGS
@given(required=group_name)
def test_property1_false_when_groups_is_empty_str(required: str) -> None:
    """Empty string `""` for `cognito:groups` → False.

    Validates: Requirement 1.4
    """
    claims = {COGNITO_GROUPS_CLAIM: ""}
    assert is_authorized(claims, required) is False


@PBT_SETTINGS
@given(required=group_name, n_spaces=st.integers(min_value=1, max_value=10))
def test_property1_false_when_groups_is_whitespace_only_str(
    required: str, n_spaces: int
) -> None:
    """Whitespace-only string for `cognito:groups` → False (splits to []).

    Validates: Requirement 1.4
    """
    claims = {COGNITO_GROUPS_CLAIM: " " * n_spaces}
    assert is_authorized(claims, required) is False


# ---------------------------------------------------------------------------
# Property 1 (f) — False: cognito:groups contains only other groups.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(case=_claims_with_others_only_list())
def test_property1_false_when_list_lacks_required(
    case: tuple[dict, str],
) -> None:
    """List form lacking required → False.

    Validates: Requirement 1.4
    """
    claims, required = case
    assert is_authorized(claims, required) is False, (
        f"expected False for others-only list claims={claims!r} "
        f"required={required!r}"
    )


@PBT_SETTINGS
@given(case=_claims_with_others_only_str())
def test_property1_false_when_str_lacks_required(
    case: tuple[dict, str],
) -> None:
    """Whitespace-separated string lacking required → False.

    Validates: Requirement 1.4
    """
    claims, required = case
    assert is_authorized(claims, required) is False, (
        f"expected False for others-only string claims={claims!r} "
        f"required={required!r}"
    )


# ---------------------------------------------------------------------------
# Property 1 (g) — False: claims lacks the `cognito:groups` key.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=({}, ADMINISTRATOR_GROUP))
@given(case=_claims_missing_groups_key())
def test_property1_false_when_groups_key_missing(case: tuple[dict, str]) -> None:
    """`cognito:groups` key absent from claims dict → False.

    Validates: Requirement 1.4
    """
    claims, required = case
    assert COGNITO_GROUPS_CLAIM not in claims  # generator invariant
    assert is_authorized(claims, required) is False


# ---------------------------------------------------------------------------
# Property 1 (h) — False: claims is None / non-dict.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(bogus=non_string_value, required=group_name)
def test_property1_false_when_claims_is_non_dict(bogus: object, required: str) -> None:
    """Any non-dict `claims` (None / int / list / str / bool / ...) → False.

    Validates: Requirement 1.4
    """
    # Skip the rare draw where `non_string_value` happens to yield a dict
    # subclass — there are no such draws in `non_string_value` today, but
    # be explicit to harden against future strategy edits.
    if isinstance(bogus, dict):
        return
    assert is_authorized(bogus, required) is False


def test_unit_string_claims_is_false() -> None:
    """str-typed claims → False (anchor for case h)."""
    assert is_authorized("Administrator", "Administrator") is False


def test_unit_none_claims_is_false() -> None:
    """None claims → False (anchor for case h)."""
    assert is_authorized(None, "Administrator") is False


# ---------------------------------------------------------------------------
# Property 1 (i) — False: required_group is None / non-str / empty.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(bogus=non_string_value)
def test_property1_false_when_required_is_non_str(bogus: object) -> None:
    """Non-str `required_group` → False.

    Validates: Requirement 1.4
    """
    if isinstance(bogus, str):
        return  # non_string_value doesn't emit str today but guard anyway.
    claims = {COGNITO_GROUPS_CLAIM: [ADMINISTRATOR_GROUP]}
    assert is_authorized(claims, bogus) is False


def test_unit_empty_required_is_false() -> None:
    """Empty-string `required_group` → False (anchor for case i)."""
    claims = {COGNITO_GROUPS_CLAIM: [ADMINISTRATOR_GROUP, ""]}
    # Even though "" is in the parsed list, required="" is rejected upfront.
    assert is_authorized(claims, "") is False


def test_unit_none_required_is_false() -> None:
    """None `required_group` → False (anchor for case i)."""
    claims = {COGNITO_GROUPS_CLAIM: [ADMINISTRATOR_GROUP]}
    assert is_authorized(claims, None) is False


# ---------------------------------------------------------------------------
# Property 1 (j) — False: case mismatch (case-sensitive comparison).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(
    case=({COGNITO_GROUPS_CLAIM: ["administrator"]}, "Administrator"),
)
@example(
    case=({COGNITO_GROUPS_CLAIM: "ADMINISTRATOR"}, "Administrator"),
)
@given(case=_case_mismatched_pair())
def test_property1_false_on_case_mismatch(case: tuple[dict, str]) -> None:
    """Case-mismatched group name → False (Cognito is case-sensitive).

    Validates: Requirement 1.4
    """
    claims, required = case
    assert is_authorized(claims, required) is False, (
        f"expected False for case-mismatch claims={claims!r} "
        f"required={required!r}"
    )


# ---------------------------------------------------------------------------
# Additional defensive / cross-case anchors.
# ---------------------------------------------------------------------------


def test_unit_groups_key_value_is_int_is_false() -> None:
    """`cognito:groups` value of unsupported type (int) → False."""
    claims = {COGNITO_GROUPS_CLAIM: 42}
    assert is_authorized(claims, "Administrator") is False


def test_unit_groups_key_value_is_dict_is_false() -> None:
    """`cognito:groups` value of unsupported type (dict) → False."""
    claims = {COGNITO_GROUPS_CLAIM: {"Administrator": True}}
    assert is_authorized(claims, "Administrator") is False


def test_unit_groups_key_value_is_none_is_false() -> None:
    """`cognito:groups` value is None → False."""
    claims = {COGNITO_GROUPS_CLAIM: None}
    assert is_authorized(claims, "Administrator") is False


def test_unit_list_with_non_str_members_filtered() -> None:
    """List with mixed types: non-str members are filtered out; if required
    is among the remaining str members → True; otherwise → False.
    """
    # Required is present as a str alongside non-str junk → True.
    claims_pos = {COGNITO_GROUPS_CLAIM: [42, ADMINISTRATOR_GROUP, None, "Other"]}
    assert is_authorized(claims_pos, ADMINISTRATOR_GROUP) is True
    # Required is NOT present; only non-str junk and "Other" exist → False.
    claims_neg = {COGNITO_GROUPS_CLAIM: [42, None, "Other"]}
    assert is_authorized(claims_neg, ADMINISTRATOR_GROUP) is False


def test_unit_realistic_apigw_cognito_authorizer_event_shape() -> None:
    """Anchor: shape produced by API Gateway Cognito Authorizer (list[str])."""
    claims = {
        "sub": "abc-123",
        "email": "admin@example.com",
        COGNITO_GROUPS_CLAIM: [ADMINISTRATOR_GROUP],
        "iss": "https://cognito-idp.ap-northeast-1.amazonaws.com/pool",
    }
    assert is_authorized(claims, ADMINISTRATOR_GROUP) is True


def test_unit_realistic_jwt_payload_shape() -> None:
    """Anchor: shape produced by raw JWT payload (space-separated str)."""
    claims = {
        "sub": "abc-123",
        "email": "admin@example.com",
        COGNITO_GROUPS_CLAIM: f"Other {ADMINISTRATOR_GROUP}",
    }
    assert is_authorized(claims, ADMINISTRATOR_GROUP) is True
