"""EmployeeApi anonymize handler — unit tests (Task 15.12).

Validates: Requirements 15.5 / Property 20 extension (irreversible
anonymization of past Cycle Response rows on personal-data request).

Covered cases:
    * Happy path:
        - Logically-deleted employee with N Response rows across
          multiple cycles
        - Verifies: (1) 200 status + correct response body,
                    (2) TransactWriteItems issued with Delete+Put pairs,
                    (3) audit log records the original employee_id as
                        ``target`` and NOT the anonymized id,
                    (4) ``responseCountUpdated`` matches the row count
                        actually rewritten.
    * Authorization: caller without Administrator group -> 403.
    * Salt fail-fast: EMPLOYEE_ANONYMIZE_SALT empty -> 503; no DynamoDB
      writes happen; no audit log emitted.
    * Target validation:
        - Employee not found -> 404.
        - Employee exists but not deleted -> 409.
    * Path-param validation: missing ``id`` -> 400.
    * Batching: 30 Response rows must split into two TransactWriteItems
      calls (chunk size = 12 rows = 24 transact ops, well under the
      25-op limit).
    * Zero rows: an employee with no Response rows still returns 200
      and emits one EMPLOYEE_ANONYMIZE audit record with
      ``responseCountUpdated == 0``.
"""

# ruff: noqa: N803
#   The handler test mocks expose boto3-style PascalCase keyword args
#   (TransactItems, Key, ...) that mirror the production call shapes;
#   renaming would break the assertions.

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from lambdas.employee_api import handler


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_employee_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="EmployeeTable")
    monkeypatch.setattr(handler, "_TABLE", table)
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
def mock_ddb_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="DDBClient")
    monkeypatch.setattr(handler, "_DDB_CLIENT", client)
    return client


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _admin_event(
    employee_id: str = "emp-001",
    *,
    is_admin: bool = True,
) -> dict[str, Any]:
    """Build a minimal API Gateway Proxy event for the anonymize route."""
    groups = "Administrator" if is_admin else "SomeOtherGroup"
    return {
        "httpMethod": "POST",
        "resource": "/employees/{id}/anonymize",
        "pathParameters": {"id": employee_id},
        "body": None,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "test-principal-sub",
                    "cognito:groups": groups,
                }
            }
        },
    }


def _deleted_employee_item(employee_id: str = "emp-001") -> dict[str, Any]:
    return {
        "Item": {
            "employeeId": employee_id,
            "name": "Departed Person",
            "phoneNumber": "",  # NULL-ed by the delete handler.
            "role": "employee",
            "deleted": True,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-06-01T00:00:00Z",
        }
    }


