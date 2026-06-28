"""Unit tests for ``shared.dictionary.active_count`` (Phase 4.4).

The module exposes two pure helpers used by CycleApi to enforce
Requirement 8.6 (at least one active keyword required to start a cycle):

* ``count_active_keywords(safe, injured, unavailable)`` — sums the
  lengths of three lists.
* ``is_dictionary_empty(safe, injured, unavailable)`` — True iff the
  total count is zero.

These tests cover boundary (0 and 1 entries), the three single-category
populations, every two-category combination, and a large-input case.
"""

from __future__ import annotations

import pytest

from shared.dictionary.active_count import count_active_keywords, is_dictionary_empty

# --- count_active_keywords -------------------------------------------------


def _stub(n: int) -> list[dict[str, str]]:
    """Generate ``n`` placeholder DynamoDB-shaped items."""
    return [{"category": "STUB", "keyword": f"kw-{i}"} for i in range(n)]


class TestCountActiveKeywords:
    def test_all_empty_returns_zero(self) -> None:
        assert count_active_keywords([], [], []) == 0

    def test_safe_only_returns_safe_length(self) -> None:
        assert count_active_keywords(_stub(3), [], []) == 3

    def test_injured_only_returns_injured_length(self) -> None:
        assert count_active_keywords([], _stub(2), []) == 2

    def test_unavailable_only_returns_unavailable_length(self) -> None:
        assert count_active_keywords([], [], _stub(5)) == 5

    def test_two_category_combinations(self) -> None:
        assert count_active_keywords(_stub(1), _stub(1), []) == 2
        assert count_active_keywords(_stub(1), [], _stub(1)) == 2
        assert count_active_keywords([], _stub(1), _stub(1)) == 2

    def test_three_categories_combined(self) -> None:
        assert count_active_keywords(_stub(4), _stub(5), _stub(6)) == 15

    def test_large_input(self) -> None:
        assert count_active_keywords(_stub(1000), _stub(2000), _stub(3000)) == 6000

    @pytest.mark.parametrize(
        ("safe_n", "injured_n", "unavailable_n", "expected"),
        [
            (0, 0, 0, 0),
            (1, 0, 0, 1),
            (0, 1, 0, 1),
            (0, 0, 1, 1),
            (1, 1, 1, 3),
            (10, 20, 30, 60),
        ],
    )
    def test_parametric_totals(
        self, safe_n: int, injured_n: int, unavailable_n: int, expected: int
    ) -> None:
        assert (
            count_active_keywords(_stub(safe_n), _stub(injured_n), _stub(unavailable_n))
            == expected
        )


# --- is_dictionary_empty ---------------------------------------------------


class TestIsDictionaryEmpty:
    def test_all_empty_returns_true(self) -> None:
        assert is_dictionary_empty([], [], []) is True

    def test_safe_single_keyword_returns_false(self) -> None:
        assert is_dictionary_empty(_stub(1), [], []) is False

    def test_injured_single_keyword_returns_false(self) -> None:
        assert is_dictionary_empty([], _stub(1), []) is False

    def test_unavailable_single_keyword_returns_false(self) -> None:
        assert is_dictionary_empty([], [], _stub(1)) is False

    def test_all_three_categories_populated_returns_false(self) -> None:
        assert is_dictionary_empty(_stub(2), _stub(3), _stub(4)) is False

    def test_threshold_boundary_zero_vs_one(self) -> None:
        # Boundary: count == 0 → empty; count == 1 → not empty.
        assert is_dictionary_empty([], [], []) is True
        assert is_dictionary_empty(_stub(1), [], []) is False
