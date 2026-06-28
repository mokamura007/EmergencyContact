"""KeywordMatcher Lambda — classify one transcript into Voice_Status (Phase 8.1).

Triggered by EventBridge for S3 ``Object Created`` events on the
TranscriptsBucket. Responsible for:

1. **Parsing the S3 transcript key.**
   :func:`shared.recording.s3_keys.parse_transcript_key` resolves the
   key into ``meta_pk`` / ``meta_sk`` / employee_id / cycle linkage
   for both outbound
   (``transcripts/{cycleId}/{employeeId}/{seq}.json``) and inbound
   (``inbound/{yyyymm}/{employeeId}/{contactId}.json``) schemas.

   **Phase 8.1 scope (outbound only).** Inbound transcripts also flow
   through the EventBridge Rule, but resolving them to a Cycle (and
   therefore to a ``dictionaryVersion``) requires
   ``InboundContactTable`` linkage that Phase 9 InboundHandler
   introduces. For now, inbound keys raise ``ValueError`` and surface
   in the Lambda Errors metric so they are visible while Phase 9 is in
   flight. This mirrors the RecordingRelocator Phase 7.2 "Phase 9
   follow-up" pattern.

2. **Reading the transcript body from S3.** The body is the JSON
   payload Amazon Transcribe writes to ``OutputKey``;
   :func:`shared.keyword.transcript.extract_transcript_payload`
   extracts the text and an average confidence.

3. **Resolving the dictionary snapshot.** ``CycleTable.GetItem`` reads
   ``dictionaryVersion`` recorded at Cycle start (Phase 5.3 CycleApi).
   :func:`shared.dictionary.snapshot.get_dictionary_snapshot` then
   reconstructs the keyword set from ``KeywordDictionaryHistory`` for
   that version (Property 19 — invariant under wall-clock).

4. **Classifying.**
   :func:`shared.keyword.matcher.classify_voice_status` applies the
   ``INJURED > UNAVAILABLE > SAFE > OTHER`` priority (Property 10).

5. **Writing the result.**
    * Response.UpdateItem — ``voiceStatus``, ``matchedKeywords``,
      ``transcriptExcerpt`` (first 100 chars), ``dictionaryVersion``.
    * TranscriptMetaTable.UpdateItem — adds ``transcriptExcerpt``,
      ``confidence``, ``languageCode = ja-JP`` to the row that
      TranscribeStarter (Phase 6.4) put into the table when it
      started the Transcribe job. UpdateItem (vs PutItem) preserves
      the ``transcribeJobId`` and ``transcriptS3Key`` that
      TranscribeStarter wrote without us having to re-read them.

Failure semantics follow project principle 19(b): no silent fallbacks
for input-shape errors — they raise ``ValueError``. Missing Cycle row
also raises (a transcript with no Cycle is a data-integrity bug worth
surfacing). Empty dictionary snapshot is **not** an error: it yields
``voiceStatus = OTHER`` per Property 10's natural definition.

**Phase 8.4 — retryable transient-failure fallback.** Transient AWS
errors (DynamoDB / S3 throttling and internal-service errors) during
the matching pipeline are retried up to ``_MAX_KEYWORD_MATCHER_ATTEMPTS``
times with exponential backoff (reusing
:func:`shared.connect.backoff.compute_backoff_delay`, same pattern as
Phase 6.2 ConnectDispatcher and Phase 6.4 TranscribeStarter — DRY,
principle 19(a)). When retries are exhausted, the fallback writer
:func:`_record_other_fallback` sets ``voiceStatus = OTHER`` on the
Response row and the Lambda returns a non-error result so the
EventBridge / SFN orchestration treats the row as a final-classified
state. Phase 6.5 RetryEvaluator then re-evaluates the call against
``Retry_Count`` (Requirement 9.4) — exactly the same code path used
when the transcript naturally contains no keywords.
"""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.connect.backoff import compute_backoff_delay
from shared.dictionary.snapshot import get_dictionary_snapshot
from shared.keyword.matcher import classify_voice_status
from shared.keyword.transcript import extract_transcript_payload
from shared.recording.s3_keys import RecordingKeyInfo, parse_transcript_key

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

TRANSCRIPTS_BUCKET_NAME = os.environ["TRANSCRIPTS_BUCKET_NAME"]
TRANSCRIPT_META_TABLE_NAME = os.environ["TRANSCRIPT_META_TABLE_NAME"]
RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]
CYCLE_TABLE_NAME = os.environ["CYCLE_TABLE_NAME"]
KEYWORD_DICT_HISTORY_TABLE_NAME = os.environ["KEYWORD_DICT_HISTORY_TABLE_NAME"]

