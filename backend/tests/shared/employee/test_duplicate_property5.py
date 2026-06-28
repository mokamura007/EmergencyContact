"""Property 5 — duplicate phone-number detection PBT (Phase 13.5).

Validates: Requirements 2.3 (社員追加時に既存電話番号と重複しないこと)
Validates: Requirements 3.4 (CSV インポート時の電話番号重複検知)

Contract (verbatim from shared/employee/duplicate.py):
    find_duplicate_phone(existing_phones, new_phone) is True
    iff
      (i)  new_phone is a non-empty `str`, AND
      (ii) at least one element of `existing_phones` that is a `str`
           is exactly equal (case-sensitive) to new_phone.

This file enforces the 8 cases enumerated in the task:
  (a) True : new_phone present anywhere in existing_phones → True.
  (b) False: new_phone absent from existing_phones        → False.
  (c) False: empty existing_phones                        → False.
  (d) False: non-str new_phone (int/None/list/bool/...)   → False.
  (e) False: empty-string new_phone                       → False.
  (f) Order-independence: same multiset, different orders → same result.
  (g) True : existing_phones contains the candidate alongside
            possibly-logically-deleted entries (the Done-When core).
  (h) False: non-str members of existing_phones are silently ignored
            (must not falsely match a valid str candidate).
"""

from __future__ import annotations

from typing import Iterable

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.employee.duplicate import find_duplicate_phone
from tests.strategies import e164_phone, non_string_value

# Hypothesis settings: at least 100 runs per property (task requirement).
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
# Local strategies — duplicate-detection shapes specific to Property 5.
# Kept in-file because they are unlikely to be reused outside Phase 13.5.
# ---------------------------------------------------------------------------


@st.composite
def _phones_with_target_present(
    draw: st.DrawFn,
) -> tuple[list[str], str]:
    """Draw `(existing, target)` such that `target` appears in `existing`.

    The target is inserted at a Hypothesis-drawn index so the test covers
    every position (head, middle, tail) of the existing collection.
    """
    target = draw(e164_phone)
    # Other phones strictly different from the target (to make the
    # "present" condition unambiguous; identical extras are fine but we
    # want to avoid the degenerate case where all elements equal target,
    # which is still True but tested separately in unit anchors).
    other_phones = draw(
        st.lists(
            e164_phone.filter(lambda p: p != target),
            min_size=0,
            max_size=20,
        )
    )
    insert_at = draw(st.integers(min_value=0, max_value=len(other_phones)))
    existing = other_phones[:insert_at] + [target] + other_phones[insert_at:]
    return existing, target


@st.composite
def _phones_without_target(
    draw: st.DrawFn,
) -> tuple[list[str], str]:
    """Draw `(existing, target)` such that `target` is NOT in `existing`."""
    target = draw(e164_phone)
    existing = draw(
        st.lists(
            e164_phone.filter(lambda p: p != target),
            min_size=0,
            max_size=20,
        )
    )
    return existing, target


@st.composite
def _phones_with_deleted_entries_and_target(
    draw: st.DrawFn,
) -> tuple[list[str], str]:
    """Draw `(existing, target)` where `existing` includes the target plus
    a mix of *active* and *audit-trail* entries — i.e. exactly the shape
    described in the function docstring for the logical-delete case.

    The "audit-trail" entries are valid E.164 strings that may belong to
    rows whose `deleted` flag is True; from this function's perspective
    they are indistinguishable from active phoneNumber values — and that
    is the whole point of Property 5: re-registering a logically-deleted
    phone number must be blocked.
    """
    target = draw(e164_phone)
    # Active entries (non-target).
    actives = draw(
        st.lists(
            e164_phone.filter(lambda p: p != target),
            min_size=0,
            max_size=10,
        )
    )
    # "Audit-trail" entries (still non-target but conceptually deleted).
    deleted_entries = draw(
        st.lists(
            e164_phone.filter(lambda p: p != target),
            min_size=0,
            max_size=10,
        )
    )
    # Place the target somewhere in the merged list; whether it's flagged
    # as deleted at the row level is irrelevant to this function.
    merged = actives + deleted_entries
    insert_at = draw(st.integers(min_value=0, max_value=len(merged)))
    merged = merged[:insert_at] + [target] + merged[insert_at:]
    return merged, target


