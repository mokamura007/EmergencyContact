"""Unit tests for the KeywordMatcher Lambda (Phase 8.1).

The handler talks to AWS through four module-level globals:
    * ``handler._S3``                    — S3 client
    * ``handler._CYCLE_TABLE``           — DynamoDB Table (Cycle)
    * ``handler._TRANSCRIPT_META_TABLE`` — DynamoDB Table (TranscriptMeta)
    * ``handler._RESPONSE_TABLE``        — DynamoDB Table (Response)
Each is swapped out for a :class:`MagicMock` per test. The
``shared.dictionary.snapshot.get_dictionary_snapshot`` function is
mocked at the handler-module level too because it constructs its own
boto3 resource internally.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.keyword_matcher import handler

# --- Fixtures ----------------------------------------------------------


@pytest.fixture
def mock_s3(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="S3Client")
    monkeypatch.setattr(handler, "_S3", client)
    return client


@pytest.fixture
def mock_cycle_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="CycleTable")
    monkeypatch.setattr(handler, "_CYCLE_TABLE", table)
    return table


@pytest.fixture
def mock_response_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="ResponseTable")
    monkeypatch.setattr(handler, "_RESPONSE_TABLE", table)
    return table


@pytest.fixture
def mock_meta_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="TranscriptMetaTable")
    monkeypatch.setattr(handler, "_TRANSCRIPT_META_TABLE", table)
    return table


@pytest.fixture
def mock_snapshot(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub ``get_dictionary_snapshot`` imported into the handler module."""
    snap = MagicMock(name="get_dictionary_snapshot")
    monkeypatch.setattr(handler, "get_dictionary_snapshot", snap)
    return snap


@pytest.fixture
def silence_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``time.sleep`` inside the handler module with a counter.

    Phase 8.4 retry loop calls ``time.sleep`` between attempts; tests
    don't want to wait, but they DO want to count how many times the
    sleep happened (one sleep per retry boundary).
    """
    sleeper = MagicMock(name="time.sleep")
    monkeypatch.setattr(handler.time, "sleep", sleeper)
    return sleeper


# --- Helpers -----------------------------------------------------------


def _event(key: str) -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-id",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {
                "name": "safety-confirmation-transcripts-test-111122223333-ap-northeast-1"
            },
            "object": {"key": key},
        },
    }


def _transcript_body_bytes(
    text: str,
    confidences: list[float] | None = None,
) -> bytes:
    items: list[dict[str, Any]] = []
    if confidences is not None:
        for c in confidences:
            items.append(
                {
                    "type": "pronunciation",
                    "alternatives": [{"confidence": str(c), "content": "x"}],
                }
            )
    body: dict[str, Any] = {
        "jobName": "j",
        "accountId": "111122223333",
        "status": "COMPLETED",
        "results": {"transcripts": [{"transcript": text}]},
    }
    if items:
        body["results"]["items"] = items
    return json.dumps(body).encode("utf-8")


def _s3_get_obj(content: bytes) -> dict[str, Any]:
    return {"Body": io.BytesIO(content)}


def _client_error(code: str, op: str = "GetObject") -> ClientError:
    """Build a synthetic ``ClientError`` for failure-injection tests."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": f"{code} simulated"}},
        operation_name=op,
    )


# --- 1. Happy path: outbound SAFE -------------------------------------


