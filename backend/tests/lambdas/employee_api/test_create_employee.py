"""EmployeeApi ``_create_employee`` handler unit tests.

Validates:
    - Requirement 2.1 (revised): SPA can create employees with an
      optional ``isAdmin=true`` + ``adminEmail`` payload; when present,
      the API creates a Cognito admin user before the DynamoDB write.
    - Requirement 2.2: 監査ログ (EMPLOYEE_ADD) emission.
    - Admin registration side effect: an independent
      ``COGNITO_USER_CREATE`` audit event is emitted symmetrically with
      ``COGNITO_USER_DELETE`` (Task 15.16).
    - DRY: ``is_valid_email`` shared with ``shared/employee/validate``
      is used for pre-validation before hitting ``admin_create_user``.

Covered cases:
    (Non-admin path)
    * Happy path: isAdmin=false + valid inputs -> 201, DDB write,
      EMPLOYEE_ADD only, no Cognito call, no COGNITO_USER_CREATE.

    (Admin path)
    * Happy path: isAdmin=true + valid adminEmail -> 201, Cognito
      admin_create_user + admin_add_user_to_group called, DDB write
      includes cognitoSub, EMPLOYEE_ADD + COGNITO_USER_CREATE audit
      events (2 records) both keyed by employeeId.
    * Cognito UsernameExistsException -> 409, no DDB write, no audit.
    * Cognito other ClientError -> raised (5xx), no DDB write.

    (Validation)
    * Missing name / phone / adminEmail -> 400.
    * Invalid E.164 phone -> 400.
    * Invalid adminEmail format -> 400 (RFC 5322 simplified).
    * adminEmail omitted while isAdmin=true -> 400.

    (Duplicates)
    * Phone already registered (active row) -> 409, no Cognito call.
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
    """Replace ``handler._TABLE`` with a MagicMock and default no-duplicate."""
    table = MagicMock(name="EmployeeTable")
    # Default: no duplicate phone (query returns empty Items).
    table.query.return_value = {"Items": []}
    monkeypatch.setattr(handler, "_TABLE", table)
    return table


@pytest.fixture
def mock_cognito_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``handler._COGNITO`` with a MagicMock."""
    client = MagicMock(name="CognitoClient")
    # Default: admin_create_user returns a sub attribute.
    client.admin_create_user.return_value = {
        "User": {"Attributes": [{"Name": "sub", "Value": "cog-sub-uuid-new"}]}
    }
    monkeypatch.setattr(handler, "_COGNITO", client)
    return client


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _event(body: dict[str, Any], principal: str = "caller-sub-001") -> dict[str, Any]:
    """Build a minimal API Gateway Proxy event for POST /employees."""
    return {
        "httpMethod": "POST",
        "resource": "/employees",
        "pathParameters": None,
        "body": json.dumps(body),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": principal,
                    "cognito:groups": "Administrator",
                }
            }
        },
    }


def _parse_body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


def _audit_records(mock_logger: MagicMock) -> list[dict[str, Any]]:
    """Extract audit records emitted via ``put_log_events``."""
    records: list[dict[str, Any]] = []
    for call in mock_logger.put_log_events.call_args_list:
        for entry in call.kwargs["logEvents"]:
            records.append(json.loads(entry["message"]))
    return records


# ---------------------------------------------------------------------------
# Non-admin happy path.
# ---------------------------------------------------------------------------


