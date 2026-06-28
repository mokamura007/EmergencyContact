"""Cognito PreAuthentication Trigger — account lockout enforcement.

Implements the **lock-judgement half** of Requirement 1.6 (5 consecutive
auth failures within 30 minutes locks the account for 30 minutes) by
reading LockoutTable before Cognito evaluates the user's password.

Scope split (Phase 3.4 revision, 2026-06-25 session 6):
    - This handler: read-only `is_locked` check. Raises when locked.
    - Phase 5.6 AuthFailureReporter API: SPA-driven `list_append` of
      failure timestamps (Cognito does not expose a failure-time Lambda
      Trigger, so the recording side runs outside Cognito).
    - Phase 3.5 PostAuthFn: clears `failedAts` on successful login.

When this handler raises, Cognito reports the auth attempt as failed.
With `PreventUserExistenceErrors=ENABLED` on the App Client (Phase 3.2),
the SPA only sees the generic "Incorrect username or password" error.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, cast

import boto3
from botocore.exceptions import ClientError

from shared.auth.lockout import is_locked

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

LOCKOUT_TABLE_NAME = os.environ["LOCKOUT_TABLE_NAME"]
LOCKOUT_THRESHOLD = int(os.environ.get("LOCKOUT_THRESHOLD", "5"))
LOCKOUT_WINDOW_SECONDS = int(os.environ.get("LOCKOUT_WINDOW_SECONDS", "1800"))

_DDB = boto3.resource("dynamodb")
_TABLE = _DDB.Table(LOCKOUT_TABLE_NAME)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Cognito PreAuthentication Trigger entry point.

    Returns the event unchanged when the account is not locked, which
    tells Cognito to proceed with the standard password check.

    Raises:
        ValueError: The event payload is malformed (no userName).
        ClientError: LockoutTable read failed; propagated so CloudWatch
            captures the underlying DynamoDB error and Cognito records
            the attempt as failed.
        PermissionError: The account is currently locked. Cognito will
            surface this as an authentication failure.
    """
    user_identifier = event.get("userName")
    if not isinstance(user_identifier, str) or not user_identifier:
        raise ValueError("event.userName is missing or not a non-empty string")

    now_epoch = int(time.time())

    try:
        response = _TABLE.get_item(Key={"userIdentifier": user_identifier})
    except ClientError as exc:
        LOGGER.error(
            "LockoutTable GetItem failed for user=%s: %s", user_identifier, exc
        )
        raise

    item = response.get("Item")
    failed_ats: list[int] = []
    if item is not None:
        raw_failed = cast(list[Any], item.get("failedAts", []))
        failed_ats = [int(v) for v in raw_failed]

    if is_locked(
        failed_ats,
        now_epoch,
        threshold=LOCKOUT_THRESHOLD,
        window_sec=LOCKOUT_WINDOW_SECONDS,
    ):
        LOGGER.warning(
            "Account is locked: user=%s failed_count=%d",
            user_identifier,
            len(failed_ats),
        )
        raise PermissionError(
            "Account is locked due to repeated authentication failures."
        )

    LOGGER.info("PreAuth lockout check passed: user=%s", user_identifier)
    return event
