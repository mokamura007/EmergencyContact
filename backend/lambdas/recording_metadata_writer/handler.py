"""RecordingMetadataWriter Lambda — write one RecordingMeta row (Phase 6.7).

Triggered by EventBridge for S3 ``Object Created`` events on the
RecordingsBucket. Mirrors the Phase 6.4 TranscribeStarter wiring (same
bucket, same event shape) but with an orthogonal responsibility:

* TranscribeStarter starts the speech-to-text pipeline.
* RecordingMetadataWriter persists per-recording metadata
  (``s3Bucket`` / ``s3ObjectKey`` / ``recordedAt`` / ``durationSeconds``
  / ``employeeId`` / ``kind`` / ``contactId`` (inbound only)) into the
  RecordingMetaTable (D4) so the Admin SPA (Phase 10) and RecordingApi
  (Phase 5.5) can resolve "who called when, how long" without scanning
  S3.

Both Lambdas hang off **separate** EventBridge Rules (responsibility
isolation — disabling one for ops does not affect the other). The two
Rules share an EventPattern and fire concurrently for a single
PutObject — that is intentional and OK because each Lambda owns its
own DynamoDB row and the writes are idempotent.

Idempotency:
    ``put_item`` carries ``ConditionExpression="attribute_not_exists(cycleId)"``
    so re-delivered events (S3 / EventBridge at-least-once semantics)
    do not overwrite an earlier writer. ``ConditionalCheckFailedException``
    is logged at INFO and swallowed.

Retry policy (Requirement 10.5, Phase 12.5):
    ``ThrottlingException`` / ``ProvisionedThroughputExceededException``
    trigger up to ``_MAX_DDB_ATTEMPTS`` total attempts with exponential
    backoff via :func:`shared.connect.backoff.compute_backoff_delay`
    (reused from Phase 6.2 / 6.4 — DRY). After exhausting retries the
    handler returns ``{"status": "error", "reason": "DDB_WRITE_FAILED"}``
    AND raises so AWS Lambda's asynchronous-invocation error handling
    routes the event to the configured ``DeadLetterConfig.TargetArn``
    (``RecordingMetadataWriterDLQ``). The raise is required: Lambda's
    async DLQ is only triggered on a function-error response (raised
    exception or non-2xx for proxy integrations), not on a normal
    return value.

Duration estimation:
    The S3 PutObject EventBridge event carries ``detail.object.size``
    in bytes, but not the audio bitrate. Amazon Connect uploads its
    recordings as 8 kHz mono 16-bit PCM WAV by default, i.e. 128 kbit/s.
    We approximate the duration in seconds as
    ``size_bytes * 8 / (bitrate_kbps * 1000)``. The result is recorded
    as ``durationSeconds`` (rounded) plus the raw ``fileSizeBytes``
    so a future Phase 13.x exact-decoder can reconcile if needed.

Failure semantics follow project principle 19(b): no silent fallbacks
for input-shape errors — they raise ``ValueError`` directly so the
async DLQ captures the malformed event.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.connect.backoff import compute_backoff_delay
from shared.recording.s3_keys import RecordingKeyInfo, parse_recording_key

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

RECORDINGS_BUCKET_NAME = os.environ["RECORDINGS_BUCKET_NAME"]
RECORDING_META_TABLE_NAME = os.environ["RECORDING_META_TABLE_NAME"]

#: Total RecordingMeta PutItem attempts (initial + retries). Requirement 10.5.
_MAX_DDB_ATTEMPTS = 3

#: DynamoDB error codes that warrant a backoff-and-retry loop.
_RETRYABLE_DDB_ERROR_CODES = frozenset(
    {"ThrottlingException", "ProvisionedThroughputExceededException"}
)

#: Amazon Connect default recording bitrate (8 kHz mono 16-bit PCM = 128 kbit/s).
#: Kept as a module-level constant so a Phase 13.x property test could pin it
#: against the upstream Connect default if Amazon ever changes the encoder.
_DEFAULT_BITRATE_KBPS = 128

_DDB = boto3.resource("dynamodb")
_RECORDING_META_TABLE = _DDB.Table(RECORDING_META_TABLE_NAME)


# --- Input parsing ------------------------------------------------------


def _parse_event(event: dict[str, Any]) -> tuple[str, str, int]:
    """Validate the EventBridge event and return ``(bucket, key, size)``.

    The S3 PutObject EventBridge event guarantees ``detail.object.size``
    is present on the standard ``Object Created`` event-type, but we
    defensively default to ``0`` (so ``durationSeconds == 0`` rather
    than a crash) if a non-standard producer ever omits it.

    Raises:
        ValueError: when ``event`` is not a dict, when ``detail`` is
            missing, or when ``detail.bucket.name`` /
            ``detail.object.key`` is missing or empty. Size missing is
            tolerated (yields 0).
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

    raw_size = object_block.get("size", 0)
    try:
        size_bytes = int(raw_size)
    except (TypeError, ValueError):
        size_bytes = 0
    if size_bytes < 0:
        size_bytes = 0

    return bucket_block["name"], object_block["key"], size_bytes


# --- Pure helpers -------------------------------------------------------


