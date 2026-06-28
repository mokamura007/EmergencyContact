"""Unit tests for the ConnectDispatcher Lambda (Phase 6.2).

Covers the five mandatory cases from the task brief:
    1. Happy path — single ``StartOutboundVoiceContact`` succeeds, Response
       receives ``contactId`` + ``dispatchedAt``, handler returns
       ``{"status": "ok", "retry": False}``.
    2. Throttling once, success on second try — retry loop functions and
       ``time.sleep`` is invoked once.
    3. Throttling three times in a row — every attempt fails; Response gets
       ``callResultCode=ERROR``; SFN ``SendTaskSuccess`` is called with
       ``retry=true``; handler returns ``{"status": "error", "retry": True}``.
    4. ``LimitExceededException`` behaves identically to ``ThrottlingException``.
    5. Missing required input → ``ValueError``.

The handler talks to AWS through three module-level globals:
    * ``handler._CONNECT``         — ``connect`` client
    * ``handler._RESPONSE_TABLE``  — ``DynamoDB.Table`` for Response
    * ``handler._SFN``             — ``stepfunctions`` client
Each is swapped out for a :class:`MagicMock` per test, and ``time.sleep``
is replaced with a no-op to keep the suite fast.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.connect_dispatcher import handler

# --- Fixtures ----------------------------------------------------------


@pytest.fixture
def mock_connect(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="ConnectClient")
    monkeypatch.setattr(handler, "_CONNECT", client)
    return client


@pytest.fixture
def mock_response_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="ResponseTable")
    monkeypatch.setattr(handler, "_RESPONSE_TABLE", table)
    return table


@pytest.fixture
def mock_sfn(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="SfnClient")
    monkeypatch.setattr(handler, "_SFN", client)
    return client


@pytest.fixture
def silence_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``time.sleep`` inside the handler module with a counter."""
    sleeper = MagicMock(name="time.sleep")
    monkeypatch.setattr(handler.time, "sleep", sleeper)
    return sleeper


# --- Helpers -----------------------------------------------------------


def _make_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "cycleId": "cycle-abc",
        "employeeId": "emp-001",
        "phoneNumber": "+819011112222",
        "attempt": 1,
        "taskToken": "task-token-xyz",
    }
    event.update(overrides)
    return event


def _client_error(code: str) -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": f"{code} simulated"}},
        operation_name="StartOutboundVoiceContact",
    )


# --- Test 1: happy path ------------------------------------------------


