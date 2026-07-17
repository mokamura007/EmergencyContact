"""ResponseApi Lambda — read-only access to cycle response history.

Routes:
    GET /cycles/{id}/responses
        Returns paginated Response items for a cycle (50 per page,
        startedAt-equivalent ordering). Each row carries the resolved
        employee name and a transcript excerpt where available.
    GET /cycles/{id}/responses/{employeeId}
        Returns a single Response item with full transcript excerpt.

Phase 5.4 scope (Requirement 12.1, Property 1):
    Read-only. Authorization is enforced upstream by the Cognito
    Authorizer + Administrator group claim check. This Lambda assumes
    the request has already passed authorization.

Transcript excerpt note:
    Transcript JSON bodies live in S3 (TranscriptsBucket) and are
    parsed by KeywordMatcher (Phase 8). At Phase 5.4 we expose the
    text excerpt that KeywordMatcher writes back into
    TranscriptMetadata (`excerpt` attribute). If not yet populated
    (Phase 5 deployment without Phase 8), the excerpt is null.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.api.cors import with_cors_headers

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]
EMPLOYEE_TABLE_NAME = os.environ["EMPLOYEE_TABLE_NAME"]
TRANSCRIPT_META_TABLE_NAME = os.environ["TRANSCRIPT_META_TABLE_NAME"]
INBOUND_CONTACT_TABLE_NAME = os.environ.get("INBOUND_CONTACT_TABLE_NAME", "")

PAGE_SIZE = 50

_DDB = boto3.resource("dynamodb")
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)
_EMPLOYEE_TABLE = _DDB.Table(EMPLOYEE_TABLE_NAME)
_TRANSCRIPT_META_TABLE = _DDB.Table(TRANSCRIPT_META_TABLE_NAME)
_INBOUND_CONTACT_TABLE = _DDB.Table(INBOUND_CONTACT_TABLE_NAME) if INBOUND_CONTACT_TABLE_NAME else None


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": with_cors_headers(
            {"Content-Type": "application/json; charset=utf-8"}
        ),
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def _resolve_employee_name(employee_id: str) -> str | None:
    if not employee_id:
        return None
    try:
        item = _EMPLOYEE_TABLE.get_item(Key={"employeeId": employee_id}).get("Item")
    except ClientError as exc:
        LOGGER.warning("Employee lookup failed for %s: %s", employee_id, exc)
        return None
    if item is None:
        return None
    name = item.get("name")
    return name if isinstance(name, str) else None


def _latest_transcript_excerpt(cycle_id: str, employee_id: str) -> str | None:
    """Find the most-recent transcript meta entry for (cycleId, employeeId)
    and return its excerpt field if populated.

    SK is `{employeeId}#{seq}` per design.md D6 schema, so we query with
    `begins_with` on the composite SK and pick the lexicographically
    last item (highest seq).
    """
    try:
        resp = _TRANSCRIPT_META_TABLE.query(
            KeyConditionExpression=(
                Key("cycleId").eq(cycle_id)
                & Key("employeeIdSeq").begins_with(f"{employee_id}#")
            ),
            ScanIndexForward=False,
            Limit=1,
        )
    except ClientError as exc:
        LOGGER.warning(
            "Transcript meta lookup failed for cycle=%s employee=%s: %s",
            cycle_id,
            employee_id,
            exc,
        )
        return None
    items = resp.get("Items", [])
    if not items:
        return None
    excerpt = items[0].get("excerpt")
    return excerpt if isinstance(excerpt, str) else None


def _list_responses(path_params: dict[str, Any], query_params: dict[str, Any]) -> dict[str, Any]:
    cycle_id = path_params.get("id")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("id path parameter is required")
    start_key_raw = query_params.get("nextToken")

    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("cycleId").eq(cycle_id),
        "Limit": PAGE_SIZE,
        "ScanIndexForward": True,
    }
    if isinstance(start_key_raw, str) and start_key_raw:
        try:
            kwargs["ExclusiveStartKey"] = json.loads(start_key_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid nextToken: {exc.msg}") from exc

    resp = _RESPONSE_TABLE.query(**kwargs)
    items: list[dict[str, Any]] = []
    for row in resp.get("Items", []):
        employee_id = str(row.get("employeeId", ""))
        excerpt = _latest_transcript_excerpt(cycle_id, employee_id) if employee_id else None
        items.append(
            {
                "cycleId": row.get("cycleId"),
                "employeeId": employee_id,
                "employeeName": _resolve_employee_name(employee_id),
                "voiceStatus": row.get("voiceStatus"),
                "callResultCode": row.get("callResultCode"),
                "retryCount": int(str(row.get("retryCount", 0))),
                "lastCalledAt": row.get("lastCalledAt"),
                "finalizedAt": row.get("finalizedAt"),
                "transcriptExcerpt": excerpt,
            }
        )

    next_token = None
    last_key = resp.get("LastEvaluatedKey")
    if last_key is not None:
        next_token = json.dumps(last_key, default=str)

    return _response(200, {"items": items, "pageSize": PAGE_SIZE, "nextToken": next_token})


def _get_single_response(path_params: dict[str, Any]) -> dict[str, Any]:
    cycle_id = path_params.get("id")
    employee_id = path_params.get("employeeId")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("id path parameter is required")
    if not isinstance(employee_id, str) or not employee_id:
        raise ValueError("employeeId path parameter is required")

    item = _RESPONSE_TABLE.get_item(
        Key={"cycleId": cycle_id, "employeeId": employee_id}
    ).get("Item")
    if item is None:
        return _response(404, {"error": f"Response not found: {cycle_id}/{employee_id}"})

    return _response(
        200,
        {
            "cycleId": item.get("cycleId"),
            "employeeId": item.get("employeeId"),
            "employeeName": _resolve_employee_name(employee_id),
            "voiceStatus": item.get("voiceStatus"),
            "callResultCode": item.get("callResultCode"),
            "retryCount": int(str(item.get("retryCount", 0))),
            "lastCalledAt": item.get("lastCalledAt"),
            "finalizedAt": item.get("finalizedAt"),
            "transcriptExcerpt": _latest_transcript_excerpt(cycle_id, employee_id),
        },
    )


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    try:
        if method == "GET" and resource == "/cycles/{id}/responses":
            return _list_responses(path_params, query_params)
        if method == "GET" and resource == "/cycles/{id}/responses/{employeeId}":
            return _get_single_response(path_params)
        if method == "GET" and resource == "/inbound":
            return _list_inbound_contacts(query_params)
        return _response(405, {"error": f"Method {method} not allowed on {resource}"})
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except ClientError as exc:
        LOGGER.error("ClientError on %s %s: %s", method, resource, exc)
        raise


def _list_inbound_contacts(query_params: dict[str, Any]) -> dict[str, Any]:
    """Return paginated InboundContact items (receivedAt descending).

    Uses Scan with ExclusiveStartKey for pagination. InboundContact table
    has contactId as PK. For proper ordering we would need a GSI on
    receivedAt, but for now we scan and sort in-memory (acceptable for
    small datasets in dev). In production, a GSI should be added.
    """
    if _INBOUND_CONTACT_TABLE is None:
        return _response(500, {"error": "INBOUND_CONTACT_TABLE_NAME not configured"})

    next_token_raw = query_params.get("nextToken")
    kwargs: dict[str, Any] = {"Limit": PAGE_SIZE}
    if isinstance(next_token_raw, str) and next_token_raw:
        try:
            kwargs["ExclusiveStartKey"] = json.loads(next_token_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid nextToken: {exc.msg}") from exc

    resp = _INBOUND_CONTACT_TABLE.scan(**kwargs)
    raw_items = resp.get("Items", [])

    # Sort by receivedAt descending
    raw_items.sort(key=lambda x: x.get("receivedAt", ""), reverse=True)

    items: list[dict[str, Any]] = []
    for row in raw_items:
        employee_id = row.get("employeeId")
        employee_name = _resolve_employee_name(employee_id) if employee_id else None
        items.append({
            "contactId": row.get("contactId"),
            "receivedAt": row.get("receivedAt"),
            "callerNumberMasked": row.get("callerNumberMasked", "***"),
            "cycleId": row.get("cycleId"),
            "employeeName": employee_name,
            "flow": row.get("flow"),
            "voiceStatus": row.get("voiceStatus"),
            "transcriptExcerpt": row.get("transcriptExcerpt"),
        })

    next_token = None
    last_key = resp.get("LastEvaluatedKey")
    if last_key is not None:
        next_token = json.dumps(last_key, default=str)

    return _response(200, {"items": items, "pageSize": PAGE_SIZE, "nextToken": next_token})
