"""Cognito PostAuthentication Trigger — clear lockout history on success.

Cognito invokes this Lambda after a SUCCESSFUL authentication
(Cognito does NOT invoke any Lambda on auth FAILURE; the failure side
is recorded by the AuthFailureReporter API in Phase 5.6).

Responsibilities (Phase 3.5):
    1. Clear ``LockoutTable.failedAts`` and expire the TTL immediately
       so a subsequent lookup by AuthPreAuthFn sees an empty history.
    2. Emit a structured AUTH_SUCCESS audit log line to the consolidated
       ``AuditLogGroup`` via :func:`shared.audit.logger.write_audit_log`
       (Phase 12.3 rewiring of the previous per-Lambda audit pattern).

Property 21 requires the audit log to carry: (1) event type,
(2) UTC ISO 8601 timestamp, (3) principal, (4) target, (5) phone
numbers (none here). Requirement 1.8 additionally requires the
source IP — BUT the Cognito PostAuth event payload does NOT include
the caller's IP. Per user decision (α, session 6) we emit
``sourceIp: null`` in ``extra`` and accept the gap; Req 1.8 is therefore
not fully satisfied by this Lambda. See _progress.md history.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.audit.logger import write_audit_log

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

LOCKOUT_TABLE_NAME = os.environ["LOCKOUT_TABLE_NAME"]

_DDB = boto3.resource("dynamodb")
_TABLE = _DDB.Table(LOCKOUT_TABLE_NAME)


def _clear_lockout(user_identifier: str, now_epoch: int) -> None:
    """Reset failedAts to an empty list and expire the TTL immediately.

    If no LockoutTable entry exists for the user (no prior failures),
    the ConditionExpression intentionally suppresses item creation —
    we do not want to materialize empty rows.
    """
    try:
        _TABLE.update_item(
            Key={"userIdentifier": user_identifier},
            UpdateExpression="SET failedAts = :empty, expireAt = :expired",
            ConditionExpression="attribute_exists(userIdentifier)",
            ExpressionAttributeValues={
                ":empty": [],
                ":expired": now_epoch - 1,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            LOGGER.info("No lockout record to clear: user=%s", user_identifier)
            return
        LOGGER.error(
            "LockoutTable UpdateItem failed for user=%s: %s",
            user_identifier,
            exc,
        )
        raise


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Cognito PostAuthentication Trigger entry point.

    Returns the event unchanged. Cognito ignores the return value of
    PostAuthentication beyond the absence of an exception.

    Raises:
        ValueError: The event payload is malformed (no userName).
        ClientError: LockoutTable update failed (excluding the benign
            "no prior record" case, which is swallowed).
    """
    user_identifier = event.get("userName")
    if not isinstance(user_identifier, str) or not user_identifier:
        raise ValueError("event.userName is missing or not a non-empty string")

    now_epoch = int(time.time())
    _clear_lockout(user_identifier, now_epoch)

    # Phase 12.3: emit to AuditLogGroup. sourceIp: null reflects the
    # Cognito PostAuth event's lack of an IP field (Req 1.8 gap, α).
    write_audit_log(
        event_type="AUTH_SUCCESS",
        principal=user_identifier,
        target=user_identifier,
        extra={"sourceIp": None},
    )

    return event
