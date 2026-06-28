"""In-memory fakes for AWS resources used by the Cycle workflow.

These fakes are scoped to the ``test_sla_300_mock.py`` integration
test (Task 14.11a). They implement the **minimal** subset of the
``boto3.resource('dynamodb').Table`` API that the
ConnectDispatcher / CallEndHandler / TranscribeStarter /
RetryEvaluator / CycleFinalizer Lambdas actually invoke, so the
real handler code can run unchanged against the fakes:

* ``put_item(Item, ConditionExpression=None, ExpressionAttributeValues=None)``
* ``get_item(Key)`` / ``get_item(Key, ConsistentRead=...)``
* ``update_item(Key, UpdateExpression, ConditionExpression?,
  ExpressionAttributeValues, ExpressionAttributeNames?)``
* ``query(KeyConditionExpression, ExpressionAttributeValues, ExclusiveStartKey?)``

The keyword names above match AWS SDK conventions (``CamelCase``) on
purpose so the real handler code does not need wrappers. ``ruff``
``N803`` suppressions on every method below acknowledge this.

Three deliberate non-features:

1. **No silent fallbacks.** When an UpdateExpression / ConditionExpression
   shape lands here that the parser does not recognise, the fake raises
   ``NotImplementedError``. Project principle 19(b) — surface the gap
   rather than absorb it.
2. **No table-wide scans.** All access is by PK + optional SK; nothing
   in the Cycle workflow uses ``Scan`` and we do not want to encourage
   it accidentally.
3. **Single-key tables only.** Cycle (pk=cycleId) and Response
   (pk=cycleId, sk=employeeId) and TranscriptMeta (pk=cycleId,
   sk=employeeIdSeq) are configurable via the constructor.

The :class:`ConditionalCheckFailedError` is raised in the
``botocore.exceptions.ClientError`` shape the handler expects
(``error_response["Error"]["Code"] == "ConditionalCheckFailedException"``)
so we don't have to mutate handler code under test.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from botocore.exceptions import ClientError


def _conditional_check_failed(operation_name: str) -> ClientError:
    """Return a ``ClientError`` shaped like DynamoDB's check failure."""
    return ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "The conditional request failed",
            }
        },
        operation_name=operation_name,
    )


# ---------------------------------------------------------------------------
# Expression parsing
# ---------------------------------------------------------------------------


def _resolve_name(token: str, names: dict[str, str] | None) -> str:
    """Map a ``#alias`` to its attribute name; pass plain names through."""
    if token.startswith("#"):
        if names is None or token not in names:
            raise NotImplementedError(
                f"ExpressionAttributeNames missing alias {token!r}"
            )
        return names[token]
    return token


