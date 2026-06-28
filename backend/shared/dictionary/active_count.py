"""Active keyword counting (Requirement 8.6, Phase 4.4).

CycleApi guards against starting a cycle when the keyword dictionary is
empty across the three active categories (SAFE / INJURED / UNAVAILABLE).
Per Requirement 8.6, at least one keyword must be present in any one
of those categories before a Cycle is allowed to start.

Layered for testability:
    - ``count_active_keywords(safe, injured, unavailable)`` is a PURE
      function that sums the lengths of the three input lists. PBT
      candidate (Hypothesis): for any three lists, the return value
      equals ``len(safe) + len(injured) + len(unavailable)``, and the
      function never raises.
    - ``is_dictionary_empty(safe, injured, unavailable)`` is also PURE:
      it returns ``True`` iff every input list is empty (count == 0).

The CycleApi handler queries the three categories from the
KeywordDictionary table (one query per category), passes the resulting
Items lists into these helpers, and rejects with 400 when
``is_dictionary_empty`` returns True. The split keeps the I/O layer
thin and the decision layer trivially testable.
"""

from __future__ import annotations

from typing import Any


def count_active_keywords(
    safe_items: list[dict[str, Any]],
    injured_items: list[dict[str, Any]],
    unavailable_items: list[dict[str, Any]],
) -> int:
    """Return the total count of keywords across the three active categories.

    PURE FUNCTION. No I/O.

    Args:
        safe_items: DynamoDB items returned from the SAFE category Query.
            The element shape is not inspected — only ``len()`` is used,
            so callers may pass any list of dictionaries (or any list
            whose length corresponds to the keyword count, including
            ``Limit=1`` truncated results when the caller only needs to
            establish a lower bound on emptiness).
        injured_items: Same shape as ``safe_items`` for the INJURED
            category.
        unavailable_items: Same shape for the UNAVAILABLE category.

    Returns:
        The sum ``len(safe_items) + len(injured_items) + len(unavailable_items)``.
        Always non-negative.
    """
    return len(safe_items) + len(injured_items) + len(unavailable_items)


def is_dictionary_empty(
    safe_items: list[dict[str, Any]],
    injured_items: list[dict[str, Any]],
    unavailable_items: list[dict[str, Any]],
) -> bool:
    """Return True iff every active category list is empty.

    PURE FUNCTION. No I/O.

    Args:
        safe_items: Items list for the SAFE category.
        injured_items: Items list for the INJURED category.
        unavailable_items: Items list for the UNAVAILABLE category.

    Returns:
        ``True`` when the total active keyword count is zero (i.e.,
        ``count_active_keywords(...) == 0``), otherwise ``False``.
    """
    return count_active_keywords(safe_items, injured_items, unavailable_items) == 0
