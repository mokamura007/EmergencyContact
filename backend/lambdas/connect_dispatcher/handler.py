"""ConnectDispatcher Lambda — initiate one outbound call (Phase 6.2).

Invoked per Map iteration of the Cycle Step Functions state machine
(Phase 6.8). Calls ``connect:StartOutboundVoiceContact`` to dial one
employee, with exponential-backoff retries on
``ThrottlingException`` / ``LimitExceededException`` per Requirements
5.1 / 5.2 / 9.6.

Input event (from SFN Map iteration):

    {
        "cycleId":     "<uuid>",
        "employeeId":  "<uuid>",
        "phoneNumber": "+819012345678",
        "attempt":     1,                  # 1-based attempt index for this employee
        "taskToken":   "<sfn-task-token>"  # for CallEndHandler to release later
    }

On success:
    1. ``connect:StartOutboundVoiceContact`` returns a ``ContactId``.
    2. Response table receives an ``UpdateItem`` to record ``contactId``,
       ``dispatchedAt`` and increment ``callAttempts``.
    3. The handler returns ``{"status": "ok", "contactId": ..., "retry": false}``.
       The SFN ``Dispatch`` state is configured as ``.waitForTaskToken``;
       the eventual ``SendTaskSuccess`` is the responsibility of
       ``CallEndHandler`` (Phase 6.3), NOT this function.

On retryable error (``ThrottlingException`` / ``LimitExceededException``):
    The handler sleeps for ``compute_backoff_delay(retry_idx)`` seconds
    and retries up to ``_MAX_DISPATCH_ATTEMPTS`` times total. After all
    attempts are exhausted:

    1. Response table receives an ``UpdateItem`` to set
       ``callResultCode=ERROR`` (with ``ConditionExpression`` so a later
       writer does not overwrite). ``callAttempts`` is incremented.
    2. SFN ``SendTaskSuccess`` is invoked with the original taskToken and
       ``{"retry": true, "reason": "DISPATCH_FAILED"}`` so the state
       machine can branch to retry-evaluation without waiting for
       ``CallEndHandler`` (which will never fire because the call
       never connected).
    3. The handler returns ``{"status": "error", "contactId": null,
       "retry": true}``.

Other Connect API errors (non-retryable ``ClientError``):
    Logged and re-raised. The SFN ``Catch`` block at the Map iteration
    level routes the failure to the cycle's error branch.

Failure semantics follow project principle 19(b): no silent fallbacks.

Mock-mode branch (ADR-0010, Phase 16.2)
---------------------------------------
When ``MOCK_MODE=true`` AND ``ENVIRONMENT_NAME != "prod"`` (the two-stage
prod guard from ADR-0010 §3.4), the handler short-circuits the Amazon
Connect API call entirely and instead:

1. Calls :func:`shared.connect.mock.derive_mock_response` to derive a
   deterministic ``(callResultCode, transcript)`` from the ``employeeId``
   suffix (ADR-0010 §3.2).
2. Synthesises a mock ``ContactId`` of the form
   ``mock-{cycleId}-{employeeId}-{attempt}``.
3. For ``RECORDED`` results only, ``PutObject``s a 1 KB placeholder
   blob at ``recordings/{cycleId}/{employeeId}/{seq}.wav`` so the
   existing ``TranscribeStarterEventRule`` fires downstream
   (ADR-0010 §3.5.1).
4. Writes ``contactId`` / ``dispatchedAt`` + increments ``callAttempts``
   on the Response row, mirroring the production happy-path write.
5. Calls ``SendTaskSuccess`` directly with a payload that carries the
   final ``contactId`` and ``callResultCode``, so the state machine can
   branch without waiting for ``CallEndHandler`` (which is never
   invoked in mock mode because no real call ever happens).

The production code path is unchanged when ``MOCK_MODE=false``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.connect.backoff import compute_backoff_delay
from shared.connect.mock import derive_mock_response
from shared.recording.connect_key import derive_target_outbound_key

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


# --- Mock mode toggle (ADR-0010 §3.4 two-stage prod guard) -------------
#
# ``MOCK_MODE=true`` alone is not enough. We additionally require that
# ``ENVIRONMENT_NAME != "prod"`` so that even a misconfigured prod stack
# (e.g. parameters/prod.json edited by accident) cannot accidentally run
# the mock path. The CFn template adds a third defence layer via
# ``Rules.ProdMockModeForbidden`` (Phase 16.4) but we keep this code-side
# check because the rule depends on Parameter wiring being correct.
_MOCK_MODE_ENABLED: bool = (
    os.environ.get("MOCK_MODE", "false").lower() == "true"
    and os.environ.get("ENVIRONMENT_NAME", "") != "prod"
)


def _env_for_connect(name: str) -> str:
    """Resolve an Amazon Connect environment variable.

    In production (mock mode disabled) we KeyError-out at import time on
    a missing var, matching the long-standing fail-fast contract. In
    mock mode the Connect API is never called, so missing vars are
    tolerated as empty strings — this is what lets a dev stack deploy
    before the externally-managed Connect instance is provisioned
    (ADR-0010 §3.7).
    """
    if _MOCK_MODE_ENABLED:
        return os.environ.get(name, "")
    return os.environ[name]


CONNECT_INSTANCE_ID = _env_for_connect("CONNECT_INSTANCE_ID")
OUTBOUND_CONTACT_FLOW_ID = _env_for_connect("OUTBOUND_CONTACT_FLOW_ID")
OUTBOUND_PHONE_NUMBER = _env_for_connect("OUTBOUND_PHONE_NUMBER")
RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]
SFN_STATE_MACHINE_ARN = os.environ["SFN_STATE_MACHINE_ARN"]
# Mock-mode only — empty in production. The S3 client is also unused in
# production but boto3 client construction is cheap and avoids a second
# import-time branch.
RECORDINGS_BUCKET_NAME = os.environ.get("RECORDINGS_BUCKET_NAME", "")

#: 1 KB placeholder bytes for mock-mode ``recordings/*.wav`` PutObject.
#: Content is irrelevant — TranscribeStarter mock skips reading it
#: (ADR-0010 §3.5.1).
_MOCK_WAV_PLACEHOLDER: bytes = b"\x00" * 1024

#: Total Connect dispatch attempts (initial + retries). Requirements 5.2 / 9.6.
_MAX_DISPATCH_ATTEMPTS = 3

#: AWS error codes that warrant a backoff-and-retry loop.
_RETRYABLE_ERROR_CODES = frozenset({"ThrottlingException", "LimitExceededException"})

_REQUIRED_INPUT_KEYS: tuple[str, ...] = (
    "cycleId",
    "employeeId",
    "phoneNumber",
    "attempt",
    "taskToken",
)

_DDB = boto3.resource("dynamodb")
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)
_CONNECT = boto3.client("connect")
_SFN = boto3.client("stepfunctions")
_S3 = boto3.client("s3")


# --- Input parsing ------------------------------------------------------


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    """Validate the SFN-supplied event and return its fields.

    Raises:
        ValueError: when ``event`` is not a dict, when any required key
            is missing, or when ``attempt`` is not a positive integer.
    """
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    # ``attempt`` is checked separately below so that an explicitly
    # provided ``attempt=0`` produces a value-shape error, not a
    # "missing key" error.
    missing = [
        k
        for k in _REQUIRED_INPUT_KEYS
        if k != "attempt" and not event.get(k)
    ]
    if "attempt" not in event:
        missing.append("attempt")
    if missing:
        raise ValueError(f"missing required input keys: {missing}")
    attempt = event["attempt"]
    if not isinstance(attempt, int) or attempt < 1:
        raise ValueError(f"attempt must be a positive integer; got {attempt!r}")
    return {
        "cycleId": event["cycleId"],
        "employeeId": event["employeeId"],
        "phoneNumber": event["phoneNumber"],
        "attempt": attempt,
        "taskToken": event["taskToken"],
    }


# --- Connect call -------------------------------------------------------


def _start_outbound_call(parsed: dict[str, Any]) -> str:
    """Invoke ``StartOutboundVoiceContact`` with retry loop.

    Returns the resulting ``ContactId``.

    Raises:
        ClientError: on non-retryable Connect errors (propagated).
        _DispatchExhausted: after ``_MAX_DISPATCH_ATTEMPTS`` retryable
            failures.
    """
    attributes = {
        "cycleId": parsed["cycleId"],
        "employeeId": parsed["employeeId"],
        "attempt": str(parsed["attempt"]),
        "taskToken": parsed["taskToken"],
    }
    last_error: ClientError | None = None
    for try_idx in range(_MAX_DISPATCH_ATTEMPTS):
        try:
            resp = _CONNECT.start_outbound_voice_contact(
                InstanceId=CONNECT_INSTANCE_ID,
                ContactFlowId=OUTBOUND_CONTACT_FLOW_ID,
                DestinationPhoneNumber=parsed["phoneNumber"],
                SourcePhoneNumber=OUTBOUND_PHONE_NUMBER,
                Attributes=attributes,
            )
            contact_id = resp.get("ContactId")
            if not isinstance(contact_id, str) or not contact_id:
                # boto3 should always return ContactId on success — bail
                # loudly rather than silently treat as a retryable error.
                raise ValueError(
                    f"StartOutboundVoiceContact returned no ContactId: {resp!r}"
                )
            LOGGER.info(
                "ConnectDispatch ok cycleId=%s employeeId=%s attempt=%s "
                "tryIdx=%s contactId=%s",
                parsed["cycleId"],
                parsed["employeeId"],
                parsed["attempt"],
                try_idx,
                contact_id,
            )
            return contact_id
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in _RETRYABLE_ERROR_CODES:
                LOGGER.error(
                    "ConnectDispatch non-retryable error cycleId=%s employeeId=%s "
                    "code=%s: %s",
                    parsed["cycleId"],
                    parsed["employeeId"],
                    code,
                    exc,
                )
                raise
            last_error = exc
            # If we still have retries left, sleep and continue.
            if try_idx < _MAX_DISPATCH_ATTEMPTS - 1:
                delay_s = compute_backoff_delay(try_idx)
                LOGGER.warning(
                    "ConnectDispatch retryable error cycleId=%s employeeId=%s "
                    "code=%s tryIdx=%s sleep=%.3fs",
                    parsed["cycleId"],
                    parsed["employeeId"],
                    code,
                    try_idx,
                    delay_s,
                )
                time.sleep(delay_s)
            else:
                LOGGER.error(
                    "ConnectDispatch exhausted cycleId=%s employeeId=%s "
                    "code=%s tryIdx=%s",
                    parsed["cycleId"],
                    parsed["employeeId"],
                    code,
                    try_idx,
                )
    # All attempts exhausted with retryable errors.
    raise _DispatchExhaustedError(last_error)


class _DispatchExhaustedError(Exception):
    """Raised when ``_MAX_DISPATCH_ATTEMPTS`` retryable errors occur."""

    def __init__(self, last_error: ClientError | None) -> None:
        super().__init__(
            f"StartOutboundVoiceContact exhausted retries (last={last_error!r})"
        )
        self.last_error = last_error


# --- DynamoDB writes ----------------------------------------------------


def _record_success(parsed: dict[str, Any], contact_id: str) -> None:
    """UpdateItem the Response row with ``contactId`` + ``dispatchedAt``.

    ``callAttempts`` is incremented atomically. No ``ConditionExpression``
    is used here because the SFN flow guarantees a single dispatcher
    iteration per ``(cycleId, employeeId, attempt)`` tuple and the
    initial Response row was put by ``InitAttempt`` (Phase 6.8) before
    this Lambda runs.
    """
    now_iso = _utc_now_iso()
    _RESPONSE_TABLE.update_item(
        Key={"cycleId": parsed["cycleId"], "employeeId": parsed["employeeId"]},
        UpdateExpression=(
            "SET contactId = :cid, dispatchedAt = :now "
            "ADD callAttempts :one"
        ),
        ExpressionAttributeValues={
            ":cid": contact_id,
            ":now": now_iso,
            ":one": 1,
        },
    )


def _record_error(parsed: dict[str, Any]) -> None:
    """UpdateItem the Response row with ``callResultCode=ERROR``.

    The ``ConditionExpression`` ``attribute_not_exists(callResultCode)``
    prevents this writer from overwriting a callResultCode that some
    earlier iteration / inbound flow had already established. A
    ``ConditionalCheckFailedException`` is logged but otherwise
    swallowed because it means another writer beat us — exactly the
    scenario the condition was put in place to handle.
    """
    now_iso = _utc_now_iso()
    try:
        _RESPONSE_TABLE.update_item(
            Key={"cycleId": parsed["cycleId"], "employeeId": parsed["employeeId"]},
            UpdateExpression=(
                "SET callResultCode = :code, dispatchedAt = :now "
                "ADD callAttempts :one"
            ),
            ConditionExpression="attribute_not_exists(callResultCode)",
            ExpressionAttributeValues={
                ":code": "ERROR",
                ":now": now_iso,
                ":one": 1,
            },
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.info(
                "ConnectDispatch ERROR write skipped (callResultCode already set) "
                "cycleId=%s employeeId=%s",
                parsed["cycleId"],
                parsed["employeeId"],
            )
            return
        raise


# --- SFN notify ---------------------------------------------------------


def _send_retry_task_success(parsed: dict[str, Any]) -> None:
    """Release the SFN task with a ``retry=True`` payload.

    The Map iteration's ``Dispatch`` state is configured as
    ``.waitForTaskToken``. When dispatch fails for good, we still need
    to unblock the state so ``EvaluateRetry`` can run; the payload
    signals the failure mode to the SFN definition.
    """
    output = json.dumps({"retry": True, "reason": "DISPATCH_FAILED"})
    _SFN.send_task_success(taskToken=parsed["taskToken"], output=output)


# --- Mock-mode (ADR-0010 Phase 16.2) -----------------------------------


def _mock_contact_id(parsed: dict[str, Any]) -> str:
    """Build a deterministic synthetic ContactId for mock mode.

    Format: ``mock-{cycleId}-{employeeId}-{attempt}`` per ADR-0010 §3.5.1.
    The ``mock-`` prefix lets downstream observability filter mock
    traffic out of real Connect telemetry.
    """
    return (
        f"mock-{parsed['cycleId']}-{parsed['employeeId']}-{parsed['attempt']}"
    )


def _put_mock_recording(parsed: dict[str, Any]) -> str:
    """Upload a 1 KB placeholder ``.wav`` to RecordingsBucket.

    The key follows the design layout
    ``recordings/{cycleId}/{employeeId}/{seq}.wav`` (mirrored by
    :func:`shared.recording.connect_key.derive_target_outbound_key`).
    Triggering the existing ``TranscribeStarterEventRule`` is the sole
    purpose of this PutObject; the bytes themselves are never read by
    the downstream TranscribeStarter mock branch (ADR-0010 §3.5.1).
    """
    key = derive_target_outbound_key(
        parsed["cycleId"], parsed["employeeId"], parsed["attempt"]
    )
    _S3.put_object(
        Bucket=RECORDINGS_BUCKET_NAME,
        Key=key,
        Body=_MOCK_WAV_PLACEHOLDER,
        ContentType="audio/wav",
    )
    return key


def _send_mock_task_success(
    parsed: dict[str, Any],
    *,
    contact_id: str,
    call_result_code: str,
    retry: bool,
) -> None:
    """Release the SFN task with the mock-mode payload.

    Unlike :func:`_send_retry_task_success` (which only signals failure),
    the mock-mode payload also carries ``contactId`` and
    ``callResultCode`` so the state machine can branch on the simulated
    call outcome without waiting for a CallEndHandler invocation that
    will never come (ADR-0010 §3.5.1).
    """
    output = json.dumps(
        {
            "retry": retry,
            "contactId": contact_id,
            "callResultCode": call_result_code,
        }
    )
    _SFN.send_task_success(taskToken=parsed["taskToken"], output=output)


def _dispatch_mock(parsed: dict[str, Any]) -> dict[str, Any]:
    """Run the mock-mode dispatch path end-to-end.

    Steps (ADR-0010 §3.5.1):
        1. Derive ``(callResultCode, transcript)`` from ``employeeId``
           via :func:`shared.connect.mock.derive_mock_response`. The
           ``transcript`` value is intentionally unused here — it is the
           TranscribeStarter mock branch that consumes it (Phase 16.3).
        2. Build a synthetic ``ContactId`` (deterministic per dispatch).
        3. For ``RECORDED`` outcomes only, PutObject a placeholder wav
           so TranscribeStarter fires; ``NO_ANSWER`` / ``BUSY`` skip
           this step (no audio is ever recorded for those outcomes in
           the production path either).
        4. Persist ``contactId`` + ``dispatchedAt`` to Response,
           increment ``callAttempts``.
        5. SendTaskSuccess with a payload that signals retry semantics
           identical to what ``EvaluateRetry`` expects from the
           production CallEndHandler.
    """
    call_result_code, _transcript = derive_mock_response(parsed["employeeId"])
    contact_id = _mock_contact_id(parsed)
    LOGGER.info(
        "ConnectDispatch MOCK start cycleId=%s employeeId=%s attempt=%s "
        "callResultCode=%s contactId=%s",
        parsed["cycleId"],
        parsed["employeeId"],
        parsed["attempt"],
        call_result_code,
        contact_id,
    )

    if call_result_code == "RECORDED":
        recording_key = _put_mock_recording(parsed)
        LOGGER.info(
            "ConnectDispatch MOCK wav put bucket=%s key=%s",
            RECORDINGS_BUCKET_NAME,
            recording_key,
        )
        retry = False
    else:
        # NO_ANSWER / BUSY: no recording, EvaluateRetry must retry.
        retry = True

    _record_success(parsed, contact_id)
    _send_mock_task_success(
        parsed,
        contact_id=contact_id,
        call_result_code=call_result_code,
        retry=retry,
    )
    return {"status": "ok", "contactId": contact_id, "retry": retry}


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
    """SFN Map-iteration entry point for one outbound dispatch."""
    parsed = _parse_event(event)
    LOGGER.info(
        "ConnectDispatch start cycleId=%s employeeId=%s attempt=%s mockMode=%s",
        parsed["cycleId"],
        parsed["employeeId"],
        parsed["attempt"],
        _MOCK_MODE_ENABLED,
    )

    if _MOCK_MODE_ENABLED:
        return _dispatch_mock(parsed)

    try:
        contact_id = _start_outbound_call(parsed)
    except _DispatchExhaustedError:
        # All retries exhausted. Record ERROR + release SFN task with retry.
        _record_error(parsed)
        _send_retry_task_success(parsed)
        return {"status": "error", "contactId": None, "retry": True}

    _record_success(parsed, contact_id)
    return {"status": "ok", "contactId": contact_id, "retry": False}