def test_outbound_happy_path_safe(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("私は無事です", [0.9, 0.8])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 7}
    }
    mock_snapshot.return_value = {
        "SAFE": ["無事"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["不在"],
    }

    result = handler.lambda_handler(
        _event("transcripts/cycle-1/emp-1/0.json"), None
    )

    assert result["status"] == "ok"
    assert result["cycleId"] == "cycle-1"
    assert result["employeeId"] == "emp-1"
    assert result["voiceStatus"] == "SAFE"
    assert result["matchedKeywords"] == ["無事"]
    assert result["dictionaryVersion"] == 7

    # S3 get_object called with the right bucket and key.
    mock_s3.get_object.assert_called_once_with(
        Bucket="safety-confirmation-transcripts-test-111122223333-ap-northeast-1",
        Key="transcripts/cycle-1/emp-1/0.json",
    )
    # Cycle GetItem with cycleId-only key.
    mock_cycle_table.get_item.assert_called_once_with(
        Key={"cycleId": "cycle-1"}
    )
    # Dictionary snapshot called with the resolved version.
    mock_snapshot.assert_called_once()
    assert mock_snapshot.call_args.args == (7,)
    # Response UpdateItem with the four fields.
    mock_response_table.update_item.assert_called_once()
    upd = mock_response_table.update_item.call_args.kwargs
    assert upd["Key"] == {"cycleId": "cycle-1", "employeeId": "emp-1"}
    assert "voiceStatus" in upd["UpdateExpression"]
    assert "matchedKeywords" in upd["UpdateExpression"]
    assert "transcriptExcerpt" in upd["UpdateExpression"]
    assert "dictionaryVersion" in upd["UpdateExpression"]
    assert upd["ExpressionAttributeValues"][":vs"] == "SAFE"
    assert upd["ExpressionAttributeValues"][":mk"] == ["無事"]
    assert upd["ExpressionAttributeValues"][":ex"] == "私は無事です"
    assert upd["ExpressionAttributeValues"][":dv"] == 7
    # TranscriptMeta UpdateItem with excerpt + confidence + languageCode.
    mock_meta_table.update_item.assert_called_once()
    meta = mock_meta_table.update_item.call_args.kwargs
    assert meta["Key"] == {"cycleId": "cycle-1", "employeeIdSeq": "emp-1#0"}
    assert "transcriptExcerpt" in meta["UpdateExpression"]
    assert "confidence" in meta["UpdateExpression"]
    assert "languageCode" in meta["UpdateExpression"]
    assert meta["ExpressionAttributeValues"][":ex"] == "私は無事です"
    assert meta["ExpressionAttributeValues"][":lang"] == "ja-JP"
    # Confidence is Decimal(str(0.85)) — the parsing average.
    assert isinstance(meta["ExpressionAttributeValues"][":conf"], Decimal)


# --- 2. Priority resolution: INJURED beats UNAVAILABLE and SAFE -------


def test_outbound_priority_resolution_injured_wins(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes(
            "無事ですが少し怪我があり対応できません", [0.7, 0.6, 0.8]
        )
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 9}
    }
    mock_snapshot.return_value = {
        "SAFE": ["無事"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["対応"],
    }

    result = handler.lambda_handler(
        _event("transcripts/cycle-1/emp-2/3.json"), None
    )

    assert result["voiceStatus"] == "INJURED"
    assert result["matchedKeywords"] == ["怪我"]
    assert (
        mock_response_table.update_item.call_args.kwargs[
            "ExpressionAttributeValues"
        ][":vs"]
        == "INJURED"
    )


# --- 3. No match → OTHER ----------------------------------------------


def test_outbound_no_match_returns_other(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("こんにちは", [0.5])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 1}
    }
    mock_snapshot.return_value = {
        "SAFE": ["無事"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["不在"],
    }

    result = handler.lambda_handler(
        _event("transcripts/cycle-1/emp-1/0.json"), None
    )

    assert result["voiceStatus"] == "OTHER"
    assert result["matchedKeywords"] == []
    mock_response_table.update_item.assert_called_once()


# --- 4. Excerpt truncation at 100 chars -------------------------------


def test_excerpt_truncated_to_100_chars(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    long_text = "あ" * 200
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes(long_text, [0.9])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 1}
    }
    mock_snapshot.return_value = {"SAFE": [], "INJURED": [], "UNAVAILABLE": []}

    handler.lambda_handler(_event("transcripts/cycle-1/emp-1/0.json"), None)

    excerpt = mock_response_table.update_item.call_args.kwargs[
        "ExpressionAttributeValues"
    ][":ex"]
    assert excerpt == "あ" * 100
    meta_excerpt = mock_meta_table.update_item.call_args.kwargs[
        "ExpressionAttributeValues"
    ][":ex"]
    assert meta_excerpt == "あ" * 100


