"""EmployeeApi Lambda — employee master CRUD + CSV import + anonymize.

Routes (API Gateway Proxy event):
    POST   /employees                       create_employee  (with optional Cognito admin)
    GET    /employees                       list_employees   (?includeDeleted=true to
                                            include logically-deleted rows; Task 15.16)
    GET    /employees/{id}                  get_employee
    PUT    /employees/{id}                  update_employee
    DELETE /employees/{id}                  delete_employee  (logical delete + phone null-out)
    POST   /employees/import                import_csv       (TransactWriteItems 25/batch)
    POST   /employees/{id}/anonymize        anonymize_employee  (Task 15.12: irreversibly
                                            replace past Cycle Response employeeId values
                                            with SHA-256 hash on personal request)
    DELETE /employees/{id}/cognito-user     delete_cognito_user (Task 15.16: Administrator-
                                            only Cognito admin-delete-user for retired
                                            admin accounts; target must be logically
                                            deleted AND have a cognitoSub attribute)

Cognito linkage:
    POST /employees with body.isAdmin=true triggers Cognito user creation
    BEFORE the DynamoDB write (Requirement 2.2 — "Cognito first, DynamoDB
    second"). On Cognito failure, no DynamoDB write happens. On DynamoDB
    failure AFTER Cognito create, the Cognito user is left in place
    (design.md does not require automatic rollback; manual cleanup is
    an operational concern).

Audit log emission:
    Each successful mutation emits a JSON line to the Lambda's default
    CloudWatch log group. Phone numbers are passed through `mask_phone`
    (Property 22). Phase 12.3 will reroute these to AuditLogGroup.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.api.cors import with_cors_headers
from shared.audit.logger import write_audit_log
from shared.auth.authorization import ADMINISTRATOR_GROUP, is_authorized
from shared.employee.csv_parser import parse_employee_csv
from shared.employee.validate import (
    domestic_to_e164,
    is_valid_domestic_jp,
    is_valid_e164,
    is_valid_email,
    is_valid_name,
)
from shared.privacy import ANONYMIZED_ID_PREFIX, anonymize_employee_id

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.type_defs import TransactWriteItemTypeDef

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

EMPLOYEE_TABLE_NAME = os.environ["EMPLOYEE_TABLE_NAME"]
COGNITO_USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
ADMIN_GROUP_NAME = os.environ.get("ADMIN_GROUP_NAME", "Administrator")
# Task 15.12: Cycle / Response tables for the anonymize handler. Read at
# module-import time per Phase 5 convention; the salt is read at call time
# (see _anonymize_employee) so an empty value can fail-fast with 503.
CYCLE_TABLE_NAME = os.environ["CYCLE_TABLE_NAME"]
RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]

TRANSACT_MAX_ITEMS = 25  # DynamoDB hard limit per TransactWriteItems request
# Each Response anonymization is a (Delete old, Put new) pair = 2 transact
# operations, so the per-batch row limit is half the per-request item cap.
ANONYMIZE_ROWS_PER_BATCH = TRANSACT_MAX_ITEMS // 2

_DDB_RESOURCE = boto3.resource("dynamodb")
_DDB_CLIENT = boto3.client("dynamodb")
_TABLE = _DDB_RESOURCE.Table(EMPLOYEE_TABLE_NAME)
_CYCLE_TABLE = _DDB_RESOURCE.Table(CYCLE_TABLE_NAME)
_RESPONSE_TABLE = _DDB_RESOURCE.Table(RESPONSE_TABLE_NAME)
_COGNITO = boto3.client("cognito-idp")


# --------- Response / parsing helpers ---------


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


def _principal(event: dict[str, Any]) -> str:
    return str(
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
        .get("sub", "<unknown>")
    )


def _audit(event_type: str, principal: str, employee_id: str, phone: str | None = None) -> None:
    """Emit one audit record via the consolidated Phase 12.3 logger.

    ``phone`` is forwarded as-is — the shared logger applies
    :func:`shared.audit.mask.mask_phone` internally before serialising.
    """
    write_audit_log(
        event_type=event_type,
        principal=principal,
        target=employee_id,
        phone=phone,
    )


# --------- DynamoDB helpers ---------


def _phone_already_registered(phone: str) -> bool:
    """Check the PhoneNumberIndex GSI for an active row with this phone."""
    resp = _TABLE.query(
        IndexName="PhoneNumberIndex",
        KeyConditionExpression="phoneNumber = :p",
        ExpressionAttributeValues={":p": phone},
    )
    for item in resp.get("Items", []):
        if not item.get("deleted", False):
            return True
    return False


def _get_employee(employee_id: str) -> dict[str, Any] | None:
    return _TABLE.get_item(Key={"employeeId": employee_id}).get("Item")


# --------- Cognito helpers ---------


def _create_cognito_admin(email: str, name: str) -> str:
    """Create a Cognito admin user and add them to the Administrator group.

    Cognito generates a temporary password and emails an invitation.
    Returns the new user's `sub` claim (empty string if not present in
    the response, which would be unusual).
    """
    create_resp = _COGNITO.admin_create_user(
        UserPoolId=COGNITO_USER_POOL_ID,
        Username=email,
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
            {"Name": "name", "Value": name},
        ],
        DesiredDeliveryMediums=["EMAIL"],
    )
    _COGNITO.admin_add_user_to_group(
        UserPoolId=COGNITO_USER_POOL_ID,
        Username=email,
        GroupName=ADMIN_GROUP_NAME,
    )
    for attr in create_resp.get("User", {}).get("Attributes", []):
        if attr.get("Name") == "sub":
            return str(attr.get("Value", ""))
    return ""


# --------- Route handlers ---------


def _create_employee(body: dict[str, Any], principal: str) -> dict[str, Any]:
    name = body.get("name")
    phone = body.get("phoneNumber")
    is_admin = bool(body.get("isAdmin", False))
    admin_email = body.get("adminEmail")

    if not is_valid_name(name):
        raise ValueError("name is required: non-empty string up to 100 chars")
    if not isinstance(phone, str) or (not is_valid_e164(phone) and not is_valid_domestic_jp(phone)):
        raise ValueError("phoneNumber must be domestic JP format (0 + 9-10 digits) or E.164")
    if is_admin and not is_valid_email(admin_email):
        raise ValueError(
            "adminEmail is required and must be a valid email address "
            "(RFC 5322 simplified) when isAdmin=true"
        )

    # mypy: validators above guarantee these are str
    assert isinstance(name, str)
    assert isinstance(phone, str)

    # Normalize to E.164 for storage
    phone = domestic_to_e164(phone)

    if _phone_already_registered(phone):
        return _response(409, {"error": "Phone number already registered"})

    employee_id = str(uuid.uuid4())
    cognito_sub = ""

    # Requirement 2.2: Cognito first, DynamoDB second.
    if is_admin:
        assert isinstance(admin_email, str)
        try:
            cognito_sub = _create_cognito_admin(admin_email, name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "UsernameExistsException":
                return _response(409, {"error": "Admin email already registered in Cognito"})
            LOGGER.error("Cognito admin_create_user failed: %s", exc)
            raise  # Per design.md: no DynamoDB write on Cognito failure.

    now_iso = _now_iso()
    item: dict[str, Any] = {
        "employeeId": employee_id,
        "name": name,
        "phoneNumber": phone,
        "role": "admin" if is_admin else "employee",
        "deleted": False,
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }
    if cognito_sub:
        item["cognitoSub"] = cognito_sub
    _TABLE.put_item(Item=item)
    _audit("EMPLOYEE_ADD", principal, employee_id, phone)
    # Admin-registration side effect: emit an independent audit event
    # symmetric with ``COGNITO_USER_DELETE`` (Task 15.16), so the
    # Cognito user lifecycle can be queried per event in AuditLogGroup.
    # ``target`` is the plain employee_id (same convention as
    # ``COGNITO_USER_DELETE``); ``cognitoSub`` is captured in ``extra``
    # for traceability without leaking the raw email into audit records.
    if is_admin and cognito_sub:
        write_audit_log(
            event_type="COGNITO_USER_CREATE",
            principal=principal,
            target=employee_id,
            outcome="SUCCESS",
            extra={"cognitoSub": cognito_sub},
        )
    return _response(
        201,
        {
            "employeeId": employee_id,
            "name": name,
            "phoneNumber": phone,
            "isAdmin": is_admin,
        },
    )


def _list_employees(event: dict[str, Any]) -> dict[str, Any]:
    """GET /employees handler.

    Query parameters:
        includeDeleted ("true" | absent) — when ``true`` the response
            includes logically-deleted rows with ``deleted: true`` in
            each entry. Used by the SPA Employee List page to expose
            the Cognito-delete flow for retired admins (Task 15.16).
            Any other value (or absence) preserves the legacy active-
            only behaviour for backward compatibility.
    """
    qsp = event.get("queryStringParameters") or {}
    include_deleted = str(qsp.get("includeDeleted", "")).lower() == "true"

    items: list[dict[str, Any]] = []
    last_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {}
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _TABLE.scan(**kwargs)
        for it in resp.get("Items", []):
            row_deleted = bool(it.get("deleted", False))
            if row_deleted and not include_deleted:
                continue
            entry: dict[str, Any] = {
                "employeeId": it.get("employeeId"),
                "name": it.get("name"),
                "phoneNumber": it.get("phoneNumber"),
                "isAdmin": it.get("role") == "admin",
                "deleted": row_deleted,
            }
            items.append(entry)
        last_key = resp.get("LastEvaluatedKey")
        if last_key is None:
            break
    return _response(200, {"employees": items, "total": len(items)})


def _get_employee_by_id(path_params: dict[str, Any]) -> dict[str, Any]:
    employee_id = path_params.get("id")
    if not isinstance(employee_id, str) or not employee_id:
        raise ValueError("id path parameter is required")
    item = _get_employee(employee_id)
    if item is None or item.get("deleted", False):
        return _response(404, {"error": f"Employee not found: {employee_id}"})
    return _response(
        200,
        {
            "employeeId": item.get("employeeId"),
            "name": item.get("name"),
            "phoneNumber": item.get("phoneNumber"),
            "isAdmin": item.get("role") == "admin",
            "createdAt": item.get("createdAt"),
            "updatedAt": item.get("updatedAt"),
        },
    )


def _update_employee(
    path_params: dict[str, Any], body: dict[str, Any], principal: str
) -> dict[str, Any]:
    employee_id = path_params.get("id")
    if not isinstance(employee_id, str) or not employee_id:
        raise ValueError("id path parameter is required")
    existing = _get_employee(employee_id)
    if existing is None or existing.get("deleted", False):
        return _response(404, {"error": f"Employee not found: {employee_id}"})

    new_name = body.get("name", existing.get("name"))
    new_phone = body.get("phoneNumber", existing.get("phoneNumber"))
    if not is_valid_name(new_name):
        raise ValueError("name is invalid")
    if not isinstance(new_phone, str) or (not is_valid_e164(new_phone) and not is_valid_domestic_jp(new_phone)):
        raise ValueError("phoneNumber is invalid")
    assert isinstance(new_name, str)
    assert isinstance(new_phone, str)

    # Normalize to E.164 for storage
    new_phone = domestic_to_e164(new_phone)

    if new_phone != existing.get("phoneNumber") and _phone_already_registered(new_phone):
        return _response(409, {"error": "Phone number already registered"})

    _TABLE.update_item(
        Key={"employeeId": employee_id},
        UpdateExpression="SET #n = :n, phoneNumber = :p, updatedAt = :u",
        ExpressionAttributeNames={"#n": "name"},
        ExpressionAttributeValues={
            ":n": new_name,
            ":p": new_phone,
            ":u": _now_iso(),
        },
    )
    _audit("EMPLOYEE_UPDATE", principal, employee_id, new_phone)
    return _response(
        200,
        {"employeeId": employee_id, "name": new_name, "phoneNumber": new_phone},
    )


def _delete_employee(path_params: dict[str, Any], principal: str) -> dict[str, Any]:
    employee_id = path_params.get("id")
    if not isinstance(employee_id, str) or not employee_id:
        raise ValueError("id path parameter is required")
    existing = _get_employee(employee_id)
    if existing is None or existing.get("deleted", False):
        return _response(404, {"error": f"Employee not found: {employee_id}"})

    # Logical delete + phone NULL-out (Requirement 15.3, Property 20).
    # Setting phoneNumber to a sentinel "" disables the PhoneNumberIndex GSI
    # entry, so the deleted row no longer participates in inbound lookups.
    _TABLE.update_item(
        Key={"employeeId": employee_id},
        UpdateExpression=(
            "SET deleted = :d, phoneNumber = :nullPhone, updatedAt = :u "
            "REMOVE cognitoSub"
        ),
        ExpressionAttributeValues={
            ":d": True,
            ":nullPhone": "",
            ":u": _now_iso(),
        },
    )
    _audit("EMPLOYEE_DELETE", principal, employee_id, existing.get("phoneNumber"))
    return _response(200, {"employeeId": employee_id, "deleted": True})


def _import_csv(body: dict[str, Any], principal: str) -> dict[str, Any]:
    raw_b64 = body.get("csvBase64")
    if not isinstance(raw_b64, str) or not raw_b64:
        raise ValueError("csvBase64 is required (base64-encoded CSV content)")
    try:
        raw_bytes = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        return _response(400, {"error": f"csvBase64 is not valid base64: {exc}"})

    parsed = parse_employee_csv(raw_bytes)
    if parsed.errors:
        return _response(
            400,
            {
                "imported": 0,
                "attempted": len(parsed.rows) + len(parsed.errors),
                "errors": [{"line": e.line, "reason": e.reason} for e in parsed.errors],
            },
        )

    # Check duplicate against the live master.
    duplicates: list[str] = []
    for row in parsed.rows:
        if _phone_already_registered(row.phone_number):
            duplicates.append(row.phone_number)
    if duplicates:
        return _response(
            409,
            {
                "imported": 0,
                "attempted": len(parsed.rows),
                "errors": [
                    {"reason": f"Phone already registered: {p}"} for p in sorted(set(duplicates))
                ],
            },
        )

    # Build TransactWriteItems batches (25 per batch).
    now_iso = _now_iso()
    inserted_phones: list[str] = []
    try:
        for i in range(0, len(parsed.rows), TRANSACT_MAX_ITEMS):
            slice_rows = parsed.rows[i : i + TRANSACT_MAX_ITEMS]
            transact_items: list[TransactWriteItemTypeDef] = [
                {
                    "Put": {
                        "TableName": EMPLOYEE_TABLE_NAME,
                        "Item": {
                            "employeeId": {"S": str(uuid.uuid4())},
                            "name": {"S": row.name},
                            "phoneNumber": {"S": row.phone_number},
                            "role": {"S": "employee"},
                            "deleted": {"BOOL": False},
                            "createdAt": {"S": now_iso},
                            "updatedAt": {"S": now_iso},
                        },
                    }
                }
                for row in slice_rows
            ]
            _DDB_CLIENT.transact_write_items(TransactItems=transact_items)
            inserted_phones.extend(r.phone_number for r in slice_rows)
    except ClientError as exc:
        # Requirement 3.7: roll back partially-inserted rows.
        LOGGER.error(
            "CSV import transact failed after %d rows: %s", len(inserted_phones), exc
        )
        _rollback_inserted(inserted_phones)
        return _response(
            500,
            {
                "imported": 0,
                "attempted": len(parsed.rows),
                "errors": [
                    {
                        "reason": "DynamoDB transact failed; rolled back: "
                        + str(exc.response.get("Error", {}).get("Message", "")),
                    }
                ],
            },
        )

    _audit(
        "EMPLOYEE_CSV_IMPORT",
        principal,
        f"<batch:{len(inserted_phones)}rows>",
        None,
    )
    return _response(
        201,
        {"imported": len(inserted_phones), "attempted": len(parsed.rows), "errors": []},
    )


def _rollback_inserted(phones: list[str]) -> None:
    """Best-effort delete of rows inserted by earlier batches in a failed import."""
    for phone in phones:
        try:
            resp = _TABLE.query(
                IndexName="PhoneNumberIndex",
                KeyConditionExpression="phoneNumber = :p",
                ExpressionAttributeValues={":p": phone},
            )
            for it in resp.get("Items", []):
                _TABLE.delete_item(Key={"employeeId": it["employeeId"]})
        except ClientError as cleanup_exc:
            LOGGER.error("Rollback delete failed for %s: %s", phone, cleanup_exc)


# --------- Anonymize (Task 15.12) ---------


def _list_all_cycle_ids() -> list[str]:
    """Enumerate every cycleId in the CycleTable via Scan.

    The CycleTable PK is ``cycleId`` only, so a Scan with a projection
    expression on that single attribute is the simplest way to obtain
    the full list. The anonymize endpoint is triggered only on a
    personal-data request (GDPR Right to Erasure etc.) — frequency is
    very low, so the Scan cost is negligible compared to the
    correctness gain of avoiding a Scan of the much-larger Response
    table.
    """
    cycle_ids: list[str] = []
    last_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {"ProjectionExpression": "cycleId"}
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _CYCLE_TABLE.scan(**kwargs)
        for item in resp.get("Items", []):
            cid = item.get("cycleId")
            if isinstance(cid, str) and cid:
                cycle_ids.append(cid)
        last_key = resp.get("LastEvaluatedKey")
        if last_key is None:
            break
    return cycle_ids


def _query_responses_for_employee(
    cycle_id: str, employee_id: str
) -> list[dict[str, Any]]:
    """Query Response rows for one (cycleId, employeeId) pair.

    ResponseTable PK is ``cycleId``, SK is ``employeeId`` — there is at
    most one row per pair, but Query is used (not GetItem) to align
    with the boto3 condition-builder shape that the rest of the
    codebase uses.
    """
    resp = _RESPONSE_TABLE.query(
        KeyConditionExpression=Key("cycleId").eq(cycle_id)
        & Key("employeeId").eq(employee_id),
    )
    return list(resp.get("Items", []))


def _build_anonymize_transact_items(
    rows: list[dict[str, Any]], anonymized_id: str
) -> list[dict[str, Any]]:
    """Build a list of TransactWriteItems (Delete old + Put new pairs).

    DynamoDB composite-key tables do not allow in-place SK mutation, so
    each row must be deleted and re-inserted under the anonymized SK.
    The pair is wrapped in a TransactWriteItems batch so the read-back
    state never observes a half-written row.
    """
    items: list[dict[str, Any]] = []
    for row in rows:
        cycle_id = row["cycleId"]
        original_id = row["employeeId"]
        new_row = dict(row)
        new_row["employeeId"] = anonymized_id
        items.append(
            {
                "Delete": {
                    "TableName": RESPONSE_TABLE_NAME,
                    "Key": {
                        "cycleId": {"S": cycle_id},
                        "employeeId": {"S": original_id},
                    },
                }
            }
        )
        items.append(
            {
                "Put": {
                    "TableName": RESPONSE_TABLE_NAME,
                    "Item": _marshal_response_item(new_row),
                }
            }
        )
    return items


def _marshal_response_item(item: dict[str, Any]) -> dict[str, Any]:
    """Marshal a plain-Python Response row into the boto3 client (low-level) shape.

    The ``boto3.resource("dynamodb").Table.query`` returns rows where
    values are already deserialised (str / int / Decimal / bool / dict /
    list). The ``boto3.client("dynamodb").transact_write_items`` API
    requires the AttributeValue wire format ({"S": ...}, {"N": ...}
    etc.), so we re-serialise each value here. The function recognises
    the shapes the production code actually writes; any unexpected
    shape raises ``TypeError`` so we don't silently truncate data.
    """
    out: dict[str, Any] = {}
    for key, value in item.items():
        out[key] = _marshal_attribute_value(value)
    return out


def _marshal_attribute_value(value: Any) -> dict[str, Any]:
    """Convert a single Python value into a DynamoDB AttributeValue dict.

    Supports: str, bool, int, Decimal, bytes, list, set, dict, None.
    Distinguishes bool from int because ``isinstance(True, int)`` is
    True in Python.
    """
    from decimal import Decimal

    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, (int, Decimal)):
        return {"N": str(value)}
    if isinstance(value, float):
        # DynamoDB rejects native floats; the production code only
        # writes Decimal, so float here means a contract drift.
        raise TypeError(
            "float values cannot be marshalled to DynamoDB; use Decimal"
        )
    if isinstance(value, bytes):
        return {"B": value}
    if isinstance(value, list):
        return {"L": [_marshal_attribute_value(v) for v in value]}
    if isinstance(value, set):
        # Empty set is invalid per DynamoDB; non-empty sets must be
        # homogeneous. Production code does not write sets to Response,
        # so we reject defensively.
        raise TypeError("set values not supported by anonymize marshalling")
    if isinstance(value, dict):
        return {"M": {k: _marshal_attribute_value(v) for k, v in value.items()}}
    raise TypeError(
        f"anonymize marshalling: unsupported value type {type(value).__name__}"
    )


def _anonymize_employee(
    path_params: dict[str, Any], event: dict[str, Any], principal: str
) -> dict[str, Any]:
    """POST /employees/{id}/anonymize handler (Task 15.12).

    Replaces every Response row's ``employeeId`` for the given employee
    with an irreversible SHA-256-based hash. The target employee must
    already be logically deleted (``deleted=true``) — anonymize is the
    *third* step of the GDPR-style personal-data deletion runbook
    (privacy.md §6.3), not a substitute for the logical delete itself.

    Authorization:
        Administrator group required. Cognito Authorizer at the API GW
        layer only verifies the JWT; group enforcement is the Lambda's
        responsibility per template.yaml comments on the Administrator
        group.

    Status codes:
        200 — success; body contains employeeId / anonymizedId /
              responseCountUpdated.
        400 — id path parameter missing.
        403 — caller is not in the Administrator group.
        404 — target employee not found.
        409 — target employee is NOT logically deleted (deleted != true)
              — anonymize is gated on the §6.3 step 2 (logical delete)
              having already happened.
        503 — ``EMPLOYEE_ANONYMIZE_SALT`` env var missing / empty.
              Fail-fast: anonymizing with a wrong / unset salt would
              irreversibly orphan Response rows.
    """
    # ----- 1. Salt presence check (fail-fast). -----
    salt = os.environ.get("EMPLOYEE_ANONYMIZE_SALT", "")
    if not salt:
        LOGGER.error(
            "EMPLOYEE_ANONYMIZE_SALT is unset or empty; refusing to anonymize"
        )
        return _response(
            503,
            {
                "error": "Anonymization is unavailable: salt is not configured.",
            },
        )

    # ----- 2. Administrator authorization. -----
    claims = (
        event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    )
    if not is_authorized(claims, ADMINISTRATOR_GROUP):
        return _response(403, {"error": "Administrator group required"})

    # ----- 3. Path parameter validation. -----
    employee_id = path_params.get("id")
    if not isinstance(employee_id, str) or not employee_id:
        raise ValueError("id path parameter is required")

    # ----- 4. Verify target exists AND is logically deleted. -----
    existing = _get_employee(employee_id)
    if existing is None:
        return _response(404, {"error": f"Employee not found: {employee_id}"})
    if not existing.get("deleted", False):
        return _response(
            409,
            {
                "error": (
                    "Employee must be logically deleted before anonymize "
                    "(privacy.md §6.3 step 2 must precede step 3)"
                ),
            },
        )

    # ----- 5. Compute the anonymized ID. -----
    anonymized_id = anonymize_employee_id(employee_id, salt)

    # ----- 6. Enumerate cycles, query Response per cycle, batch update. -----
    cycle_ids = _list_all_cycle_ids()
    rows_to_rewrite: list[dict[str, Any]] = []
    for cid in cycle_ids:
        rows_to_rewrite.extend(_query_responses_for_employee(cid, employee_id))

    response_count_updated = len(rows_to_rewrite)

    if response_count_updated > 0:
        # Each row -> 2 transact ops (Delete + Put), so batch in chunks
        # of ANONYMIZE_ROWS_PER_BATCH rows = 25 ops max.
        for start in range(0, response_count_updated, ANONYMIZE_ROWS_PER_BATCH):
            chunk = rows_to_rewrite[start : start + ANONYMIZE_ROWS_PER_BATCH]
            transact_items = _build_anonymize_transact_items(chunk, anonymized_id)
            _DDB_CLIENT.transact_write_items(TransactItems=transact_items)

    # ----- 7. Audit log (EMPLOYEE_ANONYMIZE). -----
    # IMPORTANT: ``target`` carries the ORIGINAL employee_id, NOT the
    # anonymized one — recording both would make the audit log itself a
    # reverse-lookup channel and defeat the irreversibility contract.
    # Only ``anonymizedIdPrefix`` (the constant "ANON_") is logged as a
    # marker; the actual hash is never captured.
    write_audit_log(
        event_type="EMPLOYEE_ANONYMIZE",
        principal=principal,
        target=employee_id,
        outcome="SUCCESS",
        extra={
            "responseCountUpdated": response_count_updated,
            "anonymizedIdPrefix": ANONYMIZED_ID_PREFIX,
        },
    )

    return _response(
        200,
        {
            "employeeId": employee_id,
            "anonymizedId": anonymized_id,
            "responseCountUpdated": response_count_updated,
        },
    )


# --------- Cognito user delete (Task 15.16) ---------


def _delete_cognito_user(
    path_params: dict[str, Any], event: dict[str, Any], principal: str
) -> dict[str, Any]:
    """DELETE /employees/{id}/cognito-user handler (Task 15.16).

    Removes the Cognito user associated with a retired admin employee
    via ``admin_delete_user`` and REMOVEs the ``cognitoSub`` attribute
    from the Employee row to prevent re-deletion / stale references.

    Pre-conditions enforced in order (fail-fast):
        1. Caller is in the Administrator group -> else 403.
        2. Employee record exists -> else 404.
        3. Record is logically deleted (``deleted=true``) -> else 409.
           Cognito deletion is the *final* step of the retirement
           runbook (privacy.md §6.1), gated on the logical-delete
           having already happened.
        4. Record has a non-empty ``cognitoSub`` attribute -> else 404
           (employees who were never admins have no Cognito user to
           delete; treat as "not found" rather than 409 to keep the
           SPA UI simple — the Cognito-delete button is only shown
           for rows that DO have a Cognito user).

    Cognito side-effect (irreversible):
        ``admin_delete_user(UserPoolId, Username=<cognitoSub>)``. Per
        AWS docs, Username may be either the email alias or the sub
        attribute; the sub form is preferred because it is the stable
        identifier we already persist.

    DynamoDB side-effect:
        ``UpdateExpression="REMOVE cognitoSub"`` on the Employee row
        so a redeploy-recreate of the Cognito user pool does not
        accidentally re-link a new sub to the deleted record.

    Audit log:
        Emits a ``COGNITO_USER_DELETE`` event with ``target`` = the
        plain employee_id (anonymization is a *separate* task — Task
        15.12 — and is gated on a different runbook). The deleted
        Cognito sub is recorded in ``extra.cognitoSubDeleted`` for
        traceability; this is acceptable because the sub is a
        Cognito-internal opaque identifier that does not by itself
        permit linking back to personal data once both Cognito and
        Employee.cognitoSub have been deleted.
    """
    # ----- 1. Administrator authorization. -----
    claims = (
        event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    )
    if not is_authorized(claims, ADMINISTRATOR_GROUP):
        return _response(403, {"error": "Administrator group required"})

    # ----- 2. Path parameter validation. -----
    employee_id = path_params.get("id")
    if not isinstance(employee_id, str) or not employee_id:
        raise ValueError("id path parameter is required")

    # ----- 3. Employee record existence. -----
    existing = _get_employee(employee_id)
    if existing is None:
        return _response(404, {"error": f"Employee not found: {employee_id}"})

    # ----- 4. Logical-delete pre-condition (privacy.md §6.1 step 2 must precede). -----
    if not existing.get("deleted", False):
        return _response(
            409,
            {
                "error": (
                    "Employee must be logically deleted before "
                    "Cognito user deletion (privacy.md §6.1)"
                ),
            },
        )

    # ----- 5. Cognito linkage pre-condition. -----
    cognito_sub = existing.get("cognitoSub")
    if not isinstance(cognito_sub, str) or not cognito_sub:
        return _response(
            404,
            {
                "error": (
                    f"No Cognito user linked to employee: {employee_id}"
                ),
            },
        )

    # ----- 6. Cognito admin_delete_user (irreversible). -----
    try:
        _COGNITO.admin_delete_user(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=cognito_sub,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "UserNotFoundException":
            # The Cognito user is already gone (manual cleanup, prior
            # call). Still clear the Employee.cognitoSub to keep state
            # consistent — the audit record will document the no-op.
            LOGGER.warning(
                "Cognito user already absent for employee %s (sub=%s)",
                employee_id,
                cognito_sub,
            )
        else:
            LOGGER.error(
                "Cognito admin_delete_user failed for %s: %s", employee_id, exc
            )
            raise

    # ----- 7. Remove cognitoSub from the Employee row. -----
    _TABLE.update_item(
        Key={"employeeId": employee_id},
        UpdateExpression="REMOVE cognitoSub SET updatedAt = :u",
        ExpressionAttributeValues={":u": _now_iso()},
    )

    # ----- 8. Audit log. -----
    write_audit_log(
        event_type="COGNITO_USER_DELETE",
        principal=principal,
        target=employee_id,
        outcome="SUCCESS",
        extra={"cognitoSubDeleted": cognito_sub},
    )

    return _response(
        200,
        {
            "employeeId": employee_id,
            "cognitoUserDeleted": True,
        },
    )


# --------- Entry point ---------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    raw_body = event.get("body")
    principal = _principal(event)

    try:
        if method == "POST" and resource == "/employees":
            return _create_employee(_parse_body(raw_body), principal)
        if method == "GET" and resource == "/employees":
            return _list_employees(event)
        if method == "GET" and resource == "/employees/{id}":
            return _get_employee_by_id(path_params)
        if method == "PUT" and resource == "/employees/{id}":
            return _update_employee(path_params, _parse_body(raw_body), principal)
        if method == "DELETE" and resource == "/employees/{id}":
            return _delete_employee(path_params, principal)
        if method == "POST" and resource == "/employees/import":
            return _import_csv(_parse_body(raw_body), principal)
        if method == "POST" and resource == "/employees/{id}/anonymize":
            return _anonymize_employee(path_params, event, principal)
        if method == "DELETE" and resource == "/employees/{id}/cognito-user":
            return _delete_cognito_user(path_params, event, principal)
        return _response(405, {"error": f"Method {method} not allowed on {resource}"})
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ConditionalCheckFailedException", "TransactionCanceledException"):
            return _response(
                409, {"error": "Conflict (concurrent modification or constraint violation)"}
            )
        LOGGER.error("ClientError on %s %s: %s", method, resource, exc)
        raise
