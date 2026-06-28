"""RecordingRelocator Lambda — rename Connect-native recordings (Phase 7.2).

Amazon Connect writes call recordings to the configured S3 location
using a *fixed* key layout that the service controls
(``<prefix>/...?/CallRecordings/<yyyy>/<mm>/<dd>/<contactId>_<ts>_UTC.wav``).
The project design mandates a different layout
(``recordings/{cycleId}/{employeeId}/{seq}.wav``) so that
``TranscribeStarter`` (Phase 6.4) and ``RecordingMetadataWriter``
(Phase 6.7), which key off the design layout, can do their job.

This Lambda bridges the two layouts:

1. EventBridge fires on every ``Object Created`` event in the
   ``RecordingsBucket`` whose key starts with the configured
   Connect-native prefix (``CONNECT_RECORDINGS_PREFIX``, e.g.
   ``"connect-raw/"``). The Phase 6.4 / 6.7 sibling rules are
   prefix-filtered the opposite way so they don't fire on the raw
   key.
2. ``shared.recording.connect_key.parse_connect_native_key`` extracts
   the Connect ``contactId`` from the key.
3. The Response table's ``ContactIdIndex`` GSI resolves the
   ``contactId`` back to the originating ``(cycleId, employeeId,
   callAttempts)`` tuple (Phase 6.2 ``ConnectDispatcher`` wrote
   ``contactId`` and incremented ``callAttempts`` atomically on
   ``StartOutboundVoiceContact`` success).
4. ``S3.copy_object`` writes the recording to
   ``recordings/{cycleId}/{employeeId}/{seq}.wav`` (with
   ``seq = callAttempts``, 1-based per Phase 6.2 convention).
5. ``S3.delete_object`` removes the Connect-native original.

Idempotency:
    Each step is individually idempotent — repeated EventBridge
    delivery for the same key results in the same target write, the
    same DLQ behaviour on missing GSI rows, and a no-op delete after
    the original is gone. The ``RecordingMetadataWriter`` Lambda is
    also separately idempotent so duplicate relocation simply triggers
    duplicate (silently-skipped) put_items downstream.

GSI eventual consistency:
    DynamoDB Global Secondary Indexes are eventually consistent.
    ``ConnectDispatcher`` writes ``contactId`` synchronously on
    ``StartOutboundVoiceContact`` success — long before Connect
    finishes the call and uploads the recording — so by the time this
    Lambda fires, the GSI lag (typically under 1 second) has resolved.
    We still retry the Query up to ``_MAX_GSI_ATTEMPTS`` times with a
    backoff just in case (reuses
    ``shared.connect.backoff.compute_backoff_delay`` for DRY).

Inbound recordings:
    Phase 9 will introduce a separate Inbound Contact Flow. Because
    Connect uses the *same* InstanceStorageConfig for all recordings
    on one instance, inbound recordings will also land in the
    Connect-native prefix. This Lambda's Phase 9 follow-up will branch
    on a Response-lookup miss (i.e. contactId not in
    ``ContactIdIndex``) and consult ``InboundContactTable`` to compute
    ``inbound/{yyyymm}/{employeeId}/{contactId}.wav``. For now we
    raise so the event lands in the DLQ — Phase 9 will replace the
    raise with the inbound branch.

Failure semantics follow project principle 19(b): input-shape errors
and unresolved ``contactId`` lookups raise ``ValueError`` /
:class:`_GsiLookupExhaustedError`. AWS Lambda's asynchronous-invocation
error handling forwards these to the configured DLQ.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.connect.backoff import compute_backoff_delay
from shared.recording.connect_key import (
    derive_target_outbound_key,
    parse_connect_native_key,
)

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

RECORDINGS_BUCKET_NAME = os.environ["RECORDINGS_BUCKET_NAME"]
RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]
CONTACT_ID_INDEX_NAME = os.environ.get("CONTACT_ID_INDEX_NAME", "ContactIdIndex")
CONNECT_RECORDINGS_PREFIX = os.environ.get(
    "CONNECT_RECORDINGS_PREFIX", "connect-raw/"
)

#: Total Response-GSI Query attempts (initial + retries) before giving up.
_MAX_GSI_ATTEMPTS = 3

#: DynamoDB error codes that warrant a backoff-and-retry loop.
_RETRYABLE_DDB_ERROR_CODES = frozenset(
    {"ThrottlingException", "ProvisionedThroughputExceededException"}
)

_DDB = boto3.resource("dynamodb")
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)
_S3 = boto3.client("s3")


# --- Input parsing ------------------------------------------------------


def _parse_event(event: dict[str, Any]) -> tuple[str, str]:
    """Validate the EventBridge event and return ``(bucket, key)``.

    Raises:
        ValueError: when ``event`` is not a dict, or when any of
            ``detail`` / ``detail.bucket.name`` / ``detail.object.key``
            is missing or empty.
    """
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise ValueError("event.detail must be a JSON object")
    bucket_block = detail.get("bucket")
    object_block = detail.get("object")
    if not isinstance(bucket_block, dict) or not bucket_block.get("name"):
        raise ValueError("event.detail.bucket.name is required")
    if not isinstance(object_block, dict) or not object_block.get("key"):
        raise ValueError("event.detail.object.key is required")
    return bucket_block["name"], object_block["key"]


# --- GSI lookup ---------------------------------------------------------


class _GsiLookupExhaustedError(Exception):
    """Raised when the Response GSI Query exhausts retries or finds no row."""

    def __init__(self, contact_id: str, reason: str) -> None:
        super().__init__(
            f"Response GSI lookup exhausted for contactId={contact_id!r}: {reason}"
        )
        self.contact_id = contact_id
        self.reason = reason


def _lookup_response_by_contact_id(contact_id: str) -> dict[str, Any]:
    """Query the Response ``ContactIdIndex`` GSI and return one row.

    The GSI is sparse — only Response rows where
    ``ConnectDispatcher._record_success`` set ``contactId`` are
    indexed. We Query with ``Limit=1`` because (cycleId, employeeId)
    pairs are unique per contactId (Phase 6.2 guarantees one dispatch
    per attempt and ``contactId`` is reset on each retry attempt — see
    ``_record_success`` ``UpdateExpression`` which overwrites the field).

    Retries on throttling exceptions with exponential backoff; on
    final exhaustion or "no item found" raises
    :class:`_GsiLookupExhaustedError`.
    """
    last_error: ClientError | None = None
    for try_idx in range(_MAX_GSI_ATTEMPTS):
        try:
            resp = _RESPONSE_TABLE.query(
                IndexName=CONTACT_ID_INDEX_NAME,
                KeyConditionExpression="contactId = :cid",
                ExpressionAttributeValues={":cid": contact_id},
                Limit=1,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in _RETRYABLE_DDB_ERROR_CODES:
                LOGGER.error(
                    "Response GSI Query non-retryable error contactId=%s code=%s: %s",
                    contact_id,
                    code,
                    exc,
                )
                raise
            last_error = exc
            if try_idx < _MAX_GSI_ATTEMPTS - 1:
                delay_s = compute_backoff_delay(try_idx)
                LOGGER.warning(
                    "Response GSI Query retryable error contactId=%s code=%s "
                    "tryIdx=%s sleep=%.3fs",
                    contact_id,
                    code,
                    try_idx,
                    delay_s,
                )
                time.sleep(delay_s)
                continue
            LOGGER.error(
                "Response GSI Query exhausted contactId=%s code=%s tryIdx=%s",
                contact_id,
                code,
                try_idx,
            )
            raise _GsiLookupExhaustedError(
                contact_id, f"throttling ({code}); last_error={last_error!r}"
            )

        items = resp.get("Items", [])
        if items:
            return items[0]
        # No row — could be (a) inbound contact (Phase 9 follow-up),
        # (b) GSI lag, or (c) genuinely orphan recording. Retry a few
        # times before raising so transient (b) self-heals.
        if try_idx < _MAX_GSI_ATTEMPTS - 1:
            delay_s = compute_backoff_delay(try_idx)
            LOGGER.warning(
                "Response GSI Query no rows contactId=%s tryIdx=%s sleep=%.3fs",
                contact_id,
                try_idx,
                delay_s,
            )
            time.sleep(delay_s)
            continue
        raise _GsiLookupExhaustedError(
            contact_id, "no row in Response.ContactIdIndex after retries"
        )

    # Unreachable — loop always returns or raises. Defensive.
    raise _GsiLookupExhaustedError(contact_id, "loop terminated unexpectedly")


# --- S3 operations ------------------------------------------------------


def _copy_and_delete(bucket: str, source_key: str, target_key: str) -> None:
    """``CopyObject`` then ``DeleteObject`` the recording.

    The copy uses the bucket's default SSE-KMS encryption (set on
    ``RecordingsBucket`` at Phase 2.10) so we don't pass an explicit
    ``ServerSideEncryption`` / ``SSEKMSKeyId`` — the bucket-level
    configuration handles it.

    ``DeleteObject`` is best-effort: if the original was already
    deleted by a previous invocation (idempotent re-delivery), S3
    returns success anyway.
    """
    LOGGER.info(
        "Relocator copy bucket=%s source=%s target=%s",
        bucket,
        source_key,
        target_key,
    )
    _S3.copy_object(
        Bucket=bucket,
        Key=target_key,
        CopySource={"Bucket": bucket, "Key": source_key},
    )
    LOGGER.info(
        "Relocator delete bucket=%s source=%s",
        bucket,
        source_key,
    )
    _S3.delete_object(Bucket=bucket, Key=source_key)


# --- Entry point --------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """EventBridge S3 ObjectCreated entry point.

    Returns a JSON-serialisable payload describing the rename. On
    terminal failure the handler **also raises** so the Lambda
    asynchronous-invocation DLQ captures the original event.
    """
    bucket, source_key = _parse_event(event)
    LOGGER.info(
        "Relocator received bucket=%s key=%s",
        bucket,
        source_key,
    )

    # Defensive: only act on the configured Connect-native prefix.
    # Without this guard a misconfigured EventBridge Rule (or a manual
    # ``aws s3 cp`` to the wrong prefix) could send us a design-layout
    # key, which we'd then loop-rename infinitely.
    if not source_key.startswith(CONNECT_RECORDINGS_PREFIX):
        raise ValueError(
            f"recording key does not start with CONNECT_RECORDINGS_PREFIX="
            f"{CONNECT_RECORDINGS_PREFIX!r}: key={source_key!r}"
        )

    info = parse_connect_native_key(source_key)
    if info is None:
        raise ValueError(
            f"recording key did not match Connect-native schema: "
            f"key={source_key!r}"
        )

    row = _lookup_response_by_contact_id(info.contact_id)

    cycle_id = row.get("cycleId")
    employee_id = row.get("employeeId")
    call_attempts_raw = row.get("callAttempts")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError(
            f"Response row missing cycleId; contactId={info.contact_id!r} row={row!r}"
        )
    if not isinstance(employee_id, str) or not employee_id:
        raise ValueError(
            f"Response row missing employeeId; contactId={info.contact_id!r} row={row!r}"
        )
    if call_attempts_raw is None:
        raise ValueError(
            f"Response row missing callAttempts; contactId={info.contact_id!r} row={row!r}"
        )
    try:
        seq = int(call_attempts_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Response row callAttempts not int-coercible; "
            f"contactId={info.contact_id!r} row={row!r}"
        ) from exc

    target_key = derive_target_outbound_key(cycle_id, employee_id, seq)

    _copy_and_delete(bucket, source_key, target_key)

    return {
        "status": "ok",
        "contactId": info.contact_id,
        "cycleId": cycle_id,
        "employeeId": employee_id,
        "seq": seq,
        "sourceKey": source_key,
        "targetKey": target_key,
    }
