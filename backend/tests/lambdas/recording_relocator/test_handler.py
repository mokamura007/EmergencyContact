"""Unit tests for the RecordingRelocator Lambda (Phase 7.2).

Covers the mandatory scenarios for the Connect-native → design-layout
rename:

1.  Happy path — Response GSI returns one row, S3 copy + delete fire.
2.  Multi-channel export — ``_UTC_FROM-CUSTOMER`` suffix is accepted
    and the contactId is still extracted correctly.
3.  GSI throttling 1x then success — one sleep, one retry.
4.  GSI throttling 3x — raise so the async DLQ captures.
5.  GSI no-match retried then raised.
6.  Source key outside the configured Connect-native prefix — raise.
7.  Source key matches prefix but not the Connect-native schema —
    raise (and no S3 ops).
8.  Response row missing cycleId / employeeId / callAttempts — raise.
9.  Non-dict event — raise.
10. Missing detail.object.key — raise.
11. Non-retryable DynamoDB error propagates.
12. callAttempts as string ``"3"`` (DynamoDB sometimes returns
    Decimal-stringy types) is accepted via ``int()`` coercion.

The handler talks to AWS through two module-level globals:
    * ``handler._RESPONSE_TABLE`` — ``DynamoDB.Table``
    * ``handler._S3`` — ``s3`` client
which are swapped for :class:`MagicMock` per test. ``time.sleep`` is
patched to a no-op to keep the suite fast.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.recording_relocator import handler

# --- Fixtures ----------------------------------------------------------


@pytest.fixture
def mock_response_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="ResponseTable")
    monkeypatch.setattr(handler, "_RESPONSE_TABLE", table)
    return table


@pytest.fixture
def mock_s3(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="s3")
    monkeypatch.setattr(handler, "_S3", client)
    return client


@pytest.fixture
def silence_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    sleeper = MagicMock(name="time.sleep")
    monkeypatch.setattr(handler.time, "sleep", sleeper)
    return sleeper


# --- Helpers -----------------------------------------------------------


_BUCKET = "safety-confirmation-recordings-test-111122223333-ap-northeast-1"
_CONTACT_ID = "11111111-1111-1111-1111-111111111111"
_CONNECT_KEY = (
    "connect-raw/my-alias/CallRecordings/2026/06/25/"
    f"{_CONTACT_ID}_2026-06-25T07:00:00_UTC.wav"
)


def _event(key: str = _CONNECT_KEY) -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-id",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {"name": _BUCKET},
            "object": {"key": key, "size": 320_000},
        },
    }


def _response_row(
    cycle_id: str = "cycle-uuid",
    employee_id: str = "emp-uuid",
    call_attempts: int = 1,
) -> dict[str, Any]:
    return {
        "cycleId": cycle_id,
        "employeeId": employee_id,
        "callAttempts": call_attempts,
        "contactId": _CONTACT_ID,
    }


def _throttling_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "rate exceeded"}},
        "Query",
    )


def _non_retryable_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "Query",
    )


# --- Happy path --------------------------------------------------------


def test_handler_relocates_outbound_recording(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_response_table.query.return_value = {"Items": [_response_row()]}

    result = handler.lambda_handler(_event(), None)

    assert result == {
        "status": "ok",
        "contactId": _CONTACT_ID,
        "cycleId": "cycle-uuid",
        "employeeId": "emp-uuid",
        "seq": 1,
        "sourceKey": _CONNECT_KEY,
        "targetKey": "recordings/cycle-uuid/emp-uuid/1.wav",
    }

    # GSI query shape.
    query_kwargs = mock_response_table.query.call_args.kwargs
    assert query_kwargs["IndexName"] == "ContactIdIndex"
    assert query_kwargs["KeyConditionExpression"] == "contactId = :cid"
    assert query_kwargs["ExpressionAttributeValues"] == {":cid": _CONTACT_ID}
    assert query_kwargs["Limit"] == 1

    # S3 ops.
    mock_s3.copy_object.assert_called_once_with(
        Bucket=_BUCKET,
        Key="recordings/cycle-uuid/emp-uuid/1.wav",
        CopySource={"Bucket": _BUCKET, "Key": _CONNECT_KEY},
    )
    mock_s3.delete_object.assert_called_once_with(
        Bucket=_BUCKET, Key=_CONNECT_KEY
    )
    silence_sleep.assert_not_called()


def test_handler_accepts_multi_channel_export(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """Multi-channel exports append ``_FROM-CUSTOMER`` / ``_TO-CUSTOMER``."""
    multi_channel_key = (
        "connect-raw/my-alias/CallRecordings/2026/06/25/"
        f"{_CONTACT_ID}_2026-06-25T07:00:00_UTC_FROM-CUSTOMER.wav"
    )
    mock_response_table.query.return_value = {
        "Items": [_response_row(call_attempts=2)]
    }

    result = handler.lambda_handler(_event(multi_channel_key), None)

    assert result["seq"] == 2
    assert result["targetKey"] == "recordings/cycle-uuid/emp-uuid/2.wav"
    mock_s3.copy_object.assert_called_once()
    mock_s3.delete_object.assert_called_once()


def test_handler_coerces_string_call_attempts(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """DynamoDB may return Decimal which renders as ``"3"``-stringy."""
    mock_response_table.query.return_value = {
        "Items": [_response_row(call_attempts="3")]  # type: ignore[arg-type]
    }

    result = handler.lambda_handler(_event(), None)

    assert result["seq"] == 3


# --- GSI retry behaviour ----------------------------------------------


def test_handler_retries_throttled_query_then_succeeds(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_response_table.query.side_effect = [
        _throttling_error(),
        {"Items": [_response_row()]},
    ]

    result = handler.lambda_handler(_event(), None)

    assert result["status"] == "ok"
    assert mock_response_table.query.call_count == 2
    silence_sleep.assert_called_once()  # one backoff sleep before retry


def test_handler_raises_after_throttling_exhaustion(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_response_table.query.side_effect = [
        _throttling_error(),
        _throttling_error(),
        _throttling_error(),
    ]

    with pytest.raises(handler._GsiLookupExhaustedError, match="throttling"):
        handler.lambda_handler(_event(), None)

    assert mock_response_table.query.call_count == 3
    # ``_MAX_GSI_ATTEMPTS = 3``, one sleep between each of attempts 0→1 and 1→2.
    assert silence_sleep.call_count == 2
    mock_s3.copy_object.assert_not_called()
    mock_s3.delete_object.assert_not_called()


def test_handler_retries_empty_gsi_result_then_raises(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """Empty GSI result is retried (GSI lag) before raising."""
    mock_response_table.query.return_value = {"Items": []}

    with pytest.raises(handler._GsiLookupExhaustedError, match="no row"):
        handler.lambda_handler(_event(), None)

    assert mock_response_table.query.call_count == 3
    mock_s3.copy_object.assert_not_called()


def test_handler_propagates_non_retryable_dynamodb_error(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_response_table.query.side_effect = _non_retryable_error()

    with pytest.raises(ClientError):
        handler.lambda_handler(_event(), None)

    assert mock_response_table.query.call_count == 1
    silence_sleep.assert_not_called()
    mock_s3.copy_object.assert_not_called()


# --- Defensive prefix / schema guards ---------------------------------


def test_handler_rejects_key_outside_connect_prefix(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """A key that doesn't start with ``connect-raw/`` is fatal."""
    with pytest.raises(ValueError, match="CONNECT_RECORDINGS_PREFIX"):
        handler.lambda_handler(
            _event("recordings/cycle-1/emp-1/0.wav"), None
        )

    mock_response_table.query.assert_not_called()
    mock_s3.copy_object.assert_not_called()


