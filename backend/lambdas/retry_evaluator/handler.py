"""RetryEvaluator Lambda — decide whether to redial (Phase 6.5).

Invoked from the Step Functions ``EvaluateRetry`` state with the
current Response snapshot and Cycle retry parameters. Returns a
decision payload that branches the SFN into either ``WaitInterval`` →
``Dispatch`` (retry) or ``FinalizeOne`` (terminate with a final
status).

All real logic lives in :mod:`shared.retry.evaluator` so the same
functions can be tested under Hypothesis at Phase 13.12 / 13.13
without going through this handler. This module is intentionally
**stateless and side-effect-free** — no DynamoDB, no SFN client, no
KMS, no boto3 at all — because every input the function needs is
supplied by the SFN as part of the state input. Per project principle
19(b), input-shape errors raise ``ValueError`` directly.

Input shape (from SFN ``EvaluateRetry`` state)::

    {
        "cycleId":              "<uuid>",
        "employeeId":           "<uuid>",
        "voiceStatus":          "PENDING|SAFE|INJURED|UNAVAILABLE|OTHER|UNREACHABLE",
        "callResultCode":       "RECORDED|NO_ANSWER|BUSY|VOICEMAIL|ERROR|TRANSCRIBE_FAILED|INBOUND" | null,
        "attempts":             <int, >= 0>,
        "retryCount":           <int, 0..5>,
        "retryIntervalMinutes": <int, 1..60>,
        "prevEndAt":            "<ISO 8601 with Z or +00:00>"
    }

Output shape (consumed by SFN choice on ``$.retry``)::

    retry == true:
        {
            "retry":            True,
            "retryWaitSeconds": <int, >= 0>,
            "nextDispatchAt":   "<ISO 8601 with Z>",
            "finalStatus":      None
        }

    retry == false:
        {
            "retry":            False,
            "retryWaitSeconds": 0,
            "nextDispatchAt":   None,
            "finalStatus":      "SAFE" | "INJURED" | "UNAVAILABLE" | "UNREACHABLE"
        }
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from shared.retry.evaluator import (
    compute_next_dispatch_at,
    compute_retry_wait_seconds,
    derive_final_status,
    should_retry,
)

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

#: Required keys on the input event. ``callResultCode`` is allowed to
#: be ``None`` so it's checked separately.
_REQUIRED_KEYS: tuple[str, ...] = (
    "cycleId",
    "employeeId",
    "voiceStatus",
    "attempts",
    "retryCount",
    "retryIntervalMinutes",
    "prevEndAt",
)


def _utc_now_iso() -> str:
    """Return ``datetime.now(UTC)`` formatted as ISO 8601 with ``Z``."""
    return (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _parse_event(
    event: dict[str, Any],
) -> tuple[str, str, str, str | None, int, int, int, str]:
    """Validate the event and return its fields as a positional tuple.

    Returns:
        ``(cycleId, employeeId, voiceStatus, callResultCode, attempts,
        retryCount, retryIntervalMinutes, prevEndAt)``

    Raises:
        ValueError: missing required key, wrong primitive type, or
            invalid value (downstream pure functions raise on their
            own domain errors as well).
    """
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")

    missing = [k for k in _REQUIRED_KEYS if k not in event]
    if missing:
        raise ValueError(f"event missing required keys: {missing}")

    cycle_id = event["cycleId"]
    employee_id = event["employeeId"]
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("cycleId must be a non-empty string")
    if not isinstance(employee_id, str) or not employee_id:
        raise ValueError("employeeId must be a non-empty string")

    voice_status = event["voiceStatus"]
    call_result_code = event.get("callResultCode")  # may be None
    if call_result_code is not None and not isinstance(call_result_code, str):
        raise ValueError(
            "callResultCode must be a string or null; "
            f"got {type(call_result_code).__name__}"
        )

    attempts = event["attempts"]
    retry_count = event["retryCount"]
    retry_interval_minutes = event["retryIntervalMinutes"]
    prev_end_at = event["prevEndAt"]

    return (
        cycle_id,
        employee_id,
        voice_status,
        call_result_code,
        attempts,
        retry_count,
        retry_interval_minutes,
        prev_end_at,
    )


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Decide retry-vs-finalize for the current employee in the cycle.

    See module docstring for input/output shapes.
    """
    (
        cycle_id,
        employee_id,
        voice_status,
        call_result_code,
        attempts,
        retry_count,
        retry_interval_minutes,
        prev_end_at,
    ) = _parse_event(event)

    LOGGER.info(
        "RetryEvaluator start cycleId=%s employeeId=%s voiceStatus=%s "
        "callResultCode=%s attempts=%s retryCount=%s "
        "retryIntervalMinutes=%s prevEndAt=%s",
        cycle_id,
        employee_id,
        voice_status,
        call_result_code,
        attempts,
        retry_count,
        retry_interval_minutes,
        prev_end_at,
    )

    retry = should_retry(voice_status, call_result_code, attempts, retry_count)

    if retry:
        next_dispatch_at = compute_next_dispatch_at(
            prev_end_at, retry_interval_minutes
        )
        wait_seconds = compute_retry_wait_seconds(
            prev_end_at, retry_interval_minutes, _utc_now_iso()
        )
        result: dict[str, Any] = {
            "retry": True,
            "retryWaitSeconds": wait_seconds,
            "nextDispatchAt": next_dispatch_at,
            "finalStatus": None,
        }
    else:
        final_status = derive_final_status(voice_status)
        result = {
            "retry": False,
            "retryWaitSeconds": 0,
            "nextDispatchAt": None,
            "finalStatus": final_status,
        }

    LOGGER.info(
        "RetryEvaluator done cycleId=%s employeeId=%s result=%s",
        cycle_id,
        employee_id,
        result,
    )
    return result