# --- 5. Excerpt is text body when shorter than 100 chars --------------


def test_excerpt_full_text_when_under_limit(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("短いテキスト", [0.9])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 1}
    }
    mock_snapshot.return_value = {"SAFE": [], "INJURED": [], "UNAVAILABLE": []}

    handler.lambda_handler(_event("transcripts/cycle-1/emp-1/0.json"), None)

    excerpt = mock_response_table.update_item.call_args.kwargs[
        "ExpressionAttributeValues"
    ][":ex"]
    assert excerpt == "短いテキスト"


# --- 6. Confidence stored as Decimal ----------------------------------


def test_confidence_stored_as_decimal(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("無事", [0.9, 0.8])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 1}
    }
    mock_snapshot.return_value = {"SAFE": ["無事"], "INJURED": [], "UNAVAILABLE": []}

    handler.lambda_handler(_event("transcripts/cycle-1/emp-1/0.json"), None)

    conf = mock_meta_table.update_item.call_args.kwargs[
        "ExpressionAttributeValues"
    ][":conf"]
    assert isinstance(conf, Decimal)
    # 0.85 = (0.9 + 0.8) / 2; float arithmetic produces a tiny tail
    # which Decimal(str(float)) preserves verbatim — verify the value
    # is *close* rather than bit-identical.
    assert abs(conf - Decimal("0.85")) < Decimal("0.001")


# --- 7. Inbound transcript raises ValueError (Phase 9 follow-up) ------


def test_inbound_transcript_raises_phase9_followup(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    with pytest.raises(ValueError, match="inbound transcripts are not handled"):
        handler.lambda_handler(
            _event("inbound/202606/emp-2/contact-abc.json"), None
        )
    # No I/O happened.
    mock_s3.get_object.assert_not_called()
    mock_cycle_table.get_item.assert_not_called()
    mock_response_table.update_item.assert_not_called()
    mock_meta_table.update_item.assert_not_called()


# --- 8. Malformed S3 key raises ValueError ----------------------------


def test_malformed_key_raises(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    with pytest.raises(ValueError, match="did not match expected schema"):
        handler.lambda_handler(_event("random/garbage.json"), None)
    mock_s3.get_object.assert_not_called()
    mock_cycle_table.get_item.assert_not_called()


# --- 9. Non-dict event raises -----------------------------------------


def test_non_dict_event_raises() -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        handler.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]


# --- 10. Missing detail.object.key raises -----------------------------


def test_missing_object_key_raises() -> None:
    with pytest.raises(ValueError, match=r"event\.detail\.object\.key is required"):
        handler.lambda_handler(
            {
                "detail": {
                    "bucket": {"name": "any"},
                    "object": {},
                }
            },
            None,
        )


# --- 11. Cycle row missing raises -------------------------------------


def test_cycle_row_missing_raises(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("hello", [0.9])
    )
    mock_cycle_table.get_item.return_value = {}  # no Item

    with pytest.raises(ValueError, match="Cycle row not found"):
        handler.lambda_handler(
            _event("transcripts/cycle-1/emp-1/0.json"), None
        )
    # No DynamoDB writes.
    mock_response_table.update_item.assert_not_called()
    mock_meta_table.update_item.assert_not_called()


# --- 12. Cycle.dictionaryVersion missing raises -----------------------


def test_cycle_dictionary_version_missing_raises(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("hello", [0.9])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "RUNNING"}  # no dictionaryVersion
    }

    with pytest.raises(ValueError, match="missing dictionaryVersion"):
        handler.lambda_handler(
            _event("transcripts/cycle-1/emp-1/0.json"), None
        )


# --- 13. Empty dictionary snapshot → OTHER (no error) -----------------


