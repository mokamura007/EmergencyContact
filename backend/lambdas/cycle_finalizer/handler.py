"""CycleFinalizer Lambda — terminate a cycle (Phase 6.6).

Three trigger sources, all multiplexed through a single
``lambda_handler`` entry point keyed by ``event["trigger"]``:

* ``"MAP_COMPLETED"`` — fired from the Step Functions ``Finalize``
  state when the ``CallMap`` state has drained. We verify every
  Response has reached a terminal ``voiceStatus`` and flip the cycle
  to ``COMPLETED``. Then we delete the 30/60-minute timer rules so
  they don't fire after the work is done (Requirement 14.6).

* ``"TIMER_30MIN"`` — fired from EventBridge rule
  ``cycle-30min-{cycleId}``. If the initial outbound dispatch round
  has not finished (any Response with ``callAttempts == 0``), we set
  ``slaWarning30min=true`` on the cycle row, emit a CloudWatch metric
  (``Namespace=SafetyConfirmation / MetricName=SlaWarning30Min``), and
  publish an SNS notification to the operator topic (Requirement
  14.5).

* ``"TIMER_60MIN"`` — fired from EventBridge rule
  ``cycle-60min-{cycleId}``. We Stop the SFN execution, force every
  non-terminal Response to ``voiceStatus="UNREACHABLE"`` (each with a
  ConditionExpression so an in-flight ``KeywordMatcher`` answer is not
  clobbered), flip the cycle to ``TIMEOUT`` with ``completedAt=now``,
  publish an SNS notification, and delete the timer rules
  (Requirements 14.4, 14.6).

All real classification work happens in :mod:`shared.cycle.finalize`;
this module is the I/O shell.

Operator-Topic note (Phase 12.4 dependency): the SNS topic ARN passed
in via ``OPERATOR_TOPIC_ARN`` is currently a *forward-named* value —
Phase 12.4 will create the actual topic with the same name. Until then
the ``Publish`` calls in this Lambda will fail at runtime with
``NotFoundException``. We treat that as the expected behavior of the
forward-named pattern: the deployment succeeds, the IAM Policy points
at the correct future ARN, and the topic comes online with a Phase 12.4
``cfn deploy``. The handler does not swallow ``NotFoundException`` —
the SFN ``Catch`` block at the Map level (Phase 6.8) routes the
failure into an alarming code path.

Input contract (one of the three shapes below)::

    {"trigger": "MAP_COMPLETED",  "cycleId": "...", "executionArn": "..."}
    {"trigger": "TIMER_30MIN",   "cycleId": "...", "ruleName": "cycle-30min-..."}
    {"trigger": "TIMER_60MIN",   "cycleId": "...", "ruleName": "cycle-60min-...",
                                  "executionArn": "..."}
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.cycle.finalize import (
    apply_timeout,
    compute_summary,
    count_pending_responses,
    is_cycle_completed,
    is_first_dispatch_incomplete,
)

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

CYCLE_TABLE_NAME = os.environ["CYCLE_TABLE_NAME"]
RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]
OPERATOR_TOPIC_ARN = os.environ["OPERATOR_TOPIC_ARN"]
SFN_STATE_MACHINE_ARN = os.environ["SFN_STATE_MACHINE_ARN"]
CLOUDWATCH_NAMESPACE = os.environ.get("CLOUDWATCH_NAMESPACE", "SafetyConfirmation")

_VALID_TRIGGERS: frozenset[str] = frozenset(
    {"MAP_COMPLETED", "TIMER_30MIN", "TIMER_60MIN"}
)

_DDB = boto3.resource("dynamodb")
_CYCLE_TABLE = _DDB.Table(CYCLE_TABLE_NAME)
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)
_SFN = boto3.client("stepfunctions")
_SNS = boto3.client("sns")
_EVENTS = boto3.client("events")
_CLOUDWATCH = boto3.client("cloudwatch")


# --- Utilities ----------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 ``Z`` string."""
    return (
        dt.datetime.now(tz=dt.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _validate_event(event: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Validate the event and return ``(trigger, cycleId, event)``."""
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    trigger = event.get("trigger")
    if trigger not in _VALID_TRIGGERS:
        raise ValueError(
            f"trigger must be one of {sorted(_VALID_TRIGGERS)}; got {trigger!r}"
        )
    cycle_id = event.get("cycleId")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("cycleId must be a non-empty string")
    return trigger, cycle_id, event


def _query_responses(cycle_id: str) -> list[dict[str, Any]]:
    """Return all Response rows for ``cycle_id`` (PK query).

    Handles DynamoDB pagination so cycles with > 1 MB of Response data
    (300 employees x roughly 1 KB each is well under, but the loop is
    cheap insurance for the future).
    """
    items: list[dict[str, Any]] = []
    last_evaluated_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "cycleId = :cid",
            "ExpressionAttributeValues": {":cid": cycle_id},
        }
        if last_evaluated_key is not None:
            kwargs["ExclusiveStartKey"] = last_evaluated_key
        resp = _RESPONSE_TABLE.query(**kwargs)
        items.extend(resp.get("Items", []))
        last_evaluated_key = resp.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
    return items


def _get_cycle(cycle_id: str) -> dict[str, Any] | None:
    """Return the cycle row, or ``None`` if it does not exist."""
    resp = _CYCLE_TABLE.get_item(Key={"cycleId": cycle_id})
    return resp.get("Item")


def _set_cycle_status(
    cycle_id: str,
    new_status: str,
    *,
    completed_at: str | None = None,
) -> bool:
    """Set the cycle ``status`` (and optionally ``completedAt``).

    The update is guarded by ``ConditionExpression`` so we never write
    a terminal status onto a cycle that has already been finalized by
    another path. Returns ``True`` if the row was updated, ``False`` if
    the conditional check failed (already terminal).
    """
    update_expr = "SET #s = :new"
    expr_values: dict[str, Any] = {":new": new_status, ":running": "RUNNING"}
    expr_names: dict[str, str] = {"#s": "status"}
    if completed_at is not None:
        update_expr += ", completedAt = :ca"
        expr_values[":ca"] = completed_at
    try:
        _CYCLE_TABLE.update_item(
            Key={"cycleId": cycle_id},
            UpdateExpression=update_expr,
            ConditionExpression="#s = :running",
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.info(
                "Cycle status transition skipped (no longer RUNNING) "
                "cycleId=%s targetStatus=%s",
                cycle_id,
                new_status,
            )
            return False
        raise


def _set_sla_warning_flag(cycle_id: str) -> bool:
    """Persist ``slaWarning30min=true`` if the cycle is still RUNNING.

    Returns ``True`` on a successful flip, ``False`` if the cycle has
    already left the ``RUNNING`` state (in which case there is no
    operator left to warn).
    """
    try:
        _CYCLE_TABLE.update_item(
            Key={"cycleId": cycle_id},
            UpdateExpression="SET slaWarning30min = :true",
            ConditionExpression="#s = :running",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":true": True, ":running": "RUNNING"},
        )
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.info(
                "SLA-warning flag skipped (cycle no longer RUNNING) "
                "cycleId=%s",
                cycle_id,
            )
            return False
        raise


def _force_response_unreachable(cycle_id: str, employee_id: str) -> None:
    """UpdateItem Response ``voiceStatus`` PENDING/OTHER → UNREACHABLE.

    The ConditionExpression mirrors the truth table in
    :func:`shared.cycle.finalize.apply_timeout`: only PENDING / OTHER
    rows are eligible. A confirmed answer arriving between the SFN Stop
    and the UpdateItem must not be clobbered. ``ConditionalCheckFailedException``
    is logged at INFO and swallowed (race with KeywordMatcher).
    """
    try:
        _RESPONSE_TABLE.update_item(
            Key={"cycleId": cycle_id, "employeeId": employee_id},
            UpdateExpression="SET voiceStatus = :unreachable",
            ConditionExpression=(
                "voiceStatus = :pending OR voiceStatus = :other"
            ),
            ExpressionAttributeValues={
                ":unreachable": "UNREACHABLE",
                ":pending": "PENDING",
                ":other": "OTHER",
            },
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.info(
                "Response timeout flip skipped (already terminal) "
                "cycleId=%s employeeId=%s",
                cycle_id,
                employee_id,
            )
            return
        raise


def _delete_timer_rules(cycle_id: str) -> None:
    """Best-effort delete both EventBridge rules for the cycle.

    Each rule is named after Phase 6.8's StartTimers state convention:
    ``cycle-30min-{cycleId}`` / ``cycle-60min-{cycleId}``. We always
    call ``RemoveTargets`` first because EventBridge refuses to delete
    a rule that still has targets attached. ``ResourceNotFoundException``
    (rule already gone) is logged and swallowed — a re-run of the
    finalizer must remain idempotent.
    """
    for prefix in ("cycle-30min", "cycle-60min"):
        rule_name = f"{prefix}-{cycle_id}"
        try:
            _EVENTS.remove_targets(Rule=rule_name, Ids=["1"], Force=True)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ResourceNotFoundException":
                raise
            LOGGER.info(
                "remove_targets skipped (rule not found) ruleName=%s",
                rule_name,
            )
        try:
            _EVENTS.delete_rule(Name=rule_name, Force=True)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ResourceNotFoundException":
                raise
            LOGGER.info(
                "delete_rule skipped (rule not found) ruleName=%s",
                rule_name,
            )


def _publish_operator_notification(subject: str, message: dict[str, Any]) -> None:
    """Publish a JSON message to the operator SNS topic.

    The topic itself is created later (Phase 12.4). The IAM policy
    already points at the future ARN, so at deploy time this call will
    fail with ``NotFoundException``; that is the documented behavior
    until 12.4 lands and the topic exists.
    """
    _SNS.publish(
        TopicArn=OPERATOR_TOPIC_ARN,
        Subject=subject,
        Message=json.dumps(message, ensure_ascii=False, default=str),
    )


def _put_cycle_metric(metric_name: str, cycle_id: str) -> None:
    """Emit a single cycle-scoped CloudWatch datum (Count=1) for ``cycle_id``.

    The Namespace is fixed to :data:`CLOUDWATCH_NAMESPACE`
    (``SafetyConfirmation``) and the only Dimension is
    ``CycleId``. Two metric names flow through this helper:

    * ``SlaWarning30Min`` — emitted by :func:`_handle_timer_30min`
      when the initial outbound dispatch round has not finished by
      the 30-minute SLA boundary (Requirement 14.5).
    * ``CycleTimeout`` — emitted by :func:`_handle_timer_60min`
      exactly once per cycle, at the moment the cycle row flips to
      ``TIMEOUT``. Watched by ``CycleTimeoutAlarm`` in
      ``infrastructure/template.yaml`` (Requirements 14.4 / 14.6).
    """
    _CLOUDWATCH.put_metric_data(
        Namespace=CLOUDWATCH_NAMESPACE,
        MetricData=[
            {
                "MetricName": metric_name,
                "Dimensions": [{"Name": "CycleId", "Value": cycle_id}],
                "Value": 1,
                "Unit": "Count",
            }
        ],
    )


def _stop_sfn_execution(execution_arn: str, cycle_id: str) -> None:
    """Best-effort StopExecution. ``ExecutionDoesNotExist`` is swallowed."""
    try:
        _SFN.stop_execution(
            executionArn=execution_arn,
            error="CycleFinalizer.Timeout60Min",
            cause=f"60-minute cycle SLA exceeded for {cycle_id}",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"ExecutionDoesNotExist", "InvalidArn"}:
            LOGGER.info(
                "SFN StopExecution skipped executionArn=%s code=%s",
                execution_arn,
                code,
            )
            return
        raise


# --- Trigger branches ---------------------------------------------------


def _handle_map_completed(cycle_id: str) -> dict[str, Any]:
    """Map-completion path (Requirement 11.4)."""
    responses = _query_responses(cycle_id)
    if not is_cycle_completed(responses):
        pending = count_pending_responses(responses)
        LOGGER.warning(
            "MAP_COMPLETED arrived with pending Responses cycleId=%s "
            "pendingCount=%s",
            cycle_id,
            pending,
        )
        return {"status": "incomplete", "pendingCount": pending}

    summary = compute_summary(responses)
    flipped = _set_cycle_status(
        cycle_id, "COMPLETED", completed_at=_utc_now_iso()
    )
    if flipped:
        _delete_timer_rules(cycle_id)
    return {
        "status": "completed" if flipped else "already_terminal",
        "summary": summary,
    }


def _handle_timer_30min(cycle_id: str) -> dict[str, Any]:
    """30-minute SLA path (Requirement 14.5)."""
    cycle = _get_cycle(cycle_id)
    if cycle is None or cycle.get("status") != "RUNNING":
        LOGGER.info(
            "TIMER_30MIN no-op (cycle missing or not RUNNING) cycleId=%s "
            "currentStatus=%s",
            cycle_id,
            cycle.get("status") if cycle else None,
        )
        return {"status": "no_op"}

    responses = _query_responses(cycle_id)
    if not is_first_dispatch_incomplete(responses):
        LOGGER.info(
            "TIMER_30MIN no warning needed cycleId=%s targetTotal=%s",
            cycle_id,
            len(responses),
        )
        return {"status": "no_warning_needed"}

    if not _set_sla_warning_flag(cycle_id):
        return {"status": "no_op"}
    _put_cycle_metric("SlaWarning30Min", cycle_id)
    summary = compute_summary(responses)
    _publish_operator_notification(
        subject=f"[SafetyConfirmation] SLA 30min warning ({cycle_id})",
        message={
            "cycleId": cycle_id,
            "event": "SLA_WARNING_30MIN",
            "summary": summary,
        },
    )
    return {"status": "warning_applied", "summary": summary}


def _handle_timer_60min(cycle_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """60-minute timeout path (Requirements 14.4, 14.6)."""
    cycle = _get_cycle(cycle_id)
    if cycle is None or cycle.get("status") != "RUNNING":
        LOGGER.info(
            "TIMER_60MIN no-op (cycle missing or not RUNNING) cycleId=%s "
            "currentStatus=%s",
            cycle_id,
            cycle.get("status") if cycle else None,
        )
        return {"status": "no_op"}

    execution_arn = event.get("executionArn")
    if isinstance(execution_arn, str) and execution_arn:
        _stop_sfn_execution(execution_arn, cycle_id)

    responses = _query_responses(cycle_id)
    rewrites = apply_timeout(responses)
    for employee_id, _ in rewrites:
        _force_response_unreachable(cycle_id, employee_id)

    flipped = _set_cycle_status(
        cycle_id, "TIMEOUT", completed_at=_utc_now_iso()
    )
    if not flipped:
        return {"status": "already_terminal"}

    _put_cycle_metric("CycleTimeout", cycle_id)

    # Re-query after rewrites so the SNS payload reflects the post-
    # timeout truth (UNREACHABLE counted properly).
    responses_after = _query_responses(cycle_id)
    summary = compute_summary(responses_after)
    _publish_operator_notification(
        subject=f"[SafetyConfirmation] Cycle timeout ({cycle_id})",
        message={
            "cycleId": cycle_id,
            "event": "CYCLE_TIMEOUT_60MIN",
            "summary": summary,
            "stoppedResponses": len(rewrites),
        },
    )
    _delete_timer_rules(cycle_id)
    return {
        "status": "timeout_applied",
        "stoppedResponses": len(rewrites),
        "summary": summary,
    }


# --- Entry point --------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """CycleFinalizer entry point — multiplex on ``event['trigger']``."""
    trigger, cycle_id, ev = _validate_event(event)
    LOGGER.info("CycleFinalizer start trigger=%s cycleId=%s", trigger, cycle_id)

    if trigger == "MAP_COMPLETED":
        result = _handle_map_completed(cycle_id)
    elif trigger == "TIMER_30MIN":
        result = _handle_timer_30min(cycle_id)
    else:  # TIMER_60MIN — guarded by _validate_event
        result = _handle_timer_60min(cycle_id, ev)

    LOGGER.info(
        "CycleFinalizer done trigger=%s cycleId=%s result=%s",
        trigger,
        cycle_id,
        result,
    )
    return result
