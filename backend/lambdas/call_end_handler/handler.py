"""CallEndHandler Lambda — finalise one outbound call (Phase 6.3).

Invoked from the terminal ``Invoke AWS Lambda function`` block of the
Outbound Contact Flow (Phase 7.1). Responsible for two things:

1. **Persisting the call outcome.** Updates the Response row with
   ``callResultCode`` and ``endedAt``. The update is guarded by a
   ``ConditionExpression`` so a late retry (Connect occasionally
   invokes the terminal Lambda twice on transient network blips) does
   **not** overwrite the first successfully-recorded result.

2. **Releasing the Step Functions task token.** The Map iteration's
   ``Dispatch`` state in the cycle SFN (Phase 6.8) is configured as
   ``.waitForTaskToken``. Until ``SendTaskSuccess`` fires here, that
   state stays blocked. The output payload signals ``retry=false`` so
   ``EvaluateRetry`` (Phase 6.5) downstream can read ``callResultCode``
   and decide the final per-employee status.

Input event (from Outbound Contact Flow's ``Invoke AWS Lambda function``
block — the canonical shape produced by Amazon Connect):

    {
        "Details": {
            "ContactData": {
                "ContactId": "<connect-contact-id>",
                "Attributes": {
                    "cycleId":    "<uuid>",
                    "employeeId": "<uuid>",
                    "attempt":    "1",         # Contact Attributes are strings
                    "taskToken":  "<sfn-task-token>"
                },
                ...
            },
            "Parameters": {
                "callResultCode": "RECORDED"   # from LambdaInvocationAttributes
            }
        },
        "Name": "ContactFlowEvent"
    }

The handler also accepts a *flat* legacy event shape — historically
used in unit tests and during the Phase 6.3 build before Phase 7.1
finalised the Contact Flow JSON:

    {
        "contactId":      "<connect-contact-id>",
        "cycleId":        "<uuid>",
        "employeeId":     "<uuid>",
        "attempt":        "1",
        "callResultCode": "RECORDED",
        "taskToken":      "<sfn-task-token>"
    }

The Phase 7.3 ``_normalize_connect_event`` step turns the Connect
nested shape into the flat shape and the rest of the handler then
processes a single, canonical structure. There is no silent fallback:
if the event matches neither shape, ``_parse_event`` raises
``ValueError`` (project principle 19(b)).

Design choices recorded for Phase 6.3:

* ``callAttempts`` is **not** incremented here. Phase 6.2
  ``ConnectDispatcher`` already does ``ADD callAttempts :one`` when the
  outbound call is successfully dispatched; adding again here would
  double-count. CallEndHandler therefore only ``SET`` s the terminal
  fields (``callResultCode``, ``endedAt``).

* ``ConditionExpression`` is the simple
  ``attribute_not_exists(callResultCode)`` form. The ERROR-sentinel
  variant from ``ConnectDispatcher._record_error`` is unnecessary here
  because Phase 6.2 only writes ERROR when dispatch failed, in which
  case the Outbound Contact Flow never reaches this handler at all.
  When the condition fails (genuine double-invocation from Connect),
  we swallow the ``ConditionalCheckFailedException`` and still call
  ``SendTaskSuccess`` so the state machine doesn't deadlock — the
  first writer already populated the row, our job here reduces to
  releasing the task token (idempotent design, mirrors the swallow
  pattern in 6.2 ``_record_error``).

Failure semantics follow project principle 19(b): no silent fallbacks
for input-shape errors — they raise ``ValueError``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.connect.call_result import VALID_CALL_RESULT_CODES

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]
SFN_STATE_MACHINE_ARN = os.environ["SFN_STATE_MACHINE_ARN"]

_REQUIRED_INPUT_KEYS: tuple[str, ...] = (
    "contactId",
    "cycleId",
    "employeeId",
    "attempt",
    "callResultCode",
    "taskToken",
)

_DDB = boto3.resource("dynamodb")
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)
_SFN = boto3.client("stepfunctions")


# --- Input parsing ------------------------------------------------------


def _normalize_connect_event(event: dict[str, Any]) -> dict[str, Any]:
    """Flatten Amazon Connect's nested ``Invoke Lambda`` payload.

    Amazon Connect's ``InvokeLambdaFunction`` Contact Flow action wraps
    the runtime context in a documented nested envelope:

        event["Details"]["ContactData"]["Attributes"][<attr>]   # cycleId, employeeId, attempt, taskToken
        event["Details"]["ContactData"]["ContactId"]            # contactId
        event["Details"]["Parameters"][<param>]                 # callResultCode (from LambdaInvocationAttributes)
        event["Name"] == "ContactFlowEvent"

    When this shape is detected, the function returns a *new* dict
    laid out in the flat form ``_parse_event`` expects. When the
    nested marker (``Details.ContactData`` sub-tree) is absent, the
    event is returned unchanged so the existing flat-shape test
    fixtures (and any other direct callers) keep working without
    modification.

    The behaviour is deliberately a normalisation step rather than a
    silent fallback: if the nested shape is partially present but
    malformed, ``_parse_event``'s required-key check downstream will
    surface a ``ValueError`` (project principle 19(b) — no silent
    failures).
    """
    details = event.get("Details")
    if not isinstance(details, dict):
        return event
    contact_data = details.get("ContactData")
    if not isinstance(contact_data, dict):
        return event

    # Connect nested envelope detected — flatten to the canonical form.
    attributes = contact_data.get("Attributes") or {}
    parameters = details.get("Parameters") or {}
    flat: dict[str, Any] = {
        "contactId": contact_data.get("ContactId"),
        "cycleId": attributes.get("cycleId"),
        "employeeId": attributes.get("employeeId"),
        "attempt": attributes.get("attempt"),
        "taskToken": attributes.get("taskToken"),
        "callResultCode": parameters.get("callResultCode"),
    }
    return flat


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    """Validate the Contact-Flow-supplied event and return its fields.

    The Outbound Contact Flow passes ``attempt`` as a string (all
    Contact Flow Attributes are strings); we coerce to ``int`` here so
    the downstream logic can use a numeric type.

    Raises:
        ValueError: when ``event`` is not a dict, any required key is
            missing or empty, ``attempt`` is not convertible to a
            positive integer, or ``callResultCode`` is not in
            :data:`VALID_CALL_RESULT_CODES`.
    """
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    event = _normalize_connect_event(event)
    missing = [k for k in _REQUIRED_INPUT_KEYS if not event.get(k)]
    if missing:
        raise ValueError(f"missing required input keys: {missing}")

    raw_attempt = event["attempt"]
    try:
        attempt = int(raw_attempt)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"attempt must be an integer (string-coercible); got {raw_attempt!r}"
        ) from exc
    if attempt < 1:
        raise ValueError(f"attempt must be a positive integer; got {attempt}")

    call_result_code = event["callResultCode"]
    if call_result_code not in VALID_CALL_RESULT_CODES:
        raise ValueError(
            f"callResultCode must be one of {sorted(VALID_CALL_RESULT_CODES)}; "
            f"got {call_result_code!r}"
        )

    return {
        "contactId": event["contactId"],
        "cycleId": event["cycleId"],
        "employeeId": event["employeeId"],
        "attempt": attempt,
        "callResultCode": call_result_code,
        "taskToken": event["taskToken"],
    }


# --- DynamoDB write -----------------------------------------------------


def _record_call_end(parsed: dict[str, Any]) -> None:
    """UpdateItem the Response row with the terminal call fields.

    Writes only ``callResultCode`` and ``endedAt``; ``callAttempts`` is
    left untouched because ``ConnectDispatcher`` (Phase 6.2) already
    incremented it on dispatch.

    A ``ConditionalCheckFailedException`` (i.e. ``callResultCode`` is
    already set by a previous invocation) is logged at INFO level and
    swallowed so the caller can still release the SFN task token. Any
    other ``ClientError`` is re-raised so the SFN ``Catch`` block at
    the Map level can route the failure.
    """
    now_iso = _utc_now_iso()
    try:
        _RESPONSE_TABLE.update_item(
            Key={"cycleId": parsed["cycleId"], "employeeId": parsed["employeeId"]},
            UpdateExpression="SET callResultCode = :code, endedAt = :now",
            ConditionExpression="attribute_not_exists(callResultCode)",
            ExpressionAttributeValues={
                ":code": parsed["callResultCode"],
                ":now": now_iso,
            },
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.info(
                "CallEnd write skipped (callResultCode already set) "
                "cycleId=%s employeeId=%s contactId=%s incomingCode=%s",
                parsed["cycleId"],
                parsed["employeeId"],
                parsed["contactId"],
                parsed["callResultCode"],
            )
            return
        raise


# --- SFN release --------------------------------------------------------


def _send_task_success(parsed: dict[str, Any]) -> None:
    """Release the SFN Map-iteration ``Dispatch`` state.

    Output payload mirrors the 6.2 ``ConnectDispatcher`` schema
    (``{"retry": bool, ...}``) so the SFN ``EvaluateRetry`` state can
    treat both code paths uniformly. ``retry=False`` here means
    "dispatch succeeded and the call has ended"; ``EvaluateRetry``
    then inspects ``callResultCode`` to decide the final status.
    """
    output = json.dumps(
        {
            "retry": False,
            "contactId": parsed["contactId"],
            "callResultCode": parsed["callResultCode"],
        }
    )
    _SFN.send_task_success(taskToken=parsed["taskToken"], output=output)


# --- Misc ---------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 ``Z`` string."""
    return (
        dt.datetime.now(tz=dt.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# --- Entry point --------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Outbound Contact Flow terminal entry point for one finished call."""
    parsed = _parse_event(event)
    LOGGER.info(
        "CallEnd start cycleId=%s employeeId=%s attempt=%s contactId=%s code=%s",
        parsed["cycleId"],
        parsed["employeeId"],
        parsed["attempt"],
        parsed["contactId"],
        parsed["callResultCode"],
    )

    _record_call_end(parsed)
    _send_task_success(parsed)

    return {
        "status": "ok",
        "contactId": parsed["contactId"],
        "callResultCode": parsed["callResultCode"],
    }