#: Transcript excerpt length recorded on Response / TranscriptMeta.
#: Requirement 7.7 + design.md Keyword_Matcher: 先頭 100 文字.
_EXCERPT_MAX_CHARS = 100

#: Total matching-pipeline attempts (initial + retries). Design.md
#: Error Handling — "Lambda 内で最大 3 回再試行".
_MAX_KEYWORD_MATCHER_ATTEMPTS = 3

#: AWS error codes that warrant a backoff-and-retry loop. The set
#: mirrors typical DynamoDB and S3 transient-failure codes — the same
#: catalog Phase 6.4 TranscribeStarter retries on.
_RETRYABLE_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
        "InternalServerError",
        "ServiceUnavailable",
        "SlowDown",
    }
)

_DDB = boto3.resource("dynamodb")
_TRANSCRIPT_META_TABLE = _DDB.Table(TRANSCRIPT_META_TABLE_NAME)
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)
_CYCLE_TABLE = _DDB.Table(CYCLE_TABLE_NAME)
_S3 = boto3.client("s3")


class _KeywordMatcherExhaustedError(Exception):
    """Raised when ``_MAX_KEYWORD_MATCHER_ATTEMPTS`` retryable errors occur."""

    def __init__(self, last_error: ClientError | None) -> None:
        super().__init__(
            f"KeywordMatcher exhausted retries (last={last_error!r})"
        )
        self.last_error = last_error


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


# --- S3 read ------------------------------------------------------------


def _read_transcript_body(bucket: str, key: str) -> dict[str, Any]:
    """``GetObject`` the JSON transcript and parse the body.

    Raises:
        ClientError: propagated for any S3 error (Phase 12 alarms will
            surface ``Errors`` count).
        ValueError: when the body is not valid JSON or not a dict.
    """
    resp = _S3.get_object(Bucket=bucket, Key=key)
    raw = resp["Body"].read()
    try:
        body = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"transcript body is not valid JSON: bucket={bucket!r} key={key!r}"
        ) from exc
    if not isinstance(body, dict):
        raise ValueError(
            f"transcript body must decode to a JSON object; "
            f"got {type(body).__name__}: bucket={bucket!r} key={key!r}"
        )
    return body


# --- Cycle / dictionary lookup -----------------------------------------


def _resolve_dictionary_version(cycle_id: str) -> int:
    """Read ``dictionaryVersion`` from CycleTable for ``cycle_id``.

    Raises:
        ValueError: when the Cycle row is missing, or when the row has
            no ``dictionaryVersion`` attribute, or when the value
            cannot be coerced to ``int``.
    """
    resp = _CYCLE_TABLE.get_item(Key={"cycleId": cycle_id})
    item = resp.get("Item")
    if item is None:
        raise ValueError(
            f"Cycle row not found in CycleTable; cycleId={cycle_id!r}"
        )
    raw = item.get("dictionaryVersion")
    if raw is None:
        raise ValueError(
            f"Cycle row missing dictionaryVersion; cycleId={cycle_id!r} item={item!r}"
        )
    try:
        return int(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Cycle.dictionaryVersion not int-coercible; "
            f"cycleId={cycle_id!r} value={raw!r}"
        ) from exc


# --- DynamoDB writes ----------------------------------------------------


def _record_response(
    info: RecordingKeyInfo,
    voice_status: str,
    matched_keywords: list[str],
    excerpt: str,
    dictionary_version: int,
) -> None:
    """Write classification fields onto the Response row.

    Outbound only. The Key uses ``cycleId`` / ``employeeId``; the
    Response row was created by Phase 5.3 CycleApi at Cycle start and
    has been mutated by Phase 6.2 (callAttempts) / Phase 6.3
    (callResultCode). KeywordMatcher only ``SET`` s its four fields;
    nothing on the row is removed.

    There is no ``ConditionExpression``: the design intentionally
    permits a later transcript (e.g. an inbound follow-up call within
    the same Cycle, Phase 9) to overwrite the four fields with the
    most-recent classification (per design.md Keyword_Matcher 出力
    "Response テーブルに ... を書込" + Phase 9.2 "Response を最新の
    Voice_Status で更新").

    ``matchedKeywords`` is written as a DynamoDB List of String (``L<S>``)
    matching the schema in design.md D3 Response.
    """
    _RESPONSE_TABLE.update_item(
        Key={"cycleId": info.meta_pk, "employeeId": info.employee_id},
        UpdateExpression=(
            "SET voiceStatus = :vs, matchedKeywords = :mk, "
            "transcriptExcerpt = :ex, dictionaryVersion = :dv"
        ),
        ExpressionAttributeValues={
            ":vs": voice_status,
            ":mk": matched_keywords,
            ":ex": excerpt,
            ":dv": dictionary_version,
        },
    )