def test_success_records_contact_id_and_returns_ok(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_connect.start_outbound_voice_contact.return_value = {"ContactId": "contact-123"}

    result = handler.lambda_handler(_make_event(), None)

    assert result == {"status": "ok", "contactId": "contact-123", "retry": False}
    mock_connect.start_outbound_voice_contact.assert_called_once()
    call_kwargs = mock_connect.start_outbound_voice_contact.call_args.kwargs
    assert call_kwargs["InstanceId"] == "test-instance-id"
    assert call_kwargs["ContactFlowId"] == "test-flow-id"
    assert call_kwargs["SourcePhoneNumber"] == "+810000000000"
    assert call_kwargs["DestinationPhoneNumber"] == "+819011112222"
    # Attributes carry the SFN-side correlation handles for CallEndHandler.
    assert call_kwargs["Attributes"] == {
        "cycleId": "cycle-abc",
        "employeeId": "emp-001",
        "attempt": "1",
        "taskToken": "task-token-xyz",
    }

    mock_response_table.update_item.assert_called_once()
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["Key"] == {"cycleId": "cycle-abc", "employeeId": "emp-001"}
    assert ":cid" in update_kwargs["ExpressionAttributeValues"]
    assert update_kwargs["ExpressionAttributeValues"][":cid"] == "contact-123"

    silence_sleep.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()


# --- Test 2: throttling once then success -----------------------------


def test_throttling_then_success_sleeps_once(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_connect.start_outbound_voice_contact.side_effect = [
        _client_error("ThrottlingException"),
        {"ContactId": "contact-456"},
    ]

    result = handler.lambda_handler(_make_event(), None)

    assert result == {"status": "ok", "contactId": "contact-456", "retry": False}
    assert mock_connect.start_outbound_voice_contact.call_count == 2
    # One sleep between the failed first try and the successful second try.
    assert silence_sleep.call_count == 1
    mock_response_table.update_item.assert_called_once()
    mock_sfn.send_task_success.assert_not_called()


# --- Test 3: throttling three times in a row --------------------------


def test_throttling_three_times_records_error_and_signals_retry(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_connect.start_outbound_voice_contact.side_effect = [
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
    ]

    result = handler.lambda_handler(_make_event(), None)

    assert result == {"status": "error", "contactId": None, "retry": True}
    assert mock_connect.start_outbound_voice_contact.call_count == 3
    # Sleeps between try 0->1 and try 1->2 only (no sleep after final failure).
    assert silence_sleep.call_count == 2

    # Response UpdateItem with callResultCode=ERROR + ConditionExpression.
    mock_response_table.update_item.assert_called_once()
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["ExpressionAttributeValues"][":code"] == "ERROR"
    assert "attribute_not_exists(callResultCode)" in update_kwargs["ConditionExpression"]

    # SFN release with retry=True payload.
    mock_sfn.send_task_success.assert_called_once()
    sfn_kwargs = mock_sfn.send_task_success.call_args.kwargs
    assert sfn_kwargs["taskToken"] == "task-token-xyz"

    payload = json.loads(sfn_kwargs["output"])
    assert payload == {"retry": True, "reason": "DISPATCH_FAILED"}


# --- Test 4: LimitExceededException treated identically ---------------


def test_limit_exceeded_three_times_records_error_and_signals_retry(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    mock_connect.start_outbound_voice_contact.side_effect = [
        _client_error("LimitExceededException"),
        _client_error("LimitExceededException"),
        _client_error("LimitExceededException"),
    ]

    result = handler.lambda_handler(_make_event(), None)

    assert result == {"status": "error", "contactId": None, "retry": True}
    assert mock_connect.start_outbound_voice_contact.call_count == 3
    assert silence_sleep.call_count == 2
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["ExpressionAttributeValues"][":code"] == "ERROR"
    mock_sfn.send_task_success.assert_called_once()


# --- Test 5: input validation -----------------------------------------


def test_missing_task_token_raises_value_error(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    bad_event = _make_event()
    bad_event.pop("taskToken")
    with pytest.raises(ValueError, match="missing required input keys"):
        handler.lambda_handler(bad_event, None)
    mock_connect.start_outbound_voice_contact.assert_not_called()
    mock_response_table.update_item.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()


def test_non_dict_event_raises_value_error() -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        handler.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]


def test_attempt_must_be_positive_int(mock_connect: MagicMock) -> None:
    with pytest.raises(ValueError, match="attempt must be a positive integer"):
        handler.lambda_handler(_make_event(attempt=0), None)
    mock_connect.start_outbound_voice_contact.assert_not_called()


# --- Extra: non-retryable Connect error propagates ---------------------


def test_non_retryable_connect_error_propagates(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    mock_connect.start_outbound_voice_contact.side_effect = _client_error(
        "DestinationNotAllowedException"
    )
    with pytest.raises(ClientError):
        handler.lambda_handler(_make_event(), None)
    mock_response_table.update_item.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()


# --- Extra: ConditionalCheckFailedException on ERROR write is swallowed ----


def test_error_write_swallows_conditional_check_failed(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    silence_sleep: MagicMock,
) -> None:
    """Concurrent writer already set callResultCode; our update must not crash."""
    mock_connect.start_outbound_voice_contact.side_effect = [
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
        _client_error("ThrottlingException"),
    ]
    # Simulate "another writer beat us to it".
    mock_response_table.update_item.side_effect = ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "callResultCode already set",
            }
        },
        operation_name="UpdateItem",
    )

    result = handler.lambda_handler(_make_event(), None)

    # The handler still returns the error sentinel and still releases SFN.
    assert result == {"status": "error", "contactId": None, "retry": True}
    mock_sfn.send_task_success.assert_called_once()


# ======================================================================
# Mock-mode tests (ADR-0010, Phase 16.2)
# ======================================================================
#
# Each test below enables the mock path via ``_MOCK_MODE_ENABLED`` rather
# than re-importing ``handler`` with ``MOCK_MODE=true`` in the
# environment. The module-level constant is computed at import time, so
# in-test ``monkeypatch.setenv`` would have no effect; patching the
# constant directly is the cleanest equivalent and matches the runtime
# semantics (the handler reads ``_MOCK_MODE_ENABLED``, not ``os.environ``,
# on every invocation).


@pytest.fixture
def mock_s3(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="S3Client")
    monkeypatch.setattr(handler, "_S3", client)
    return client


@pytest.fixture
def enable_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip the module-level ``_MOCK_MODE_ENABLED`` flag for one test."""
    monkeypatch.setattr(handler, "_MOCK_MODE_ENABLED", True)
    monkeypatch.setattr(handler, "RECORDINGS_BUCKET_NAME", "test-recordings-bucket")


def _assert_response_update_records_contact(
    table: MagicMock, contact_id: str, *, employee_id: str = "emp-001"
) -> None:
    """Shared assertion: Response row received contactId + dispatchedAt + ADD callAttempts."""
    table.update_item.assert_called_once()
    kwargs = table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"cycleId": "cycle-abc", "employeeId": employee_id}
    assert kwargs["ExpressionAttributeValues"][":cid"] == contact_id
    assert kwargs["ExpressionAttributeValues"][":one"] == 1
    assert "SET contactId = :cid" in kwargs["UpdateExpression"]
    assert "ADD callAttempts :one" in kwargs["UpdateExpression"]


# --- Mock test 1: employeeId suffix "0" → RECORDED + SAFE -------------


def test_mock_suffix_0_records_wav_and_signals_no_retry(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    event = _make_event(employeeId="emp-0")  # suffix '0' → RECORDED / SAFE
    result = handler.lambda_handler(event, None)

    expected_contact_id = "mock-cycle-abc-emp-0-1"
    assert result == {
        "status": "ok",
        "contactId": expected_contact_id,
        "retry": False,
    }
    # No real Connect API call.
    mock_connect.start_outbound_voice_contact.assert_not_called()

    # Placeholder wav uploaded to design-layout key.
    mock_s3.put_object.assert_called_once()
    s3_kwargs = mock_s3.put_object.call_args.kwargs
    assert s3_kwargs["Bucket"] == "test-recordings-bucket"
    assert s3_kwargs["Key"] == "recordings/cycle-abc/emp-0/1.wav"
    assert s3_kwargs["ContentType"] == "audio/wav"
    assert len(s3_kwargs["Body"]) == 1024
    assert s3_kwargs["Body"] == b"\x00" * 1024

    # Response UpdateItem records contactId + ADD callAttempts.
    mock_response_table.update_item.assert_called_once()
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["ExpressionAttributeValues"][":cid"] == expected_contact_id

    # SFN payload carries contactId + RECORDED + retry=False.
    mock_sfn.send_task_success.assert_called_once()
    payload = json.loads(mock_sfn.send_task_success.call_args.kwargs["output"])
    assert payload == {
        "retry": False,
        "contactId": expected_contact_id,
        "callResultCode": "RECORDED",
    }


# --- Mock test 2: employeeId suffix "3" → RECORDED + INJURED ----------


def test_mock_suffix_3_records_wav_and_signals_no_retry(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    event = _make_event(employeeId="emp-3", attempt=2)
    result = handler.lambda_handler(event, None)

    expected_contact_id = "mock-cycle-abc-emp-3-2"
    assert result["retry"] is False
    assert result["contactId"] == expected_contact_id

    # seq picks up the attempt number; the second attempt writes to /2.wav.
    s3_kwargs = mock_s3.put_object.call_args.kwargs
    assert s3_kwargs["Key"] == "recordings/cycle-abc/emp-3/2.wav"

    payload = json.loads(mock_sfn.send_task_success.call_args.kwargs["output"])
    assert payload["callResultCode"] == "RECORDED"
    assert payload["retry"] is False
    mock_connect.start_outbound_voice_contact.assert_not_called()


# --- Mock test 3: employeeId suffix "5" → RECORDED + UNAVAILABLE ------


def test_mock_suffix_5_records_wav_and_signals_no_retry(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    event = _make_event(employeeId="emp-5")
    result = handler.lambda_handler(event, None)

    assert result["retry"] is False
    assert result["contactId"] == "mock-cycle-abc-emp-5-1"
    mock_s3.put_object.assert_called_once()
    payload = json.loads(mock_sfn.send_task_success.call_args.kwargs["output"])
    assert payload["callResultCode"] == "RECORDED"
    _assert_response_update_records_contact(
        mock_response_table, "mock-cycle-abc-emp-5-1", employee_id="emp-5"
    )


# --- Mock test 4: employeeId suffix "7" → NO_ANSWER -------------------


def test_mock_suffix_7_skips_s3_and_signals_retry_no_answer(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    event = _make_event(employeeId="emp-7")
    result = handler.lambda_handler(event, None)

    expected_contact_id = "mock-cycle-abc-emp-7-1"
    assert result == {
        "status": "ok",
        "contactId": expected_contact_id,
        "retry": True,
    }
    # No recording for NO_ANSWER (no audio in production path either).
    mock_s3.put_object.assert_not_called()
    mock_connect.start_outbound_voice_contact.assert_not_called()

    _assert_response_update_records_contact(
        mock_response_table, expected_contact_id, employee_id="emp-7"
    )

    payload = json.loads(mock_sfn.send_task_success.call_args.kwargs["output"])
    assert payload == {
        "retry": True,
        "contactId": expected_contact_id,
        "callResultCode": "NO_ANSWER",
    }


# --- Mock test 5: employeeId suffix "8" → BUSY ------------------------


def test_mock_suffix_8_skips_s3_and_signals_retry_busy(
    mock_connect: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_s3: MagicMock,
    enable_mock_mode: None,
) -> None:
    event = _make_event(employeeId="emp-8", attempt=3)
    result = handler.lambda_handler(event, None)

    expected_contact_id = "mock-cycle-abc-emp-8-3"
    assert result == {
        "status": "ok",
        "contactId": expected_contact_id,
        "retry": True,
    }
    mock_s3.put_object.assert_not_called()
    mock_connect.start_outbound_voice_contact.assert_not_called()

    payload = json.loads(mock_sfn.send_task_success.call_args.kwargs["output"])
    assert payload == {
        "retry": True,
        "contactId": expected_contact_id,
        "callResultCode": "BUSY",
    }
