"""Pure helpers for the inbound-contact list endpoint (Phase 10.8).

The InboundApi (extension of :mod:`response_api` Lambda) returns a
``receivedAt``-descending, 50-item-per-page view of
``InboundContactTable``. DynamoDB's global ``Scan`` does not guarantee
ordering and there is no global GSI sorted by ``receivedAt``
(``EmployeeReceivedAtIndex`` is keyed on ``employeeId``), so the Lambda
performs an in-memory sort after pulling all matching rows. For the
admin-facing volume envisaged by Requirement 13.7 this is acceptable;
should the volume grow significantly, callers may swap in a GSI-backed
implementation by keeping the same wire contract.

The two helpers below are extracted as a pure module so they can be
exercised without DynamoDB mocks. They are deliberate candidates for
Phase 13 property-based tests (sort stability, pagination round-trip
properties — see ``docs/notes/_progress.md`` for follow-up).

Design constraints honoured here (19原則):
* (a) DRY — sort key resolution + missing-key handling lives in a
  single function reused by sort and pagination logic.
* (b) Errors are surfaced as ``ValueError`` rather than silently
  defaulted; the handler converts them to HTTP 400.
"""

from __future__ import annotations

from typing import Any

INBOUND_PAGE_SIZE = 50
"""Page size for ``GET /inbound`` (Requirement 12.1 / 13.7 — 50 件)."""


def sort_by_received_at_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``items`` sorted by the ``receivedAt`` field, descending.

    A row whose ``receivedAt`` is missing or non-string is sorted to the
    bottom (treated as the "oldest" possible value). Two rows with the
    same ``receivedAt`` retain their original relative order
    (``sorted`` is stable in CPython), which keeps test fixtures
    deterministic.

    The input is not mutated; a new list is returned.

    Args:
        items: Raw DynamoDB items (already deserialised to native dicts).

    Returns:
        A fresh list of the same dicts in descending ``receivedAt``
        order.
    """
    return sorted(items, key=_received_at_sort_key, reverse=True)


def paginate(
    items: list[dict[str, Any]], page_size: int, offset: int
) -> tuple[list[dict[str, Any]], int | None]:
    """Return ``items[offset:offset+page_size]`` and the next offset.

    Args:
        items: The full, already-sorted list.
        page_size: Number of items per page (must be ≥ 1).
        offset: Zero-based start index within ``items``.

    Returns:
        ``(page, next_offset)`` — ``page`` is the slice; ``next_offset``
        is the offset for the following call, or ``None`` if there are
        no more items.

    Raises:
        ValueError: If ``page_size`` is non-positive or ``offset`` is
            negative.
    """
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    end = offset + page_size
    page = items[offset:end]
    next_offset = end if end < len(items) else None
    return page, next_offset


def _received_at_sort_key(item: dict[str, Any]) -> str:
    """Return the receivedAt key used for descending sort.

    Items lacking a valid string ``receivedAt`` get an empty string,
    which sorts below any non-empty ISO-8601 timestamp under descending
    ``reverse=True`` ordering. This is intentional: malformed rows are
    relegated to the end of the list rather than crashing the handler.
    """
    value = item.get("receivedAt")
    if isinstance(value, str):
        return value
    return ""
