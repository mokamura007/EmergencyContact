"""Unit tests for CycleFinalizer Lambda (Phase 6.6).

The handler dispatches on ``event['trigger']`` (MAP_COMPLETED /
TIMER_30MIN / TIMER_60MIN) and talks to five AWS APIs:

* ``handler._CYCLE_TABLE``      — DynamoDB Table for the cycle row
* ``handler._RESPONSE_TABLE``   — DynamoDB Table for the per-employee rows
* ``handler._SFN``              — Step Functions client (StopExecution)
* ``handler._SNS``              — SNS client (Publish)
* ``handler._EVENTS``           — EventBridge client (RemoveTargets / DeleteRule)
* ``handler._CLOUDWATCH``       — CloudWatch client (PutMetricData)

Each test swaps these out for :class:`MagicMock` so the suite stays
deterministic and offline.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.cycle_finalizer import handler

# --- Fixtures ----------------------------------------------------------


@pytest.fixture
def mock_cycle_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="CycleTable")
    monkeypatch.setattr(handler, "_CYCLE_TABLE", table)
    return table


@pytest.fixture
def mock_response_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="ResponseTable")
    # query() returns an empty page by default — tests override per-case.
    table.query.return_value = {"Items": []}
    monkeypatch.setattr(handler, "_RESPONSE_TABLE", table)
    return table


@pytest.fixture
def mock_sfn(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="SFN")
    monkeypatch.setattr(handler, "_SFN", client)
    return client


@pytest.fixture
def mock_sns(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="SNS")
    monkeypatch.setattr(handler, "_SNS", client)
    return client


@pytest.fixture
def mock_events(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="Events")
    monkeypatch.setattr(handler, "_EVENTS", client)
    return client


@pytest.fixture
def mock_cloudwatch(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="CloudWatch")
    monkeypatch.setattr(handler, "_CLOUDWATCH", client)
    return client


# --- Helpers -----------------------------------------------------------


def _conditional_check_failed(op: str = "UpdateItem") -> ClientError:
    return ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "guard failed",
            }
        },
        operation_name=op,
    )


def _resource_not_found(op: str = "DeleteRule") -> ClientError:
    return ClientError(
        error_response={
            "Error": {"Code": "ResourceNotFoundException", "Message": "gone"}
        },
        operation_name=op,
    )


def _resp(employee: str, voice: str, attempts: int = 1) -> dict[str, Any]:
    return {
        "cycleId": "cycle-1",
        "employeeId": employee,
        "voiceStatus": voice,
        "callAttempts": attempts,
    }


# --- MAP_COMPLETED branch ----------------------------------------------


def test_map_completed_all_terminal_flips_cycle_and_deletes_rules(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_events: MagicMock,
    mock_sns: MagicMock,
    mock_sfn: MagicMock,
) -> None:
    mock_response_table.query.return_value = {
        "Items": [
            _resp("e1", "SAFE"),
            _resp("e2", "UNREACHABLE", attempts=3),
        ]
    }
    event = {
        "trigger": "MAP_COMPLETED",
        "cycleId": "cycle-1",
        "executionArn": "arn:aws:states:exec:1",
    }

    result = handler.lambda_handler(event, None)

    assert result["status"] == "completed"
    assert result["summary"]["targetTotal"] == 2
    assert result["summary"]["responded"] == 1
    assert result["summary"]["unreachable"] == 1

    mock_cycle_table.update_item.assert_called_once()
    upd_kwargs = mock_cycle_table.update_item.call_args.kwargs
    assert upd_kwargs["Key"] == {"cycleId": "cycle-1"}
    assert upd_kwargs["UpdateExpression"] == "SET #s = :new, completedAt = :ca"
    assert upd_kwargs["ConditionExpression"] == "#s = :running"
    assert upd_kwargs["ExpressionAttributeNames"] == {"#s": "status"}
    assert upd_kwargs["ExpressionAttributeValues"][":new"] == "COMPLETED"
    assert upd_kwargs["ExpressionAttributeValues"][":running"] == "RUNNING"
    assert ":ca" in upd_kwargs["ExpressionAttributeValues"]

    # Both timer rules removed.
    assert mock_events.remove_targets.call_count == 2
    assert mock_events.delete_rule.call_count == 2

    # No SNS / SFN side-effects on the happy completion path.
    mock_sns.publish.assert_not_called()
    mock_sfn.stop_execution.assert_not_called()


def test_map_completed_with_pending_responses_returns_incomplete(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_events: MagicMock,
) -> None:
    mock_response_table.query.return_value = {
        "Items": [_resp("e1", "PENDING", attempts=0)]
    }
    result = handler.lambda_handler(
        {"trigger": "MAP_COMPLETED", "cycleId": "cycle-1"}, None
    )
    assert result == {"status": "incomplete", "pendingCount": 1}
    mock_cycle_table.update_item.assert_not_called()
    mock_events.delete_rule.assert_not_called()


def test_map_completed_swallows_conditional_check_failed_on_status_flip(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_events: MagicMock,
) -> None:
    """Cycle already terminal → status flip skipped, but no exception."""
    mock_response_table.query.return_value = {"Items": [_resp("e1", "SAFE")]}
    mock_cycle_table.update_item.side_effect = _conditional_check_failed()

    result = handler.lambda_handler(
        {"trigger": "MAP_COMPLETED", "cycleId": "cycle-1"}, None
    )
    assert result["status"] == "already_terminal"
    # Timer rules untouched when the flip didn't actually happen.
    mock_events.delete_rule.assert_not_called()


# --- TIMER_30MIN branch ------------------------------------------------


def test_timer_30min_warning_path_sets_flag_metric_and_sns(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_sns: MagicMock,
    mock_cloudwatch: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "RUNNING"}
    }
    mock_response_table.query.return_value = {
        "Items": [
            _resp("e1", "PENDING", attempts=0),  # not dispatched
            _resp("e2", "SAFE", attempts=1),
        ]
    }

    result = handler.lambda_handler(
        {
            "trigger": "TIMER_30MIN",
            "cycleId": "cycle-1",
            "ruleName": "cycle-30min-cycle-1",
        },
        None,
    )

    assert result["status"] == "warning_applied"
    assert result["summary"]["targetTotal"] == 2
    assert result["summary"]["dispatched"] == 1

    # CycleTable update with slaWarning30min flag.
    mock_cycle_table.update_item.assert_called_once()
    upd_kwargs = mock_cycle_table.update_item.call_args.kwargs
    assert (
        upd_kwargs["UpdateExpression"] == "SET slaWarning30min = :true"
    )
    assert upd_kwargs["ConditionExpression"] == "#s = :running"
    assert upd_kwargs["ExpressionAttributeValues"][":true"] is True

    # CloudWatch metric.
    mock_cloudwatch.put_metric_data.assert_called_once()
    cw_kwargs = mock_cloudwatch.put_metric_data.call_args.kwargs
    assert cw_kwargs["Namespace"] == handler.CLOUDWATCH_NAMESPACE
    metric_datum = cw_kwargs["MetricData"][0]
    assert metric_datum["MetricName"] == "SlaWarning30Min"
    assert metric_datum["Value"] == 1
    assert metric_datum["Dimensions"] == [
        {"Name": "CycleId", "Value": "cycle-1"}
    ]

    # SNS notification with summary.
    mock_sns.publish.assert_called_once()
    sns_kwargs = mock_sns.publish.call_args.kwargs
    assert sns_kwargs["TopicArn"] == handler.OPERATOR_TOPIC_ARN
    assert "SLA 30min" in sns_kwargs["Subject"]
    msg = json.loads(sns_kwargs["Message"])
    assert msg["event"] == "SLA_WARNING_30MIN"
    assert msg["cycleId"] == "cycle-1"


def test_timer_30min_skips_when_first_dispatch_complete(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_sns: MagicMock,
    mock_cloudwatch: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "RUNNING"}
    }
    mock_response_table.query.return_value = {
        "Items": [
            _resp("e1", "PENDING", attempts=1),
            _resp("e2", "SAFE", attempts=1),
        ]
    }

    result = handler.lambda_handler(
        {"trigger": "TIMER_30MIN", "cycleId": "cycle-1"}, None
    )
    assert result == {"status": "no_warning_needed"}
    mock_cycle_table.update_item.assert_not_called()
    mock_cloudwatch.put_metric_data.assert_not_called()
    mock_sns.publish.assert_not_called()


def test_timer_30min_no_op_when_cycle_already_completed(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_sns: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "COMPLETED"}
    }
    result = handler.lambda_handler(
        {"trigger": "TIMER_30MIN", "cycleId": "cycle-1"}, None
    )
    assert result == {"status": "no_op"}
    mock_response_table.query.assert_not_called()
    mock_cycle_table.update_item.assert_not_called()
    mock_sns.publish.assert_not_called()


def test_timer_30min_no_op_when_cycle_missing(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {}
    result = handler.lambda_handler(
        {"trigger": "TIMER_30MIN", "cycleId": "cycle-1"}, None
    )
    assert result == {"status": "no_op"}
    mock_response_table.query.assert_not_called()


# --- TIMER_60MIN branch ------------------------------------------------


def test_timer_60min_stops_sfn_flips_unreachable_and_publishes(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_sns: MagicMock,
    mock_events: MagicMock,
    mock_cloudwatch: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "RUNNING"}
    }
    pending_rows = [
        _resp("e1", "PENDING", attempts=1),
        _resp("e2", "OTHER", attempts=2),
        _resp("e3", "SAFE", attempts=1),
    ]
    after_rows = [
        _resp("e1", "UNREACHABLE", attempts=1),
        _resp("e2", "UNREACHABLE", attempts=2),
        _resp("e3", "SAFE", attempts=1),
    ]
    mock_response_table.query.side_effect = [
        {"Items": pending_rows},
        {"Items": after_rows},
    ]

    result = handler.lambda_handler(
        {
            "trigger": "TIMER_60MIN",
            "cycleId": "cycle-1",
            "ruleName": "cycle-60min-cycle-1",
            "executionArn": "arn:aws:states:exec:1",
        },
        None,
    )

    assert result["status"] == "timeout_applied"
    assert result["stoppedResponses"] == 2

    # SFN StopExecution was called with the supplied ARN.
    mock_sfn.stop_execution.assert_called_once()
    sfn_kwargs = mock_sfn.stop_execution.call_args.kwargs
    assert sfn_kwargs["executionArn"] == "arn:aws:states:exec:1"
    assert sfn_kwargs["error"] == "CycleFinalizer.Timeout60Min"

    # Response.update_item called twice (e1 and e2) with the rewrite condition.
    assert mock_response_table.update_item.call_count == 2
    employee_ids = sorted(
        call.kwargs["Key"]["employeeId"]
        for call in mock_response_table.update_item.call_args_list
    )
    assert employee_ids == ["e1", "e2"]
    for call in mock_response_table.update_item.call_args_list:
        kw = call.kwargs
        assert kw["UpdateExpression"] == "SET voiceStatus = :unreachable"
        assert kw["ConditionExpression"] == (
            "voiceStatus = :pending OR voiceStatus = :other"
        )
        assert kw["ExpressionAttributeValues"][":unreachable"] == "UNREACHABLE"

    # CycleTable status flip to TIMEOUT.
    mock_cycle_table.update_item.assert_called_once()
    upd_kwargs = mock_cycle_table.update_item.call_args.kwargs
    assert upd_kwargs["ExpressionAttributeValues"][":new"] == "TIMEOUT"

    # CloudWatch CycleTimeout metric emitted exactly once (Requirements 14.4 / 14.6).
    mock_cloudwatch.put_metric_data.assert_called_once()
    cw_kwargs = mock_cloudwatch.put_metric_data.call_args.kwargs
    assert cw_kwargs["Namespace"] == handler.CLOUDWATCH_NAMESPACE
    metric_datum = cw_kwargs["MetricData"][0]
    assert metric_datum["MetricName"] == "CycleTimeout"
    assert metric_datum["Value"] == 1
    assert metric_datum["Unit"] == "Count"
    assert metric_datum["Dimensions"] == [
        {"Name": "CycleId", "Value": "cycle-1"}
    ]

    # SNS published once with cycle-timeout summary.
    mock_sns.publish.assert_called_once()
    sns_msg = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    assert sns_msg["event"] == "CYCLE_TIMEOUT_60MIN"
    assert sns_msg["stoppedResponses"] == 2

    # Timer rules removed.
    assert mock_events.delete_rule.call_count == 2


def test_timer_60min_no_op_when_cycle_already_completed(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_sns: MagicMock,
    mock_cloudwatch: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "COMPLETED"}
    }
    result = handler.lambda_handler(
        {
            "trigger": "TIMER_60MIN",
            "cycleId": "cycle-1",
            "executionArn": "arn:aws:states:exec:1",
        },
        None,
    )
    assert result == {"status": "no_op"}
    mock_sfn.stop_execution.assert_not_called()
    mock_response_table.update_item.assert_not_called()
    mock_sns.publish.assert_not_called()
    # No CycleTimeout metric on the no-op path (no cycle was timed out).
    mock_cloudwatch.put_metric_data.assert_not_called()


def test_timer_60min_swallows_conditional_check_on_response_flip(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_sns: MagicMock,
    mock_events: MagicMock,
    mock_cloudwatch: MagicMock,
) -> None:
    """Race with KeywordMatcher: PENDING → SAFE between query and update.

    The ConditionExpression rejects the rewrite; the handler must swallow
    the exception and continue with the cycle-status flip.
    """
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "RUNNING"}
    }
    mock_response_table.query.side_effect = [
        {"Items": [_resp("e1", "PENDING", attempts=1)]},
        {"Items": [_resp("e1", "SAFE", attempts=1)]},
    ]
    mock_response_table.update_item.side_effect = (
        _conditional_check_failed()
    )

    result = handler.lambda_handler(
        {
            "trigger": "TIMER_60MIN",
            "cycleId": "cycle-1",
            "executionArn": "arn:aws:states:exec:1",
        },
        None,
    )
    assert result["status"] == "timeout_applied"
    # CycleTable flip still attempted (cycle ends regardless of races).
    mock_cycle_table.update_item.assert_called_once()
    mock_sns.publish.assert_called_once()
    assert mock_events.delete_rule.call_count == 2
    # CycleTimeout metric still emitted on the successful flip.
    mock_cloudwatch.put_metric_data.assert_called_once()


def test_timer_60min_swallows_resource_not_found_on_event_rule_delete(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_sns: MagicMock,
    mock_events: MagicMock,
    mock_cloudwatch: MagicMock,
) -> None:
    """Idempotent finalizer: re-run after rules already deleted must succeed."""
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "RUNNING"}
    }
    mock_response_table.query.side_effect = [
        {"Items": []},
        {"Items": []},
    ]
    mock_events.remove_targets.side_effect = _resource_not_found(
        op="RemoveTargets"
    )
    mock_events.delete_rule.side_effect = _resource_not_found(op="DeleteRule")

    result = handler.lambda_handler(
        {
            "trigger": "TIMER_60MIN",
            "cycleId": "cycle-1",
            "executionArn": "arn:aws:states:exec:1",
        },
        None,
    )
    assert result["status"] == "timeout_applied"
    # Two attempts per call (30min + 60min rule names).
    assert mock_events.remove_targets.call_count == 2
    assert mock_events.delete_rule.call_count == 2
    # CycleTimeout metric still emitted even when the rules were already gone.
    mock_cloudwatch.put_metric_data.assert_called_once()


def test_timer_60min_skips_cycle_timeout_metric_when_already_terminal(
    mock_cycle_table: MagicMock,
    mock_response_table: MagicMock,
    mock_sfn: MagicMock,
    mock_sns: MagicMock,
    mock_events: MagicMock,
    mock_cloudwatch: MagicMock,
) -> None:
    """Race: cycle flipped to a terminal status between get_item and update_item.

    The conditional-check failure on ``_set_cycle_status`` must short-
    circuit before the CycleTimeout metric is emitted — otherwise the
    alarm would double-count the cycle.
    """
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "status": "RUNNING"}
    }
    mock_response_table.query.return_value = {"Items": []}
    mock_cycle_table.update_item.side_effect = _conditional_check_failed()

    result = handler.lambda_handler(
        {
            "trigger": "TIMER_60MIN",
            "cycleId": "cycle-1",
            "executionArn": "arn:aws:states:exec:1",
        },
        None,
    )
    assert result == {"status": "already_terminal"}
    # The metric must not be emitted on the already-terminal path.
    mock_cloudwatch.put_metric_data.assert_not_called()
    # And no operator notification either.
    mock_sns.publish.assert_not_called()
    mock_events.delete_rule.assert_not_called()


# --- Validation errors -------------------------------------------------


def test_non_dict_event_rejected() -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        handler.lambda_handler("not-a-dict", None)  # type: ignore[arg-type]


def test_invalid_trigger_rejected() -> None:
    with pytest.raises(ValueError, match="trigger must be one of"):
        handler.lambda_handler(
            {"trigger": "UNKNOWN", "cycleId": "cycle-1"}, None
        )


def test_missing_cycle_id_rejected() -> None:
    with pytest.raises(ValueError, match="cycleId must be a non-empty string"):
        handler.lambda_handler({"trigger": "MAP_COMPLETED"}, None)


def test_empty_cycle_id_rejected() -> None:
    with pytest.raises(ValueError, match="cycleId must be a non-empty string"):
        handler.lambda_handler(
            {"trigger": "MAP_COMPLETED", "cycleId": ""}, None
        )