def _record_transcript_meta(
    info: RecordingKeyInfo,
    excerpt: str,
    confidence: float,
) -> None:
    """Add KeywordMatcher-side fields to the TranscriptMeta row.

    Phase 6.4 TranscribeStarter wrote the row with ``transcribeJobId``
    + ``transcriptS3Key`` at job-start time (gated by
    ``attribute_not_exists(cycleId)``). KeywordMatcher uses
    ``UpdateItem`` here to *augment* that row with the post-completion
    fields without disturbing what TranscribeStarter wrote.

    ``confidence`` is stored as a Decimal-compatible value. DynamoDB
    refuses native ``float`` for ``N`` types — boto3's serializer
    converts ``Decimal`` instances faithfully; we pre-convert via
    string so the conversion is lossless and explicit.
    """
    confidence_decimal = Decimal(str(confidence))
    _TRANSCRIPT_META_TABLE.update_item(
        Key={"cycleId": info.meta_pk, "employeeIdSeq": info.meta_sk},
        UpdateExpression=(
            "SET transcriptExcerpt = :ex, confidence = :conf, "
            "languageCode = :lang"
        ),
        ExpressionAttributeValues={
            ":ex": excerpt,
            ":conf": confidence_decimal,
            ":lang": "ja-JP",
        },
    )


# --- Helpers ------------------------------------------------------------


def _truncate_excerpt(text: str) -> str:
    """Return the first ``_EXCERPT_MAX_CHARS`` characters of ``text``.

    String slicing in Python is by code point, not byte, which is what
    Requirement 7.7 means by "100 文字" — i.e. 100 user-visible
    characters not 100 bytes.
    """
    return text[:_EXCERPT_MAX_CHARS]


# --- Entry point --------------------------------------------------------


def _run_matching_pipeline(
    bucket: str, key: str, info: RecordingKeyInfo
) -> dict[str, Any]:
    """Run the full S3 read → classify → DDB write pipeline once.

    Extracted from :func:`lambda_handler` so :func:`_run_with_retry`
    can invoke it inside a retry loop. The pipeline is idempotent
    (DynamoDB ``UpdateItem`` with ``SET`` semantics), so re-running
    after a transient mid-pipeline failure is safe — a later attempt
    overwrites the same fields with the same value.

    Raises:
        ClientError: any S3 / DynamoDB error. The caller distinguishes
            retryable vs. non-retryable codes.
        ValueError: data-integrity errors (missing Cycle row, missing
            ``dictionaryVersion``, malformed transcript body). These
            are **not** retried — they propagate as-is.
    """
    body = _read_transcript_body(bucket, key)
    text, confidence = extract_transcript_payload(body)
    LOGGER.info(
        "KeywordMatcher transcript bucket=%s key=%s textLen=%d avgConfidence=%.4f",
        bucket,
        key,
        len(text),
        confidence,
    )

    dictionary_version = _resolve_dictionary_version(info.meta_pk)
    dictionary = get_dictionary_snapshot(
        dictionary_version, table_name=KEYWORD_DICT_HISTORY_TABLE_NAME
    )

    voice_status, matched_keywords = classify_voice_status(text, dictionary)
    excerpt = _truncate_excerpt(text)
    LOGGER.info(
        "KeywordMatcher classify cycleId=%s employeeId=%s seq=%s "
        "voiceStatus=%s matchedKeywords=%s dictionaryVersion=%s",
        info.meta_pk,
        info.employee_id,
        info.seq_or_contact,
        voice_status,
        matched_keywords,
        dictionary_version,
    )

    _record_response(
        info, voice_status, matched_keywords, excerpt, dictionary_version
    )
    _record_transcript_meta(info, excerpt, confidence)

    return {
        "status": "ok",
        "cycleId": info.meta_pk,
        "employeeId": info.employee_id,
        "seq": info.seq_or_contact,
        "voiceStatus": voice_status,
        "matchedKeywords": matched_keywords,
        "dictionaryVersion": dictionary_version,
        "confidence": confidence,
    }