def _eval_condition(  # noqa: PLR0911 - leaf-by-leaf form keeps readability
    expr: str,
    item: dict[str, Any] | None,
    values: dict[str, Any],
    names: dict[str, str] | None,
) -> bool:
    """Evaluate a small subset of DynamoDB ConditionExpression syntax.

    Supported shapes (only what the Cycle Lambdas use):

    * ``attribute_not_exists(<attr>)``
    * ``attribute_exists(<attr>)``
    * ``<attr> = :val``
    * ``<expr> AND <expr>``
    * ``<expr> OR <expr>``

    ``<attr>`` may be a ``#alias`` resolved via ``names``.
    ``item`` is ``None`` when the row does not exist (e.g. a fresh
    ``put_item``); ``attribute_not_exists`` then returns ``True`` and
    ``attribute_exists`` returns ``False`` (matches DynamoDB's
    behavior for absent rows).
    """
    expr = expr.strip()

    # ``OR`` binds looser than ``AND`` — split on top-level OR first.
    or_parts = _split_top_level(expr, " OR ")
    if len(or_parts) > 1:
        return any(_eval_condition(p, item, values, names) for p in or_parts)

    and_parts = _split_top_level(expr, " AND ")
    if len(and_parts) > 1:
        return all(_eval_condition(p, item, values, names) for p in and_parts)

    # Leaf predicate.
    m = re.match(r"^attribute_not_exists\(\s*([#\w]+)\s*\)$", expr)
    if m:
        attr = _resolve_name(m.group(1), names)
        if item is None:
            return True
        return attr not in item

    m = re.match(r"^attribute_exists\(\s*([#\w]+)\s*\)$", expr)
    if m:
        attr = _resolve_name(m.group(1), names)
        if item is None:
            return False
        return attr in item

    m = re.match(r"^([#\w]+)\s*=\s*(:\w+)$", expr)
    if m:
        attr = _resolve_name(m.group(1), names)
        placeholder = m.group(2)
        if placeholder not in values:
            raise NotImplementedError(
                f"ExpressionAttributeValues missing {placeholder!r}"
            )
        if item is None:
            return False
        return item.get(attr) == values[placeholder]

    raise NotImplementedError(f"Unsupported ConditionExpression leaf: {expr!r}")


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split ``s`` on ``sep`` but respect parenthesis nesting depth."""
    parts: list[str] = []
    depth = 0
    last = 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and s[i : i + len(sep)] == sep:
            parts.append(s[last:i])
            last = i + len(sep)
            i += len(sep)
            continue
        i += 1
    parts.append(s[last:])
    return [p.strip() for p in parts if p.strip()]


def _apply_update_expression(  # noqa: PLR0912 - DynamoDB grammar is wide
    item: dict[str, Any],
    update_expr: str,
    values: dict[str, Any],
    names: dict[str, str] | None,
) -> dict[str, Any]:
    """Apply a tiny subset of DynamoDB UpdateExpression syntax.

    Supports a single ``SET`` clause (with comma-separated assignments)
    and a single ``ADD`` clause (numeric increments only).

    Examples handled (all observed in the Cycle Lambdas):

    * ``SET contactId = :cid, dispatchedAt = :now ADD callAttempts :one``
    * ``SET callResultCode = :code, endedAt = :now``
    * ``SET #s = :new, completedAt = :ca``
    """
    update_expr = update_expr.strip()
    # Tokenise into top-level ``SET ...`` and ``ADD ...`` segments.
    set_clause = None
    add_clause = None
    upper = update_expr.upper()
    set_idx = upper.find("SET ")
    add_idx = upper.find(" ADD ")
    if add_idx == -1:
        add_idx = upper.find("ADD ") if upper.startswith("ADD ") else -1
        if add_idx >= 0:
            # ADD as the only clause; SET absent.
            add_clause = update_expr[add_idx + 4 :].strip()
        elif set_idx >= 0:
            set_clause = update_expr[set_idx + 4 :].strip()
    elif set_idx >= 0 and set_idx < add_idx:
        set_clause = update_expr[set_idx + 4 : add_idx].strip()
        add_clause = update_expr[add_idx + 5 :].strip()
    elif add_idx >= 0:
        add_clause = update_expr[add_idx + 5 :].strip()

    if set_clause is None and add_clause is None:
        raise NotImplementedError(
            f"UpdateExpression must contain SET and/or ADD: {update_expr!r}"
        )

    if set_clause:
        for assignment in _split_top_level(set_clause, ", "):
            m = re.match(r"^([#\w]+)\s*=\s*(:\w+)$", assignment.strip())
            if not m:
                raise NotImplementedError(
                    f"Unsupported SET assignment shape: {assignment!r}"
                )
            attr = _resolve_name(m.group(1), names)
            placeholder = m.group(2)
            if placeholder not in values:
                raise NotImplementedError(
                    f"ExpressionAttributeValues missing {placeholder!r}"
                )
            item[attr] = values[placeholder]

    if add_clause:
        # ``ADD attr :val`` (single increment) — what the handlers use.
        m = re.match(r"^([#\w]+)\s+(:\w+)$", add_clause.strip())
        if not m:
            raise NotImplementedError(
                f"Unsupported ADD shape (only single increment): {add_clause!r}"
            )
        attr = _resolve_name(m.group(1), names)
        placeholder = m.group(2)
        if placeholder not in values:
            raise NotImplementedError(
                f"ExpressionAttributeValues missing {placeholder!r}"
            )
        delta = values[placeholder]
        if not isinstance(delta, (int, float)):
            raise NotImplementedError(
                f"ADD only supports numeric deltas; got {type(delta).__name__}"
            )
        current = item.get(attr, 0)
        item[attr] = current + delta

    return item


# ---------------------------------------------------------------------------
# Table fake
# ---------------------------------------------------------------------------


class InMemoryTable:
    """Threadsafe in-memory implementation of ``boto3.resource(...).Table``.

    Instances are keyed by ``(partition_key, sort_key | None)``. The
    sort key is optional: Cycle uses only ``cycleId``, Response uses
    ``(cycleId, employeeId)``, TranscriptMeta uses ``(cycleId,
    employeeIdSeq)``.
    """

    def __init__(self, partition_key: str, sort_key: str | None = None) -> None:
        self._partition_key = partition_key
        self._sort_key = sort_key
        self._items: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._lock = threading.RLock()

    # -- introspection ---------------------------------------------------

    def all_items(self) -> list[dict[str, Any]]:
        """Return a deep-copied snapshot of every item in the table."""
        with self._lock:
            return [dict(v) for v in self._items.values()]

    # -- key helpers -----------------------------------------------------

    def _make_key_tuple(self, key: dict[str, Any]) -> tuple[Any, ...]:
        pk = key[self._partition_key]
        if self._sort_key is None:
            return (pk,)
        sk = key[self._sort_key]
        return (pk, sk)

    # -- DynamoDB API surface -------------------------------------------

    def put_item(
        self,
        Item: dict[str, Any],  # noqa: N803 (DynamoDB API)
        ConditionExpression: str | None = None,  # noqa: N803
        ExpressionAttributeValues: dict[str, Any] | None = None,  # noqa: N803
        ExpressionAttributeNames: dict[str, str] | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        with self._lock:
            key_tuple = self._make_key_tuple(Item)
            existing = self._items.get(key_tuple)
            if ConditionExpression is not None:
                ok = _eval_condition(
                    ConditionExpression,
                    existing,
                    ExpressionAttributeValues or {},
                    ExpressionAttributeNames,
                )
                if not ok:
                    raise _conditional_check_failed("PutItem")
            self._items[key_tuple] = dict(Item)
            return {"Attributes": dict(Item)}

    def get_item(
        self,
        Key: dict[str, Any],  # noqa: N803 (DynamoDB API)
        ConsistentRead: bool | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        with self._lock:
            key_tuple = self._make_key_tuple(Key)
            item = self._items.get(key_tuple)
            if item is None:
                return {}
            return {"Item": dict(item)}

    def update_item(
        self,
        Key: dict[str, Any],  # noqa: N803 (DynamoDB API)
        UpdateExpression: str,  # noqa: N803
        ExpressionAttributeValues: dict[str, Any],  # noqa: N803
        ConditionExpression: str | None = None,  # noqa: N803
        ExpressionAttributeNames: dict[str, str] | None = None,  # noqa: N803
        **_unused: Any,
    ) -> dict[str, Any]:
        with self._lock:
            key_tuple = self._make_key_tuple(Key)
            existing = self._items.get(key_tuple)
            if ConditionExpression is not None:
                ok = _eval_condition(
                    ConditionExpression,
                    existing,
                    ExpressionAttributeValues,
                    ExpressionAttributeNames,
                )
                if not ok:
                    raise _conditional_check_failed("UpdateItem")
            # When the row does not exist, DynamoDB creates it with the
            # provided Key. Mirror that behaviour.
            if existing is None:
                existing = dict(Key)
            updated = _apply_update_expression(
                dict(existing),
                UpdateExpression,
                ExpressionAttributeValues,
                ExpressionAttributeNames,
            )
            # Ensure the key columns are not overwritten.
            updated[self._partition_key] = Key[self._partition_key]
            if self._sort_key is not None:
                updated[self._sort_key] = Key[self._sort_key]
            self._items[key_tuple] = updated
            return {"Attributes": dict(updated)}

    def query(
        self,
        KeyConditionExpression: str,  # noqa: N803 (DynamoDB API)
        ExpressionAttributeValues: dict[str, Any],  # noqa: N803
        ExclusiveStartKey: dict[str, Any] | None = None,  # noqa: N803
        **_unused: Any,
    ) -> dict[str, Any]:
        # Only support the ``<pk> = :cid`` form used in handlers.
        m = re.match(
            r"^\s*([#\w]+)\s*=\s*(:\w+)\s*$",
            KeyConditionExpression,
        )
        if m is None:
            raise NotImplementedError(
                f"Unsupported KeyConditionExpression: {KeyConditionExpression!r}"
            )
        pk_attr = m.group(1)
        placeholder = m.group(2)
        if pk_attr != self._partition_key:
            raise NotImplementedError(
                f"Query PK mismatch: got {pk_attr!r}, table PK is {self._partition_key!r}"
            )
        pk_value = ExpressionAttributeValues[placeholder]
        with self._lock:
            items = [
                dict(v)
                for k, v in self._items.items()
                if k[0] == pk_value
            ]
        # Sort by sort key for determinism (when present).
        if self._sort_key is not None:
            items.sort(key=lambda it: it.get(self._sort_key, ""))
        return {"Items": items}
