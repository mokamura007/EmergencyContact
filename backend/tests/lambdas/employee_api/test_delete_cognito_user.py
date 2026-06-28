"""EmployeeApi delete_cognito_user handler — unit tests (Task 15.16).

Validates: Requirements 15.3 / 15.4 / NFR3 — Cognito account deletion
for retired admins via the SPA.

Covered cases:
    * Happy path: deleted admin with a cognitoSub -> 200 + Cognito
      admin_delete_user called with the sub + Employee.cognitoSub
      REMOVEd + audit log emitted with target=employee_id.
    * Authorization: caller without Administrator group -> 403.
    * Path validation: missing id -> 400.
    * Employee not found -> 404, no Cognito call, no audit log.
    * Employee active (not deleted) -> 409, no Cognito call.
    * Employee deleted but no cognitoSub -> 404, no Cognito call.
    * Cognito UserNotFoundException -> still 200 (idempotent cleanup
      of the Employee.cognitoSub attribute) + audit log emitted.
    * Cognito other ClientError -> raised (propagates as 5xx by the
      handler's outer try/except, not caught by anonymize-style 409
      mapping because admin_delete_user does not return Conditional/
      Transaction errors).

Audit-log invariant:
    The audit record's ``target`` field carries the plain employee_id
    (not anonymized) — Cognito deletion is a *different* runbook step
    from anonymization (Task 15.12). The deleted Cognito sub is
    captured in ``extra.cognitoSubDeleted`` for traceability.
"""

# ruff: noqa: N803
#   Mocked boto3-style PascalCase keyword args mirror production call
#   shapes; renaming would break the assertions.

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

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
def mock_cognito_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="CognitoClient")
    monkeypatch.setattr(handler, "_COGNITO", client)
    return client


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _admin_event(
    employee_id: str = "emp-001",
    *,
    is_admin: bool = True,
) -> dict[str, Any]:
    """Build a minimal API Gateway Proxy event for the cognito-user route."""
    groups = "Administrator" if is_admin else "SomeOtherGroup"
    return {
        "httpMethod": "DELETE",
        "resource": "/employees/{id}/cognito-user",
        "pathParameters": {"id": employee_id},
        "body": None,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "caller-sub-001",
                    "cognito:groups": groups,
                }
            }
        },
    }


def _deleted_admin_item(
    employee_id: str = "emp-001",
    cognito_sub: str = "cog-sub-uuid-001",
) -> dict[str, Any]:
    return {
        "Item": {
            "employeeId": employee_id,
            "name": "Retired Admin",
            "phoneNumber": "",  # NULL-ed by the logical-delete handler.
            "role": "admin",
            "deleted": True,
            "cognitoSub": cognito_sub,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-06-01T00:00:00Z",
        }
    }