def test_empty_dictionary_snapshot_yields_other(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("無事です", [0.9])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 3}
    }
    mock_snapshot.return_value = {"SAFE": [], "INJURED": [], "UNAVAILABLE": []}

    result = handler.lambda_handler(
        _event("transcripts/cycle-1/emp-1/0.json"), None
    )

    assert result["voiceStatus"] == "OTHER"
    assert result["matchedKeywords"] == []
    mock_response_table.update_item.assert_called_once()
    mock_meta_table.update_item.assert_called_once()


# --- 14. Malformed transcript body raises -----------------------------


def test_invalid_json_body_raises(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(b"{not-valid-json")

    with pytest.raises(ValueError, match="not valid JSON"):
        handler.lambda_handler(
            _event("transcripts/cycle-1/emp-1/0.json"), None
        )


def test_non_dict_transcript_body_raises(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    mock_s3.get_object.return_value = _s3_get_obj(b"[1, 2, 3]")

    with pytest.raises(ValueError, match="must decode to a JSON object"):
        handler.lambda_handler(
            _event("transcripts/cycle-1/emp-1/0.json"), None
        )


# --- 15. UUID-style identifiers (real-world shapes) -------------------


def test_uuid_style_cycle_and_employee_ids(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    cycle = "550e8400-e29b-41d4-a716-446655440000"
    emp = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("無事", [0.9])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": cycle, "dictionaryVersion": 42}
    }
    mock_snapshot.return_value = {"SAFE": ["無事"], "INJURED": [], "UNAVAILABLE": []}

    result = handler.lambda_handler(
        _event(f"transcripts/{cycle}/{emp}/5.json"), None
    )

    assert result["cycleId"] == cycle
    assert result["employeeId"] == emp
    assert result["seq"] == "5"
    assert result["dictionaryVersion"] == 42
    upd = mock_response_table.update_item.call_args.kwargs
    assert upd["Key"] == {"cycleId": cycle, "employeeId": emp}
    meta = mock_meta_table.update_item.call_args.kwargs
    assert meta["Key"] == {"cycleId": cycle, "employeeIdSeq": f"{emp}#5"}


# --- 16. dictionaryVersion as Decimal string is coerced to int --------


def test_dictionary_version_decimal_coerced_to_int(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
) -> None:
    """DynamoDB returns ``Number`` types as Decimal; int() handles them."""
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("無事", [0.9])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": Decimal("11")}
    }
    mock_snapshot.return_value = {"SAFE": ["無事"], "INJURED": [], "UNAVAILABLE": []}

    result = handler.lambda_handler(
        _event("transcripts/cycle-1/emp-1/0.json"), None
    )

    assert result["dictionaryVersion"] == 11
    assert mock_snapshot.call_args.args == (11,)


# =============================================================================
# Phase 8.4 — retry + OTHER fallback (failure-injection tests).
# =============================================================================


# --- 17. Retryable throttling on S3 → retry succeeds (no fallback) ----


def test_throttling_then_success_sleeps_once(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """First S3 GetObject throttles; the retry succeeds → status=ok.

    Validates: Task 8.4 retry-up-to-3 happy path. Phase 6.5 RetryEvaluator
    is *not* invoked because the pipeline succeeded on the second attempt.
    """
    body = _s3_get_obj(_transcript_body_bytes("こんにちは", [0.9]))
    mock_s3.get_object.side_effect = [
        _client_error("ThrottlingException"),
        body,
    ]
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 1}
    }
    mock_snapshot.return_value = {"SAFE": [], "INJURED": [], "UNAVAILABLE": []}

    result = handler.lambda_handler(
        _event("transcripts/cycle-1/emp-1/0.json"), None
    )

    assert result["status"] == "ok"
    assert result["voiceStatus"] == "OTHER"  # no keywords matched
    assert mock_s3.get_object.call_count == 2
    # One sleep between failed try 0 and successful try 1.
    assert silence_sleep.call_count == 1
    # Full Response write (4 fields) happened — not the fallback shape.
    mock_response_table.update_item.assert_called_once()
    upd_kwargs = mock_response_table.update_item.call_args.kwargs
    assert "dictionaryVersion" in upd_kwargs["UpdateExpression"]
    assert "transcriptExcerpt" in upd_kwargs["UpdateExpression"]