def test_create_non_admin_employee_happy_path(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """isAdmin=false: 201, DDB put_item, EMPLOYEE_ADD only, no Cognito call."""
    result = handler.lambda_handler(
        _event({"name": "山田太郎", "phoneNumber": "+819012345678"}), None
    )

    assert result["statusCode"] == 201
    body = _parse_body(result)
    assert body["name"] == "山田太郎"
    assert body["phoneNumber"] == "+819012345678"
    assert body["isAdmin"] is False

    # DDB was written once, with role="employee" and NO cognitoSub.
    mock_employee_table.put_item.assert_called_once()
    written = mock_employee_table.put_item.call_args.kwargs["Item"]
    assert written["role"] == "employee"
    assert "cognitoSub" not in written

    # Cognito must NOT be called for non-admin registrations.
    mock_cognito_client.admin_create_user.assert_not_called()
    mock_cognito_client.admin_add_user_to_group.assert_not_called()

    # Exactly ONE audit record: EMPLOYEE_ADD.
    records = _audit_records(_mock_audit_logger)
    assert len(records) == 1
    assert records[0]["event"] == "EMPLOYEE_ADD"
    assert records[0]["principal"] == "caller-sub-001"


# ---------------------------------------------------------------------------
# Admin happy path.
# ---------------------------------------------------------------------------


def test_create_admin_employee_happy_path(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """isAdmin=true + valid adminEmail: Cognito created, DDB written, 2 audit events."""
    result = handler.lambda_handler(
        _event(
            {
                "name": "田中花子",
                "phoneNumber": "+819011112222",
                "isAdmin": True,
                "adminEmail": "hanako@example.com",
            }
        ),
        None,
    )

    assert result["statusCode"] == 201
    body = _parse_body(result)
    assert body["isAdmin"] is True

    # Cognito admin_create_user must be called with the email + name.
    mock_cognito_client.admin_create_user.assert_called_once()
    create_kwargs = mock_cognito_client.admin_create_user.call_args.kwargs
    assert create_kwargs["UserPoolId"] == handler.COGNITO_USER_POOL_ID
    assert create_kwargs["Username"] == "hanako@example.com"
    assert create_kwargs["DesiredDeliveryMediums"] == ["EMAIL"]
    email_attr = next(
        a for a in create_kwargs["UserAttributes"] if a["Name"] == "email"
    )
    assert email_attr["Value"] == "hanako@example.com"

    # Administrator group must be joined.
    mock_cognito_client.admin_add_user_to_group.assert_called_once_with(
        UserPoolId=handler.COGNITO_USER_POOL_ID,
        Username="hanako@example.com",
        GroupName=handler.ADMIN_GROUP_NAME,
    )

    # DDB was written with role="admin" AND cognitoSub.
    written = mock_employee_table.put_item.call_args.kwargs["Item"]
    assert written["role"] == "admin"
    assert written["cognitoSub"] == "cog-sub-uuid-new"

    # Exactly TWO audit records: EMPLOYEE_ADD + COGNITO_USER_CREATE.
    records = _audit_records(_mock_audit_logger)
    events = [r["event"] for r in records]
    assert events == ["EMPLOYEE_ADD", "COGNITO_USER_CREATE"]

    employee_add = records[0]
    cognito_create = records[1]
    assert employee_add["principal"] == "caller-sub-001"
    assert cognito_create["target"] == employee_add["target"], (
        "COGNITO_USER_CREATE target must equal EMPLOYEE_ADD target (employee_id)"
    )
    assert cognito_create["outcome"] == "SUCCESS"
    assert cognito_create["cognitoSub"] == "cog-sub-uuid-new"


# ---------------------------------------------------------------------------
# Cognito failure paths.
# ---------------------------------------------------------------------------


def test_create_admin_cognito_username_exists_returns_409(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Cognito UsernameExistsException -> 409, no DDB write, no audit."""
    mock_cognito_client.admin_create_user.side_effect = ClientError(
        {"Error": {"Code": "UsernameExistsException", "Message": "already exists"}},
        "AdminCreateUser",
    )

    result = handler.lambda_handler(
        _event(
            {
                "name": "田中花子",
                "phoneNumber": "+819011112222",
                "isAdmin": True,
                "adminEmail": "existing@example.com",
            }
        ),
        None,
    )

    assert result["statusCode"] == 409
    assert "already registered in Cognito" in _parse_body(result)["error"]

    # No DDB write on Cognito failure (Req 2.2 "Cognito first").
    mock_employee_table.put_item.assert_not_called()
    # Administrator group join must not be attempted.
    mock_cognito_client.admin_add_user_to_group.assert_not_called()
    # No audit records on the failure branch.
    assert _audit_records(_mock_audit_logger) == []


def test_create_admin_cognito_other_client_error_raises(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Cognito ClientError other than UsernameExists propagates as 5xx.

    The outer ``lambda_handler`` re-raises non-conditional ClientError
    after logging; the runtime returns a 5xx to the API Gateway. The
    DDB row must NOT be written and no audit record emitted.
    """
    mock_cognito_client.admin_create_user.side_effect = ClientError(
        {"Error": {"Code": "InvalidParameterException", "Message": "bad"}},
        "AdminCreateUser",
    )

    with pytest.raises(ClientError):
        handler.lambda_handler(
            _event(
                {
                    "name": "田中花子",
                    "phoneNumber": "+819011112222",
                    "isAdmin": True,
                    "adminEmail": "hanako@example.com",
                }
            ),
            None,
        )

    mock_employee_table.put_item.assert_not_called()
    assert _audit_records(_mock_audit_logger) == []


# ---------------------------------------------------------------------------
# Validation paths (400).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected_error_fragment"),
    [
        pytest.param(
            {"name": "", "phoneNumber": "+819012345678"},
            "name",
            id="empty-name",
        ),
        pytest.param(
            # 電話番号は E.164 or Domestic JP のどちらでもない場合に 400。
            # "12345" は 5 桁で Domestic JP (10-11 桁) にも E.164 (+ 必須)
            # にも該当しない。
            {"name": "山田", "phoneNumber": "12345"},
            "phoneNumber",
            id="phone-not-e164-and-not-domestic",
        ),
        pytest.param(
            {
                "name": "山田",
                "phoneNumber": "+819012345678",
                "isAdmin": True,
                # adminEmail omitted.
            },
            "adminEmail",
            id="admin-missing-email",
        ),
        pytest.param(
            {
                "name": "山田",
                "phoneNumber": "+819012345678",
                "isAdmin": True,
                "adminEmail": "",
            },
            "adminEmail",
            id="admin-empty-email",
        ),
        pytest.param(
            {
                "name": "山田",
                "phoneNumber": "+819012345678",
                "isAdmin": True,
                "adminEmail": "not-an-email",  # no "@" or "."
            },
            "adminEmail",
            id="admin-invalid-email-format",
        ),
        pytest.param(
            {
                "name": "山田",
                "phoneNumber": "+819012345678",
                "isAdmin": True,
                "adminEmail": "foo@bar",  # missing "."
            },
            "adminEmail",
            id="admin-email-missing-dot",
        ),
    ],
)
def test_create_employee_validation_returns_400(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
    body: dict[str, Any],
    expected_error_fragment: str,
) -> None:
    """Every validation failure returns 400, with no side effects."""
    result = handler.lambda_handler(_event(body), None)

    assert result["statusCode"] == 400
    assert expected_error_fragment in _parse_body(result)["error"]

    mock_employee_table.put_item.assert_not_called()
    mock_cognito_client.admin_create_user.assert_not_called()
    assert _audit_records(_mock_audit_logger) == []


# ---------------------------------------------------------------------------
# Phone duplicate (409).
# ---------------------------------------------------------------------------


def test_create_employee_duplicate_phone_returns_409(
    mock_employee_table: MagicMock,
    mock_cognito_client: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Duplicate active phone number -> 409, no Cognito call, no audit."""
    # GSI query for the phone returns an active (non-deleted) row.
    mock_employee_table.query.return_value = {
        "Items": [
            {
                "employeeId": "emp-existing",
                "phoneNumber": "+819011112222",
                "deleted": False,
            }
        ]
    }

    result = handler.lambda_handler(
        _event(
            {
                "name": "田中花子",
                "phoneNumber": "+819011112222",
                "isAdmin": True,
                "adminEmail": "hanako@example.com",
            }
        ),
        None,
    )

    assert result["statusCode"] == 409
    assert "already registered" in _parse_body(result)["error"].lower()

    # Duplicate check fires BEFORE Cognito -> no Cognito calls.
    mock_cognito_client.admin_create_user.assert_not_called()
    mock_employee_table.put_item.assert_not_called()
    assert _audit_records(_mock_audit_logger) == []
