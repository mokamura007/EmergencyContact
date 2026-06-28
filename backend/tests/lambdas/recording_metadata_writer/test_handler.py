"""Unit tests for the RecordingMetadataWriter Lambda (Phase 6.7).

Covers the mandatory cases listed in the Phase 6.7 task brief:

1.  Outbound happy path — put_item with full attribute set, no
    ``contactId`` attribute.
2.  Inbound happy path — meta_pk = ``INBOUND#{contactId}``,
    meta_sk = ``{employeeId}#0``, ``contactId`` attribute added.
3.  Throttling 1x then success — one sleep, one retry.
4.  Throttling 3x — handler re-raises so Lambda async DLQ captures.
5.  ConditionalCheckFailedException — treated as ok, single put_item.
6.  Malformed S3 key — ValueError, no put_item.
7.  Non-dict event — ValueError.
8.  Missing detail.object.key — ValueError.
9.  Missing detail.object.size — durationSeconds = 0 (defensive default).
10. ProvisionedThroughputExceededException — retried like Throttling.

Plus one extra test for ``_estimate_duration_seconds`` boundary cases
(zero and negative size).

The handler talks to AWS through one module-level global:
    * ``handler._RECORDING_META_TABLE`` — ``DynamoDB.Table``
which is swapped for a :class:`MagicMock` per test. ``time.sleep`` is
patched to a no-op to keep the suite fast.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.recording_metadata_writer import handler

# --- Fixtures ----------------------------------------------------------


@pytest.fixture
def mock_meta_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="RecordingMetaTable")
    monkeypatch.setattr(handler, "_RECORDING_META_TABLE", table)
    return table


@pytest.fixture
def silence_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``time.sleep`` inside the handler module with a counter."""
    sleeper = MagicMock(name="time.sleep")
    monkeypatch.setattr(handler.time, "sleep", sleeper)
    return sleeper


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> str:
    """Freeze ``handler._utc_now_iso`` so tests can assert recordedAt."""
    frozen = "2026-06-25T12:34:56Z"
    monkeypatch.setattr(handler, "_utc_now_iso", lambda: frozen)
    return frozen


# --- Helpers -----------------------------------------------------------


_BUCKET = "safety-confirmation-recordings-test-111122223333-ap-northeast-1"


def _outbound_event(
    key: str = "recordings/cycle-1/emp-1/0.wav",
    size: int | None = 320_000,
) -> dict[str, Any]:
    object_block: dict[str, Any] = {"key": key}
    if size is not None:
        object_block["size"] = size
    return {
        "version": "0",
        "id": "evt-id",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {"name": _BUCKET},
            "object": object_block,
        },
    }


def _inbound_event(
    key: str = "inbound/202606/emp-2/contact-abc.wav",
    size: int = 256_000,
) -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-id-inbound",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {"name": _BUCKET},
            "object": {"key": key, "size": size},
        },
    }


def _client_error(code: str, op: str = "PutItem") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": f"{code} simulated"}},
        operation_name=op,
    )


# --- Test 1: outbound happy path --------------------------------------


def test_outbound_success_writes_meta_without_contact_id(
    mock_meta_table: MagicMock,
    silence_sleep: MagicMock,
    frozen_now: str,
) -> None:
    """320,000-byte WAV at 128 kbit/s ≈ 20 seconds."""
    mock_meta_table.put_item.return_value = {}

    result = handler.lambda_handler(_outbound_event(), None)

    assert result == {
        "status": "ok",
        "metaPk": "cycle-1",
        "metaSk": "emp-1#0",
        "kind": "outbound",
    }
    mock_meta_table.put_item.assert_called_once()
    kwargs = mock_meta_table.put_item.call_args.kwargs
    assert kwargs["ConditionExpression"] == "attribute_not_exists(cycleId)"
    item = kwargs["Item"]
    assert item == {
        "cycleId": "cycle-1",
        "employeeIdSeq": "emp-1#0",
        "employeeId": "emp-1",
        "s3Bucket": _BUCKET,
        "s3ObjectKey": "recordings/cycle-1/emp-1/0.wav",
        "recordedAt": frozen_now,
        "fileSizeBytes": 320_000,
        "durationSeconds": 20,
        "kind": "outbound",
    }
    # No contactId on outbound rows.
    assert "contactId" not in item
    silence_sleep.assert_not_called()


# --- Test 2: inbound happy path ---------------------------------------


def test_inbound_success_writes_meta_with_contact_id(
    mock_meta_table: MagicMock,
    silence_sleep: MagicMock,
    frozen_now: str,
) -> None:
    mock_meta_table.put_item.return_value = {}

    result = handler.lambda_handler(_inbound_event(), None)

    assert result == {
        "status": "ok",
        "metaPk": "INBOUND#contact-abc",
        "metaSk": "emp-2#0",
        "kind": "inbound",
    }
    kwargs = mock_meta_table.put_item.call_args.kwargs
    item = kwargs["Item"]
    assert item["cycleId"] == "INBOUND#contact-abc"
    assert item["employeeIdSeq"] == "emp-2#0"
    assert item["employeeId"] == "emp-2"
    assert item["contactId"] == "contact-abc"  # inbound-only attribute
    assert item["kind"] == "inbound"
    assert item["s3ObjectKey"] == "inbound/202606/emp-2/contact-abc.wav"
    assert item["durationSeconds"] == 16  # 256_000 * 8 / 128_000


# --- Test 3: throttling once then success -----------------------------


def test_throttling_once_then_success(
    mock_meta_table: MagicMock,
    silence_sleep: MagicMock,
    frozen_now: str,
) -> None:
    mock_meta_table.put_item.side_effect = [
        _client_error("ThrottlingException"),
        {},
    ]
    result = handler.lambda_handler(_outbound_event(), None)
    assert result["status"] == "ok"
    assert mock_meta_table.put_item.call_count == 2
    assert silence_sleep.call_count == 1


