"""TranscribeStarter Lambda — start one Transcribe job (Phase 6.4).

Triggered by EventBridge for S3 ``Object Created`` events on the
RecordingsBucket. Responsible for:

1. **Parsing the S3 key.** :func:`shared.recording.s3_keys.parse_recording_key`
   resolves the key into ``meta_pk`` / ``meta_sk`` / transcript output
   key for both outbound (``recordings/{cycleId}/{employeeId}/{seq}.wav``)
   and inbound (``inbound/{yyyymm}/{employeeId}/{contactId}.wav``)
   schemas. Anything else is a fatal input error.

2. **Starting the Transcribe job.** ``start_transcription_job`` is
   called with an idempotent job name (so re-delivered events from
   S3 / EventBridge land on the same job, returning
   ``ConflictException`` which we treat as success).
   ``ThrottlingException`` / ``LimitExceededException`` trigger a
   bounded exponential-backoff retry loop using
   :func:`shared.connect.backoff.compute_backoff_delay` — the same
   pure function Phase 6.2 ConnectDispatcher reuses (DRY).

3. **Recording the job metadata.** On the success path the
   ``transcribeJobId`` and ``transcriptS3Key`` are written to
   TranscriptMetaTable with a ``ConditionExpression`` so concurrent
   re-deliveries don't overwrite an earlier writer.

4. **Failure semantics.** After ``_MAX_TRANSCRIBE_ATTEMPTS`` retryable
   failures:

   * Outbound: a transition ``callResultCode = RECORDED ->
     TRANSCRIBE_FAILED`` is attempted on the Response row with a
     conditional update so we only overwrite when the recording was
     actually captured. Other callResultCode states (BUSY, NO_ANSWER,
     etc.) leave the row untouched — the recording metadata is still
     in TranscriptMetaTable for debugging.
   * Inbound: Response is **not** touched. Inbound bookkeeping lives
     in InboundContactTable and is the responsibility of Phase 9
     InboundHandler. Here we only log a warning so the failure is
     observable.

Failure semantics follow project principle 19(b): no silent fallbacks
for input-shape errors — they raise ``ValueError`` directly.

Mock-mode branch (ADR-0010, Phase 16.3)
---------------------------------------
When ``MOCK_MODE=true`` AND ``ENVIRONMENT_NAME != "prod"`` (the two-stage
prod guard from ADR-0010 §3.4, mirrored from Phase 16.2 ConnectDispatcher),
the handler short-circuits the Amazon Transcribe API call entirely and
instead:

1. Parses the incoming S3 ObjectCreated event with
   :func:`shared.recording.s3_keys.parse_recording_key` exactly like
   the production path (same ``RecordingKeyInfo`` is reused).
2. Calls :func:`shared.connect.mock.derive_mock_response` to recover the
   deterministic ``(callResultCode, transcript)`` derived from the
   ``employeeId`` suffix (ADR-0010 §3.2). Only ``RECORDED`` outcomes
   reach this handler because Phase 16.2 ConnectDispatcher skips the
   placeholder-wav PutObject for ``NO_ANSWER`` / ``BUSY``; the defensive
   ``transcript is None`` guard here is a belt-and-braces check.
3. Synthesises a minimal real-Transcribe-output-shaped JSON document
   (``results.transcripts[0].transcript`` + a single
   ``results.items[0]`` token + ``status: "COMPLETED"``) per
   ADR-0010 §3.5.2.
4. ``PutObject``s it to ``TranscriptsBucket`` at the same key the
   production path would have written via Amazon Transcribe's
   ``OutputKey`` parameter (``parse_recording_key`` returns
   ``transcripts/{cycleId}/{employeeId}/{seq}.json`` for outbound
   recordings). The existing ``KeywordMatcherEventRule`` matches on
   the ``transcripts/`` prefix, so KeywordMatcher fires downstream
   without any rule modification.
5. Writes the synthetic ``transcribeJobId`` + ``transcriptS3Key`` to
   TranscriptMetaTable via the shared :func:`_record_transcript_meta`
   helper (idempotent on re-delivery via the existing
   ``attribute_not_exists(cycleId)`` ConditionExpression).

The production code path is unchanged when ``MOCK_MODE=false``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.connect.backoff import compute_backoff_delay
from shared.connect.mock import derive_mock_response
from shared.recording.s3_keys import (
    RecordingKeyInfo,
    derive_transcribe_job_name,
    parse_recording_key,
)

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
# Identical pattern to Phase 16.2 ConnectDispatcher (DRY, principle 19(a)).
_MOCK_MODE_ENABLED: bool = (
    os.environ.get("MOCK_MODE", "false").lower() == "true"
    and os.environ.get("ENVIRONMENT_NAME", "") != "prod"
)


def _env_for_transcribe(name: str) -> str:
    """Resolve a Transcribe environment variable.

    In production (mock mode disabled) we KeyError-out at import time on
    a missing var, matching the long-standing fail-fast contract
    (principle 19(b)). In mock mode the Transcribe API is never called,
    so missing vars are tolerated as empty strings — this lets a dev
    stack deploy before language-code parameter wiring is finalised
    (mirrors :func:`shared.connect.mock` env handling from Phase 16.2).
    """
    if _MOCK_MODE_ENABLED:
        return os.environ.get(name, "")
    return os.environ[name]


RECORDINGS_BUCKET_NAME = os.environ["RECORDINGS_BUCKET_NAME"]
TRANSCRIPTS_BUCKET_NAME = os.environ["TRANSCRIPTS_BUCKET_NAME"]
TRANSCRIPT_META_TABLE_NAME = os.environ["TRANSCRIPT_META_TABLE_NAME"]
RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]
KMS_CMK_ARN = os.environ["KMS_CMK_ARN"]
#: Transcribe language code (Requirement 6.2). Injected from CFn Parameter
#: ``TranscribeLanguageCode`` via TranscribeStarterFn Environment. Missing
#: env var is a deploy-time configuration bug, so we let KeyError propagate
#: in production (principle 19(b): no silent fallback). Mock mode tolerates
#: an empty string because Transcribe is never invoked.
TRANSCRIBE_LANGUAGE_CODE = _env_for_transcribe("TRANSCRIBE_LANGUAGE_CODE")

#: Total Transcribe StartJob attempts (initial + retries). Requirement 6.6.
_MAX_TRANSCRIBE_ATTEMPTS = 3

#: AWS error codes that warrant a backoff-and-retry loop.
_RETRYABLE_ERROR_CODES = frozenset({"ThrottlingException", "LimitExceededException"})

_DDB = boto3.resource("dynamodb")
_TRANSCRIPT_META_TABLE = _DDB.Table(TRANSCRIPT_META_TABLE_NAME)
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)
_TRANSCRIBE = boto3.client("transcribe")
#: Mock-mode only. The boto3 S3 client construction itself is cheap and
#: avoids a second import-time branch; the client is never used when
#: ``_MOCK_MODE_ENABLED`` is False.
_S3 = boto3.client("s3")


# --- Input parsing ------------------------------------------------------


def _parse_event(event: dict[str, Any]) -> tuple[str, str]:
    """Validate the EventBridge event and return (bucket, key).

    Raises:
        ValueError: when ``event`` is not a dict, when ``detail`` is
            missing, when ``detail.bucket.name`` or
            ``detail.object.key`` is missing or empty.
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