# --- 18. Three retryable failures → OTHER fallback --------------------


def test_three_retryable_failures_fallback_to_other(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """All three S3 attempts throttle → fallback writes voiceStatus=OTHER.

    Validates: Task 8.4 — 最終失敗で Response の voiceStatus を OTHER に
    確定し CloudWatch Logs に記録, OTHER 確定で再発信判定（Phase 6.5）に
    委ねる. The fallback Response UpdateItem uses the *short* expression
    (voiceStatus + matchedKeywords only), distinguishing it from the
    happy-path 4-field write.
    """
    mock_s3.get_object.side_effect = [
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
    ]

    result = handler.lambda_handler(
        _event("transcripts/cycle-1/emp-1/0.json"), None
    )

    # Lambda returns successfully — the EventBridge / SFN orchestration
    # sees a closed row, not a function error.
    assert result == {
        "status": "fallback",
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "seq": "0",
        "voiceStatus": "OTHER",
        "matchedKeywords": [],
        "reason": "MATCHING_FAILED",
    }
    # 3 attempts, 2 sleeps (between tries 0→1 and 1→2; no sleep after final).
    assert mock_s3.get_object.call_count == 3
    assert silence_sleep.call_count == 2

    # Fallback Response UpdateItem: short form (2 fields, no excerpt / version).
    mock_response_table.update_item.assert_called_once()
    upd_kwargs = mock_response_table.update_item.call_args.kwargs
    assert upd_kwargs["Key"] == {"cycleId": "cycle-1", "employeeId": "emp-1"}
    assert upd_kwargs["UpdateExpression"] == (
        "SET voiceStatus = :vs, matchedKeywords = :mk"
    )
    assert upd_kwargs["ExpressionAttributeValues"] == {
        ":vs": "OTHER",
        ":mk": [],
    }
    # TranscriptMeta is NOT touched on the fallback path.
    mock_meta_table.update_item.assert_not_called()


# --- 19. Retryable error on DDB UpdateItem also triggers fallback -----


def test_three_ddb_throttles_fallback_to_other(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """All three Response UpdateItem attempts throttle → OTHER fallback.

    Validates that the retry boundary is the WHOLE pipeline, not just
    the S3 call — a late-stage DDB failure also triggers the fallback.
    The fallback then re-issues UpdateItem with the OTHER payload, which
    we let succeed; so the table sees 4 UpdateItem calls total (3 happy
    attempts + 1 fallback).

    Because the retry loop re-reads the S3 transcript on every attempt
    (the pipeline is idempotent end-to-end), the test supplies a fresh
    ``BytesIO`` body on each call via ``side_effect``.
    """
    mock_s3.get_object.side_effect = lambda *_, **__: _s3_get_obj(
        _transcript_body_bytes("こんにちは", [0.9])
    )
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "dictionaryVersion": 1}
    }
    mock_snapshot.return_value = {"SAFE": [], "INJURED": [], "UNAVAILABLE": []}
    mock_response_table.update_item.side_effect = [
        _client_error("ProvisionedThroughputExceededException", op="UpdateItem"),
        _client_error("ProvisionedThroughputExceededException", op="UpdateItem"),
        _client_error("ProvisionedThroughputExceededException", op="UpdateItem"),
        None,  # fallback write succeeds
    ]

    result = handler.lambda_handler(
        _event("transcripts/cycle-1/emp-1/0.json"), None
    )

    assert result["status"] == "fallback"
    assert result["voiceStatus"] == "OTHER"
    # 3 attempted happy-path writes + 1 fallback write = 4 calls.
    assert mock_response_table.update_item.call_count == 4
    fallback_kwargs = mock_response_table.update_item.call_args_list[-1].kwargs
    assert fallback_kwargs["UpdateExpression"] == (
        "SET voiceStatus = :vs, matchedKeywords = :mk"
    )
    assert silence_sleep.call_count == 2