# --- Test 4: throttling 3x → handler re-raises (DLQ capture) -----------


def test_throttling_three_times_raises_for_dlq(
    mock_meta_table: MagicMock,
    silence_sleep: MagicMock,
    frozen_now: str,
) -> None:
    mock_meta_table.put_item.side_effect = [
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
    ]
    # Lambda async DLQ requires the function to raise on terminal
    # failure — a normal-return error sentinel is not enough.
    with pytest.raises(handler._DdbWriteExhaustedError):
        handler.lambda_handler(_outbound_event(), None)
    assert mock_meta_table.put_item.call_count == 3
    # Sleeps between try 0->1 and try 1->2 only (no sleep after final
    # failure).
    assert silence_sleep.call_count == 2


# --- Test 5: ConditionalCheckFailedException treated as ok -------------


def test_conditional_check_failed_is_treated_as_ok(
    mock_meta_table: MagicMock,
    silence_sleep: MagicMock,
    frozen_now: str,
) -> None:
    """Re-delivered event: first writer's row stays, handler returns ok."""
    mock_meta_table.put_item.side_effect = _client_error(
        "ConditionalCheckFailedException"
    )
    result = handler.lambda_handler(_outbound_event(), None)
    assert result["status"] == "ok"
    assert result["metaPk"] == "cycle-1"
    # Only one attempt — no retry on conditional-check failure.
    assert mock_meta_table.put_item.call_count == 1
    silence_sleep.assert_not_called()


# --- Test 6: malformed S3 key -----------------------------------------


def test_malformed_s3_key_raises_value_error(
    mock_meta_table: MagicMock,
) -> None:
    bad_event = _outbound_event(key="random/key.mp3")
    with pytest.raises(
        ValueError, match="recording key did not match expected schema"
    ):
        handler.lambda_handler(bad_event, None)
    mock_meta_table.put_item.assert_not_called()


# --- Test 7: non-dict event -------------------------------------------


def test_non_dict_event_raises_value_error(mock_meta_table: MagicMock) -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        handler.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]
    mock_meta_table.put_item.assert_not_called()


# --- Test 8: missing detail.object.key --------------------------------


def test_missing_object_key_raises_value_error(mock_meta_table: MagicMock) -> None:
    event = _outbound_event()
    del event["detail"]["object"]["key"]
    with pytest.raises(ValueError, match="event.detail.object.key is required"):
        handler.lambda_handler(event, None)
    mock_meta_table.put_item.assert_not_called()


def test_missing_bucket_name_raises_value_error(mock_meta_table: MagicMock) -> None:
    """Companion to the key-missing test — covers detail.bucket.name path."""
    event = _outbound_event()
    del event["detail"]["bucket"]["name"]
    with pytest.raises(ValueError, match="event.detail.bucket.name is required"):
        handler.lambda_handler(event, None)
    mock_meta_table.put_item.assert_not_called()


# --- Test 9: missing detail.object.size → durationSeconds=0 -----------


def test_missing_size_yields_zero_duration(
    mock_meta_table: MagicMock,
    silence_sleep: MagicMock,
    frozen_now: str,
) -> None:
    """Defensive default: non-standard event without ``size`` still writes."""
    mock_meta_table.put_item.return_value = {}
    event = _outbound_event(size=None)
    result = handler.lambda_handler(event, None)
    assert result["status"] == "ok"
    item = mock_meta_table.put_item.call_args.kwargs["Item"]
    assert item["fileSizeBytes"] == 0
    assert item["durationSeconds"] == 0


# --- Test 10: ProvisionedThroughputExceededException is retried --------


def test_provisioned_throughput_exceeded_is_retried_like_throttling(
    mock_meta_table: MagicMock,
    silence_sleep: MagicMock,
    frozen_now: str,
) -> None:
    mock_meta_table.put_item.side_effect = [
        _client_error("ProvisionedThroughputExceededException"),
        _client_error("ProvisionedThroughputExceededException"),
        {},
    ]
    result = handler.lambda_handler(_outbound_event(), None)
    assert result["status"] == "ok"
    assert mock_meta_table.put_item.call_count == 3
    assert silence_sleep.call_count == 2


# --- Extra: non-retryable DDB error propagates ------------------------


def test_non_retryable_ddb_error_propagates(
    mock_meta_table: MagicMock,
    silence_sleep: MagicMock,
    frozen_now: str,
) -> None:
    """Any DDB error besides retryable / conditional must surface."""
    mock_meta_table.put_item.side_effect = _client_error("ResourceNotFoundException")
    with pytest.raises(ClientError):
        handler.lambda_handler(_outbound_event(), None)
    # No retries on non-retryable errors.
    assert mock_meta_table.put_item.call_count == 1
    silence_sleep.assert_not_called()


# --- Extra: duration estimation pure-function boundary cases ----------


@pytest.mark.parametrize(
    ("size_bytes", "expected_seconds"),
    [
        (0, 0),
        (-1, 0),
        (16_000, 1),  # 16_000 * 8 / 128_000 = 1.0
        (128_000, 8),  # 8 seconds at 128 kbit/s
        (1, 0),  # round(1*8/128_000) = 0
    ],
)
def test_estimate_duration_seconds_pure_helper(
    size_bytes: int, expected_seconds: int
) -> None:
    assert handler._estimate_duration_seconds(size_bytes) == expected_seconds


def test_estimate_duration_seconds_zero_bitrate_safe() -> None:
    """Bitrate=0 must not divide by zero."""
    assert handler._estimate_duration_seconds(128_000, bitrate_kbps=0) == 0