# --- Transcribe call ----------------------------------------------------


class _TranscribeExhaustedError(Exception):
    """Raised when ``_MAX_TRANSCRIBE_ATTEMPTS`` retryable errors occur."""

    def __init__(self, last_error: ClientError | None) -> None:
        super().__init__(
            f"StartTranscriptionJob exhausted retries (last={last_error!r})"
        )
        self.last_error = last_error


def _start_transcribe_job_with_retry(
    job_name: str, bucket: str, object_key: str, info: RecordingKeyInfo
) -> str:
    """Invoke ``StartTranscriptionJob`` with bounded retry loop.

    Returns:
        The ``TranscriptionJobName`` (i.e. ``job_name``) on success.
        ``ConflictException`` from a duplicate-delivery scenario is
        treated as success (idempotent job naming via
        :func:`derive_transcribe_job_name`).

    Raises:
        _TranscribeExhaustedError: after ``_MAX_TRANSCRIBE_ATTEMPTS``
            retryable failures.
        ClientError: on non-retryable Transcribe errors (propagated).
    """
    media_uri = f"s3://{bucket}/{object_key}"
    last_error: ClientError | None = None
    for try_idx in range(_MAX_TRANSCRIBE_ATTEMPTS):
        try:
            _TRANSCRIBE.start_transcription_job(
                TranscriptionJobName=job_name,
                LanguageCode=TRANSCRIBE_LANGUAGE_CODE,
                MediaFormat="wav",
                Media={"MediaFileUri": media_uri},
                OutputBucketName=TRANSCRIPTS_BUCKET_NAME,
                OutputKey=info.transcript_s3_key,
                OutputEncryptionKMSKeyId=KMS_CMK_ARN,
            )
            LOGGER.info(
                "TranscribeStart ok jobName=%s mediaUri=%s outputKey=%s tryIdx=%s",
                job_name,
                media_uri,
                info.transcript_s3_key,
                try_idx,
            )
            return job_name
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConflictException":
                # Same job name already exists. Idempotent naming makes
                # this the expected outcome for re-delivered events.
                LOGGER.info(
                    "TranscribeStart conflict (already started) jobName=%s tryIdx=%s",
                    job_name,
                    try_idx,
                )
                return job_name
            if code not in _RETRYABLE_ERROR_CODES:
                LOGGER.error(
                    "TranscribeStart non-retryable error jobName=%s code=%s: %s",
                    job_name,
                    code,
                    exc,
                )
                raise
            last_error = exc
            if try_idx < _MAX_TRANSCRIBE_ATTEMPTS - 1:
                delay_s = compute_backoff_delay(try_idx)
                LOGGER.warning(
                    "TranscribeStart retryable error jobName=%s code=%s "
                    "tryIdx=%s sleep=%.3fs",
                    job_name,
                    code,
                    try_idx,
                    delay_s,
                )
                time.sleep(delay_s)
            else:
                LOGGER.error(
                    "TranscribeStart exhausted jobName=%s code=%s tryIdx=%s",
                    job_name,
                    code,
                    try_idx,
                )
    raise _TranscribeExhaustedError(last_error)