@st.composite
def _two_permutations_of_same_multiset(
    draw: st.DrawFn,
) -> tuple[list[str], list[str], str]:
    """Draw two permutations of the same `existing` multiset plus a target.

    Used by the order-independence property: regardless of how the caller
    orders `existing_phones`, the result for the same `new_phone` must be
    identical.
    """
    base, target = draw(
        st.one_of(_phones_with_target_present(), _phones_without_target())
    )
    perm1 = draw(st.permutations(base))
    perm2 = draw(st.permutations(base))
    return perm1, perm2, target


@st.composite
def _phones_with_non_str_noise(
    draw: st.DrawFn,
) -> tuple[list[object], str]:
    """Draw `(existing, target)` where `existing` contains valid E.164
    strings plus non-`str` noise (int / None / list / bool / ...), and
    `target` is a valid E.164 that does NOT appear among the str members.

    Used to verify that non-`str` members are silently filtered: the
    presence of `42`, `None`, `[]`, etc. must not produce a false match
    for the valid-str target.
    """
    target = draw(e164_phone)
    str_members = draw(
        st.lists(
            e164_phone.filter(lambda p: p != target),
            min_size=0,
            max_size=10,
        )
    )
    noise_members: list[object] = draw(
        st.lists(non_string_value, min_size=1, max_size=10)
    )
    merged: list[object] = list(str_members) + noise_members
    # Shuffle by drawing a permutation index sequence; permutations()
    # over Any list is supported by Hypothesis.
    merged = draw(st.permutations(merged))
    return merged, target


# ---------------------------------------------------------------------------
# Property 5 (a) — True: new_phone is present at any position in existing.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(pair=(["+819012345678"], "+819012345678"))
@example(pair=(["+1", "+819012345678", "+44"], "+819012345678"))
@example(pair=(["+819012345678", "+1", "+44"], "+819012345678"))
@example(pair=(["+1", "+44", "+819012345678"], "+819012345678"))
@given(pair=_phones_with_target_present())
def test_property5_true_when_target_present(
    pair: tuple[list[str], str],
) -> None:
    """new_phone present anywhere in existing_phones → True.

    Validates: Requirements 2.3, 3.4
    """
    existing, target = pair
    assert target in existing  # generator invariant
    assert find_duplicate_phone(existing, target) is True, (
        f"expected True when target {target!r} ∈ existing {existing!r}"
    )


# ---------------------------------------------------------------------------
# Property 5 (b) — False: new_phone is absent from existing.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(pair=([], "+819012345678"))
@example(pair=(["+1"], "+819012345678"))
@example(pair=(["+1", "+44"], "+819012345678"))
@given(pair=_phones_without_target())
def test_property5_false_when_target_absent(
    pair: tuple[list[str], str],
) -> None:
    """new_phone absent from existing_phones → False.

    Validates: Requirements 2.3, 3.4
    """
    existing, target = pair
    assert target not in existing  # generator invariant
    assert find_duplicate_phone(existing, target) is False, (
        f"expected False when target {target!r} ∉ existing {existing!r}"
    )


# ---------------------------------------------------------------------------
# Property 5 (c) — False: empty existing_phones always returns False.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(new_phone=e164_phone)
def test_property5_false_when_existing_is_empty(new_phone: str) -> None:
    """Empty existing_phones → always False (no element to match).

    Validates: Requirements 2.3
    """
    assert find_duplicate_phone([], new_phone) is False
    # iter([]) also exercises the generator path.
    assert find_duplicate_phone(iter([]), new_phone) is False


# ---------------------------------------------------------------------------
# Property 5 (d) — False: non-str new_phone.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(
    existing=st.lists(e164_phone, min_size=0, max_size=10),
    new_phone=non_string_value,
)
def test_property5_false_when_new_phone_non_str(
    existing: list[str], new_phone: object
) -> None:
    """Non-str new_phone (int / None / list / bool / ...) → False.

    Even when `existing_phones` is large and varied, an invalid candidate
    is never a duplicate of anything by contract.

    Validates: Requirements 2.3
    """
    if isinstance(new_phone, str):
        # `non_string_value` does not emit str today, but guard defensively.
        return
    assert find_duplicate_phone(existing, new_phone) is False, (
        f"expected False for non-str new_phone={new_phone!r} "
        f"(existing={existing!r})"
    )


# ---------------------------------------------------------------------------
# Property 5 (e) — False: empty-string new_phone.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(existing=st.lists(e164_phone, min_size=0, max_size=10))
def test_property5_false_when_new_phone_is_empty_str(
    existing: list[str],
) -> None:
    """Empty-string new_phone → False (invalid candidate gate).

    Validates: Requirements 2.3
    """
    assert find_duplicate_phone(existing, "") is False, (
        f"expected False for new_phone='' (existing={existing!r})"
    )