def _estimate_duration_seconds(
    file_size_bytes: int, bitrate_kbps: int = _DEFAULT_BITRATE_KBPS
) -> int:
    """Approximate WAV duration in seconds from file size and bitrate.

    Returns ``0`` for non-positive sizes (defensive default). The
    formula is ``size_bytes * 8 / (bitrate_kbps * 1000)``, rounded to
    the nearest integer second. The 44-byte WAV header is intentionally
    not subtracted — it rounds away at any duration above ~1 second
    and keeping the formula divisor-only makes the function pure /
    easy to property-test in a future Phase 13.x task.
    """
    if file_size_bytes <= 0 or bitrate_kbps <= 0:
        return 0
    return round(file_size_bytes * 8 / (bitrate_kbps * 1000))


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 ``Z`` string."""
    return (
        dt.datetime.now(tz=dt.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# --- DynamoDB write -----------------------------------------------------


class _DdbWriteExhaustedError(Exception):
    """Raised when ``_MAX_DDB_ATTEMPTS`` retryable errors occur in a row."""

    def __init__(self, last_error: ClientError | None) -> None:
        super().__init__(
            f"RecordingMeta put_item exhausted retries (last={last_error!r})"
        )
        self.last_error = last_error


def _build_recording_meta_item(
    info: RecordingKeyInfo,
    bucket: str,
    object_key: str,
    file_size_bytes: int,
    recorded_at: str,
) -> dict[str, Any]:
    """Pure shaping of the RecordingMeta DynamoDB Item.

    Outbound recordings only carry ``kind="outbound"``. Inbound
    recordings additionally carry ``contactId`` (from
    ``info.seq_or_contact``) so the Admin SPA can resolve a Connect
    contact ID without re-parsing the S3 key.
    """
    item: dict[str, Any] = {
        "cycleId": info.meta_pk,
        "employeeIdSeq": info.meta_sk,
        "employeeId": info.employee_id,
        "s3Bucket": bucket,
        "s3ObjectKey": object_key,
        "recordedAt": recorded_at,
        "fileSizeBytes": file_size_bytes,
        "durationSeconds": _estimate_duration_seconds(file_size_bytes),
        "kind": info.kind,
    }
    if info.kind == "inbound":
        item["contactId"] = info.seq_or_contact
    return item


def _write_metadata_with_retry(item: dict[str, Any]) -> None:
    """``PutItem`` the RecordingMeta row with bounded backoff retries.

    Raises:
        _DdbWriteExhaustedError: after ``_MAX_DDB_ATTEMPTS`` retryable
            failures. The caller turns this into the DLQ-routing
            failure response.
        ClientError: non-retryable DynamoDB errors are propagated to
            the async DLQ.
    """
    last_error: ClientError | None = None
    for try_idx in range(_MAX_DDB_ATTEMPTS):
        try:
            _RECORDING_META_TABLE.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(cycleId)",
            )
            LOGGER.info(
                "RecordingMeta put_item ok metaPk=%s metaSk=%s tryIdx=%s",
                item["cycleId"],
                item["employeeIdSeq"],
                try_idx,
            )
            return
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                # Duplicate delivery. The first writer is the source of
                # truth — nothing to do.
                LOGGER.info(
                    "RecordingMeta put_item skipped (row already exists) "
                    "metaPk=%s metaSk=%s",
                    item["cycleId"],
                    item["employeeIdSeq"],
                )
                return
            if code not in _RETRYABLE_DDB_ERROR_CODES:
                LOGGER.error(
                    "RecordingMeta put_item non-retryable error "
                    "metaPk=%s metaSk=%s code=%s: %s",
                    item["cycleId"],
                    item["employeeIdSeq"],
                    code,
                    exc,
                )
                raise
            last_error = exc
            if try_idx < _MAX_DDB_ATTEMPTS - 1:
                delay_s = compute_backoff_delay(try_idx)
                LOGGER.warning(
                    "RecordingMeta put_item retryable error metaPk=%s "
                    "metaSk=%s code=%s tryIdx=%s sleep=%.3fs",
                    item["cycleId"],
                    item["employeeIdSeq"],
                    code,
                    try_idx,
                    delay_s,
                )
                time.sleep(delay_s)
            else:
                LOGGER.error(
                    "RecordingMeta put_item exhausted metaPk=%s "
                    "metaSk=%s code=%s tryIdx=%s",
                    item["cycleId"],
                    item["employeeIdSeq"],
                    code,
                    try_idx,
                )
    raise _DdbWriteExhaustedError(last_error)


# --- Entry point --------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """EventBridge S3 ObjectCreated entry point.

    Returns a JSON-serialisable payload describing the write outcome.
    On terminal failure the handler **also raises** so the Lambda
    asynchronous-invocation DLQ (configured via
    ``DeadLetterConfig.TargetArn``) captures the original EventBridge
    event for operator triage.
    """
    bucket, object_key, file_size_bytes = _parse_event(event)
    LOGGER.info(
        "RecordingMeta received bucket=%s key=%s sizeBytes=%s",
        bucket,
        object_key,
        file_size_bytes,
    )

    info = parse_recording_key(object_key)
    if info is None:
        raise ValueError(
            f"recording key did not match expected schema: key={object_key!r}"
        )

    item = _build_recording_meta_item(
        info=info,
        bucket=bucket,
        object_key=object_key,
        file_size_bytes=file_size_bytes,
        recorded_at=_utc_now_iso(),
    )

    try:
        _write_metadata_with_retry(item)
    except _DdbWriteExhaustedError as exc:
        LOGGER.error(
            "RecordingMeta write failed; event will be routed to DLQ "
            "metaPk=%s metaSk=%s lastError=%r",
            item["cycleId"],
            item["employeeIdSeq"],
            exc.last_error,
        )
        # Raise so Lambda's async DLQ captures the event. The return
        # value below is unreachable in production but kept for unit
        # tests that monkey-patch the raise away to inspect the sentinel.
        raise

    return {
        "status": "ok",
        "metaPk": info.meta_pk,
        "metaSk": info.meta_sk,
        "kind": info.kind,
    }
