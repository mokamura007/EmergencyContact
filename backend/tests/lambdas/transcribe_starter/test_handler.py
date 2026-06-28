"""Unit tests for the TranscribeStarter Lambda (Phase 6.4).

Covers the ten mandatory cases from the task brief:
    1.  Outbound happy path — Transcribe job started + TranscriptMeta written.
    2.  Inbound happy path — meta_pk = "INBOUND#{contactId}", meta_sk = "{emp}#0".
    3.  Throttling once then success.
    4.  Outbound throttling 3x — Response.callResultCode → TRANSCRIBE_FAILED
        with ConditionExpression "RECORDED only" verified.
    5.  Inbound throttling 3x — Response is NOT touched.
    6.  ConflictException (job already exists) — treated as ok, TranscriptMeta
        write still performed for idempotency.
    7.  Malformed S3 key — ValueError, no Transcribe call.
    8.  Non-dict event — ValueError.
    9.  Missing detail.object.key — ValueError.
    10. callResultCode != RECORDED on TRANSCRIBE_FAILED write — swallowed
        via ConditionalCheckFailedException, handler still returns error.

Phase 16.3 mock-mode tests (added below the production-path section):
    * Mock 1-4: employeeId suffix 0 / 3 / 5 / 9 (RECORDED outcomes only;
      7 / 8 never reach this handler because Phase 16.2 ConnectDispatcher
      skips the placeholder wav PutObject for NO_ANSWER / BUSY).
    * Mock 5: KeywordMatcher input shape — the synthetic JSON must
      mirror the real Transcribe output JSON exactly so the existing
      Phase 8.1 parser accepts it unchanged.

The handler talks to AWS through four module-level globals:
    * ``handler._TRANSCRIBE``             — ``transcribe`` client
    * ``handler._TRANSCRIPT_META_TABLE``  — ``DynamoDB.Table``
    * ``handler._RESPONSE_TABLE``         — ``DynamoDB.Table``
    * ``handler._S3``                     — ``s3`` client (mock mode only)
Each is swapped out for a :class:`MagicMock` per test, and ``time.sleep``
is replaced with a no-op via a fixture to keep the suite fast.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.transcribe_starter import handler

# --- Fixtures ----------------------------------------------------------


@pytest.fixture
def mock_transcribe(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="TranscribeClient")
    monkeypatch.setattr(handler, "_TRANSCRIBE", client)
    return client


@pytest.fixture
def mock_meta_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="TranscriptMetaTable")
    monkeypatch.setattr(handler, "_TRANSCRIPT_META_TABLE", table)
    return table


@pytest.fixture
def mock_response_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="ResponseTable")
    monkeypatch.setattr(handler, "_RESPONSE_TABLE", table)
    return table


@pytest.fixture
def silence_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``time.sleep`` inside the handler module with a counter."""
    sleeper = MagicMock(name="time.sleep")
    monkeypatch.setattr(handler.time, "sleep", sleeper)
    return sleeper


# --- Helpers -----------------------------------------------------------


def _outbound_event(key: str = "recordings/cycle-1/emp-1/0.wav") -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-id",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {
                "name": "safety-confirmation-recordings-test-111122223333-ap-northeast-1"
            },
            "object": {"key": key},
        },
    }


def _inbound_event(
    key: str = "inbound/202606/emp-2/contact-abc.wav",
) -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-id-inbound",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {
                "name": "safety-confirmation-recordings-test-111122223333-ap-northeast-1"
            },
            "object": {"key": key},
        },
    }


def _client_error(code: str, op: str = "StartTranscriptionJob") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": f"{code} simulated"}},
        operation_name=op,
    )


# --- Test 1: outbound happy path --------------------------------------


