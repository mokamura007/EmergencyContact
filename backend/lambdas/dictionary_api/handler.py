"""DictionaryApi Lambda — keyword dictionary CRUD with version snapshots.

Implements tasks.md 4.1 / 4.3 (optimistic-lock side).

Routes (dispatched internally by httpMethod + resource path):
    GET    /keyword-dictionary                          -> list_all
    GET    /keyword-dictionary/version                  -> get_current_version
    POST   /keyword-dictionary                          -> create_keyword
    PATCH  /keyword-dictionary/{category}/{keyword}     -> update_keyword
    DELETE /keyword-dictionary/{category}/{keyword}     -> delete_keyword

Versioning model (design.md D7):
    - KeywordDictionary table holds the *current* set, plus one META record
      (PK=category="META", SK=keyword="META") whose `currentVersion`
      attribute is the latest version number.
    - Every write op (POST / PATCH / DELETE) atomically increments
      `currentVersion` via `ConditionExpression: currentVersion = :expected`.
      On mismatch, this Lambda returns HTTP 409 Conflict.
    - After the increment succeeds, the writer mutates the row and then
      snapshots the entire active set (every non-META row) into
      KeywordDictionaryHistory under the new version. KeywordMatcher in
      Phase 8 reads that snapshot via `getDictionarySnapshot(version)`
      (Phase 4.2).

Audit logging (Requirement 8.7 / Property 21):
    Each successful mutation emits a JSON line to the Lambda's default
    CloudWatch log group. Phase 12.3 will reroute this to AuditLogGroup.
    Self-correlation fields: event, principal, category, keyword,
    oldValue (for PATCH), newVersion, timestamp.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.api.cors import with_cors_headers
from shared.audit.logger import write_audit_log

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

DICT_TABLE_NAME = os.environ["KEYWORD_DICT_TABLE_NAME"]
HISTORY_TABLE_NAME = os.environ["KEYWORD_DICT_HISTORY_TABLE_NAME"]

VALID_CATEGORIES = frozenset({"SAFE", "INJURED", "UNAVAILABLE"})
META_PK = "META"
META_SK = "META"

_DDB = boto3.resource("dynamodb")
_DICT_TABLE = _DDB.Table(DICT_TABLE_NAME)
_HISTORY_TABLE = _DDB.Table(HISTORY_TABLE_NAME)


# --------- HTTP response helpers ---------


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": with_cors_headers(
            {"Content-Type": "application/json; charset=utf-8"}
        ),
        "body": json.dumps(body, ensure_ascii=False, default=_decimal_default),
    }


def _decimal_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _parse_body(body: str | None) -> dict[str, Any]:
    if not body:
        raise ValueError("Request body is required")
    try:
        loaded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON body: {exc.msg}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("Request body must be a JSON object")
    return loaded


def _now_iso() -> str:
    """Return the current instant as ISO 8601 UTC with trailing ``Z``.

    Phase 12.3 routed audit timestamps through
    :func:`shared.audit.logger.write_audit_log`; this helper survives
    in case future non-audit code paths need a UTC ISO string.
    """
    import datetime as _dt

    return (
        _dt.datetime.now(tz=_dt.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# --------- Versioning (optimistic lock, atomic increment) ---------


def _get_current_version() -> int:
    resp = _DICT_TABLE.get_item(Key={"category": META_PK, "keyword": META_SK})
    item = resp.get("Item")
    if item is None:
        return 0
    return int(str(item.get("currentVersion", 0)))


def _increment_version(expected: int) -> int:
    """Atomically bump META.currentVersion from `expected` to `expected + 1`.

    Raises ClientError(ConditionalCheckFailedException) if the current
    META.currentVersion differs from `expected` (concurrent modification).
    For the very first write (expected == 0) we accept either "META does
    not exist yet" or "META.currentVersion == 0".
    """
    new_version = expected + 1
    if expected == 0:
        condition = "attribute_not_exists(category) OR currentVersion = :expected"
    else:
        condition = "currentVersion = :expected"
    _DICT_TABLE.update_item(
        Key={"category": META_PK, "keyword": META_SK},
        UpdateExpression="SET currentVersion = :new",
        ConditionExpression=condition,
        ExpressionAttributeValues={":new": new_version, ":expected": expected},
    )
    return new_version


# --------- KeywordDictionary CRUD ---------


def _scan_all_active() -> list[dict[str, Any]]:
    """Return every non-META row in KeywordDictionary."""
    items: list[dict[str, Any]] = []
    last_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {}
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _DICT_TABLE.scan(**kwargs)
        for row in resp.get("Items", []):
            if row.get("category") != META_PK:
                items.append(row)
        last_key = resp.get("LastEvaluatedKey")
        if last_key is None:
            break
    return items


def _list_all() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {c: [] for c in VALID_CATEGORIES}
    for row in _scan_all_active():
        category = row.get("category")
        keyword = row.get("keyword")
        if isinstance(category, str) and category in VALID_CATEGORIES and isinstance(keyword, str):
            grouped[category].append(keyword)
    return grouped


def _write_history_snapshot(new_version: int) -> None:
    """Write a snapshot of every active keyword under PK=new_version."""
    active = _scan_all_active()
    with _HISTORY_TABLE.batch_writer() as batch:
        for row in active:
            category = row.get("category")
            keyword = row.get("keyword")
            if not isinstance(category, str) or not isinstance(keyword, str):
                continue
            batch.put_item(
                Item={
                    "version": new_version,
                    "categoryKeyword": f"{category}#{keyword}",
                    "category": category,
                    "keyword": keyword,
                }
            )


def _create_keyword(body: dict[str, Any], principal: str) -> dict[str, Any]:
    category = body.get("category")
    keyword = body.get("keyword")
    expected_version = body.get("expectedVersion")
    if not isinstance(category, str) or category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(VALID_CATEGORIES)}")
    if not isinstance(keyword, str) or not keyword:
        raise ValueError("keyword must be a non-empty string")
    if not isinstance(expected_version, int):
        raise ValueError("expectedVersion must be an integer")

    new_version = _increment_version(expected_version)
    _DICT_TABLE.put_item(
        Item={"category": category, "keyword": keyword, "version": new_version},
        ConditionExpression="attribute_not_exists(category)",
    )
    _write_history_snapshot(new_version)
    write_audit_log(
        event_type="DICTIONARY_ADD",
        principal=principal,
        target=f"{category}#{keyword}",
        extra={"category": category, "keyword": keyword, "newVersion": new_version},
    )
    return _response(201, {"category": category, "keyword": keyword, "version": new_version})


def _update_keyword(
    path_params: dict[str, Any], body: dict[str, Any], principal: str
) -> dict[str, Any]:
    category = path_params.get("category")
    keyword = path_params.get("keyword")
    expected_version = body.get("expectedVersion")
    if not isinstance(category, str) or category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(VALID_CATEGORIES)}")
    if not isinstance(keyword, str) or not keyword:
        raise ValueError("keyword path parameter must be a non-empty string")
    if not isinstance(expected_version, int):
        raise ValueError("expectedVersion must be an integer in the body")

    # PATCH semantics: refresh the row (e.g., touch the version stamp) but
    # do NOT mutate the (category, keyword) primary key. The existing row
    # must already exist.
    existing = _DICT_TABLE.get_item(Key={"category": category, "keyword": keyword}).get("Item")
    if existing is None:
        raise ValueError(f"Keyword not found: {category}#{keyword}")

    new_version = _increment_version(expected_version)
    _DICT_TABLE.update_item(
        Key={"category": category, "keyword": keyword},
        UpdateExpression="SET version = :v",
        ExpressionAttributeValues={":v": new_version},
    )
    _write_history_snapshot(new_version)
    write_audit_log(
        event_type="DICTIONARY_UPDATE",
        principal=principal,
        target=f"{category}#{keyword}",
        extra={
            "category": category,
            "keyword": keyword,
            "newVersion": new_version,
            "oldValue": {"version": int(str(existing.get("version", 0)))},
        },
    )
    return _response(200, {"category": category, "keyword": keyword, "version": new_version})


def _delete_keyword(
    path_params: dict[str, Any], body: dict[str, Any], principal: str
) -> dict[str, Any]:
    category = path_params.get("category")
    keyword = path_params.get("keyword")
    expected_version = body.get("expectedVersion")
    if not isinstance(category, str) or category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(VALID_CATEGORIES)}")
    if not isinstance(keyword, str) or not keyword:
        raise ValueError("keyword path parameter must be a non-empty string")
    if not isinstance(expected_version, int):
        raise ValueError("expectedVersion must be an integer in the body")

    new_version = _increment_version(expected_version)
    _DICT_TABLE.delete_item(
        Key={"category": category, "keyword": keyword},
        ConditionExpression="attribute_exists(category)",
    )
    _write_history_snapshot(new_version)
    write_audit_log(
        event_type="DICTIONARY_DELETE",
        principal=principal,
        target=f"{category}#{keyword}",
        extra={"category": category, "keyword": keyword, "newVersion": new_version},
    )
    return _response(200, {"category": category, "keyword": keyword, "version": new_version})


# --------- Entry point ---------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """API Gateway proxy event -> dispatch by (httpMethod, resource)."""
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    raw_body = event.get("body")
    principal = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
        .get("sub", "<unknown>")
    )

    try:
        if method == "GET" and resource == "/keyword-dictionary":
            return _response(200, _list_all())
        if method == "GET" and resource == "/keyword-dictionary/version":
            return _response(200, {"version": _get_current_version()})
        if method == "POST" and resource == "/keyword-dictionary":
            return _create_keyword(_parse_body(raw_body), principal)
        if method == "PATCH" and resource == "/keyword-dictionary/{category}/{keyword}":
            return _update_keyword(path_params, _parse_body(raw_body), principal)
        if method == "DELETE" and resource == "/keyword-dictionary/{category}/{keyword}":
            return _delete_keyword(path_params, _parse_body(raw_body), principal)
        return _response(405, {"error": f"Method {method} not allowed on {resource}"})
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.warning(
                "Optimistic lock conflict on %s %s: %s",
                method,
                resource,
                exc.response.get("Error", {}).get("Message", ""),
            )
            return _response(
                409,
                {
                    "error": "Concurrent modification detected; "
                    "refresh the dictionary version and retry"
                },
            )
        LOGGER.error("DynamoDB ClientError on %s %s: %s", method, resource, exc)
        raise
