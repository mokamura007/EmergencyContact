"""Unit tests for the LoadTargets Lambda (Phase 6.1).

Covers the four mandatory cases enumerated in the task brief:
    1. mode=ALL with a mix of visible / invisible employees →
       only the visible ones are projected.
    2. mode=UNREACHABLE_ONLY → only responses whose voiceStatus is
       UNREACHABLE or OTHER are dereferenced, then is_visible filters out
       any employees that were logically deleted between cycles.
    3. mode=ALL but no visible employees → ``NoTargetsError``.
    4. Invalid ``mode`` value → ``ValueError``.

The handler module talks to DynamoDB through three module-level globals:
``_DDB`` (boto3 resource), ``_EMPLOYEE_TABLE`` and ``_RESPONSE_TABLE``
(boto3 ``Table`` instances). These tests replace each with a
:class:`unittest.mock.MagicMock` per test via ``monkeypatch`` so the
handler runs end-to-end without any AWS dependency.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from lambdas.load_targets import handler


# --- Fixtures ------------------------------------------------------------


@pytest.fixture
def mock_employee_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``handler._EMPLOYEE_TABLE`` with a MagicMock."""
    mock_table = MagicMock(name="EmployeeTable")
    monkeypatch.setattr(handler, "_EMPLOYEE_TABLE", mock_table)
    return mock_table


@pytest.fixture
def mock_response_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``handler._RESPONSE_TABLE`` with a MagicMock."""
    mock_table = MagicMock(name="ResponseTable")
    monkeypatch.setattr(handler, "_RESPONSE_TABLE", mock_table)
    return mock_table


@pytest.fixture
def mock_ddb_resource(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``handler._DDB`` (used for ``batch_get_item``) with a MagicMock."""
    mock_resource = MagicMock(name="DDBResource")
    monkeypatch.setattr(handler, "_DDB", mock_resource)
    return mock_resource


# --- Helpers -------------------------------------------------------------


def _visible_employee(employee_id: str, name: str, phone: str) -> dict[str, Any]:
    return {
        "employeeId": employee_id,
        "name": name,
        "phoneNumber": phone,
        "deleted": False,
    }


def _deleted_employee(employee_id: str, name: str, phone: str) -> dict[str, Any]:
    # Mirrors the post-delete shape: deleted=True, phoneNumber cleared.
    return {
        "employeeId": employee_id,
        "name": name,
        "phoneNumber": "",
        "deleted": True,
    }


def _no_phone_employee(employee_id: str, name: str) -> dict[str, Any]:
    return {
        "employeeId": employee_id,
        "name": name,
        "phoneNumber": None,
        "deleted": False,
    }


# --- Test 1: mode=ALL with mixed-visibility population ------------------


def test_mode_all_filters_invisible_employees(
    mock_employee_table: MagicMock,
) -> None:
    """ALL mode returns only visible employees with the projection shape."""
    mock_employee_table.scan.return_value = {
        "Items": [
            _visible_employee("emp-001", "山田太郎", "+819011111111"),
            _deleted_employee("emp-002", "佐藤花子", "+819022222222"),
            _visible_employee("emp-003", "鈴木一郎", "+819033333333"),
            _no_phone_employee("emp-004", "高橋次郎"),
            _visible_employee("emp-005", "田中三郎", "+819055555555"),
        ],
    }

    result = handler.lambda_handler(
        {"cycleId": "cycle-abc", "mode": "ALL"}, None
    )

    assert result["cycleId"] == "cycle-abc"
    assert result["mode"] == "ALL"
    assert result["targetCount"] == 3
    target_ids = sorted(t["employeeId"] for t in result["targets"])
    assert target_ids == ["emp-001", "emp-003", "emp-005"]
    # Projection contract: only {employeeId, name, phoneNumber}; no `deleted`.
    for target in result["targets"]:
        assert set(target.keys()) == {"employeeId", "name", "phoneNumber"}
        assert target["phoneNumber"].startswith("+81")


def test_mode_all_handles_scan_pagination(
    mock_employee_table: MagicMock,
) -> None:
    """ALL mode drains paginated Scan responses (LastEvaluatedKey loop)."""
    mock_employee_table.scan.side_effect = [
        {
            "Items": [_visible_employee("emp-001", "山田", "+819011111111")],
            "LastEvaluatedKey": {"employeeId": "emp-001"},
        },
        {
            "Items": [_visible_employee("emp-002", "佐藤", "+819022222222")],
        },
    ]

    result = handler.lambda_handler(
        {"cycleId": "cycle-page", "mode": "ALL"}, None
    )

    assert result["targetCount"] == 2
    assert mock_employee_table.scan.call_count == 2


# --- Test 2: mode=UNREACHABLE_ONLY filters by voiceStatus ---------------