def _active_admin_item(employee_id: str = "emp-001") -> dict[str, Any]:
    return {
        "Item": {
            "employeeId": employee_id,
            "name": "Active Admin",
            "phoneNumber": "+819012345678",
            "role": "admin",
            "deleted": False,
            "cognitoSub": "cog-sub-uuid-002",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    }


def _deleted_employee_no_cognito(employee_id: str = "emp-002") -> dict[str, Any]:
    """A regular (non-admin) employee that was logically deleted."""
    return {
        "Item": {
            "employeeId": employee_id,
            "name": "Retired Worker",
            "phoneNumber": "",
            "role": "employee",
            "deleted": True,
            # No cognitoSub — never was an admin.
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-06-01T00:00:00Z",
        }
    }


def _parse_response_body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_delete_cognito_user_happy_path(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Deleted admin with cognitoSub -> 200, Cognito called, sub REMOVEd."""
    mock_employee_table.get_item.return_value = _deleted_admin_item(
        "emp-001", cognito_sub="cog-sub-uuid-001"
    )

    result = handler.lambda_handler(_admin_event("emp-001"), None)

    assert result["statusCode"] == 200
    body = _parse_response_body(result)
    assert body == {"employeeId": "emp-001", "cognitoUserDeleted": True}

    # Cognito admin_delete_user must be called once with the sub.
    mock_cognito_client.admin_delete_user.assert_called_once_with(
        UserPoolId=handler.COGNITO_USER_POOL_ID,
        Username="cog-sub-uuid-001",
    )

    # Employee.cognitoSub must be REMOVEd.
    mock_employee_table.update_item.assert_called_once()
    upd_kwargs = mock_employee_table.update_item.call_args.kwargs
    assert upd_kwargs["Key"] == {"employeeId": "emp-001"}
    assert "REMOVE cognitoSub" in upd_kwargs["UpdateExpression"]

    # Audit log: target is the plain employee_id (NOT anonymized).
    _mock_audit_logger.put_log_events.assert_called_once()
    audit_kwargs = _mock_audit_logger.put_log_events.call_args.kwargs
    record = json.loads(audit_kwargs["logEvents"][0]["message"])
    assert record["event"] == "COGNITO_USER_DELETE"
    assert record["target"] == "emp-001"
    assert record["principal"] == "caller-sub-001"
    assert record["outcome"] == "SUCCESS"
    assert record["cognitoSubDeleted"] == "cog-sub-uuid-001"


# ---------------------------------------------------------------------------
# Authorization.
# ---------------------------------------------------------------------------


def test_delete_cognito_user_non_administrator_returns_403(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Non-Administrator caller -> 403, no side effects."""
    result = handler.lambda_handler(
        _admin_event("emp-001", is_admin=False), None
    )

    assert result["statusCode"] == 403
    body = _parse_response_body(result)
    assert "Administrator" in body["error"]
    mock_employee_table.get_item.assert_not_called()
    mock_employee_table.update_item.assert_not_called()
    mock_cognito_client.admin_delete_user.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


def test_delete_cognito_user_missing_authorizer_returns_403(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Event without requestContext.authorizer.claims -> 403."""
    event = {
        "httpMethod": "DELETE",
        "resource": "/employees/{id}/cognito-user",
        "pathParameters": {"id": "emp-001"},
        "body": None,
        # No `requestContext` at all.
    }
    result = handler.lambda_handler(event, None)
    assert result["statusCode"] == 403


# ---------------------------------------------------------------------------
# Path parameter validation.
# ---------------------------------------------------------------------------


def test_delete_cognito_user_missing_id_returns_400(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Missing pathParameters.id -> 400."""
    event = _admin_event("emp-001")
    event["pathParameters"] = {}

    result = handler.lambda_handler(event, None)

    assert result["statusCode"] == 400
    mock_employee_table.get_item.assert_not_called()
    mock_cognito_client.admin_delete_user.assert_not_called()


# ---------------------------------------------------------------------------
# Target validation.
# ---------------------------------------------------------------------------


def test_delete_cognito_user_employee_not_found_returns_404(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """get_item returns no Item -> 404; no side effects."""
    mock_employee_table.get_item.return_value = {}

    result = handler.lambda_handler(_admin_event("emp-missing"), None)

    assert result["statusCode"] == 404
    mock_employee_table.update_item.assert_not_called()
    mock_cognito_client.admin_delete_user.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


def test_delete_cognito_user_employee_not_deleted_returns_409(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Active (deleted=False) employee -> 409; Cognito untouched."""
    mock_employee_table.get_item.return_value = _active_admin_item("emp-001")

    result = handler.lambda_handler(_admin_event("emp-001"), None)

    assert result["statusCode"] == 409
    body = _parse_response_body(result)
    assert "logically deleted" in body["error"]
    mock_employee_table.update_item.assert_not_called()
    mock_cognito_client.admin_delete_user.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


def test_delete_cognito_user_no_cognito_sub_returns_404(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Deleted non-admin employee (no cognitoSub) -> 404; Cognito untouched."""
    mock_employee_table.get_item.return_value = _deleted_employee_no_cognito(
        "emp-002"
    )

    result = handler.lambda_handler(_admin_event("emp-002"), None)

    assert result["statusCode"] == 404
    body = _parse_response_body(result)
    assert "No Cognito user linked" in body["error"]
    mock_employee_table.update_item.assert_not_called()
    mock_cognito_client.admin_delete_user.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


def test_delete_cognito_user_empty_cognito_sub_returns_404(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """cognitoSub = '' is treated the same as absent -> 404."""
    item = _deleted_admin_item("emp-001", cognito_sub="")
    mock_employee_table.get_item.return_value = item

    result = handler.lambda_handler(_admin_event("emp-001"), None)

    assert result["statusCode"] == 404
    mock_cognito_client.admin_delete_user.assert_not_called()


# ---------------------------------------------------------------------------
# Cognito error handling.
# ---------------------------------------------------------------------------


def test_delete_cognito_user_user_not_found_is_idempotent(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Cognito UserNotFoundException -> still 200 and Employee.cognitoSub is REMOVEd.

    Rationale: the Cognito user is already gone (manual cleanup or a
    prior call). The Employee row's cognitoSub must still be cleared to
    keep state consistent. The audit log records SUCCESS so that the
    operator-visible outcome matches the actual state transition.
    """
    mock_employee_table.get_item.return_value = _deleted_admin_item("emp-001")
    mock_cognito_client.admin_delete_user.side_effect = ClientError(
        {"Error": {"Code": "UserNotFoundException", "Message": "User does not exist."}},
        "AdminDeleteUser",
    )

    result = handler.lambda_handler(_admin_event("emp-001"), None)

    assert result["statusCode"] == 200
    mock_employee_table.update_item.assert_called_once()
    _mock_audit_logger.put_log_events.assert_called_once()


def test_delete_cognito_user_other_cognito_error_propagates_as_5xx(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Non-UserNotFound Cognito errors propagate (no graceful 200).

    The handler-level try/except converts ConditionalCheckFailed /
    TransactionCanceled into 409, but other ClientErrors are re-raised
    so the Lambda runtime returns a 5xx (caller can retry / escalate).
    Employee.cognitoSub MUST NOT be REMOVEd if Cognito did not actually
    delete the user.
    """
    mock_employee_table.get_item.return_value = _deleted_admin_item("emp-001")
    mock_cognito_client.admin_delete_user.side_effect = ClientError(
        {"Error": {"Code": "InternalErrorException", "Message": "Internal error."}},
        "AdminDeleteUser",
    )

    with pytest.raises(ClientError):
        handler.lambda_handler(_admin_event("emp-001"), None)

    # The Employee.cognitoSub must NOT be cleared on Cognito failure.
    mock_employee_table.update_item.assert_not_called()
    _mock_audit_logger.put_log_events.assert_not_called()


# ---------------------------------------------------------------------------
# Symmetry: route is DELETE only.
# ---------------------------------------------------------------------------


def test_delete_cognito_user_route_is_delete_only(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """GET / POST / PUT on /employees/{id}/cognito-user -> 405."""
    for method in ("GET", "POST", "PUT"):
        event = _admin_event("emp-001")
        event["httpMethod"] = method
        result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 405, (
            f"{method} on cognito-user route should be 405, "
            f"got {result['statusCode']}"
        )