def _run_with_retry(
    bucket: str, key: str, info: RecordingKeyInfo
) -> dict[str, Any]:
    """Invoke :func:`_run_matching_pipeline` with bounded retry loop.

    Retries are applied only to AWS ``ClientError`` exceptions whose
    error code appears in :data:`_RETRYABLE_ERROR_CODES`. Non-retryable
    ``ClientError`` codes and any non-``ClientError`` exception
    (including ``ValueError`` from data-integrity checks) propagate
    immediately — they are programming or data bugs that retrying
    cannot heal.

    Returns:
        The result dict from :func:`_run_matching_pipeline` on success.

    Raises:
        _KeywordMatcherExhaustedError: after
            ``_MAX_KEYWORD_MATCHER_ATTEMPTS`` retryable failures. The
            caller handles fallback to ``voiceStatus = OTHER``.
        ClientError: on the first non-retryable AWS error.
        ValueError / other Exception: propagated from
            :func:`_run_matching_pipeline` unchanged.
    """
    last_error: ClientError | None = None
    for try_idx in range(_MAX_KEYWORD_MATCHER_ATTEMPTS):
        try:
            return _run_matching_pipeline(bucket, key, info)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in _RETRYABLE_ERROR_CODES:
                LOGGER.error(
                    "KeywordMatcher non-retryable ClientError "
                    "cycleId=%s employeeId=%s code=%s: %s",
                    info.meta_pk,
                    info.employee_id,
                    code,
                    exc,
                )
                raise
            last_error = exc
            if try_idx < _MAX_KEYWORD_MATCHER_ATTEMPTS - 1:
                delay_s = compute_backoff_delay(try_idx)
                LOGGER.warning(
                    "KeywordMatcher retryable error cycleId=%s employeeId=%s "
                    "code=%s tryIdx=%s sleep=%.3fs",
                    info.meta_pk,
                    info.employee_id,
                    code,
                    try_idx,
                    delay_s,
                )
                time.sleep(delay_s)
            else:
                LOGGER.error(
                    "KeywordMatcher exhausted retries cycleId=%s employeeId=%s "
                    "code=%s tryIdx=%s",
                    info.meta_pk,
                    info.employee_id,
                    code,
                    try_idx,
                )
    raise _KeywordMatcherExhaustedError(last_error)


def _record_other_fallback(
    info: RecordingKeyInfo, last_error: ClientError | None
) -> None:
    """Final-failure fallback: write ``voiceStatus = OTHER`` on Response.

    Requirement 9.4 + design.md Error Handling table — when the
    matching pipeline cannot complete after retries, the Response row
    is finalized as ``OTHER`` so Phase 6.5 RetryEvaluator can decide
    whether to re-dispatch the call (累積発信回数 < Retry_Count なら
    再発信キューへ追加) or roll the row up to ``UNREACHABLE`` on
    retry-count exhaustion.

    ``matchedKeywords`` is set to ``[]`` because no keywords were
    successfully evaluated. ``transcriptExcerpt`` and
    ``dictionaryVersion`` are intentionally left untouched — they may
    have been populated by an earlier attempt or by Cycle creation,
    and overwriting with empty values would lose forensic context.

    CloudWatch Logs gets a ``WARNING`` line containing the last
    underlying error — this is the audit trail Phase 12 alarms will
    pivot on.

    If the fallback ``UpdateItem`` itself fails, the exception
    propagates: EventBridge will redeliver the S3 event and the whole
    handler runs again. That redelivery is the system-level retry
    boundary; in-Lambda retries are not the right place to retry the
    fallback itself.
    """
    LOGGER.warning(
        "KeywordMatcher final-failure fallback cycleId=%s employeeId=%s seq=%s "
        "voiceStatus=OTHER lastError=%r",
        info.meta_pk,
        info.employee_id,
        info.seq_or_contact,
        last_error,
    )
    _RESPONSE_TABLE.update_item(
        Key={"cycleId": info.meta_pk, "employeeId": info.employee_id},
        UpdateExpression="SET voiceStatus = :vs, matchedKeywords = :mk",
        ExpressionAttributeValues={
            ":vs": "OTHER",
            ":mk": [],
        },
    )


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """EventBridge S3 ObjectCreated entry point."""
    bucket, key = _parse_event(event)
    LOGGER.info("KeywordMatcher received bucket=%s key=%s", bucket, key)

    info = parse_transcript_key(key)
    if info is None:
        raise ValueError(
            f"transcript key did not match expected schema: key={key!r}"
        )

    if info.kind != "outbound":
        # Phase 9 will replace this raise with the InboundContactTable
        # lookup path. Surfacing as a Lambda error keeps the inbound
        # transcripts visible (CloudWatch metric) rather than silently
        # accumulating in S3 without classification.
        raise ValueError(
            f"inbound transcripts are not handled in Phase 8.1; "
            f"key={key!r} (Phase 9 InboundHandler will own this path)"
        )

    try:
        return _run_with_retry(bucket, key, info)
    except _KeywordMatcherExhaustedError as exc:
        _record_other_fallback(info, exc.last_error)
        return {
            "status": "fallback",
            "cycleId": info.meta_pk,
            "employeeId": info.employee_id,
            "seq": info.seq_or_contact,
            "voiceStatus": "OTHER",
            "matchedKeywords": [],
            "reason": "MATCHING_FAILED",
        }