# --- DynamoDB writes ----------------------------------------------------


def _record_transcript_meta(info: RecordingKeyInfo, job_name: str) -> None:
    """Write ``transcribeJobId`` + ``transcriptS3Key`` into TranscriptMetaTable.

    ``ConditionExpression="attribute_not_exists(cycleId)"`` ensures a
    re-delivered event doesn't overwrite an earlier successful write
    (PK is named ``cycleId`` in the table schema even though it carries
    ``INBOUND#{contactId}`` for inbound records). A
    ``ConditionalCheckFailedException`` is logged at INFO and swallowed
    because it means the first writer already populated the row —
    exactly the scenario the condition exists to handle.
    """
    try:
        _TRANSCRIPT_META_TABLE.put_item(
            Item={
                "cycleId": info.meta_pk,
                "employeeIdSeq": info.meta_sk,
                "transcribeJobId": job_name,
                "transcriptS3Key": info.transcript_s3_key,
            },
            ConditionExpression="attribute_not_exists(cycleId)",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.info(
                "TranscriptMeta put_item skipped (row already exists) "
                "metaPk=%s metaSk=%s",
                info.meta_pk,
                info.meta_sk,
            )
            return
        raise


def _record_transcribe_failed_on_response(info: RecordingKeyInfo) -> None:
    """Transition Response.callResultCode ``RECORDED -> TRANSCRIBE_FAILED``.

    Outbound only. The ``ConditionExpression`` permits the overwrite
    only when ``callResultCode == "RECORDED"`` was set by Phase 6.3
    CallEndHandler — any other state (BUSY, NO_ANSWER, ERROR, etc.)
    means a TRANSCRIBE_FAILED label would be misleading.

    A failed conditional check is logged at INFO and swallowed.
    """
    try:
        _RESPONSE_TABLE.update_item(
            Key={"cycleId": info.meta_pk, "employeeId": info.employee_id},
            UpdateExpression="SET callResultCode = :code",
            ConditionExpression=(
                "attribute_exists(callResultCode) AND callResultCode = :recorded"
            ),
            ExpressionAttributeValues={
                ":code": "TRANSCRIBE_FAILED",
                ":recorded": "RECORDED",
            },
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.info(
                "Response TRANSCRIBE_FAILED transition skipped "
                "(callResultCode != RECORDED) cycleId=%s employeeId=%s",
                info.meta_pk,
                info.employee_id,
            )
            return
        raise


# --- Mock-mode (ADR-0010 Phase 16.3) -----------------------------------


def _build_mock_transcript_json(
    info: RecordingKeyInfo, job_name: str, transcript_text: str
) -> dict[str, Any]:
    """Build a minimal real-Transcribe-output-shaped JSON document.

    Mirrors the subset of Amazon Transcribe's ``GetTranscriptionJob`` /
    output-bucket JSON shape that KeywordMatcher (Phase 8.1) reads —
    specifically ``results.transcripts[0].transcript`` for the
    full-utterance text and ``results.items[]`` for token-level
    timing. KeywordMatcher only consumes the former in current code,
    but the latter is included for forward-compatibility with any
    timing-aware downstream consumer (ADR-0010 §3.5.2).

    Args:
        info: Parsed recording key. Used to make ``jobName`` traceable
            back to the originating cycle / employee / seq.
        job_name: Idempotent Transcribe job name from
            :func:`derive_transcribe_job_name`. Reused here as the
            ``jobName`` field so the mock JSON references the same
            identifier the meta table records.
        transcript_text: The full-utterance text from
            :func:`derive_mock_response`. Never None — mock-mode
            recordings only land for ``RECORDED`` outcomes (the
            caller guards against ``NO_ANSWER`` / ``BUSY`` paths).

    Returns:
        A JSON-serialisable dict matching the minimal shape that
        Amazon Transcribe writes when the job completes successfully.
    """
    return {
        "jobName": job_name,
        "status": "COMPLETED",
        "results": {
            "transcripts": [{"transcript": transcript_text}],
            "items": [
                {
                    "start_time": "0.0",
                    "end_time": "1.0",
                    "alternatives": [
                        {"confidence": "1.0", "content": transcript_text}
                    ],
                    "type": "pronunciation",
                }
            ],
        },
    }


def _put_mock_transcript(
    info: RecordingKeyInfo, payload: dict[str, Any]
) -> None:
    """Upload the mock transcript JSON to TranscriptsBucket.

    The key is sourced from ``info.transcript_s3_key`` — i.e. the same
    location the production path would have asked Amazon Transcribe to
    write via ``OutputKey``. For outbound recordings this resolves to
    ``transcripts/{cycleId}/{employeeId}/{seq}.json``, which matches the
    ``transcripts/`` prefix on ``KeywordMatcherEventRule`` so the
    downstream pipeline fires identically to the production path
    (ADR-0010 §3.6 integration table; integration with the existing
    EventRule is the whole point of reusing this key shape).

    Server-side encryption is delegated to the bucket's
    ``BucketEncryption`` configuration (SSE-KMS with the project CMK),
    which means we do NOT set ``ServerSideEncryption`` /
    ``SSEKMSKeyId`` explicitly on the PutObject call. The
    ``CmkEncryptDecryptViaS3`` role policy (existing, unchanged in
    Phase 16.3) covers the KMS GenerateDataKey side-effect.
    """
    _S3.put_object(
        Bucket=TRANSCRIPTS_BUCKET_NAME,
        Key=info.transcript_s3_key,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def _handle_mock_event(
    bucket: str, object_key: str, info: RecordingKeyInfo
) -> dict[str, Any]:
    """Run the mock-mode transcript pipeline end-to-end.

    Steps (ADR-0010 §3.5.2):
        1. Derive ``(callResultCode, transcript)`` from
           ``info.employee_id`` via
           :func:`shared.connect.mock.derive_mock_response`.
        2. Guard against ``transcript is None`` (``NO_ANSWER`` /
           ``BUSY`` outcomes). Phase 16.2 ConnectDispatcher skips the
           placeholder-wav PutObject for those outcomes, so in normal
           operation this handler never sees them; this branch is
           defensive insurance against a future refactor accidentally
           changing the upstream skip logic.
        3. Build the synthetic transcript JSON (ADR-0010 §3.5.2 shape)
           and PutObject to TranscriptsBucket at
           ``info.transcript_s3_key``.
        4. Record ``transcribeJobId`` + ``transcriptS3Key`` in
           TranscriptMetaTable via the shared
           :func:`_record_transcript_meta` helper (DRY with the
           production path; principle 19(a)).

    The Response table is NOT updated here. The production happy path
    leaves the Response row at ``callResultCode == "RECORDED"`` after
    CallEndHandler runs; KeywordMatcher (Phase 8.1) is the component
    that transitions ``RECORDED -> SAFE/INJURED/...`` once the
    transcript text is classified. In mock mode, ConnectDispatcher
    (Phase 16.2) already wrote ``contactId`` + ``dispatchedAt`` and
    incremented ``callAttempts``; KeywordMatcher will later read this
    JSON and perform the keyword-based callResultCode transition.
    Touching Response here would short-circuit that flow and break
    the integration property the mock path was built to validate.
    """
    call_result_code, transcript = derive_mock_response(info.employee_id)
    LOGGER.info(
        "TranscribeStart MOCK start bucket=%s key=%s cycleId=%s "
        "employeeId=%s seq=%s callResultCode=%s",
        bucket,
        object_key,
        info.cycle_id_or_inbound_prefix,
        info.employee_id,
        info.seq_or_contact,
        call_result_code,
    )

    if transcript is None:
        # Defensive: upstream ConnectDispatcher skips the placeholder
        # wav PutObject for NO_ANSWER / BUSY outcomes, so we should
        # never see a transcript-less response on this code path. Log
        # the unexpected condition and short-circuit rather than
        # synthesise a transcript that would mislead KeywordMatcher.
        LOGGER.warning(
            "TranscribeStart MOCK skipping (no transcript expected for "
            "callResultCode=%s) cycleId=%s employeeId=%s seq=%s",
            call_result_code,
            info.cycle_id_or_inbound_prefix,
            info.employee_id,
            info.seq_or_contact,
        )
        return {
            "status": "skipped",
            "reason": "MOCK_NO_TRANSCRIPT",
            "callResultCode": call_result_code,
        }

    job_name = derive_transcribe_job_name(info.meta_pk, info.meta_sk)
    payload = _build_mock_transcript_json(info, job_name, transcript)
    _put_mock_transcript(info, payload)
    LOGGER.info(
        "TranscribeStart MOCK put bucket=%s key=%s jobName=%s",
        TRANSCRIPTS_BUCKET_NAME,
        info.transcript_s3_key,
        job_name,
    )

    _record_transcript_meta(info, job_name)
    return {
        "status": "ok",
        "transcribeJobId": job_name,
        "transcriptS3Key": info.transcript_s3_key,
    }


# --- Entry point --------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """EventBridge S3 ObjectCreated entry point."""
    bucket, object_key = _parse_event(event)
    LOGGER.info(
        "TranscribeStart received bucket=%s key=%s mockMode=%s",
        bucket,
        object_key,
        _MOCK_MODE_ENABLED,
    )

    info = parse_recording_key(object_key)
    if info is None:
        raise ValueError(
            f"recording key did not match expected schema: key={object_key!r}"
        )

    if _MOCK_MODE_ENABLED:
        return _handle_mock_event(bucket, object_key, info)

    job_name = derive_transcribe_job_name(info.meta_pk, info.meta_sk)

    try:
        _start_transcribe_job_with_retry(job_name, bucket, object_key, info)
    except _TranscribeExhaustedError:
        # All retries exhausted. Outbound: flip callResultCode if it was
        # RECORDED. Inbound: log only — InboundHandler owns its bookkeeping.
        if info.kind == "outbound":
            _record_transcribe_failed_on_response(info)
        else:
            LOGGER.warning(
                "TranscribeStart inbound failure not propagated to Response "
                "metaPk=%s employeeId=%s contactId=%s",
                info.meta_pk,
                info.employee_id,
                info.seq_or_contact,
            )
        return {"status": "error", "kind": info.kind, "reason": "TRANSCRIBE_FAILED"}

    _record_transcript_meta(info, job_name)
    return {
        "status": "ok",
        "transcribeJobId": job_name,
        "transcriptS3Key": info.transcript_s3_key,
    }
