"""Dictionary snapshot retrieval (Requirement 8.5 / Property 19).

Reconstructs the keyword dictionary state at a given version number by
reading the KeywordDictionaryHistory table.

Layered for testability:
    - `_to_snapshot(items)` is a PURE function. Property 19 PBT in
      Phase 13.x exercises it directly without touching DynamoDB —
      Hypothesis can permute the row order, drop fields, etc., and
      assert deterministic equivalence.
    - `get_dictionary_snapshot(version, ...)` performs the DynamoDB
      Query (one or more pages) and delegates shape conversion to
      `_to_snapshot`.

History table schema (design.md D7 history):
    PK = version (N)
    SK = categoryKeyword (S) = "{category}#{keyword}"
    Attributes: category (S), keyword (S), ...

Property 19 (target of Hypothesis PBT in Phase 13.x):
    For all Cycle c, the value of `get_dictionary_snapshot(c.dictionaryVersion)`
    does not depend on wall-clock time — the same input (immutable
    history rows for that version) always yields the same output set
    (and the same JSON list ordering, because we sort).
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

VALID_CATEGORIES: tuple[str, ...] = ("SAFE", "INJURED", "UNAVAILABLE")


def _to_snapshot(history_items: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Reconstruct the `{category: [keyword, ...]}` mapping from history rows.

    PURE FUNCTION. No I/O. Property 19 PBT target.

    Args:
        history_items: List of DynamoDB rows from KeywordDictionaryHistory
            for a single `version`. Each row should carry `category` and
            `keyword` string attributes; malformed rows are silently
            ignored (defensive against partial reads).

    Returns:
        Dict keyed by category name (only the three valid categories
        SAFE / INJURED / UNAVAILABLE) mapping to a SORTED list of
        keywords. Sorting makes the output bytewise-deterministic so
        Property 19 equality holds independent of row arrival order.
    """
    out: dict[str, list[str]] = {c: [] for c in VALID_CATEGORIES}
    for row in history_items:
        category = row.get("category")
        keyword = row.get("keyword")
        if (
            isinstance(category, str)
            and category in VALID_CATEGORIES
            and isinstance(keyword, str)
            and keyword
        ):
            out[category].append(keyword)
    for keywords in out.values():
        keywords.sort()
    return out


def get_dictionary_snapshot(
    version: int,
    *,
    table_name: str | None = None,
) -> dict[str, list[str]]:
    """Fetch the full dictionary state at version `version`.

    Args:
        version: Snapshot version number (KeywordDictionaryHistory PK).
        table_name: Optional override for the table name. Defaults to
            the environment variable `KEYWORD_DICT_HISTORY_TABLE_NAME`.

    Returns:
        `{"SAFE": [...], "INJURED": [...], "UNAVAILABLE": [...]}` with
        keyword lists sorted ascending. Empty categories yield empty
        lists. Empty version (no history rows) yields all-empty lists.
    """
    if table_name is None:
        table_name = os.environ["KEYWORD_DICT_HISTORY_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)

    items: list[dict[str, Any]] = []
    last_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {"KeyConditionExpression": Key("version").eq(version)}
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if last_key is None:
            break

    return _to_snapshot(items)