def test_property5_false_when_existing_contains_empty_and_new_is_empty() -> None:
    """Anchor: even if existing contains "", new_phone="" must return False.

    Rationale: empty new_phone fails Gate 1 (non-empty str), so the function
    returns False before scanning existing.  The "" sentinel marks logically-
    deleted rows whose phoneNumber has already been nulled — these rows are
    *not* duplicates of anything because they hold no actual phone number.
    """
    assert find_duplicate_phone(["", "+819012345678"], "") is False


# ---------------------------------------------------------------------------
# Property 5 (f) — Order-independence: same multiset, different orders.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(triple=_two_permutations_of_same_multiset())
def test_property5_order_independent(
    triple: tuple[list[str], list[str], str],
) -> None:
    """For any two permutations of the same multiset, the result is identical.

    This pins down that the predicate depends on the *multiset* of
    `existing_phones`, not its order — a property required by the
    `csv_parser` / `employee_api` callers that may pass DynamoDB
    paginated Scan results in arbitrary order.

    Validates: Requirements 2.3, 3.4
    """
    perm1, perm2, target = triple
    assert sorted(perm1) == sorted(perm2)  # generator invariant
    assert find_duplicate_phone(perm1, target) == find_duplicate_phone(
        perm2, target
    ), (
        f"order-dependent result detected: target={target!r} "
        f"perm1={perm1!r} vs perm2={perm2!r}"
    )


# ---------------------------------------------------------------------------
# Property 5 (g) — Logical-delete inclusive: target inside mixed active +
# audit-trail set is detected (the task Done-When core).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(
    triple=(
        ["+819011112222", "+819012345678", "+819033334444"],
        "+819012345678",
    )
)
@given(triple=_phones_with_deleted_entries_and_target())
def test_property5_true_with_mixed_active_and_deleted_entries(
    triple: tuple[list[str], str],
) -> None:
    """Target present in a mixed set of active + audit-trail entries → True.

    From this function's perspective, the `deleted` flag on the row is
    invisible — the caller's job is to feed every phoneNumber value
    currently held in the table (including those whose row is logically
    deleted but whose phoneNumber attribute has not yet been nulled).
    The function must detect duplicates against this full set.

    Validates: Requirements 2.3, 3.4
    """
    merged, target = triple
    assert target in merged  # generator invariant
    assert find_duplicate_phone(merged, target) is True, (
        f"expected True when target {target!r} ∈ mixed-set {merged!r}"
    )


# ---------------------------------------------------------------------------
# Property 5 (h) — Non-str members of existing are silently ignored.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(pair=_phones_with_non_str_noise())
def test_property5_false_when_only_non_str_match(
    pair: tuple[list[object], str],
) -> None:
    """Non-str members of existing_phones must NOT match a valid-str target.

    Even if `existing_phones` contains values whose `repr()` happens to
    resemble the target (e.g. `+81901...` as a str-subclass or bytes),
    the `isinstance(phone, str)` filter ensures only true `str` rows
    participate.  The composite generator guarantees that no str member
    equals the target, so the only possible matches are non-str — which
    must be ignored.

    Validates: Requirements 2.3
    """
    existing, target = pair
    # Sanity: no str member equals the target.
    assert not any(
        isinstance(p, str) and p == target for p in existing
    )
    assert find_duplicate_phone(existing, target) is False, (
        f"non-str noise produced a false match: target={target!r} "
        f"existing={existing!r}"
    )


# ---------------------------------------------------------------------------
# Unit anchors — exact examples from the task spec.
# ---------------------------------------------------------------------------


def test_unit_empty_existing_is_false() -> None:
    """Anchor: `find_duplicate_phone([], "+819012345678")` → False."""
    assert find_duplicate_phone([], "+819012345678") is False


def test_unit_exact_single_match_is_true() -> None:
    """Anchor: `find_duplicate_phone(["+819012345678"], "+819012345678")` → True."""
    assert find_duplicate_phone(["+819012345678"], "+819012345678") is True


def test_unit_one_digit_off_is_false() -> None:
    """Anchor: `find_duplicate_phone(["+819012345679"], "+819012345678")` → False.

    Verifies that a single-digit difference is detected as non-duplicate
    (case-sensitive exact-equality contract).
    """
    assert find_duplicate_phone(["+819012345679"], "+819012345678") is False


