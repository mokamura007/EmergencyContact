"""Unit tests for the CallEndHandler Lambda (Phase 6.3).

Covers the nine mandatory cases from the task brief:
    1. Happy path — RECORDED writes Response and releases SFN.
    2. ConditionalCheckFailedException on UpdateItem is swallowed; SFN
       is still released (idempotent design, mirrors 6.2 pattern).
    3. ERROR code follows the same flow (passes validation).
    4. NO_ANSWER code follows the same flow.
    5. Invalid ``callResultCode`` → ValueError, UpdateItem NOT called.
    6. Missing ``taskToken`` → ValueError.
    7. ``attempt="1"`` (string) is coerced to int and flows.
    8. ``attempt="abc"`` → ValueError.
    9. Non-dict event → ValueError.

The handler talks to AWS through two module-level globals:
    * ``handler._RESPONSE_TABLE`` — ``DynamoDB.Table`` for Response
    * ``handler._SFN``            — ``stepfunctions`` client
Each is swapped out for a :class:`MagicMock` per test.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.call_end_handler import handler

# --- Fixtures ----------------------------------------------------------


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


# --- Helpers -----------------------------------------------------------


def _make_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "contactId": "contact-abc",
        "cycleId": "cycle-xyz",
        "employeeId": "emp-001",
        "attempt": "1",  # Contact Flow passes strings
        "callResultCode": "RECORDED",
        "taskToken": "task-token-xyz",
    }
    event.update(overrides)
    return event


def _conditional_check_failed_error() -> ClientError:
    return ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "callResultCode already set",
            }
        },
        operation_name="UpdateItem",
    )


# --- Test 1: happy path (RECORDED) -------------------------------------


def test_recorded_writes_response_and_releases_sfn(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    result = handler.lambda_handler(_make_event(), None)

    assert result == {
        "status": "ok",
        "contactId": "contact-abc",
        "callResultCode": "RECORDED",
    }

    # UpdateItem with the documented Key, Expression, and Condition.
    mock_response_table.update_item.assert_called_once()
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["Key"] == {"cycleId": "cycle-xyz", "employeeId": "emp-001"}
    assert update_kwargs["UpdateExpression"] == (
        "SET callResultCode = :code, endedAt = :now"
    )
    assert update_kwargs["ConditionExpression"] == (
        "attribute_not_exists(callResultCode)"
    )
    assert update_kwargs["ExpressionAttributeValues"][":code"] == "RECORDED"
    assert ":now" in update_kwargs["ExpressionAttributeValues"]
    # callAttempts is NOT incremented here (6.2 already did it).
    assert ":one" not in update_kwargs["ExpressionAttributeValues"]
    assert "callAttempts" not in update_kwargs["UpdateExpression"]

    # SFN release with retry=False payload.
    mock_sfn.send_task_success.assert_called_once()
    sfn_kwargs = mock_sfn.send_task_success.call_args.kwargs
    assert sfn_kwargs["taskToken"] == "task-token-xyz"
    payload = json.loads(sfn_kwargs["output"])
    assert payload == {
        "retry": False,
        "contactId": "contact-abc",
        "callResultCode": "RECORDED",
    }


# --- Test 2: ConditionalCheckFailedException is swallowed -------------


def test_conditional_check_failed_is_swallowed_and_sfn_still_released(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """A duplicate Contact Flow invocation must not deadlock the SFN."""
    mock_response_table.update_item.side_effect = _conditional_check_failed_error()

    result = handler.lambda_handler(_make_event(), None)

    # Handler still returns the ok sentinel — the first writer is the
    # source of truth and we have nothing to add.
    assert result == {
        "status": "ok",
        "contactId": "contact-abc",
        "callResultCode": "RECORDED",
    }
    mock_response_table.update_item.assert_called_once()
    # Critical: SFN is still released so the Map iteration completes.
    mock_sfn.send_task_success.assert_called_once()


# --- Test 3: ERROR code passes validation -----------------------------


def test_error_code_follows_same_flow(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    result = handler.lambda_handler(_make_event(callResultCode="ERROR"), None)

    assert result["callResultCode"] == "ERROR"
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["ExpressionAttributeValues"][":code"] == "ERROR"
    mock_sfn.send_task_success.assert_called_once()
    payload = json.loads(mock_sfn.send_task_success.call_args.kwargs["output"])
    assert payload["callResultCode"] == "ERROR"
    assert payload["retry"] is False


# --- Test 4: NO_ANSWER code passes validation -------------------------


def test_no_answer_code_follows_same_flow(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    result = handler.lambda_handler(_make_event(callResultCode="NO_ANSWER"), None)

    assert result["callResultCode"] == "NO_ANSWER"
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["ExpressionAttributeValues"][":code"] == "NO_ANSWER"
    mock_sfn.send_task_success.assert_called_once()


# --- Test 5: invalid callResultCode -----------------------------------


def test_invalid_call_result_code_raises_value_error(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    with pytest.raises(ValueError, match="callResultCode must be one of"):
        handler.lambda_handler(_make_event(callResultCode="INVALID"), None)
    mock_response_table.update_item.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()


# --- Test 6: missing taskToken ----------------------------------------


def test_missing_task_token_raises_value_error(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    bad_event = _make_event()
    bad_event.pop("taskToken")
    with pytest.raises(ValueError, match="missing required input keys"):
        handler.lambda_handler(bad_event, None)
    mock_response_table.update_item.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()


# --- Test 7: attempt="1" string is coerced to int ---------------------


def test_attempt_string_is_coerced_to_int(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """Contact Flow Attributes are always strings; handler must accept."""
    result = handler.lambda_handler(_make_event(attempt="1"), None)
    assert result["status"] == "ok"
    mock_response_table.update_item.assert_called_once()
    mock_sfn.send_task_success.assert_called_once()


# --- Test 8: attempt="abc" non-numeric --------------------------------


def test_attempt_non_numeric_string_raises_value_error(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    with pytest.raises(ValueError, match="attempt must be an integer"):
        handler.lambda_handler(_make_event(attempt="abc"), None)
    mock_response_table.update_item.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()


# --- Test 9: non-dict event -------------------------------------------


def test_non_dict_event_raises_value_error() -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        handler.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]


# --- Extra: non-conditional ClientError propagates --------------------


def test_non_conditional_client_error_propagates(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """Any DynamoDB error other than the conditional check must surface."""
    mock_response_table.update_item.side_effect = ClientError(
        error_response={
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "rate limited",
            }
        },
        operation_name="UpdateItem",
    )
    with pytest.raises(ClientError):
        handler.lambda_handler(_make_event(), None)
    # SFN must NOT be released when the write fails for a non-conditional reason
    # (the SFN Catch block at Map level handles routing).
    mock_sfn.send_task_success.assert_not_called()


# --- Phase 7.3: Connect nested event shape (the production payload) ---


def _make_connect_nested_event(
    *,
    contact_id: str | None = "contact-abc",
    attributes: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    include_contact_data: bool = True,
    include_details: bool = True,
) -> dict[str, Any]:
    """Build the documented Amazon Connect ``Invoke Lambda`` payload.

    Mirrors the actual envelope produced by the Outbound Contact Flow's
    ``InvokeLambdaFunction`` action (Phase 7.1): cycleId / employeeId /
    attempt / taskToken come from Contact Attributes (set by
    ConnectDispatcher Phase 6.2), callResultCode comes from the
    ``LambdaInvocationAttributes`` block of outbound.json.
    """
    default_attrs: dict[str, Any] = {
        "cycleId": "cycle-xyz",
        "employeeId": "emp-001",
        "attempt": "1",
        "taskToken": "task-token-xyz",
    }
    if attributes is not None:
        default_attrs.update(attributes)

    default_params: dict[str, Any] = {"callResultCode": "RECORDED"}
    if parameters is not None:
        default_params.update(parameters)

    if not include_details:
        return {"Name": "ContactFlowEvent"}

    contact_data: dict[str, Any] = {}
    if include_contact_data:
        contact_data = {
            "ContactId": contact_id,
            "Channel": "VOICE",
            "Attributes": default_attrs,
        }
    return {
        "Details": (
            {"ContactData": contact_data, "Parameters": default_params}
            if include_contact_data
            else {"Parameters": default_params}
        ),
        "Name": "ContactFlowEvent",
    }


def test_connect_nested_event_happy_path(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """Real Connect Contact Flow payload is normalised + processed end-to-end.

    Verifies Phase 7.3 wiring: ConnectDispatcher writes Attributes →
    Connect serialises them under ``Details.ContactData.Attributes`` →
    CallEndHandler flattens them → Response + SFN are updated exactly
    as in the flat-shape happy path.
    """
    result = handler.lambda_handler(_make_connect_nested_event(), None)

    assert result == {
        "status": "ok",
        "contactId": "contact-abc",
        "callResultCode": "RECORDED",
    }
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["Key"] == {"cycleId": "cycle-xyz", "employeeId": "emp-001"}
    assert update_kwargs["ExpressionAttributeValues"][":code"] == "RECORDED"
    sfn_kwargs = mock_sfn.send_task_success.call_args.kwargs
    assert sfn_kwargs["taskToken"] == "task-token-xyz"
    payload = json.loads(sfn_kwargs["output"])
    assert payload == {
        "retry": False,
        "contactId": "contact-abc",
        "callResultCode": "RECORDED",
    }


def test_connect_nested_event_missing_attribute_raises(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """Connect envelope without ``taskToken`` Contact Attribute is rejected.

    Simulates a Contact Flow misconfiguration where one of the four
    required Attributes ConnectDispatcher passes via
    ``StartOutboundVoiceContact.Attributes`` was dropped or renamed.
    """
    event = _make_connect_nested_event(attributes={"taskToken": ""})
    with pytest.raises(ValueError, match="missing required input keys"):
        handler.lambda_handler(event, None)
    mock_response_table.update_item.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()


def test_connect_nested_event_missing_call_result_code_raises(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """Connect envelope without ``Parameters.callResultCode`` is rejected.

    Simulates a Contact Flow JSON where ``LambdaInvocationAttributes``
    was empty (e.g. outbound.json edited incorrectly).
    """
    event = _make_connect_nested_event(parameters={"callResultCode": ""})
    with pytest.raises(ValueError, match="missing required input keys"):
        handler.lambda_handler(event, None)
    mock_response_table.update_item.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()


def test_connect_nested_event_attempt_string_is_coerced(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """Contact Attributes are always strings — nested path also coerces."""
    event = _make_connect_nested_event(attributes={"attempt": "3"})
    result = handler.lambda_handler(event, None)
    assert result["status"] == "ok"
    mock_response_table.update_item.assert_called_once()
    mock_sfn.send_task_success.assert_called_once()


def test_connect_nested_event_without_contact_data_falls_through_to_flat(
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """An event with ``Details`` but no ``ContactData`` is *not* a Connect
    envelope — it falls through to flat parsing, which will then
    surface a missing-keys ValueError because the flat keys aren't
    there either.
    """
    bad_event = {"Details": {"Parameters": {"callResultCode": "RECORDED"}}}
    with pytest.raises(ValueError, match="missing required input keys"):
        handler.lambda_handler(bad_event, None)
    mock_response_table.update_item.assert_not_called()
    mock_sfn.send_task_success.assert_not_called()