# --- 20. Non-retryable ClientError propagates (no fallback) -----------


def test_non_retryable_client_error_propagates(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """AccessDenied is a programming / IAM bug — NOT retryable, NOT a fallback.

    Validates: principle 19(b) — fallback is reserved for the documented
    business requirement (transient failure → OTHER). Real bugs surface
    as Lambda errors so Phase 12 alarms catch them.
    """
    mock_s3.get_object.side_effect = _client_error("AccessDenied")

    with pytest.raises(ClientError) as exc_info:
        handler.lambda_handler(_event("transcripts/cycle-1/emp-1/0.json"), None)
    assert exc_info.value.response["Error"]["Code"] == "AccessDenied"

    # One attempt only — no retry, no sleep, no fallback Response write.
    assert mock_s3.get_object.call_count == 1
    silence_sleep.assert_not_called()
    mock_response_table.update_item.assert_not_called()


# --- 21. Fallback failure logs WARNING --------------------------------


def test_fallback_logs_warning_with_last_error(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
    silence_sleep: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Validates: 最終失敗で Response の voiceStatus を OTHER に確定し、
    CloudWatch Logs に記録 (Task 8.4 done-when bullet 2)."""
    mock_s3.get_object.side_effect = [
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
    ]

    with caplog.at_level("WARNING", logger=handler.LOGGER.name):
        handler.lambda_handler(
            _event("transcripts/cycle-1/emp-1/0.json"), None
        )

    fallback_records = [
        r for r in caplog.records
        if "final-failure fallback" in r.getMessage()
        and "voiceStatus=OTHER" in r.getMessage()
    ]
    assert fallback_records, (
        "expected a WARNING log line announcing the OTHER fallback decision"
    )
    msg = fallback_records[0].getMessage()
    assert "cycleId=cycle-1" in msg
    assert "employeeId=emp-1" in msg
    assert "ThrottlingException" in msg


# --- 22. Fallback Response UpdateItem failure propagates --------------


def test_fallback_response_write_failure_propagates(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """If the fallback's own UpdateItem fails, the Lambda must surface
    the error so EventBridge can redeliver. The in-Lambda retry budget
    is for the matching pipeline only; the fallback write is one-shot."""
    mock_s3.get_object.side_effect = [
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
    ]
    mock_response_table.update_item.side_effect = _client_error(
        "InternalServerError", op="UpdateItem"
    )

    with pytest.raises(ClientError) as exc_info:
        handler.lambda_handler(_event("transcripts/cycle-1/emp-1/0.json"), None)
    assert exc_info.value.response["Error"]["Code"] == "InternalServerError"


# --- 23. ValueError inside pipeline still propagates (existing rule) --


def test_value_error_inside_pipeline_not_subject_to_retry(
    mock_s3: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_meta_table: MagicMock,
    mock_snapshot: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """A missing Cycle row is a data-integrity bug — it is NOT retried
    and does NOT fall back to OTHER. Existing tests at lines 11/12 of
    this file pin this behavior; this test re-pins it explicitly in the
    Phase 8.4 retry context to guard against regression."""
    mock_s3.get_object.return_value = _s3_get_obj(
        _transcript_body_bytes("hello", [0.9])
    )
    mock_cycle_table.get_item.return_value = {}  # no Item

    with pytest.raises(ValueError, match="Cycle row not found"):
        handler.lambda_handler(_event("transcripts/cycle-1/emp-1/0.json"), None)

    # No retries, no sleep, no fallback write.
    assert mock_s3.get_object.call_count == 1
    assert mock_cycle_table.get_item.call_count == 1
    silence_sleep.assert_not_called()
    mock_response_table.update_item.assert_not_called()
