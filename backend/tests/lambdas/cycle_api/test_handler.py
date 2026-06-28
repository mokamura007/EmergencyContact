"""Unit tests for the CycleApi Lambda (Phase 4.4 + 5.3 portions).

Scope:
    * Phase 4.4 — empty-dictionary guard on ``POST /cycles``:
        - all three active categories empty → 400, no cycle record
          persisted, no SFN invocation.
        - at least one category populated → 201, cycle record persisted,
          SFN start_execution invoked.

The handler module exposes three boto3-derived globals
(``_CYCLE_TABLE``, ``_DICT_TABLE``, ``_SFN``) that we replace with
:class:`unittest.mock.MagicMock` per test via ``monkeypatch``. The
environment variables required at import time are seeded by the local
``conftest.py``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from lambdas.cycle_api import handler

# --- Fixtures --------------------------------------------------------------


@pytest.fixture
def mock_cycle_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_table = MagicMock(name="CycleTable")
    monkeypatch.setattr(handler, "_CYCLE_TABLE", mock_table)
    return mock_table


@pytest.fixture
def mock_dict_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_table = MagicMock(name="KeywordDictionaryTable")
    monkeypatch.setattr(handler, "_DICT_TABLE", mock_table)
    return mock_table


@pytest.fixture
def mock_sfn(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_client = MagicMock(name="SfnClient")
    monkeypatch.setattr(handler, "_SFN", mock_client)
    return mock_client


# --- Helpers ---------------------------------------------------------------


def _post_cycles_event(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "httpMethod": "POST",
        "resource": "/cycles",
        "pathParameters": None,
        "headers": {},
        "body": json.dumps(body),
    }


def _configure_dict_query_results(
    mock_dict_table: MagicMock,
    *,
    safe_items: list[dict[str, Any]],
    injured_items: list[dict[str, Any]],
    unavailable_items: list[dict[str, Any]],
) -> None:
    """Make ``mock_dict_table.query`` return per-category Items.

    The handler calls ``query(KeyConditionExpression=Key('category').eq(cat),
    Limit=1)`` once for each of SAFE / INJURED / UNAVAILABLE, in that
    fixed order. We dispatch on the call arguments so the fixture is
    insensitive to call ordering bugs introduced later.
    """

    def _dispatch(**kwargs: Any) -> dict[str, Any]:
        cond = kwargs.get("KeyConditionExpression")
        # ``Key('category').eq('SAFE')`` stringifies in a stable way
        # via boto3 conditions; compare via the public ``get_expression``.
        cond_expr = cond.get_expression()
        category = cond_expr["values"][1]
        if category == "SAFE":
            return {"Items": safe_items}
        if category == "INJURED":
            return {"Items": injured_items}
        if category == "UNAVAILABLE":
            return {"Items": unavailable_items}
        raise AssertionError(f"Unexpected category Query: {category!r}")

    mock_dict_table.query.side_effect = _dispatch


# --- Tests: empty-dictionary guard (Phase 4.4) ----------------------------


def test_post_cycles_returns_400_when_dictionary_empty(
    mock_cycle_table: MagicMock,
    mock_dict_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """All three categories empty → 400, no put_item, no SFN invocation."""
    _configure_dict_query_results(
        mock_dict_table, safe_items=[], injured_items=[], unavailable_items=[]
    )

    response = handler.lambda_handler(
        _post_cycles_event(
            {"mode": "ALL", "retryCount": 3, "retryIntervalMinutes": 5}
        ),
        None,
    )

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert "Active dictionary is empty" in body["error"]

    # The cycle record must NOT be persisted.
    mock_cycle_table.put_item.assert_not_called()
    # No RUNNING-cycle lookup (the empty-dict guard runs first).
    mock_cycle_table.query.assert_not_called()
    # No SFN invocation.
    mock_sfn.start_execution.assert_not_called()
    # All three active categories were probed.
    assert mock_dict_table.query.call_count == 3


def test_post_cycles_returns_201_when_safe_has_at_least_one_keyword(
    mock_cycle_table: MagicMock,
    mock_dict_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """SAFE has 1 keyword, others empty → 201 with cycle record + SFN start."""
    _configure_dict_query_results(
        mock_dict_table,
        safe_items=[{"category": "SAFE", "keyword": "無事"}],
        injured_items=[],
        unavailable_items=[],
    )
    # No existing RUNNING cycle.
    mock_cycle_table.query.return_value = {"Items": []}
    # META.currentVersion = 7 (read via GetItem).
    mock_dict_table.get_item.return_value = {
        "Item": {"category": "META", "keyword": "META", "currentVersion": 7}
    }
    mock_sfn.start_execution.return_value = {"executionArn": "arn:aws:states:..."}

    response = handler.lambda_handler(
        _post_cycles_event(
            {"mode": "ALL", "retryCount": 3, "retryIntervalMinutes": 5}
        ),
        None,
    )

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["status"] == "RUNNING"
    assert body["mode"] == "ALL"
    assert body["dictionaryVersion"] == 7

    # The cycle record IS persisted, and SFN is invoked.
    mock_cycle_table.put_item.assert_called_once()
    mock_sfn.start_execution.assert_called_once()
    # All three active categories were probed.
    assert mock_dict_table.query.call_count == 3


def test_post_cycles_invalid_mode_short_circuits_before_dict_check(
    mock_cycle_table: MagicMock,
    mock_dict_table: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    """Input validation runs before the dictionary check; nothing is queried."""
    response = handler.lambda_handler(
        _post_cycles_event(
            {"mode": "NOT_A_MODE", "retryCount": 3, "retryIntervalMinutes": 5}
        ),
        None,
    )

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert "mode must be one of" in body["error"]

    # No DynamoDB or SFN traffic — input validation fails first.
    mock_dict_table.query.assert_not_called()
    mock_cycle_table.put_item.assert_not_called()
    mock_sfn.start_execution.assert_not_called()


def test_query_category_uses_limit_one(
    mock_dict_table: MagicMock,
) -> None:
    """Each category Query uses Limit=1 to keep RCU constant."""
    mock_dict_table.query.return_value = {"Items": []}

    handler._query_category("SAFE")

    call = mock_dict_table.query.call_args
    assert call.kwargs.get("Limit") == 1
    # KeyConditionExpression filters by category='SAFE'.
    cond = call.kwargs["KeyConditionExpression"].get_expression()
    assert cond["values"][1] == "SAFE"


# --- Tests: Phase 12.3 audit events ----------------------------------------


def _post_cycles_event_with_auth(body: dict[str, Any], sub: str) -> dict[str, Any]:
    """Same as ``_post_cycles_event`` but with a Cognito claims envelope."""
    event = _post_cycles_event(body)
    event["requestContext"] = {"authorizer": {"claims": {"sub": sub}}}
    return event


def test_post_cycles_emits_cycle_start_audit_on_success(
    mock_cycle_table: MagicMock,
    mock_dict_table: MagicMock,
    mock_sfn: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Successful POST emits CYCLE_START with principal/target/extra fields."""
    _configure_dict_query_results(
        mock_dict_table,
        safe_items=[{"category": "SAFE", "keyword": "無事"}],
        injured_items=[],
        unavailable_items=[],
    )
    mock_cycle_table.query.return_value = {"Items": []}
    mock_dict_table.get_item.return_value = {
        "Item": {"category": "META", "keyword": "META", "currentVersion": 3}
    }
    mock_sfn.start_execution.return_value = {"executionArn": "arn:..."}

    response = handler.lambda_handler(
        _post_cycles_event_with_auth(
            {"mode": "ALL", "retryCount": 3, "retryIntervalMinutes": 5},
            "user-sub-abc",
        ),
        None,
    )

    assert response["statusCode"] == 201
    _mock_audit_logger.put_log_events.assert_called_once()
    raw = _mock_audit_logger.put_log_events.call_args.kwargs["logEvents"][0]["message"]
    record = json.loads(raw)
    assert record["event"] == "CYCLE_START"
    assert record["principal"] == "user-sub-abc"
    assert record["mode"] == "ALL"
    assert record["retryCount"] == 3
    assert record["retryIntervalMinutes"] == 5
    assert record["dictionaryVersion"] == 3


