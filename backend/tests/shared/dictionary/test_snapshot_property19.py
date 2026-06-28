"""Property 19 — Dictionary version snapshot invariance PBT (Phase 13.19).

Validates: Requirements 8.5 (Cycle 起動時に確定した `dictionaryVersion` から
辞書スナップショットを再現でき、後続の辞書 CRUD に影響されない不変性).

Target: `shared.dictionary.snapshot._to_snapshot(history_items)` — a PURE
function. The I/O wrapper `get_dictionary_snapshot` performs a DynamoDB Query
and delegates shape conversion to `_to_snapshot`; Property 19 holds at the
pure-function layer because the DynamoDB layer only re-orders / re-paginates
rows. By Property 19 (b) order-invariance, any DynamoDB ordering yields the
same output, so testing `_to_snapshot` directly is both necessary and
sufficient (no DynamoDB mock required — design intent recorded in
snapshot.py docstring).

Contract (verbatim from snapshot.py):
    1. Rows whose `category` is not in VALID_CATEGORIES, or whose `keyword`
       is empty / non-string / missing, are silently dropped.
    2. Output dict keys are always exactly the three VALID_CATEGORIES
       (empty list included for categories with no rows).
    3. Each category's keyword list is sorted ascending.
    4. Output does not depend on input row ordering.
    5. Idempotency: calling `_to_snapshot` repeatedly on the same input
       returns the same output (trivially true for a pure function, but
       asserted to detect future regressions, e.g. accidental mutation
       of the input list).

This file enforces seven properties:
  (a)  Deterministic: two successive calls on the same input agree.
  (b)  Order-invariant: two permutations of the same multiset agree.
  (c)  Output schema: keys == set(VALID_CATEGORIES) for all inputs.
  (d)  Sorted lists: out[c] == sorted(out[c]) for every category.
  (e)  Duplicate preservation: 3× the same valid row → 3× the same keyword.
  (f)  Invalid-row filtering: mixing invalid rows with a known-valid set
       yields the same output as the valid set alone.
  (g)  Empty input: all three categories map to [].

Plus a small unit-test layer pinning anchored cases.
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shared.dictionary.snapshot import VALID_CATEGORIES, _to_snapshot

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


# ---------------------------------------------------------------------------
# Local strategies (kept inside the test file — strong contextual coupling
# to Property 19 and unlikely to be reused).
# ---------------------------------------------------------------------------

# Keyword body alphabet — small, ASCII, deliberately chosen to maximise
# duplicate frequency at modest list sizes so the duplicate-preservation
# property (e) is exercised naturally during random generation.
_KEYWORD_ALPHABET = "abcdef"

#: Non-empty ASCII string used as a valid keyword.
_valid_keyword = st.text(alphabet=_KEYWORD_ALPHABET, min_size=1, max_size=8)


@st.composite
def _valid_row(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a row guaranteed to be kept by `_to_snapshot`.

    `category` is sampled from VALID_CATEGORIES and `keyword` is a
    non-empty ASCII string.
    """
    return {
        "category": draw(st.sampled_from(list(VALID_CATEGORIES))),
        "keyword": draw(_valid_keyword),
    }


#: Category strings guaranteed NOT to be in VALID_CATEGORIES.  Includes
#: the empty string, a META sentinel, and casing variants — all three
#: realistic failure modes of upstream history-table writers.
_invalid_category = st.sampled_from(
    [
        "",
        "META",
        "safe",  # lowercase (case-sensitive guard regression detector)
        "Safe",
        "SAFE ",  # trailing whitespace
        "UNKNOWN",
        "INJURED!",
    ]
)