def test_handler_rejects_prefix_match_but_bad_schema(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """A key under ``connect-raw/`` that doesn't match Connect's layout."""
    with pytest.raises(ValueError, match="Connect-native schema"):
        handler.lambda_handler(
            _event("connect-raw/not-a-real-connect-output.wav"), None
        )

    mock_response_table.query.assert_not_called()
    mock_s3.copy_object.assert_not_called()


# --- Response row integrity guards ------------------------------------


def test_handler_rejects_response_row_missing_cycle_id(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    row = _response_row()
    del row["cycleId"]
    mock_response_table.query.return_value = {"Items": [row]}

    with pytest.raises(ValueError, match="cycleId"):
        handler.lambda_handler(_event(), None)


def test_handler_rejects_response_row_missing_employee_id(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    row = _response_row()
    del row["employeeId"]
    mock_response_table.query.return_value = {"Items": [row]}

    with pytest.raises(ValueError, match="employeeId"):
        handler.lambda_handler(_event(), None)


def test_handler_rejects_response_row_missing_call_attempts(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    row = _response_row()
    del row["callAttempts"]
    mock_response_table.query.return_value = {"Items": [row]}

    with pytest.raises(ValueError, match="callAttempts"):
        handler.lambda_handler(_event(), None)


def test_handler_rejects_non_int_call_attempts(
    mock_response_table: MagicMock,
    mock_s3: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """``"abc"`` is not int-coercible."""
    mock_response_table.query.return_value = {
        "Items": [_response_row(call_attempts="abc")]  # type: ignore[arg-type]
    }

    with pytest.raises(ValueError, match="callAttempts"):
        handler.lambda_handler(_event(), None)


# --- Event-shape guards -----------------------------------------------


def test_handler_rejects_non_dict_event(
    mock_response_table: MagicMock, mock_s3: MagicMock
) -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        handler.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]


def test_handler_rejects_missing_object_key(
    mock_response_table: MagicMock, mock_s3: MagicMock
) -> None:
    event = _event()
    event["detail"]["object"] = {}
    with pytest.raises(ValueError, match="object.key"):
        handler.lambda_handler(event, None)


def test_handler_rejects_missing_bucket_name(
    mock_response_table: MagicMock, mock_s3: MagicMock
) -> None:
    event = _event()
    event["detail"]["bucket"] = {}
    with pytest.raises(ValueError, match="bucket.name"):
        handler.lambda_handler(event, None)
