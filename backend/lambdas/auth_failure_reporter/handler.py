"""AuthFailureReporter Lambda — SPA-driven authentication-failure recording.

Endpoint:
    POST /auth/record-failure   (PUBLIC, no Cognito Authorizer)

Background (see _progress.md, Phase 3.4 session 6 notes):
    Cognito User Pool does not invoke any Lambda Trigger on
    authentication failure. To enforce Requirement 1.6 (5 consecutive
    failures within 30 minutes -> lockout), the SPA detects InitiateAuth
    failures (`NotAuthorizedException` and similar) and calls this API
    to record the failure timestamp. AuthPreAuthFn (Phase 3.4) then
    reads the resulting LockoutTable.failedAts during the next login
    attempt to enforce the lock.

Threat-model note:
    A motivated attacker who calls Cognito directly (skipping the SPA)
    bypasses this recorder, leaving their failures uncounted. The
    Cognito service's built-in throttling plus an optional future move
    to Cognito Advanced Security are the mitigations; this Lambda is
    not the full solution.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.api.cors import with_cors_headers
from shared.audit.logger import write_audit_log

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

LOCKOUT_TABLE_NAME = os.environ["LOCKOUT_TABLE_NAME"]
LOCKOUT_WINDOW_SECONDS = int(os.environ.get("LOCKOUT_WINDOW_SECONDS", "1800"))

# RFC 5322 simplified email pattern. Cognito's email-as-username feature
# enforces a similar shape upstream.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

_DDB = boto3.resource("dynamodb")
_TABLE = _DDB.Table(LOCKOUT_TABLE_NAME)


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


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Record a single authentication-failure timestamp.

    Body:
        {"userIdentifier": "<email>"}

    Returns:
        202 Accepted on success (we deliberately do not echo whether
        the user exists in Cognito, to avoid enumeration leaks).
    """
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    if method != "POST" or resource != "/auth/record-failure":
        return _response(405, {"error": f"Method {method} not allowed on {resource}"})

    try:
        body = _parse_body(event.get("body"))
    except ValueError as exc:
        return _response(400, {"error": str(exc)})

    user_identifier = body.get("userIdentifier")
    if not isinstance(user_identifier, str) or not _EMAIL_RE.match(user_identifier):
        # Intentionally bland message — do not signal "user not found" vs
        # "format invalid" to a public endpoint.
        return _response(202, {"recorded": False})

    now_epoch = int(time.time())
    expire_at = now_epoch + LOCKOUT_WINDOW_SECONDS

    try:
        _TABLE.update_item(
            Key={"userIdentifier": user_identifier},
            UpdateExpression=(
                "SET failedAts = list_append(if_not_exists(failedAts, :empty), :ts), "
                "expireAt = :exp"
            ),
            ExpressionAttributeValues={
                ":empty": [],
                ":ts": [now_epoch],
                ":exp": expire_at,
            },
        )
    except ClientError as exc:
        LOGGER.error(
            "LockoutTable UpdateItem failed for user=%s: %s", user_identifier, exc
        )
        # Public endpoint — return a generic 202 to avoid leaking failure
        # mode. The error is captured in CloudWatch Logs for ops review.
        return _response(202, {"recorded": False})

    LOGGER.info(
        "Auth failure recorded for user=%s expireAt=%s", user_identifier, expire_at
    )
    # Phase 12.3: emit to AuditLogGroup. principal is "<anonymous>" because
    # this is a public endpoint — the actor isn't authenticated.
    write_audit_log(
        event_type="AUTH_FAILURE_RECORDED",
        principal="<anonymous>",
        target=user_identifier,
        outcome="RECORDED",
        extra={"expireAt": expire_at, "epochTimestamp": now_epoch},
    )
    return _response(202, {"recorded": True})