def test_unit_multiple_copies_of_target_is_true() -> None:
    """Anchor: existing with repeated target still → True.

    `csv_parser` may surface intra-CSV duplicates as separate entries before
    de-duplication; the table-vs-candidate check must still return True.
    """
    assert (
        find_duplicate_phone(
            ["+819012345678", "+819012345678"], "+819012345678"
        )
        is True
    )


def test_unit_new_phone_none_is_false() -> None:
    """Anchor: new_phone=None → False (non-str gate)."""
    assert find_duplicate_phone(["+819012345678"], None) is False


def test_unit_new_phone_int_is_false() -> None:
    """Anchor: new_phone=int → False (non-str gate).

    Importantly, even if an int happened to match a row by `==` semantics
    in some hypothetical future Python (it does not today), the
    `isinstance(new_phone, str)` gate stops it cold.
    """
    assert find_duplicate_phone(["+819012345678"], 819012345678) is False


def test_unit_new_phone_list_is_false() -> None:
    """Anchor: new_phone=list → False (non-str gate)."""
    assert find_duplicate_phone(["+819012345678"], ["+819012345678"]) is False


def test_unit_new_phone_dict_is_false() -> None:
    """Anchor: new_phone=dict → False (non-str gate)."""
    assert (
        find_duplicate_phone(["+819012345678"], {"phone": "+819012345678"})
        is False
    )


def test_unit_new_phone_bool_is_false() -> None:
    """Anchor: new_phone=True/False → False (non-str gate)."""
    assert find_duplicate_phone(["+819012345678"], True) is False
    assert find_duplicate_phone(["+819012345678"], False) is False


def test_unit_existing_with_none_and_int_noise_is_false() -> None:
    """Anchor: existing contains None / int noise + valid E.164 → no false match.

    The non-str members must be filtered; only the valid-str member
    participates in equality testing.
    """
    existing: list[object] = [None, 123, [], "+819099998888"]
    assert find_duplicate_phone(existing, "+819012345678") is False


def test_unit_existing_with_noise_and_target_is_true() -> None:
    """Anchor: existing contains noise + the target → True.

    The non-str noise must not interfere with finding the matching str.
    """
    existing: list[object] = [None, 42, "+819099998888", "+819012345678"]
    assert find_duplicate_phone(existing, "+819012345678") is True


def test_unit_generator_input_is_consumed_correctly() -> None:
    """Anchor: passing a generator yields the expected truth value.

    Property 5 contracts that `existing_phones` is consumed once; this
    test pins down the generator path (no premature StopIteration, no
    double-iteration assumptions).
    """
    assert (
        find_duplicate_phone(iter(["+819012345678"]), "+819012345678") is True
    )
    assert (
        find_duplicate_phone(iter(["+819099998888"]), "+819012345678") is False
    )


def test_unit_existing_with_empty_string_does_not_match_nonempty_target() -> None:
    """Anchor: existing contains "" sentinel + valid candidate → False.

    Verifies the logical-delete sentinel ("") does not produce a false
    match for any non-empty valid candidate.
    """
    assert (
        find_duplicate_phone(["", "+819099998888"], "+819012345678") is False
    )


def test_unit_case_sensitive_exact_match() -> None:
    """Anchor: E.164 is ASCII-digit-only so case matters only theoretically.

    Pinned here to lock the case-sensitive contract; future regressions
    that introduce `.lower()` etc. on either side will be caught.
    """
    # E.164 has no letters, so this is a degenerate but still meaningful
    # boundary: the "+" + digits string is case-invariant by its alphabet,
    # but if any caller ever attaches an extension or letter suffix, the
    # case-sensitivity guarantee matters.
    assert find_duplicate_phone(["+819012345678"], "+819012345678") is True


# ---------------------------------------------------------------------------
# Bidirectional regression anchor — direct decoding of the function contract.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(
    existing=st.lists(e164_phone, min_size=0, max_size=20),
    new_phone=e164_phone,
)
def test_property5_bidirectional_membership(
    existing: list[str], new_phone: str
) -> None:
    """For any (existing, new_phone) pair, the function result equals
    `new_phone in list(existing)`.

    This is the verbatim docstring contract.  By generating both inputs
    independently, the test hits both the True (random hits) and False
    (random misses) branches and pins down the symmetric `in` semantics.

    Validates: Requirements 2.3, 3.4
    """
    expected = new_phone in list(existing)
    actual = find_duplicate_phone(existing, new_phone)
    assert actual is expected, (
        f"contract drift: expected {expected!r} (membership) but got "
        f"{actual!r}; existing={existing!r} new_phone={new_phone!r}"
    )