def test_post_cycles_emits_rejected_audit_on_empty_dictionary(
    mock_cycle_table: MagicMock,
    mock_dict_table: MagicMock,
    mock_sfn: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """Empty-dictionary rejection emits CYCLE_START_REJECTED with reason."""
    _configure_dict_query_results(
        mock_dict_table, safe_items=[], injured_items=[], unavailable_items=[]
    )

    response = handler.lambda_handler(
        _post_cycles_event_with_auth(
            {"mode": "ALL", "retryCount": 3, "retryIntervalMinutes": 5},
            "user-sub-zzz",
        ),
        None,
    )

    assert response["statusCode"] == 400
    _mock_audit_logger.put_log_events.assert_called_once()
    record = json.loads(
        _mock_audit_logger.put_log_events.call_args.kwargs["logEvents"][0]["message"]
    )
    assert record["event"] == "CYCLE_START_REJECTED"
    assert record["outcome"] == "REJECTED"
    assert record["reason"] == "dictionary_empty"
    assert record["target"] == "dictionary_empty"


def test_post_cycles_emits_rejected_audit_on_concurrent_running(
    mock_cycle_table: MagicMock,
    mock_dict_table: MagicMock,
    mock_sfn: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """A concurrent RUNNING cycle yields CYCLE_START_REJECTED reason=cycle_running."""
    _configure_dict_query_results(
        mock_dict_table,
        safe_items=[{"category": "SAFE", "keyword": "無事"}],
        injured_items=[],
        unavailable_items=[],
    )
    mock_cycle_table.query.return_value = {
        "Items": [
            {
                "cycleId": "existing-cycle-id",
                "status": "RUNNING",
                "startedAt": "2026-06-26T01:00:00Z",
                "idempotencyKey": "different-key",
                "dictionaryVersion": 1,
            }
        ]
    }

    response = handler.lambda_handler(
        _post_cycles_event_with_auth(
            {"mode": "ALL", "retryCount": 3, "retryIntervalMinutes": 5},
            "user-sub-conflict",
        ),
        None,
    )

    assert response["statusCode"] == 409
    _mock_audit_logger.put_log_events.assert_called_once()
    record = json.loads(
        _mock_audit_logger.put_log_events.call_args.kwargs["logEvents"][0]["message"]
    )
    assert record["event"] == "CYCLE_START_REJECTED"
    assert record["reason"] == "cycle_running"
    assert record["target"] == "existing-cycle-id"


def test_post_cycles_idempotent_replay_does_not_emit_audit(
    mock_cycle_table: MagicMock,
    mock_dict_table: MagicMock,
    mock_sfn: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """An idempotent replay (matching key) returns 200 and emits no audit."""
    _configure_dict_query_results(
        mock_dict_table,
        safe_items=[{"category": "SAFE", "keyword": "無事"}],
        injured_items=[],
        unavailable_items=[],
    )
    mock_cycle_table.query.return_value = {
        "Items": [
            {
                "cycleId": "replay-cycle",
                "status": "RUNNING",
                "startedAt": "2026-06-26T01:00:00Z",
                "idempotencyKey": "shared-key",
                "dictionaryVersion": 2,
            }
        ]
    }

    event = _post_cycles_event_with_auth(
        {"mode": "ALL", "retryCount": 3, "retryIntervalMinutes": 5},
        "user-sub",
    )
    event["headers"] = {"Idempotency-Key": "shared-key"}
    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]).get("idempotentReplay") is True
    _mock_audit_logger.put_log_events.assert_not_called()


def test_post_cycles_sfn_failure_does_not_emit_cycle_start_audit(
    mock_cycle_table: MagicMock,
    mock_dict_table: MagicMock,
    mock_sfn: MagicMock,
    _mock_audit_logger: MagicMock,
) -> None:
    """When SFN StartExecution fails, no CYCLE_START audit is emitted.

    The cycle row is flipped to START_FAILED and a 500 is returned. The
    CYCLE_START audit is intentionally only emitted on the success path
    so the audit stream reflects what actually happened.
    """
    from botocore.exceptions import ClientError

    _configure_dict_query_results(
        mock_dict_table,
        safe_items=[{"category": "SAFE", "keyword": "無事"}],
        injured_items=[],
        unavailable_items=[],
    )
    mock_cycle_table.query.return_value = {"Items": []}
    mock_dict_table.get_item.return_value = {
        "Item": {"category": "META", "keyword": "META", "currentVersion": 1}
    }
    mock_sfn.start_execution.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "no sm"}},
        "StartExecution",
    )

    response = handler.lambda_handler(
        _post_cycles_event_with_auth(
            {"mode": "ALL", "retryCount": 3, "retryIntervalMinutes": 5},
            "user-sub",
        ),
        None,
    )
    assert response["statusCode"] == 500
    _mock_audit_logger.put_log_events.assert_not_called()
