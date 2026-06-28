"""Unit tests for the inbound-list pure helpers (Phase 10.8).

Covers the two helpers in ``shared.inbound.listing``:

* :func:`sort_by_received_at_desc` — stable descending sort by the
  ``receivedAt`` field; rows lacking that field land at the bottom.
* :func:`paginate` — offset/page-size slicing with ``next_offset``.

These functions are pure and so can be exercised without DynamoDB
mocks; Phase 13 PBT candidates are noted in ``shared/inbound/listing.py``.
"""

from __future__ import annotations

import pytest

from shared.inbound.listing import (
    INBOUND_PAGE_SIZE,
    paginate,
    sort_by_received_at_desc,
)


def _row(contact_id: str, received_at: str | None) -> dict[str, object]:
    item: dict[str, object] = {"contactId": contact_id}
    if received_at is not None:
        item["receivedAt"] = received_at
    return item


# --- sort_by_received_at_desc ------------------------------------------


def test_sort_descending_by_received_at() -> None:
    rows = [
        _row("c1", "2026-06-01T10:00:00Z"),
        _row("c2", "2026-06-03T10:00:00Z"),
        _row("c3", "2026-06-02T10:00:00Z"),
    ]

    out = sort_by_received_at_desc(rows)

    assert [r["contactId"] for r in out] == ["c2", "c3", "c1"]


def test_sort_is_stable_for_equal_received_at() -> None:
    rows = [
        _row("c1", "2026-06-03T10:00:00Z"),
        _row("c2", "2026-06-03T10:00:00Z"),
        _row("c3", "2026-06-03T10:00:00Z"),
    ]

    out = sort_by_received_at_desc(rows)

    # Stable sort: original relative order preserved when keys equal.
    assert [r["contactId"] for r in out] == ["c1", "c2", "c3"]


def test_sort_missing_received_at_goes_to_bottom() -> None:
    rows = [
        _row("c1", None),
        _row("c2", "2026-06-03T10:00:00Z"),
        _row("c3", None),
        _row("c4", "2026-06-01T10:00:00Z"),
    ]

    out = sort_by_received_at_desc(rows)

    # The two timestamped rows come first (desc), missing ones trail
    # in their original relative order.
    assert [r["contactId"] for r in out] == ["c2", "c4", "c1", "c3"]


def test_sort_non_string_received_at_treated_as_missing() -> None:
    rows = [
        {"contactId": "c1", "receivedAt": 12345},  # int instead of str
        _row("c2", "2026-06-03T10:00:00Z"),
    ]

    out = sort_by_received_at_desc(rows)

    assert [r["contactId"] for r in out] == ["c2", "c1"]


def test_sort_does_not_mutate_input() -> None:
    rows = [
        _row("c1", "2026-06-01T10:00:00Z"),
        _row("c2", "2026-06-03T10:00:00Z"),
    ]
    original_order = [r["contactId"] for r in rows]

    sort_by_received_at_desc(rows)

    assert [r["contactId"] for r in rows] == original_order


# --- paginate ----------------------------------------------------------


def test_paginate_first_page_with_remainder() -> None:
    items = [{"contactId": f"c{i}"} for i in range(120)]

    page, next_offset = paginate(items, page_size=50, offset=0)

    assert len(page) == 50
    assert page[0]["contactId"] == "c0"
    assert page[-1]["contactId"] == "c49"
    assert next_offset == 50


def test_paginate_middle_page() -> None:
    items = [{"contactId": f"c{i}"} for i in range(120)]

    page, next_offset = paginate(items, page_size=50, offset=50)

    assert [r["contactId"] for r in page] == [f"c{i}" for i in range(50, 100)]
    assert next_offset == 100


def test_paginate_last_partial_page_returns_no_next_offset() -> None:
    items = [{"contactId": f"c{i}"} for i in range(120)]

    page, next_offset = paginate(items, page_size=50, offset=100)

    assert len(page) == 20
    assert next_offset is None


def test_paginate_exact_boundary_returns_no_next_offset() -> None:
    items = [{"contactId": f"c{i}"} for i in range(100)]

    page, next_offset = paginate(items, page_size=50, offset=50)

    # Items 50..99 inclusive fills exactly the page; nothing remains.
    assert len(page) == 50
    assert next_offset is None


def test_paginate_offset_past_end_returns_empty_page() -> None:
    items = [{"contactId": f"c{i}"} for i in range(10)]

    page, next_offset = paginate(items, page_size=50, offset=100)

    assert page == []
    assert next_offset is None


def test_paginate_negative_offset_raises() -> None:
    with pytest.raises(ValueError, match="offset must be >= 0"):
        paginate([], page_size=50, offset=-1)


def test_paginate_zero_page_size_raises() -> None:
    with pytest.raises(ValueError, match="page_size must be >= 1"):
        paginate([], page_size=0, offset=0)


def test_inbound_page_size_constant_is_50() -> None:
    assert INBOUND_PAGE_SIZE == 50