def test_mode_unreachable_only_filters_by_voice_status(
    mock_employee_table: MagicMock,
    mock_response_table: MagicMock,
    mock_ddb_resource: MagicMock,
) -> None:
    """UNREACHABLE_ONLY mode dereferences only UNREACHABLE/OTHER responses.

    Setup:
        Response.Query for referencedCycleId=cycle-prev returns 5 rows
        across all six voiceStatus values (one row each, plus PENDING).
        Only UNREACHABLE and OTHER must flow through to BatchGetItem.
    """
    mock_response_table.query.return_value = {
        "Items": [
            {"cycleId": "cycle-prev", "employeeId": "emp-safe",        "voiceStatus": "SAFE"},
            {"cycleId": "cycle-prev", "employeeId": "emp-injured",     "voiceStatus": "INJURED"},
            {"cycleId": "cycle-prev", "employeeId": "emp-unavailable", "voiceStatus": "UNAVAILABLE"},
            {"cycleId": "cycle-prev", "employeeId": "emp-other",       "voiceStatus": "OTHER"},
            {"cycleId": "cycle-prev", "employeeId": "emp-unreachable", "voiceStatus": "UNREACHABLE"},
            {"cycleId": "cycle-prev", "employeeId": "emp-pending",     "voiceStatus": "PENDING"},
        ],
    }
    # BatchGetItem must be asked for exactly emp-other and emp-unreachable.
    # Of those, emp-unreachable became logically deleted between cycles
    # and must be filtered out by is_visible.
    mock_ddb_resource.batch_get_item.return_value = {
        "Responses": {
            "Employee-test": [
                _visible_employee("emp-other", "田中", "+819044444444"),
                _deleted_employee("emp-unreachable", "鈴木", "+819055555555"),
            ],
        },
    }

    result = handler.lambda_handler(
        {
            "cycleId": "cycle-new",
            "mode": "UNREACHABLE_ONLY",
            "referencedCycleId": "cycle-prev",
        },
        None,
    )

    assert result["cycleId"] == "cycle-new"
    assert result["mode"] == "UNREACHABLE_ONLY"
    assert result["targetCount"] == 1
    assert result["targets"] == [
        {"employeeId": "emp-other", "name": "田中", "phoneNumber": "+819044444444"},
    ]

    # Verify Response.Query was issued against the referenced cycle.
    mock_response_table.query.assert_called_once()
    # Verify BatchGetItem only requested the UNREACHABLE/OTHER ids.
    mock_ddb_resource.batch_get_item.assert_called_once()
    requested_keys = (
        mock_ddb_resource.batch_get_item.call_args.kwargs["RequestItems"]
        ["Employee-test"]["Keys"]
    )
    requested_ids = sorted(k["employeeId"] for k in requested_keys)
    assert requested_ids == ["emp-other", "emp-unreachable"]


def test_mode_unreachable_only_zero_matches_raises(
    mock_response_table: MagicMock,
    mock_ddb_resource: MagicMock,
) -> None:
    """UNREACHABLE_ONLY: no UNREACHABLE/OTHER responses → ``NoTargetsError``."""
    mock_response_table.query.return_value = {
        "Items": [
            {"cycleId": "cycle-prev", "employeeId": "emp-safe", "voiceStatus": "SAFE"},
            {"cycleId": "cycle-prev", "employeeId": "emp-inj",  "voiceStatus": "INJURED"},
        ],
    }

    with pytest.raises(handler.NoTargetsError):
        handler.lambda_handler(
            {
                "cycleId": "cycle-new",
                "mode": "UNREACHABLE_ONLY",
                "referencedCycleId": "cycle-prev",
            },
            None,
        )
    # No BatchGetItem should have been issued because the upstream filter
    # eliminated every candidate.
    mock_ddb_resource.batch_get_item.assert_not_called()


# --- Test 3: ALL mode but no visible employees → NoTargetsError ---------


def test_mode_all_zero_visible_raises_no_targets_error(
    mock_employee_table: MagicMock,
) -> None:
    """ALL mode: every employee is hidden by ``is_visible`` → ``NoTargetsError``."""
    mock_employee_table.scan.return_value = {
        "Items": [
            _deleted_employee("emp-001", "山田", "+819011111111"),
            _no_phone_employee("emp-002", "佐藤"),
        ],
    }

    with pytest.raises(handler.NoTargetsError):
        handler.lambda_handler(
            {"cycleId": "cycle-zero", "mode": "ALL"}, None
        )


# --- Test 4: Invalid input → ValueError --------------------------------


def test_invalid_mode_raises_value_error() -> None:
    """Unknown ``mode`` value → ``ValueError``."""
    with pytest.raises(ValueError, match="mode must be one of"):
        handler.lambda_handler(
            {"cycleId": "cycle-x", "mode": "NOT_A_MODE"}, None
        )


def test_missing_mode_raises_value_error() -> None:
    """Missing ``mode`` key → ``ValueError``."""
    with pytest.raises(ValueError, match="mode must be one of"):
        handler.lambda_handler({"cycleId": "cycle-x"}, None)


def test_missing_cycle_id_raises_value_error() -> None:
    """Missing ``cycleId`` → ``ValueError``."""
    with pytest.raises(ValueError, match="cycleId is required"):
        handler.lambda_handler({"mode": "ALL"}, None)


def test_unreachable_only_without_referenced_cycle_id_raises_value_error() -> None:
    """``UNREACHABLE_ONLY`` without ``referencedCycleId`` → ``ValueError``."""
    with pytest.raises(ValueError, match="referencedCycleId is required"):
        handler.lambda_handler(
            {"cycleId": "cycle-x", "mode": "UNREACHABLE_ONLY"}, None
        )


def test_event_not_dict_raises_value_error() -> None:
    """Non-dict event → ``ValueError``."""
    with pytest.raises(ValueError, match="event must be a JSON object"):
        handler.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]