def _active_employee_item(employee_id: str = "emp-001") -> dict[str, Any]:
    return {
        "Item": {
            "employeeId": employee_id,
            "name": "Active Person",
            "phoneNumber": "+819012345678",
            "role": "employee",
            "deleted": False,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    }


def _make_response_row(
    cycle_id: str, employee_id: str, voice_status: str = "SAFE"
) -> dict[str, Any]:
    return {
        "cycleId": cycle_id,
        "employeeId": employee_id,
        "voiceStatus": voice_status,
        "callAttempts": 1,
        "createdAt": "2026-06-01T00:00:00Z",
    }


def _parse_response_body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_anonymize_happy_path_writes_all_response_rows(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Happy path: deleted employee with 3 rows across 3 cycles -> 200."""
    # Arrange: employee is logically deleted.
    mock_employee_table.get_item.return_value = _deleted_employee_item("emp-001")

    # Arrange: 3 cycles exist.
    mock_cycle_table.scan.return_value = {
        "Items": [
            {"cycleId": "cycle-a"},
            {"cycleId": "cycle-b"},
            {"cycleId": "cycle-c"},
        ],
    }

    # Arrange: cycle-a and cycle-c have a row for emp-001; cycle-b has none.
    def _query_side_effect(**kwargs: Any) -> dict[str, Any]:
        # The KeyConditionExpression carries the cycleId/employeeId pair.
        # We inspect the boto3 ConditionBase shape to dispatch.
        expr = kwargs["KeyConditionExpression"]
        # ConditionBase.__and__ exposes the operands as `_values`.
        # We simply rely on call ordering for determinism in this test.
        return _query_side_effect.dispatch.pop(0)  # type: ignore[attr-defined]

    _query_side_effect.dispatch = [  # type: ignore[attr-defined]
        {"Items": [_make_response_row("cycle-a", "emp-001")]},
        {"Items": []},
        {"Items": [_make_response_row("cycle-c", "emp-001", "INJURED")]},
    ]
    mock_response_table.query.side_effect = _query_side_effect

    # Act.
    result = handler.lambda_handler(_admin_event("emp-001"), None)

    # Assert: 200 + response body.
    assert result["statusCode"] == 200
    body = _parse_response_body(result)
    assert body["employeeId"] == "emp-001"
    assert body["anonymizedId"].startswith("ANON_")
    assert body["responseCountUpdated"] == 2

    # Assert: TransactWriteItems was called once (2 rows fit in one batch).
    mock_ddb_client.transact_write_items.assert_called_once()
    transact_kwargs = mock_ddb_client.transact_write_items.call_args.kwargs
    items = transact_kwargs["TransactItems"]
    # 2 rows * 2 ops/row = 4 transact ops.
    assert len(items) == 4
    # Verify alternating Delete + Put.
    assert "Delete" in items[0]
    assert "Put" in items[1]
    assert "Delete" in items[2]
    assert "Put" in items[3]
    # Delete uses the ORIGINAL employeeId; Put uses the anonymized one.
    assert items[0]["Delete"]["Key"]["employeeId"]["S"] == "emp-001"
    assert items[1]["Put"]["Item"]["employeeId"]["S"] == body["anonymizedId"]

    # Assert: audit log records the original id, NOT the anonymized id.
    _mock_audit_logger.put_log_events.assert_called_once()
    audit_kwargs = _mock_audit_logger.put_log_events.call_args.kwargs
    record = json.loads(audit_kwargs["logEvents"][0]["message"])
    assert record["event"] == "EMPLOYEE_ANONYMIZE"
    assert record["target"] == "emp-001"
    assert record["principal"] == "test-principal-sub"
    assert record["outcome"] == "SUCCESS"
    assert record["responseCountUpdated"] == 2
    assert record["anonymizedIdPrefix"] == "ANON_"
    # CRITICAL: the audit log MUST NOT carry the anonymized id (would
    # constitute a reverse-lookup channel).
    raw_message = audit_kwargs["logEvents"][0]["message"]
    assert body["anonymizedId"] not in raw_message


def test_anonymize_zero_response_rows_still_returns_200(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Employee with no Response rows -> 200, responseCountUpdated=0, no transact."""
    mock_employee_table.get_item.return_value = _deleted_employee_item("emp-002")
    mock_cycle_table.scan.return_value = {"Items": [{"cycleId": "cycle-only"}]}
    mock_response_table.query.return_value = {"Items": []}

    result = handler.lambda_handler(_admin_event("emp-002"), None)

    assert result["statusCode"] == 200
    body = _parse_response_body(result)
    assert body["responseCountUpdated"] == 0
    # No transact write should happen for zero rows.
    mock_ddb_client.transact_write_items.assert_not_called()
    # The audit log is still emitted (audit trail of the request).
    _mock_audit_logger.put_log_events.assert_called_once()


# ---------------------------------------------------------------------------
# Batching.
# ---------------------------------------------------------------------------


def test_anonymize_batches_rows_in_chunks_of_12(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """30 Response rows -> 3 transact calls (12 + 12 + 6 rows).

    Each row is 2 transact ops (Delete + Put), so chunk size is 12
    rows = 24 ops, under the 25-op TransactWriteItems hard limit.
    """
    mock_employee_table.get_item.return_value = _deleted_employee_item("emp-001")
    # 30 cycles, one row per cycle.
    mock_cycle_table.scan.return_value = {
        "Items": [{"cycleId": f"cycle-{i}"} for i in range(30)],
    }
    mock_response_table.query.side_effect = [
        {"Items": [_make_response_row(f"cycle-{i}", "emp-001")]} for i in range(30)
    ]

    result = handler.lambda_handler(_admin_event("emp-001"), None)

    assert result["statusCode"] == 200
    assert _parse_response_body(result)["responseCountUpdated"] == 30
    # 30 rows / 12 rows-per-batch = 3 calls (12 + 12 + 6).
    assert mock_ddb_client.transact_write_items.call_count == 3
    # First batch carries 24 ops (12 rows * 2 ops).
    first_items = mock_ddb_client.transact_write_items.call_args_list[0].kwargs[
        "TransactItems"
    ]
    assert len(first_items) == 24
    # All call batches stay within the 25-op hard limit.
    for call in mock_ddb_client.transact_write_items.call_args_list:
        assert len(call.kwargs["TransactItems"]) <= 25


# ---------------------------------------------------------------------------
# Authorization.
# ---------------------------------------------------------------------------


def test_anonymize_non_administrator_returns_403(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """A caller without the Administrator group is rejected with 403."""
    result = handler.lambda_handler(
        _admin_event("emp-001", is_admin=False), None
    )

    assert result["statusCode"] == 403
    body = _parse_response_body(result)
    assert "Administrator" in body["error"]
    # Nothing must have happened on the data path.
    mock_employee_table.get_item.assert_not_called()
    mock_cycle_table.scan.assert_not_called()
    mock_response_table.query.assert_not_called()
    mock_ddb_client.transact_write_items.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


def test_anonymize_missing_authorizer_claims_returns_403(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Event with no requestContext.authorizer.claims is rejected (403)."""
    event = {
        "httpMethod": "POST",
        "resource": "/employees/{id}/anonymize",
        "pathParameters": {"id": "emp-001"},
        "body": None,
        # Note: no `requestContext` at all — `is_authorized` returns False.
    }
    result = handler.lambda_handler(event, None)
    assert result["statusCode"] == 403


# ---------------------------------------------------------------------------
# Salt fail-fast.
# ---------------------------------------------------------------------------


def test_anonymize_empty_salt_returns_503_and_no_writes(
    monkeypatch: pytest.MonkeyPatch,
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Empty EMPLOYEE_ANONYMIZE_SALT -> 503; no data is touched."""
    monkeypatch.setenv("EMPLOYEE_ANONYMIZE_SALT", "")

    result = handler.lambda_handler(_admin_event("emp-001"), None)

    assert result["statusCode"] == 503
    body = _parse_response_body(result)
    assert "salt" in body["error"].lower()
    # Fail-fast: no data path runs.
    mock_employee_table.get_item.assert_not_called()
    mock_cycle_table.scan.assert_not_called()
    mock_response_table.query.assert_not_called()
    mock_ddb_client.transact_write_items.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


def test_anonymize_unset_salt_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Unset (delenv) EMPLOYEE_ANONYMIZE_SALT also yields 503."""
    monkeypatch.delenv("EMPLOYEE_ANONYMIZE_SALT", raising=False)

    result = handler.lambda_handler(_admin_event("emp-001"), None)

    assert result["statusCode"] == 503


# ---------------------------------------------------------------------------
# Target validation.
# ---------------------------------------------------------------------------


def test_anonymize_employee_not_found_returns_404(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """get_item returning no Item -> 404; no data writes."""
    mock_employee_table.get_item.return_value = {}

    result = handler.lambda_handler(_admin_event("emp-missing"), None)

    assert result["statusCode"] == 404
    mock_cycle_table.scan.assert_not_called()
    mock_response_table.query.assert_not_called()
    mock_ddb_client.transact_write_items.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


def test_anonymize_employee_not_deleted_returns_409(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """An active (deleted=False) employee triggers 409 — anonymize is
    gated on the logical-delete step having already happened."""
    mock_employee_table.get_item.return_value = _active_employee_item("emp-001")

    result = handler.lambda_handler(_admin_event("emp-001"), None)

    assert result["statusCode"] == 409
    body = _parse_response_body(result)
    assert "logically deleted" in body["error"]
    mock_cycle_table.scan.assert_not_called()
    mock_response_table.query.assert_not_called()
    mock_ddb_client.transact_write_items.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


# ---------------------------------------------------------------------------
# Path parameter validation.
# ---------------------------------------------------------------------------


def test_anonymize_missing_id_path_parameter_returns_400(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Missing pathParameters.id -> 400 (raised by handler as ValueError)."""
    event = _admin_event("emp-001")
    event["pathParameters"] = {}

    result = handler.lambda_handler(event, None)

    assert result["statusCode"] == 400
    mock_employee_table.get_item.assert_not_called()


# ---------------------------------------------------------------------------
# Symmetry / regression — anonymize endpoint does NOT collide with delete.
# ---------------------------------------------------------------------------


def test_anonymize_route_is_post_only(
    mock_employee_table: MagicMock,
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """GET / PUT / DELETE on /employees/{id}/anonymize -> 405."""
    for method in ("GET", "PUT", "DELETE"):
        event = _admin_event("emp-001")
        event["httpMethod"] = method
        result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 405, (
            f"{method} on anonymize route should be 405, got "
            f"{result['statusCode']}"
        )