def test_outbound_success_starts_job_and_writes_meta(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_transcribe.start_transcription_job.return_value = {}

    result = handler.lambda_handler(_outbound_event(), None)

    expected_job_name = "safety-confirm-cycle-1-emp-1-0"
    assert result == {
        "status": "ok",
        "transcribeJobId": expected_job_name,
        "transcriptS3Key": "transcripts/cycle-1/emp-1/0.json",
    }

    # Transcribe StartJob with expected arguments.
    mock_transcribe.start_transcription_job.assert_called_once()
    tx_kwargs = mock_transcribe.start_transcription_job.call_args.kwargs
    assert tx_kwargs["TranscriptionJobName"] == expected_job_name
    assert tx_kwargs["LanguageCode"] == "ja-JP"
    assert tx_kwargs["MediaFormat"] == "wav"
    assert tx_kwargs["Media"]["MediaFileUri"] == (
        "s3://safety-confirmation-recordings-test-111122223333-ap-northeast-1/"
        "recordings/cycle-1/emp-1/0.wav"
    )
    assert tx_kwargs["OutputBucketName"] == handler.TRANSCRIPTS_BUCKET_NAME
    assert tx_kwargs["OutputKey"] == "transcripts/cycle-1/emp-1/0.json"
    assert tx_kwargs["OutputEncryptionKMSKeyId"] == handler.KMS_CMK_ARN

    # TranscriptMeta put_item with idempotency guard.
    mock_meta_table.put_item.assert_called_once()
    meta_kwargs = mock_meta_table.put_item.call_args.kwargs
    assert meta_kwargs["Item"] == {
        "cycleId": "cycle-1",
        "employeeIdSeq": "emp-1#0",
        "transcribeJobId": expected_job_name,
        "transcriptS3Key": "transcripts/cycle-1/emp-1/0.json",
    }
    assert meta_kwargs["ConditionExpression"] == "attribute_not_exists(cycleId)"

    # Response table is NOT touched on the happy path.
    mock_response_table.update_item.assert_not_called()
    silence_sleep.assert_not_called()


# --- Test 2: inbound happy path ---------------------------------------


def test_inbound_success_uses_inbound_meta_keys(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_transcribe.start_transcription_job.return_value = {}

    result = handler.lambda_handler(_inbound_event(), None)

    expected_job_name = "safety-confirm-INBOUND-contact-abc-emp-2-0"
    assert result == {
        "status": "ok",
        "transcribeJobId": expected_job_name,
        "transcriptS3Key": "inbound/202606/emp-2/contact-abc.json",
    }

    tx_kwargs = mock_transcribe.start_transcription_job.call_args.kwargs
    assert tx_kwargs["OutputKey"] == "inbound/202606/emp-2/contact-abc.json"

    meta_kwargs = mock_meta_table.put_item.call_args.kwargs
    assert meta_kwargs["Item"]["cycleId"] == "INBOUND#contact-abc"
    assert meta_kwargs["Item"]["employeeIdSeq"] == "emp-2#0"
    mock_response_table.update_item.assert_not_called()


# --- Test 3: throttling once then success -----------------------------


def test_throttling_then_success_sleeps_once(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_transcribe.start_transcription_job.side_effect = [
        _client_error("ThrottlingException"),
        {},
    ]

    result = handler.lambda_handler(_outbound_event(), None)

    assert result["status"] == "ok"
    assert mock_transcribe.start_transcription_job.call_count == 2
    # One sleep between failed first try and successful second try.
    assert silence_sleep.call_count == 1
    mock_meta_table.put_item.assert_called_once()
    mock_response_table.update_item.assert_not_called()


# --- Test 4: outbound 3x throttling → TRANSCRIBE_FAILED ---------------


def test_outbound_throttling_three_times_writes_transcribe_failed(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_transcribe.start_transcription_job.side_effect = [
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
    ]

    result = handler.lambda_handler(_outbound_event(), None)

    assert result == {
        "status": "error",
        "kind": "outbound",
        "reason": "TRANSCRIBE_FAILED",
    }
    assert mock_transcribe.start_transcription_job.call_count == 3
    # Sleeps between try 0->1 and try 1->2 only (no sleep after final failure).
    assert silence_sleep.call_count == 2

    # No TranscriptMeta write on the failure path.
    mock_meta_table.put_item.assert_not_called()

    # Response UpdateItem with TRANSCRIBE_FAILED + RECORDED-only condition.
    mock_response_table.update_item.assert_called_once()
    upd_kwargs = mock_response_table.update_item.call_args.kwargs
    assert upd_kwargs["Key"] == {"cycleId": "cycle-1", "employeeId": "emp-1"}
    assert upd_kwargs["UpdateExpression"] == "SET callResultCode = :code"
    assert upd_kwargs["ConditionExpression"] == (
        "attribute_exists(callResultCode) AND callResultCode = :recorded"
    )
    assert upd_kwargs["ExpressionAttributeValues"] == {
        ":code": "TRANSCRIBE_FAILED",
        ":recorded": "RECORDED",
    }


# --- Test 5: inbound 3x throttling → Response untouched ---------------


def test_inbound_throttling_three_times_does_not_touch_response(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_transcribe.start_transcription_job.side_effect = [
        _client_error("LimitExceededException"),
        _client_error("LimitExceededException"),
        _client_error("LimitExceededException"),
    ]

    result = handler.lambda_handler(_inbound_event(), None)

    assert result == {
        "status": "error",
        "kind": "inbound",
        "reason": "TRANSCRIBE_FAILED",
    }
    assert mock_transcribe.start_transcription_job.call_count == 3
    # Critical: inbound failures must NOT mutate Response (Phase 9 owns
    # InboundContactTable). Response.update_item is never called.
    mock_response_table.update_item.assert_not_called()
    mock_meta_table.put_item.assert_not_called()


# --- Test 6: ConflictException treated as success ---------------------


def test_conflict_exception_is_treated_as_ok_and_meta_still_written(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """Re-delivered event: same idempotent job name → ConflictException → ok."""
    mock_transcribe.start_transcription_job.side_effect = _client_error(
        "ConflictException"
    )

    result = handler.lambda_handler(_outbound_event(), None)

    assert result["status"] == "ok"
    assert result["transcribeJobId"] == "safety-confirm-cycle-1-emp-1-0"
    assert mock_transcribe.start_transcription_job.call_count == 1
    # No sleep — ConflictException short-circuits the retry loop.
    silence_sleep.assert_not_called()
    # Idempotent meta-write still attempted (the condition_not_exists guard
    # handles concurrent re-deliveries).
    mock_meta_table.put_item.assert_called_once()
    mock_response_table.update_item.assert_not_called()


# --- Test 7: malformed S3 key -----------------------------------------


def test_malformed_s3_key_raises_value_error(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
) -> None:
    bad_event = _outbound_event(key="random/key.mp3")
    with pytest.raises(
        ValueError, match="recording key did not match expected schema"
    ):
        handler.lambda_handler(bad_event, None)

    mock_transcribe.start_transcription_job.assert_not_called()
    mock_meta_table.put_item.assert_not_called()
    mock_response_table.update_item.assert_not_called()


# --- Test 8: non-dict event -------------------------------------------


def test_non_dict_event_raises_value_error(
    mock_transcribe: MagicMock,
) -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        handler.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]
    mock_transcribe.start_transcription_job.assert_not_called()


# --- Test 9: missing detail.object.key --------------------------------


def test_missing_object_key_raises_value_error(
    mock_transcribe: MagicMock,
) -> None:
    event = _outbound_event()
    del event["detail"]["object"]["key"]
    with pytest.raises(ValueError, match="event.detail.object.key is required"):
        handler.lambda_handler(event, None)
    mock_transcribe.start_transcription_job.assert_not_called()


def test_missing_bucket_name_raises_value_error(
    mock_transcribe: MagicMock,
) -> None:
    """Companion to the key-missing test — covers detail.bucket.name path."""
    event = _outbound_event()
    del event["detail"]["bucket"]["name"]
    with pytest.raises(ValueError, match="event.detail.bucket.name is required"):
        handler.lambda_handler(event, None)
    mock_transcribe.start_transcription_job.assert_not_called()


# --- Test 10: non-RECORDED state on TRANSCRIBE_FAILED write swallowed -


def test_transcribe_failed_write_swallows_conditional_check_failed(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """callResultCode is BUSY (or anything != RECORDED) → conditional check fails.

    The handler must still return the error sentinel and NOT crash, because
    a non-RECORDED state means the recording never came in cleanly to begin
    with and TRANSCRIBE_FAILED would be misleading. The first writer's
    callResultCode is the source of truth.
    """
    mock_transcribe.start_transcription_job.side_effect = [
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
    ]
    mock_response_table.update_item.side_effect = ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "callResultCode is not RECORDED",
            }
        },
        operation_name="UpdateItem",
    )

    result = handler.lambda_handler(_outbound_event(), None)

    assert result == {
        "status": "error",
        "kind": "outbound",
        "reason": "TRANSCRIBE_FAILED",
    }
    # The write was attempted exactly once (and swallowed).
    mock_response_table.update_item.assert_called_once()
    mock_meta_table.put_item.assert_not_called()


# --- Extra: non-retryable Transcribe error propagates -----------------


def test_non_retryable_transcribe_error_propagates(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
) -> None:
    """Any error besides retryable / Conflict must surface to the caller."""
    mock_transcribe.start_transcription_job.side_effect = _client_error(
        "BadRequestException"
    )
    with pytest.raises(ClientError):
        handler.lambda_handler(_outbound_event(), None)
    mock_meta_table.put_item.assert_not_called()
    mock_response_table.update_item.assert_not_called()


# --- Extra: TranscriptMeta ConditionalCheckFailed is swallowed ---------


def test_transcript_meta_conditional_check_failed_swallowed(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """A re-delivered event after a previous successful write must not crash."""
    mock_transcribe.start_transcription_job.return_value = {}
    mock_meta_table.put_item.side_effect = ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "row already exists",
            }
        },
        operation_name="PutItem",
    )

    result = handler.lambda_handler(_outbound_event(), None)

    # The handler still returns the ok sentinel because the first writer
    # is the source of truth and we have nothing to add.
    assert result["status"] == "ok"
    mock_meta_table.put_item.assert_called_once()
    mock_response_table.update_item.assert_not_called()


# ======================================================================
# Mock-mode tests (ADR-0010, Phase 16.3)
# ======================================================================
#
# Each test below enables the mock path via ``_MOCK_MODE_ENABLED`` rather
# than re-importing ``handler`` with ``MOCK_MODE=true`` in the
# environment. The module-level constant is computed at import time, so
# in-test ``monkeypatch.setenv`` would have no effect; patching the
# constant directly is the cleanest equivalent and matches the runtime
# semantics (the handler reads ``_MOCK_MODE_ENABLED``, not ``os.environ``,
# on every invocation). Mirrors the Phase 16.2 ConnectDispatcher pattern.


@pytest.fixture
def mock_s3(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="S3Client")
    monkeypatch.setattr(handler, "_S3", client)
    return client


@pytest.fixture
def enable_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip the module-level ``_MOCK_MODE_ENABLED`` flag for one test."""
    monkeypatch.setattr(handler, "_MOCK_MODE_ENABLED", True)


def _put_object_payload(mock_s3: MagicMock) -> dict[str, Any]:
    """Decode the JSON body passed to S3.put_object back to a dict.

    The handler serialises with ``ensure_ascii=False`` so Japanese
    glyphs round-trip cleanly; we decode with the same expectation.
    """
    kwargs = mock_s3.put_object.call_args.kwargs
    body = kwargs["Body"]
    assert isinstance(body, bytes), "Body must be bytes for S3 PutObject"
    return json.loads(body.decode("utf-8"))


# --- Mock test 1: employeeId suffix "0" → RECORDED + SAFE -------------


def test_mock_suffix_0_writes_safe_transcript_json(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    """末尾 0: RECORDED + SAFE → transcript='無事です' を JSON で投入する."""
    event = _outbound_event(key="recordings/cycle-1/emp-0/0.wav")

    result = handler.lambda_handler(event, None)

    expected_job_name = "safety-confirm-cycle-1-emp-0-0"
    expected_key = "transcripts/cycle-1/emp-0/0.json"
    assert result == {
        "status": "ok",
        "transcribeJobId": expected_job_name,
        "transcriptS3Key": expected_key,
    }

    # No real Transcribe call in mock mode.
    mock_transcribe.start_transcription_job.assert_not_called()

    # Synthetic transcript JSON uploaded to TranscriptsBucket at the
    # production key shape (``transcripts/`` prefix matches
    # KeywordMatcherEventRule's filter).
    mock_s3.put_object.assert_called_once()
    s3_kwargs = mock_s3.put_object.call_args.kwargs
    assert s3_kwargs["Bucket"] == handler.TRANSCRIPTS_BUCKET_NAME
    assert s3_kwargs["Key"] == expected_key
    assert s3_kwargs["ContentType"] == "application/json"

    payload = _put_object_payload(mock_s3)
    assert payload["jobName"] == expected_job_name
    assert payload["status"] == "COMPLETED"
    assert payload["results"]["transcripts"][0]["transcript"] == "無事です"
    assert payload["results"]["items"][0]["alternatives"][0]["content"] == "無事です"

    # TranscriptMeta receives the same job name + transcript key — same
    # idempotency guard as the production path.
    mock_meta_table.put_item.assert_called_once()
    meta_kwargs = mock_meta_table.put_item.call_args.kwargs
    assert meta_kwargs["Item"] == {
        "cycleId": "cycle-1",
        "employeeIdSeq": "emp-0#0",
        "transcribeJobId": expected_job_name,
        "transcriptS3Key": expected_key,
    }
    assert meta_kwargs["ConditionExpression"] == "attribute_not_exists(cycleId)"

    # Response is intentionally untouched — KeywordMatcher (Phase 8.1)
    # owns the keyword-based callResultCode transition.
    mock_response_table.update_item.assert_not_called()


# --- Mock test 2: employeeId suffix "3" → RECORDED + INJURED ----------


def test_mock_suffix_3_writes_injured_transcript_json(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    """末尾 3: RECORDED + INJURED → transcript='怪我をしました'."""
    event = _outbound_event(key="recordings/cycle-2/emp-3/1.wav")

    result = handler.lambda_handler(event, None)

    expected_key = "transcripts/cycle-2/emp-3/1.json"
    assert result["transcriptS3Key"] == expected_key

    payload = _put_object_payload(mock_s3)
    assert payload["results"]["transcripts"][0]["transcript"] == "怪我をしました"
    assert (
        payload["results"]["items"][0]["alternatives"][0]["content"]
        == "怪我をしました"
    )
    mock_transcribe.start_transcription_job.assert_not_called()
    mock_response_table.update_item.assert_not_called()


# --- Mock test 3: employeeId suffix "5" → RECORDED + UNAVAILABLE ------


def test_mock_suffix_5_writes_unavailable_transcript_json(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    """末尾 5: RECORDED + UNAVAILABLE → transcript='動けません'."""
    event = _outbound_event(key="recordings/cycle-3/emp-5/2.wav")

    result = handler.lambda_handler(event, None)

    expected_key = "transcripts/cycle-3/emp-5/2.json"
    assert result["transcriptS3Key"] == expected_key

    payload = _put_object_payload(mock_s3)
    assert payload["results"]["transcripts"][0]["transcript"] == "動けません"

    # Meta key reflects the multi-attempt seq (=2, not 0).
    meta_kwargs = mock_meta_table.put_item.call_args.kwargs
    assert meta_kwargs["Item"]["employeeIdSeq"] == "emp-5#2"
    assert meta_kwargs["Item"]["transcribeJobId"] == "safety-confirm-cycle-3-emp-5-2"
    mock_transcribe.start_transcription_job.assert_not_called()


# --- Mock test 4: employeeId suffix "9" → RECORDED + OTHER ------------


def test_mock_suffix_9_writes_other_transcript_json(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    """末尾 9: RECORDED + 辞書非該当 → transcript='あいうえお'.

    KeywordMatcher 経由で UNREACHABLE 確定する経路の保証。
    """
    event = _outbound_event(key="recordings/cycle-4/emp-9/0.wav")

    result = handler.lambda_handler(event, None)

    expected_key = "transcripts/cycle-4/emp-9/0.json"
    assert result["transcriptS3Key"] == expected_key

    payload = _put_object_payload(mock_s3)
    assert payload["results"]["transcripts"][0]["transcript"] == "あいうえお"
    # All four RECORDED outcomes share the same wrapper shape.
    assert payload["status"] == "COMPLETED"
    assert payload["jobName"] == "safety-confirm-cycle-4-emp-9-0"
    mock_transcribe.start_transcription_job.assert_not_called()


# --- Mock test 5: KeywordMatcher input shape — synthetic JSON parses ----
#
# The synthetic transcript JSON must mirror the real Transcribe output
# shape exactly so KeywordMatcher (Phase 8.1) can consume it without
# any conditional handling. This test pins:
#   * top-level keys: jobName, status, results
#   * results.transcripts[0].transcript (KeywordMatcher's primary input)
#   * results.items[0].alternatives[0].content (token-level shape)
#   * results.items[0].type / start_time / end_time (timing shape)
#   * status == "COMPLETED" (the only status KeywordMatcher should see)


def test_mock_transcript_json_matches_real_transcribe_shape(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    """擬似 JSON は KeywordMatcher の入力形式 (実 Transcribe output 形式) と一致する."""
    event = _outbound_event(key="recordings/cycle-shape/emp-1/0.wav")

    handler.lambda_handler(event, None)

    payload = _put_object_payload(mock_s3)

    # Top-level wrapper.
    assert set(payload.keys()) >= {"jobName", "status", "results"}
    assert payload["status"] == "COMPLETED"
    assert payload["jobName"].startswith("safety-confirm-")

    # results.transcripts shape (the field KeywordMatcher reads first).
    assert "transcripts" in payload["results"]
    assert isinstance(payload["results"]["transcripts"], list)
    assert len(payload["results"]["transcripts"]) >= 1
    assert "transcript" in payload["results"]["transcripts"][0]
    transcript_text = payload["results"]["transcripts"][0]["transcript"]
    assert isinstance(transcript_text, str)
    assert transcript_text == "大丈夫です"  # employeeId suffix "1" → SAFE alt

    # results.items shape (token-level, optional for KeywordMatcher today
    # but pinned here to guarantee forward-compatibility).
    assert "items" in payload["results"]
    items = payload["results"]["items"]
    assert isinstance(items, list)
    assert len(items) >= 1
    item = items[0]
    assert item["type"] == "pronunciation"
    assert "start_time" in item and "end_time" in item
    assert isinstance(item["alternatives"], list)
    assert len(item["alternatives"]) >= 1
    alt = item["alternatives"][0]
    assert "content" in alt
    assert "confidence" in alt
    assert alt["content"] == transcript_text

    # JSON must be re-encodable so KeywordMatcher's S3 GetObject + parse
    # round-trips without surprise (no NaN / Infinity / non-JSON values).
    re_encoded = json.dumps(payload, ensure_ascii=False)
    assert json.loads(re_encoded) == payload


# --- Mock test 6 (extra defensive): transcript=None short-circuit ------
#
# Phase 16.2 ConnectDispatcher skips the placeholder wav PutObject for
# ``NO_ANSWER`` / ``BUSY`` outcomes, so this handler should never
# observe a transcript-less employeeId in normal operation. The handler
# still has a defensive guard; this test pins that behaviour against a
# future refactor accidentally removing the upstream skip.


def test_mock_suffix_7_short_circuits_without_putobject(
    mock_transcribe: MagicMock,
    mock_meta_table: MagicMock,
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    """末尾 7 (NO_ANSWER) が誤って届いた場合: PutObject せず skipped を返す."""
    event = _outbound_event(key="recordings/cycle-x/emp-7/0.wav")

    result = handler.lambda_handler(event, None)

    assert result == {
        "status": "skipped",
        "reason": "MOCK_NO_TRANSCRIPT",
        "callResultCode": "NO_ANSWER",
    }
    mock_s3.put_object.assert_not_called()
    mock_meta_table.put_item.assert_not_called()
    mock_response_table.update_item.assert_not_called()
    mock_transcribe.start_transcription_job.assert_not_called()