@st.composite
def _invalid_row(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a row guaranteed to be dropped by `_to_snapshot`.

    Covers the four documented invalid-row shapes:
      - bad category string,
      - empty keyword,
      - non-string keyword (int, None, list, dict, bool, float),
      - missing 'category' or 'keyword' key entirely.

    A `st.one_of` over the four shape generators ensures coverage of each
    drop path without weighting one over another.
    """
    shape = draw(st.integers(min_value=0, max_value=3))
    if shape == 0:
        # Shape 0: category not in VALID_CATEGORIES, keyword arbitrary.
        return {
            "category": draw(_invalid_category),
            "keyword": draw(_valid_keyword),
        }
    if shape == 1:
        # Shape 1: empty keyword (falsy string).
        return {
            "category": draw(st.sampled_from(list(VALID_CATEGORIES))),
            "keyword": "",
        }
    if shape == 2:
        # Shape 2: non-string keyword.
        return {
            "category": draw(st.sampled_from(list(VALID_CATEGORIES))),
            "keyword": draw(
                st.one_of(
                    st.integers(),
                    st.none(),
                    st.booleans(),
                    st.floats(allow_nan=True, allow_infinity=True),
                    st.lists(st.text(max_size=3), max_size=3),
                    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
                )
            ),
        }
    # Shape 3: missing one or both required keys.
    sub = draw(st.integers(min_value=0, max_value=2))
    if sub == 0:
        return {"keyword": draw(_valid_keyword)}  # no 'category'
    if sub == 1:
        return {"category": draw(st.sampled_from(list(VALID_CATEGORIES)))}  # no 'keyword'
    return {}  # neither key


#: A history-row list mixing valid and invalid rows in random proportion
#: and order — the canonical Property 19 input shape.
_history_items = st.lists(
    st.one_of(_valid_row(), _invalid_row()),
    min_size=0,
    max_size=50,
)


# ---------------------------------------------------------------------------
# (a) Deterministic: two successive calls on the same input agree.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(items=_history_items)
def test_property19_deterministic_repeat_call(items: list[dict[str, Any]]) -> None:
    """`_to_snapshot(items) == _to_snapshot(items)` for all inputs.

    This is trivially true for a pure function, but the property guards
    against future regressions such as accidental input mutation or
    nondeterministic ordering bugs in dict iteration.

    Validates: Requirements 8.5 (contract clause 5 — idempotency).
    """
    first = _to_snapshot(items)
    second = _to_snapshot(items)
    assert first == second, (
        f"non-deterministic output: first={first!r} second={second!r}"
    )


# ---------------------------------------------------------------------------
# (b) Order-invariant: two permutations of the same multiset agree.
# ---------------------------------------------------------------------------


@st.composite
def _two_permutations_of_same_multiset(
    draw: st.DrawFn,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Draw an items list, then two independent permutations of it.

    Hypothesis's `permutations(seq)` returns a fresh permutation each call,
    so two `draw(permutations(...))` calls give two random orderings of the
    same underlying multiset.
    """
    items = draw(_history_items)
    perm_a = draw(st.permutations(items))
    perm_b = draw(st.permutations(items))
    return perm_a, perm_b


@PBT_SETTINGS
@given(pair=_two_permutations_of_same_multiset())
def test_property19_order_invariant(
    pair: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    """`_to_snapshot(perm_a) == _to_snapshot(perm_b)` for any two permutations.

    This is the core "time-independent" property: the order in which
    DynamoDB returns history rows (paginated, GSI-ordered, etc.) must not
    affect the reconstructed snapshot.

    Validates: Requirements 8.5 (contract clause 4 — order-invariance).
    """
    perm_a, perm_b = pair
    snapshot_a = _to_snapshot(perm_a)
    snapshot_b = _to_snapshot(perm_b)
    assert snapshot_a == snapshot_b, (
        f"order-dependent output: a={snapshot_a!r} b={snapshot_b!r} "
        f"perm_a={perm_a!r} perm_b={perm_b!r}"
    )


# ---------------------------------------------------------------------------
# (c) Output schema: keys are exactly set(VALID_CATEGORIES) for any input.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(items=_history_items)
def test_property19_keys_are_exactly_valid_categories(
    items: list[dict[str, Any]],
) -> None:
    """Output keys are always exactly `set(VALID_CATEGORIES)`.

    No new keys (e.g. invalid category values) leak in, and no valid
    category is ever omitted — even if the input has no rows for it.

    Validates: Requirements 8.5 (contract clause 2 — fixed three keys).
    """
    out = _to_snapshot(items)
    assert set(out.keys()) == set(VALID_CATEGORIES), (
        f"unexpected keys: got={sorted(out.keys())!r} "
        f"expected={sorted(VALID_CATEGORIES)!r}"
    )


# ---------------------------------------------------------------------------
# (d) Each category's keyword list is sorted ascending.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(items=_history_items)
def test_property19_each_list_is_sorted_ascending(
    items: list[dict[str, Any]],
) -> None:
    """For every category, `out[c] == sorted(out[c])`.

    Validates: Requirements 8.5 (contract clause 3 — sorted output).
    """
    out = _to_snapshot(items)
    for category in VALID_CATEGORIES:
        assert out[category] == sorted(out[category]), (
            f"category {category!r} not sorted: {out[category]!r}"
        )


# ---------------------------------------------------------------------------
# (e) Duplicate preservation: N copies of the same valid row produce N
#     copies of the keyword in the corresponding list.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(
    category=st.sampled_from(list(VALID_CATEGORIES)),
    keyword=_valid_keyword,
    count=st.integers(min_value=1, max_value=10),
)
def test_property19_duplicates_are_preserved(
    category: str, keyword: str, count: int
) -> None:
    """`count` copies of the same valid row yield `count` copies of the keyword.

    `_to_snapshot` is a multiset aggregator, not a set aggregator. History
    rows are sourced from `KeywordDictionaryHistoryTable` whose SK is
    `category#keyword`, so the table itself enforces uniqueness; but the
    snapshot function MUST NOT silently dedupe — that responsibility lies
    upstream. Asserting duplicate preservation pins the contract here.

    Validates: Requirements 8.5 (multiset semantics).
    """
    items = [{"category": category, "keyword": keyword}] * count
    out = _to_snapshot(items)
    assert out[category] == [keyword] * count, (
        f"expected {count}x{keyword!r} got {out[category]!r}"
    )
    # All other categories remain empty.
    for other in VALID_CATEGORIES:
        if other != category:
            assert out[other] == [], (
                f"category {other!r} should be empty, got {out[other]!r}"
            )


# ---------------------------------------------------------------------------
# (f) Invalid-row filtering: mixing invalid rows in does not affect the
#     output relative to a pure valid-row baseline.
# ---------------------------------------------------------------------------


@st.composite
def _valid_and_mixed(
    draw: st.DrawFn,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Draw a valid baseline and a mixed list = baseline ⊎ invalid rows
    (then shuffled), so a single Hypothesis draw can compare both.
    """
    valid = draw(st.lists(_valid_row(), min_size=0, max_size=20))
    invalid = draw(st.lists(_invalid_row(), min_size=0, max_size=20))
    mixed = draw(st.permutations(valid + invalid))
    return valid, mixed


@PBT_SETTINGS
@given(pair=_valid_and_mixed())
def test_property19_invalid_rows_do_not_affect_output(
    pair: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    """`_to_snapshot(valid) == _to_snapshot(valid ⊎ invalid)` for any invalid set.

    Invalid rows must be silently dropped (contract clause 1), so the
    output of the mixed list must equal the output of the valid-only
    baseline regardless of how many invalid rows are interleaved or where
    they sit.

    Validates: Requirements 8.5 (contract clause 1 — invalid-row filtering).
    """
    valid, mixed = pair
    baseline = _to_snapshot(valid)
    with_invalid = _to_snapshot(mixed)
    assert baseline == with_invalid, (
        f"invalid rows altered output: baseline={baseline!r} "
        f"with_invalid={with_invalid!r}"
    )


# ---------------------------------------------------------------------------
# (g) Empty input: all three categories map to empty lists.
# ---------------------------------------------------------------------------


def test_property19_empty_input_yields_all_empty_lists() -> None:
    """`_to_snapshot([]) == {c: [] for c in VALID_CATEGORIES}`.

    This is the only no-input shape — Hypothesis tests above also produce
    `[]` occasionally, but pinning it as an explicit unit ensures the
    invariant is visible at first glance and protected from generator drift.

    Validates: Requirements 8.5 (empty-history edge case).
    """
    assert _to_snapshot([]) == {c: [] for c in VALID_CATEGORIES}


# ---------------------------------------------------------------------------
# Explicit unit examples (anchored cases enumerated in the task).
# ---------------------------------------------------------------------------


def test_unit_one_keyword_per_valid_category() -> None:
    """One row in each VALID_CATEGORIES yields one keyword per category list."""
    items = [
        {"category": "SAFE", "keyword": "ok"},
        {"category": "INJURED", "keyword": "hurt"},
        {"category": "UNAVAILABLE", "keyword": "out"},
    ]
    out = _to_snapshot(items)
    assert out == {
        "SAFE": ["ok"],
        "INJURED": ["hurt"],
        "UNAVAILABLE": ["out"],
    }


def test_unit_all_invalid_rows_yield_all_empty() -> None:
    """Every row invalid → output identical to empty-input case."""
    items = [
        {"category": "META", "keyword": "ignored"},  # bad category
        {"category": "SAFE", "keyword": ""},  # empty keyword
        {"category": "INJURED", "keyword": 42},  # non-string keyword
        {"category": "UNAVAILABLE", "keyword": None},  # non-string keyword
        {"category": "safe", "keyword": "lower"},  # bad casing
        {"keyword": "no-cat"},  # missing 'category'
        {"category": "SAFE"},  # missing 'keyword'
        {},  # neither
    ]
    assert _to_snapshot(items) == {c: [] for c in VALID_CATEGORIES}


def test_unit_single_valid_amid_many_invalid() -> None:
    """One valid row + many invalid rows → exactly that one keyword in its list."""
    items = [
        {"category": "META", "keyword": "drop1"},
        {"category": "SAFE", "keyword": ""},
        {"category": "INJURED", "keyword": "hurt"},  # the only valid row
        {"keyword": "no-cat"},
        {"category": "UNAVAILABLE", "keyword": 123},
        {},
    ]
    out = _to_snapshot(items)
    assert out == {
        "SAFE": [],
        "INJURED": ["hurt"],
        "UNAVAILABLE": [],
    }


def test_unit_triple_duplicate_safe_keyword_preserved() -> None:
    """Three identical SAFE rows preserve the duplicate in the output list.

    Anchored case for property (e) duplicate preservation.
    """
    items = [{"category": "SAFE", "keyword": "safe"}] * 3
    out = _to_snapshot(items)
    assert out == {
        "SAFE": ["safe", "safe", "safe"],
        "INJURED": [],
        "UNAVAILABLE": [],
    }


def test_unit_keywords_are_sorted_lexicographically() -> None:
    """Unsorted input row order produces a sorted output list per category."""
    items = [
        {"category": "SAFE", "keyword": "zebra"},
        {"category": "SAFE", "keyword": "apple"},
        {"category": "SAFE", "keyword": "mango"},
        {"category": "INJURED", "keyword": "broken"},
        {"category": "INJURED", "keyword": "ankle"},
    ]
    out = _to_snapshot(items)
    assert out["SAFE"] == ["apple", "mango", "zebra"]
    assert out["INJURED"] == ["ankle", "broken"]
    assert out["UNAVAILABLE"] == []


def test_unit_input_list_not_mutated() -> None:
    """`_to_snapshot` must not mutate its input (defensive, idempotency anchor).

    Regression detector: if a future refactor accidentally calls `.sort()`
    on the input rows instead of the per-category buckets, the input list
    would change.  Asserting deep equality of input before/after pins the
    purity contract.
    """
    items = [
        {"category": "SAFE", "keyword": "z"},
        {"category": "SAFE", "keyword": "a"},
        {"category": "META", "keyword": "drop"},
    ]
    snapshot_before = [dict(row) for row in items]
    _to_snapshot(items)
    assert items == snapshot_before, (
        f"input was mutated: before={snapshot_before!r} after={items!r}"
    )
