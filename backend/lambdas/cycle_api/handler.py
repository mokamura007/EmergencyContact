"""CycleApi Lambda — cycle creation and status retrieval (Requirements 4.x).

Routes:
    POST /cycles                Create a new cycle (idempotent via Idempotency-Key)
    GET  /cycles                List all cycles, newest first
    GET  /cycles/{id}           Get cycle detail
    GET  /cycles/{id}/status    Return {summary, items, degraded}

POST flow:
    1. Parse body: mode (ALL|UNREACHABLE_ONLY), retryCount [0,5],
       retryIntervalMinutes [1,60], optional referencedCycleId.
    2. Read Idempotency-Key header (optional).
    3. Look up an existing RUNNING cycle via StatusStartedAtIndex:
         - Found, key matches  -> return existing (200 idempotent replay)
         - Found, key differs  -> 409 Conflict
         - Not found           -> continue.
    4. Snapshot the dictionary version (META.currentVersion).
    5. Put a Cycle item with status=RUNNING.
    6. Call SFN StartExecution. On failure, set status=START_FAILED
       and return 5xx (Requirement 4.11).

Phase 5.3 SFN linkage:
    SFN_STATE_MACHINE_ARN points at the eventual Phase 6 StateMachine
    name. The IAM Policy mirrors that ARN pattern. Calls will fail
    with ResourceNotFoundException until Phase 6 creates the machine.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import uuid
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.api.cors import with_cors_headers
from shared.audit.logger import write_audit_log
from shared.cycle.exclusivity import can_start_cycle
from shared.dictionary.active_count import is_dictionary_empty

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

CYCLE_TABLE_NAME = os.environ["CYCLE_TABLE_NAME"]
KEYWORD_DICT_TABLE_NAME = os.environ["KEYWORD_DICT_TABLE_NAME"]
SFN_STATE_MACHINE_ARN = os.environ["SFN_STATE_MACHINE_ARN"]

# Phase 15.27a: Retry defaults are injected via CFn Parameters
# (DefaultRetryCount / DefaultRetryIntervalMinutes) on the Lambda env.
# API body fields ``retryCount`` / ``retryIntervalMinutes`` override these
# values when supplied (backward compatible). Per principle 19(b),
# malformed env values surface ``ValueError`` from ``int()`` directly —
# no silent fallback.
DEFAULT_RETRY_COUNT = int(os.environ.get("DEFAULT_RETRY_COUNT", "3"))
DEFAULT_RETRY_INTERVAL_MINUTES = int(
    os.environ.get("DEFAULT_RETRY_INTERVAL_MINUTES", "5")
)

VALID_MODES = frozenset({"ALL", "UNREACHABLE_ONLY"})
ACTIVE_CATEGORIES: tuple[str, ...] = ("SAFE", "INJURED", "UNAVAILABLE")

_DDB = boto3.resource("dynamodb")
_CYCLE_TABLE = _DDB.Table(CYCLE_TABLE_NAME)
_DICT_TABLE = _DDB.Table(KEYWORD_DICT_TABLE_NAME)
_SFN = boto3.client("stepfunctions")


# --- Helpers ---


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": with_cors_headers(
            {"Content-Type": "application/json; charset=utf-8"}
        ),
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def _parse_body(raw: str | None) -> dict[str, Any]:
    if not raw:
        raise ValueError("Request body is required")
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON body: {exc.msg}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("Request body must be a JSON object")
    return loaded


def _now_iso() -> str:
    return (
        dt.datetime.now(tz=dt.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _get_dict_version() -> int:
    """Read META.currentVersion from KeywordDictionary table (0 if META absent)."""
    resp = _DICT_TABLE.get_item(Key={"category": "META", "keyword": "META"})
    item = resp.get("Item")
    if item is None:
        return 0
    return int(str(item.get("currentVersion", 0)))


def _query_category(category: str) -> list[dict[str, Any]]:
    """Query a single active category from KeywordDictionary (Limit=1).

    Only the *existence* of at least one keyword per category matters for
    the empty-dictionary guard, so ``Limit=1`` keeps the read cost
    constant regardless of dictionary size. The handler then forwards
    the three returned ``Items`` lists to ``is_dictionary_empty`` for
    the actual decision.
    """
    resp = _DICT_TABLE.query(
        KeyConditionExpression=Key("category").eq(category),
        Limit=1,
    )
    return list(resp.get("Items", []))


def _is_active_dictionary_empty() -> bool:
    """Sequentially probe SAFE / INJURED / UNAVAILABLE and decide emptiness.

    Per Requirement 8.6, a Cycle may only start when at least one active
    keyword exists across the three categories. Sequential (not
    parallel) Query is intentional: it avoids piling concurrent reads
    on a single partition key when the dictionary is small.
    """
    safe_items = _query_category("SAFE")
    injured_items = _query_category("INJURED")
    unavailable_items = _query_category("UNAVAILABLE")
    return is_dictionary_empty(safe_items, injured_items, unavailable_items)


def _query_running_cycles() -> list[dict[str, Any]]:
    """Return RUNNING cycles from ``StatusStartedAtIndex`` (Limit=1).

    The exclusivity *decision* itself is delegated to
    :func:`shared.cycle.exclusivity.can_start_cycle` (Property 9,
    Phase 13.9): this function is only responsible for the DynamoDB
    Query I/O — the pure function decides whether the resulting list
    permits a fresh cycle start. ``Limit=1`` is sufficient because the
    decision only needs to know whether *at least one* row exists; the
    caller can recover the colliding row from index 0 for the
    Idempotency-Key replay / 409 branches.
    """
    resp = _CYCLE_TABLE.query(
        IndexName="StatusStartedAtIndex",
        KeyConditionExpression=Key("status").eq("RUNNING"),
        ScanIndexForward=False,
        Limit=1,
    )
    return list(resp.get("Items", []))


def _idempotency_key_from(headers: dict[str, str]) -> str:
    # API Gateway header names can be either case; check both.
    return headers.get("Idempotency-Key") or headers.get("idempotency-key") or ""


def _principal_from(event: dict[str, Any]) -> str:
    """Extract Cognito ``sub`` for audit logs; ``<unknown>`` otherwise."""
    return str(
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
        .get("sub", "<unknown>")
    )


# --- Route handlers ---


def _create_cycle(
    body: dict[str, Any], headers: dict[str, str], principal: str
) -> dict[str, Any]:
    mode = body.get("mode")
    retry_count = body.get("retryCount", DEFAULT_RETRY_COUNT)
    retry_interval = body.get("retryIntervalMinutes", DEFAULT_RETRY_INTERVAL_MINUTES)
    referenced_cycle_id = body.get("referencedCycleId")
    idempotency_key = _idempotency_key_from(headers)

    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    if not isinstance(retry_count, int) or retry_count < 0 or retry_count > 5:
        raise ValueError("retryCount must be an integer in [0, 5]")
    if not isinstance(retry_interval, int) or retry_interval < 1 or retry_interval > 60:
        raise ValueError("retryIntervalMinutes must be an integer in [1, 60]")

    # --- Requirement 8.6: refuse to start a cycle when the active dictionary
    # is empty across all three categories (SAFE / INJURED / UNAVAILABLE).
    # Phase 4.4: strict (B) interpretation — count by querying every active
    # category and demand a total >= 1.
    if _is_active_dictionary_empty():
        write_audit_log(
            event_type="CYCLE_START_REJECTED",
            principal=principal,
            target="dictionary_empty",
            outcome="REJECTED",
            extra={
                "reason": "dictionary_empty",
                "categories": list(ACTIVE_CATEGORIES),
            },
        )
        return _response(
            400,
            {
                "error": (
                    "Active dictionary is empty. "
                    "Add at least one keyword before starting a cycle."
                )
            },
        )

    existing_cycles = _query_running_cycles()
    if not can_start_cycle(existing_cycles):
        existing = existing_cycles[0]
        existing_key = str(existing.get("idempotencyKey", ""))
        if idempotency_key and existing_key == idempotency_key:
            return _response(
                200,
                {
                    "cycleId": existing.get("cycleId"),
                    "status": existing.get("status"),
                    "startedAt": existing.get("startedAt"),
                    "dictionaryVersion": int(existing.get("dictionaryVersion", 0)),
                    "idempotentReplay": True,
                },
            )
        write_audit_log(
            event_type="CYCLE_START_REJECTED",
            principal=principal,
            target=str(existing.get("cycleId", "<unknown>")),
            outcome="REJECTED",
            extra={
                "reason": "cycle_running",
                "existingCycleId": existing.get("cycleId"),
            },
        )
        return _response(409, {"error": "Another cycle is already RUNNING"})

    dict_version = _get_dict_version()
    cycle_id = str(uuid.uuid4())
    started_at = _now_iso()

    item: dict[str, Any] = {
        "cycleId": cycle_id,
        "status": "RUNNING",
        "mode": mode,
        "retryCount": retry_count,
        "retryIntervalMinutes": retry_interval,
        "dictionaryVersion": dict_version,
        "startedAt": started_at,
    }
    if idempotency_key:
        item["idempotencyKey"] = idempotency_key
    if isinstance(referenced_cycle_id, str) and referenced_cycle_id:
        item["referencedCycleId"] = referenced_cycle_id
    _CYCLE_TABLE.put_item(Item=item)

    sfn_input: dict[str, Any] = {
        "cycleId": cycle_id,
        "mode": mode,
        "retryCount": retry_count,
        "retryIntervalMinutes": retry_interval,
        "dictionaryVersion": dict_version,
        # Phase 17.1: SFN ASL の NormalizeInput Pass state が
        # ``States.JsonMerge`` で ``referencedCycleId`` に null defaults
        # を補完するようになったため、本行は **二重防御** として保持する。
        # SFN ASL 側の単独修正で機能するが、CycleApi 側でも null を
        # 明示しておくと、ASL を最小構成にロールバックした場合でも
        # ``States.Runtime`` を回避できる（多層防御）。
        # 削除する場合は SFN ASL 側 NormalizeInput state が確実に
        # 動作することを deploy 後に確認すること。
        "referencedCycleId": None,
    }
    if isinstance(referenced_cycle_id, str) and referenced_cycle_id:
        sfn_input["referencedCycleId"] = referenced_cycle_id

    try:
        _SFN.start_execution(
            stateMachineArn=SFN_STATE_MACHINE_ARN,
            name=f"cycle-{cycle_id}",
            input=json.dumps(sfn_input),
        )
    except ClientError as exc:
        LOGGER.error("SFN StartExecution failed for cycle %s: %s", cycle_id, exc)
        _CYCLE_TABLE.update_item(
            Key={"cycleId": cycle_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "START_FAILED"},
        )
        return _response(
            500,
            {
                "error": "SFN StartExecution failed",
                "cycleId": cycle_id,
                "cause": exc.response.get("Error", {}).get("Message", ""),
            },
        )

    write_audit_log(
        event_type="CYCLE_START",
        principal=principal,
        target=cycle_id,
        extra={
            "mode": mode,
            "retryCount": retry_count,
            "retryIntervalMinutes": retry_interval,
            "dictionaryVersion": dict_version,
        },
    )
    return _response(
        201,
        {
            "cycleId": cycle_id,
            "status": "RUNNING",
            "mode": mode,
            "startedAt": started_at,
            "dictionaryVersion": dict_version,
        },
    )


def _list_cycles() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    last_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {}
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _CYCLE_TABLE.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if last_key is None:
            break
    items.sort(key=lambda x: str(x.get("startedAt", "")), reverse=True)
    summary = [
        {
            "cycleId": it.get("cycleId"),
            "status": it.get("status"),
            "mode": it.get("mode"),
            "startedAt": it.get("startedAt"),
            "completedAt": it.get("completedAt"),
            "dictionaryVersion": int(it.get("dictionaryVersion", 0)),
        }
        for it in items
    ]
    return _response(200, {"cycles": summary, "total": len(summary)})


def _get_cycle_detail(path_params: dict[str, Any]) -> dict[str, Any]:
    cycle_id = path_params.get("id")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("id path parameter is required")
    item = _CYCLE_TABLE.get_item(Key={"cycleId": cycle_id}).get("Item")
    if item is None:
        return _response(404, {"error": f"Cycle not found: {cycle_id}"})
    return _response(200, item)


def _get_cycle_status(path_params: dict[str, Any]) -> dict[str, Any]:
    """Return {cycleId, status, summary, items, degraded}.

    Phase 10.6 contract: フロントエンドの CycleStatusSnapshot 型に合わせた形式。
    summary には targetTotal / dispatched / responded / unreachable / byStatus を返す。
    Response テーブルからの集計は Phase 16+ で追加予定。現状は Cycle アイテムの
    targetCount を targetTotal として使い、items は空配列。
    """
    cycle_id = path_params.get("id")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("id path parameter is required")
    cycle = _CYCLE_TABLE.get_item(Key={"cycleId": cycle_id}).get("Item")
    if cycle is None:
        return _response(404, {"error": f"Cycle not found: {cycle_id}"})

    target_count = int(str(cycle.get("targetCount", 0)))
    status = str(cycle.get("status", "RUNNING"))

    result = {
        "cycleId": cycle.get("cycleId"),
        "status": status,
        "summary": {
            "targetTotal": target_count,
            "dispatched": 0,
            "responded": 0,
            "unreachable": 0,
            "byStatus": {
                "SAFE": 0,
                "INJURED": 0,
                "UNAVAILABLE": 0,
                "OTHER": 0,
                "UNREACHABLE": 0,
                "PENDING": target_count,
            },
        },
        "items": [],
        "degraded": [],
    }
    return _response(200, result)


# --- Entry point ---


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    headers = event.get("headers") or {}
    raw_body = event.get("body")
    principal = _principal_from(event)

    try:
        if method == "POST" and resource == "/cycles":
            return _create_cycle(_parse_body(raw_body), headers, principal)
        if method == "GET" and resource == "/cycles":
            return _list_cycles()
        if method == "GET" and resource == "/cycles/{id}":
            return _get_cycle_detail(path_params)
        if method == "GET" and resource == "/cycles/{id}/status":
            return _get_cycle_status(path_params)
        return _response(405, {"error": f"Method {method} not allowed on {resource}"})
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except ClientError as exc:
        LOGGER.error("ClientError on %s %s: %s", method, resource, exc)
        raise
