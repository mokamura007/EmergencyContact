"""Unit tests for the InboundHandler Lambda (Phase 9.2).

Covers the four scenarios called out by the task brief:
    1. ACTIVE_CYCLE       — registered caller, RUNNING cycle exists.
    2. NO_CYCLE           — registered caller, no eligible cycle.
    3. NOT_REGISTERED     — caller number not in Employee_Master.
    4. CYCLE_TERMINATED   — registered caller, most-recent cycle TIMEOUT.

Plus boundary and error cases:
    * step normalisation from the nested Amazon Connect envelope.
    * identify idempotency (re-invoked identify swallows
      ConditionalCheckFailedException).
    * finalize ACTIVE_CYCLE appends INBOUND to Response.callResultCodes
      and leaves callAttempts untouched.
    * finalize for non-ACTIVE flows writes NULL recording / transcript
      references and ``callResultCode = NO_RECORDING``.
    * deleted Employee record is treated as NOT_REGISTERED (Property 2).
    * input-validation ValueErrors on malformed events.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.inbound_handler import handler

# --- Fixtures ----------------------------------------------------------


@pytest.fixture
def mock_employee_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="EmployeeTable")
    monkeypatch.setattr(handler, "_EMPLOYEE_TABLE", table)
    return table


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
def mock_inbound_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="InboundContactTable")
    monkeypatch.setattr(handler, "_INBOUND_CONTACT_TABLE", table)
    return table


# --- Helpers -----------------------------------------------------------


def _iso(d: dt.datetime) -> str:
    return d.isoformat(timespec="seconds").replace("+00:00", "Z")


def _identify_event(
    *,
    contact_id: str = "contact-abc",
    caller_number: str = "+819012345678",
) -> dict[str, Any]:
    """Flat-shape identify event."""
    return {
        "step": "identify",
        "contactId": contact_id,
        "callerNumber": caller_number,
    }


def _identify_event_connect(
    *,
    contact_id: str = "contact-abc",
    caller_number: str = "+819012345678",
) -> dict[str, Any]:
    """Connect-nested-shape identify event."""
    return {
        "Details": {
            "ContactData": {
                "ContactId": contact_id,
                "CustomerEndpoint": {
                    "Address": caller_number,
                    "Type": "TELEPHONE_NUMBER",
                },
                "Attributes": {},
            },
            "Parameters": {"step": "identify"},
        },
        "Name": "ContactFlowEvent",
    }


def _finalize_event_active(
    *,
    contact_id: str = "contact-abc",
    employee_id: str = "emp-001",
    cycle_id: str = "cycle-xyz",
) -> dict[str, Any]:
    return {
        "step": "finalize",
        "contactId": contact_id,
        "flow": "ACTIVE_CYCLE",
        "employeeId": employee_id,
        "cycleId": cycle_id,
    }


def _finalize_event_non_active(
    *, contact_id: str = "contact-abc", flow: str = "NO_CYCLE"
) -> dict[str, Any]:
    return {"step": "finalize", "contactId": contact_id, "flow": flow}


def _employee_visible(
    *, employee_id: str = "emp-001", phone: str = "+819012345678"
) -> dict[str, Any]:
    return {
        "employeeId": employee_id,
        "name": "テスト 太郎",
        "phoneNumber": phone,
        "deleted": False,
    }


def _employee_deleted(
    *, employee_id: str = "emp-001", phone: str = "+819012345678"
) -> dict[str, Any]:
    return {
        "employeeId": employee_id,
        "name": "テスト 太郎",
        "phoneNumber": phone,
        "deleted": True,
    }


def _running_cycle(cycle_id: str, age_hours: int) -> dict[str, Any]:
    started = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=age_hours)
    return {
        "cycleId": cycle_id,
        "status": "RUNNING",
        "startedAt": _iso(started),
    }


def _timeout_cycle(cycle_id: str, age_hours: int) -> dict[str, Any]:
    started = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=age_hours + 1)
    completed = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=age_hours)
    return {
        "cycleId": cycle_id,
        "status": "TIMEOUT",
        "startedAt": _iso(started),
        "completedAt": _iso(completed),
    }


def _conditional_check_failed_error() -> ClientError:
    return ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "row already exists",
            }
        },
        operation_name="PutItem",
    )


# --- Scenario 1: ACTIVE_CYCLE -------------------------------------------


def test_identify_active_cycle_writes_provisional_and_returns_active(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
    mock_response_table: MagicMock,
) -> None:
    mock_employee_table.query.return_value = {"Items": [_employee_visible()]}
    # Cycle query loops over four statuses; return one RUNNING for the
    # first call, empty for the rest.
    mock_cycle_table.query.side_effect = [
        {"Items": [_running_cycle("cycle-xyz", age_hours=2)]},
        {"Items": []},
        {"Items": []},
        {"Items": []},
    ]

    result = handler.lambda_handler(_identify_event(), None)

    assert result == {
        "flow": "ACTIVE_CYCLE",
        "employeeId": "emp-001",
        "cycleId": "cycle-xyz",
    }
    # Employee GSI lookup happened with the right key.
    employee_kwargs = mock_employee_table.query.call_args.kwargs
    assert employee_kwargs["IndexName"] == "PhoneNumberIndex"
    # Cycle was queried four times (one per status).
    assert mock_cycle_table.query.call_count == 4
    # Provisional Inbound_Contact row was put with the right shape.
    put_kwargs = mock_inbound_table.put_item.call_args.kwargs
    assert put_kwargs["Item"]["contactId"] == "contact-abc"
    assert put_kwargs["Item"]["flow"] == "ACTIVE_CYCLE"
    assert put_kwargs["Item"]["callerNumber"] == "+819012345678"
    assert put_kwargs["Item"]["callerNumberMasked"].startswith("+*")
    assert put_kwargs["Item"]["employeeId"] == "emp-001"
    assert put_kwargs["Item"]["cycleId"] == "cycle-xyz"
    assert put_kwargs["ConditionExpression"] == "attribute_not_exists(contactId)"
    # Identify must not touch Response (that's finalize's job).
    mock_response_table.update_item.assert_not_called()


def test_finalize_active_cycle_appends_inbound_and_finalizes_inbound_contact(
    mock_response_table: MagicMock,
    mock_inbound_table: MagicMock,
) -> None:
    result = handler.lambda_handler(_finalize_event_active(), None)

    assert result["status"] == "ok"
    assert result["flow"] == "ACTIVE_CYCLE"
    assert result["cycleId"] == "cycle-xyz"

    # Response.put_item is called first (with attribute_not_exists) to
    # create the row when absent.
    response_put_kwargs = mock_response_table.put_item.call_args.kwargs
    assert response_put_kwargs["Item"]["cycleId"] == "cycle-xyz"
    assert response_put_kwargs["Item"]["employeeId"] == "emp-001"
    assert response_put_kwargs["Item"]["callAttempts"] == 0
    assert response_put_kwargs["ConditionExpression"] == (
        "attribute_not_exists(cycleId)"
    )

    # Response.update_item appends INBOUND to callResultCodes.
    update_kwargs = mock_response_table.update_item.call_args.kwargs
    assert update_kwargs["Key"] == {"cycleId": "cycle-xyz", "employeeId": "emp-001"}
    assert "list_append" in update_kwargs["UpdateExpression"]
    assert update_kwargs["ExpressionAttributeValues"][":inbound"] == ["INBOUND"]
    # Critically: callAttempts is NOT mentioned (Requirement 13.5).
    assert "callAttempts" not in update_kwargs["UpdateExpression"]

    # Inbound_Contact is finalised with the resolved S3 keys.
    inbound_update_kwargs = mock_inbound_table.update_item.call_args.kwargs
    expr_values = inbound_update_kwargs["ExpressionAttributeValues"]
    assert expr_values[":rc"] == "RECORDED"
    assert expr_values[":vs"] == "PENDING"
    assert expr_values[":rk"].startswith("inbound/")
    assert expr_values[":rk"].endswith("/emp-001/contact-abc.wav")
    assert expr_values[":tk"].endswith("/emp-001/contact-abc.json")
    assert expr_values[":f"] == "ACTIVE_CYCLE"


def test_finalize_active_cycle_passes_through_existing_response_row(
    mock_response_table: MagicMock,
    mock_inbound_table: MagicMock,
) -> None:
    """Response.put_item ConditionalCheckFailedException must be swallowed."""
    mock_response_table.put_item.side_effect = _conditional_check_failed_error()

    result = handler.lambda_handler(_finalize_event_active(), None)

    assert result["status"] == "ok"
    # update_item still runs after the swallowed put_item failure.
    mock_response_table.update_item.assert_called_once()


# --- Scenario 2: NO_CYCLE ---------------------------------------------


def test_identify_no_cycle_returns_no_cycle_and_omits_ids(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
) -> None:
    mock_employee_table.query.return_value = {"Items": [_employee_visible()]}
    mock_cycle_table.query.return_value = {"Items": []}

    result = handler.lambda_handler(_identify_event(), None)

    assert result == {"flow": "NO_CYCLE"}
    put_kwargs = mock_inbound_table.put_item.call_args.kwargs
    assert put_kwargs["Item"]["flow"] == "NO_CYCLE"
    # employeeId is present (caller is a registered employee); cycleId is absent.
    assert put_kwargs["Item"]["employeeId"] == "emp-001"
    assert "cycleId" not in put_kwargs["Item"]


def test_finalize_no_cycle_writes_null_recording_and_no_recording_code(
    mock_inbound_table: MagicMock,
    mock_response_table: MagicMock,
) -> None:
    result = handler.lambda_handler(_finalize_event_non_active(flow="NO_CYCLE"), None)

    assert result == {"status": "ok", "flow": "NO_CYCLE", "contactId": "contact-abc"}
    # No Response write at all for non-ACTIVE flows.
    mock_response_table.update_item.assert_not_called()
    mock_response_table.put_item.assert_not_called()

    update_kwargs = mock_inbound_table.update_item.call_args.kwargs
    expr_values = update_kwargs["ExpressionAttributeValues"]
    assert expr_values[":rc"] == "NO_RECORDING"
    assert ":rk" not in expr_values
    assert ":tk" not in expr_values
    assert ":vs" not in expr_values
    assert expr_values[":f"] == "NO_CYCLE"


# --- Scenario 3: NOT_REGISTERED ----------------------------------------


def test_identify_not_registered_returns_not_registered(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
) -> None:
    mock_employee_table.query.return_value = {"Items": []}

    result = handler.lambda_handler(_identify_event(), None)

    assert result == {"flow": "NOT_REGISTERED"}
    # Cycle query is skipped when no employee matches.
    mock_cycle_table.query.assert_not_called()
    put_kwargs = mock_inbound_table.put_item.call_args.kwargs
    assert put_kwargs["Item"]["flow"] == "NOT_REGISTERED"
    assert "employeeId" not in put_kwargs["Item"]
    assert "cycleId" not in put_kwargs["Item"]


def test_identify_deleted_employee_treated_as_not_registered(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
) -> None:
    """Property 2: deleted Employee record must not match."""
    mock_employee_table.query.return_value = {"Items": [_employee_deleted()]}

    result = handler.lambda_handler(_identify_event(), None)

    assert result == {"flow": "NOT_REGISTERED"}
    mock_cycle_table.query.assert_not_called()
    put_kwargs = mock_inbound_table.put_item.call_args.kwargs
    assert put_kwargs["Item"]["flow"] == "NOT_REGISTERED"


def test_finalize_not_registered_writes_null_recording(
    mock_inbound_table: MagicMock,
    mock_response_table: MagicMock,
) -> None:
    result = handler.lambda_handler(
        _finalize_event_non_active(flow="NOT_REGISTERED"), None
    )

    assert result == {
        "status": "ok",
        "flow": "NOT_REGISTERED",
        "contactId": "contact-abc",
    }
    update_kwargs = mock_inbound_table.update_item.call_args.kwargs
    assert update_kwargs["ExpressionAttributeValues"][":rc"] == "NO_RECORDING"


# --- Scenario 4: CYCLE_TERMINATED --------------------------------------


def test_identify_cycle_terminated_returns_cycle_terminated(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
) -> None:
    mock_employee_table.query.return_value = {"Items": [_employee_visible()]}
    # No RUNNING, no COMPLETED; only a recent TIMEOUT cycle.
    mock_cycle_table.query.side_effect = [
        {"Items": []},
        {"Items": []},
        {"Items": [_timeout_cycle("cycle-old", age_hours=2)]},
        {"Items": []},
    ]

    result = handler.lambda_handler(_identify_event(), None)

    assert result == {"flow": "CYCLE_TERMINATED"}
    put_kwargs = mock_inbound_table.put_item.call_args.kwargs
    assert put_kwargs["Item"]["flow"] == "CYCLE_TERMINATED"
    # cycleId is recorded on the provisional row for forensic linkage.
    assert put_kwargs["Item"]["cycleId"] == "cycle-old"


def test_finalize_cycle_terminated_writes_null_recording(
    mock_inbound_table: MagicMock,
    mock_response_table: MagicMock,
) -> None:
    result = handler.lambda_handler(
        _finalize_event_non_active(flow="CYCLE_TERMINATED"), None
    )

    assert result["flow"] == "CYCLE_TERMINATED"
    update_kwargs = mock_inbound_table.update_item.call_args.kwargs
    assert update_kwargs["ExpressionAttributeValues"][":rc"] == "NO_RECORDING"


# --- Connect envelope shape -------------------------------------------


def test_identify_accepts_connect_nested_envelope(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
) -> None:
    mock_employee_table.query.return_value = {"Items": [_employee_visible()]}
    mock_cycle_table.query.side_effect = [
        {"Items": [_running_cycle("cycle-xyz", age_hours=1)]},
        {"Items": []},
        {"Items": []},
        {"Items": []},
    ]

    result = handler.lambda_handler(_identify_event_connect(), None)

    assert result["flow"] == "ACTIVE_CYCLE"
    assert result["cycleId"] == "cycle-xyz"


# --- Idempotency ------------------------------------------------------


def test_identify_swallows_conditional_check_failed_on_provisional_put(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
) -> None:
    """Re-invocation of identify for the same ContactId is idempotent."""
    mock_employee_table.query.return_value = {"Items": [_employee_visible()]}
    mock_cycle_table.query.side_effect = [
        {"Items": [_running_cycle("cycle-xyz", age_hours=2)]},
        {"Items": []},
        {"Items": []},
        {"Items": []},
    ]
    mock_inbound_table.put_item.side_effect = _conditional_check_failed_error()

    # Should NOT raise; the second identify just observes the row exists.
    result = handler.lambda_handler(_identify_event(), None)
    assert result["flow"] == "ACTIVE_CYCLE"


# --- Input validation -------------------------------------------------


def test_missing_step_raises() -> None:
    with pytest.raises(ValueError, match="step must be"):
        handler.lambda_handler({"contactId": "c1"}, None)


def test_unknown_step_raises() -> None:
    with pytest.raises(ValueError, match="step must be"):
        handler.lambda_handler({"step": "weird"}, None)


def test_identify_missing_contact_id_raises() -> None:
    with pytest.raises(ValueError, match="identify: contactId"):
        handler.lambda_handler(
            {"step": "identify", "callerNumber": "+819012345678"}, None
        )


def test_identify_missing_caller_number_raises() -> None:
    with pytest.raises(ValueError, match="identify: callerNumber"):
        handler.lambda_handler({"step": "identify", "contactId": "c1"}, None)


def test_identify_non_e164_caller_number_raises() -> None:
    with pytest.raises(ValueError, match=r"E\.164"):
        handler.lambda_handler(
            {"step": "identify", "contactId": "c1", "callerNumber": "09012345678"},
            None,
        )


def test_finalize_invalid_flow_raises() -> None:
    with pytest.raises(ValueError, match="finalize: flow"):
        handler.lambda_handler(
            {"step": "finalize", "contactId": "c1", "flow": "BOGUS"}, None
        )


def test_finalize_active_cycle_missing_employee_id_raises() -> None:
    with pytest.raises(ValueError, match="finalize: employeeId"):
        handler.lambda_handler(
            {
                "step": "finalize",
                "contactId": "c1",
                "flow": "ACTIVE_CYCLE",
                "cycleId": "cycle-xyz",
            },
            None,
        )


def test_finalize_active_cycle_missing_cycle_id_raises() -> None:
    with pytest.raises(ValueError, match="finalize: cycleId"):
        handler.lambda_handler(
            {
                "step": "finalize",
                "contactId": "c1",
                "flow": "ACTIVE_CYCLE",
                "employeeId": "emp-001",
            },
            None,
        )


def test_non_dict_event_raises() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        handler.lambda_handler("not a dict", None)  # type: ignore[arg-type]


# --- Phase 12.3: INBOUND_CONTACT_RECEIVED audit emission --------------


def test_identify_emits_inbound_contact_received_audit_for_active_cycle(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Identify for ACTIVE_CYCLE flow emits INBOUND_CONTACT_RECEIVED."""
    mock_employee_table.query.return_value = {"Items": [_employee_visible()]}
    mock_cycle_table.query.return_value = {
        "Items": [_running_cycle("cycle-xyz", age_hours=1)]
    }

    handler.lambda_handler(_identify_event(), None)

    _mock_audit_logger.put_log_events.assert_called_once()
    import json as _json

    record = _json.loads(
        _mock_audit_logger.put_log_events.call_args.kwargs["logEvents"][0]["message"]
    )
    assert record["event"] == "INBOUND_CONTACT_RECEIVED"
    assert record["principal"] == "<connect-service>"
    assert record["target"] == "contact-abc"
    assert record["flow"] == "ACTIVE_CYCLE"
    assert record["employeeId"] == "emp-001"
    assert record["cycleId"] == "cycle-xyz"
    # phoneMasked: + and the last 4 digits kept (Property 22).
    assert record["phoneMasked"].startswith("+")
    assert record["phoneMasked"].endswith("5678")


def test_identify_emits_audit_for_not_registered_caller(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """NOT_REGISTERED flow still produces one audit emission."""
    mock_employee_table.query.return_value = {"Items": []}

    handler.lambda_handler(_identify_event(), None)

    _mock_audit_logger.put_log_events.assert_called_once()
    import json as _json

    record = _json.loads(
        _mock_audit_logger.put_log_events.call_args.kwargs["logEvents"][0]["message"]
    )
    assert record["event"] == "INBOUND_CONTACT_RECEIVED"
    assert record["flow"] == "NOT_REGISTERED"
    # employeeId / cycleId are null when unmatched.
    assert record["employeeId"] is None
    assert record["cycleId"] is None


def test_identify_audit_phone_masked_not_raw(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_inbound_table: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Raw caller number must not appear in the audit record (Req 16.4)."""
    raw_phone = "+819098765432"
    mock_employee_table.query.return_value = {"Items": []}

    handler.lambda_handler(
        _identify_event(contact_id="cid-1", caller_number=raw_phone), None
    )

    raw_message = _mock_audit_logger.put_log_events.call_args.kwargs[
        "logEvents"
    ][0]["message"]
    assert raw_phone not in raw_message
    # The 4-digit tail does appear (the mask preserves only the last 4).
    assert "5432" in raw_message


def test_finalize_does_not_emit_audit(
    mock_inbound_table: MagicMock,
    mock_response_table: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Phase 12.3 emits one audit record per inbound contact, at identify time.

    Finalize does not produce a second audit emission — the design
    treats the inbound contact as a single auditable event (the
    classification at identify).
    """
    handler.lambda_handler(_finalize_event_non_active(flow="NO_CYCLE"), None)
    _mock_audit_logger.put_log_events.assert_not_called()
