"""LoadTargets Lambda — cycle target enumeration (Requirements 4.3, 4.4, 4.5, 13.5).

Invoked as the first state of the Cycle Step Functions state machine
(Phase 6). Given the cycle parameters, the function enumerates the
employees who should receive an outbound call this cycle.

Modes:
    ALL
        Scan the Employee table and return every employee that is
        visible per ``shared.employee.visibility.is_visible``
        (Requirements 13.5 / 15.4 / Property 2): not logically deleted
        AND ``phoneNumber`` is a non-empty E.164 string.

    UNREACHABLE_ONLY
        Query the Response table for ``referencedCycleId`` (the most
        recently completed cycle, supplied by ``CycleApi``) and keep
        only employees whose ``voiceStatus`` is ``UNREACHABLE`` or
        ``OTHER`` (Requirement 4.4, design.md L277). The resulting
        employee IDs are dereferenced via Employee.BatchGetItem in
        chunks of 25 and then filtered by ``is_visible`` so that
        employees who became logically deleted between cycles are
        excluded.

Failure modes (per project principle 19(b) — no fallback):
    * Missing / unknown ``mode``                  → ``ValueError``
    * ``UNREACHABLE_ONLY`` without ``referencedCycleId`` → ``ValueError``
    * Zero targets after filtering                → ``NoTargetsError``
    * DynamoDB / KMS errors (ClientError, KMS Decrypt failure, …)
      propagate to the caller; the Step Functions ``Catch`` block
      added in Phase 6.8 routes them to a failure branch.

Output shape (consumed by the ``StartTimers`` / ``CallMap`` states):

    {
        "cycleId":     "<uuid>",
        "mode":        "ALL" | "UNREACHABLE_ONLY",
        "targetCount": <int>,
        "targets":     [
            {"employeeId": "<id>", "name": "<str>", "phoneNumber": "<E.164>"},
            ...
        ]
    }
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

import boto3
from boto3.dynamodb.conditions import Key

from shared.employee.visibility import is_visible

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

EMPLOYEE_TABLE_NAME = os.environ["EMPLOYEE_TABLE_NAME"]
RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]

#: Valid values for the ``mode`` input field. Mirrors ``CycleApi`` /
#: ``shared.validation.range_enum``.
VALID_MODES = frozenset({"ALL", "UNREACHABLE_ONLY"})

#: Voice statuses considered "did not reach the employee" for the
#: ``UNREACHABLE_ONLY`` re-dispatch mode (design.md L277, Requirement 4.4).
UNREACHABLE_VOICE_STATUSES = frozenset({"UNREACHABLE", "OTHER"})

#: DynamoDB BatchGetItem limit per request.
_BATCH_GET_CHUNK = 25

_DDB = boto3.resource("dynamodb")
_EMPLOYEE_TABLE = _DDB.Table(EMPLOYEE_TABLE_NAME)
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)


class NoTargetsError(Exception):
    """No employees matched the target-selection criteria.

    Raised when ``ALL`` mode finds zero visible employees, or when
    ``UNREACHABLE_ONLY`` mode finds zero ``UNREACHABLE``/``OTHER``
    responses in the referenced cycle. The Step Functions ``Catch``
    block added in Phase 6.8 routes this to a failure branch.
    """


# --- Input parsing -------------------------------------------------------


def _parse_event(event: dict[str, Any]) -> tuple[str, str, str | None]:
    """Extract and validate ``cycleId`` / ``mode`` / ``referencedCycleId``.

    Returns:
        (cycle_id, mode, referenced_cycle_id)

    Raises:
        ValueError: missing required field or invalid mode value.
    """
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")

    cycle_id = event.get("cycleId")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("cycleId is required and must be a non-empty string")

    mode = event.get("mode")
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}; got {mode!r}")

    referenced_cycle_id = event.get("referencedCycleId")
    if mode == "UNREACHABLE_ONLY":
        if not isinstance(referenced_cycle_id, str) or not referenced_cycle_id:
            raise ValueError(
                "referencedCycleId is required and must be a non-empty string "
                "when mode=UNREACHABLE_ONLY"
            )
    else:
        referenced_cycle_id = None

    return cycle_id, mode, referenced_cycle_id


# --- ALL mode ------------------------------------------------------------


def _scan_all_employees() -> list[dict[str, Any]]:
    """Return every Employee item via paginated Scan (no projection)."""
    items: list[dict[str, Any]] = []
    last_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {}
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _EMPLOYEE_TABLE.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if last_key is None:
            return items


# --- UNREACHABLE_ONLY mode ----------------------------------------------


def _query_unreachable_response_ids(referenced_cycle_id: str) -> list[str]:
    """Return employeeIds with voiceStatus ∈ UNREACHABLE_VOICE_STATUSES."""
    employee_ids: list[str] = []
    last_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("cycleId").eq(referenced_cycle_id),
        }
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _RESPONSE_TABLE.query(**kwargs)
        for item in resp.get("Items", []):
            voice_status = item.get("voiceStatus")
            employee_id = item.get("employeeId")
            if (
                isinstance(employee_id, str)
                and employee_id
                and voice_status in UNREACHABLE_VOICE_STATUSES
            ):
                employee_ids.append(employee_id)
        last_key = resp.get("LastEvaluatedKey")
        if last_key is None:
            return employee_ids


def _batch_get_employees(employee_ids: list[str]) -> list[dict[str, Any]]:
    """BatchGetItem the Employee table in chunks of 25.

    Iteratively handles ``UnprocessedKeys`` by re-issuing them until the
    batch drains. Order is not preserved — callers must not rely on it.
    """
    results: list[dict[str, Any]] = []
    for start in range(0, len(employee_ids), _BATCH_GET_CHUNK):
        chunk = employee_ids[start : start + _BATCH_GET_CHUNK]
        request_keys: list[dict[str, str]] = [{"employeeId": eid} for eid in chunk]
        while request_keys:
            resp = _DDB.batch_get_item(
                RequestItems={
                    EMPLOYEE_TABLE_NAME: {"Keys": request_keys},
                }
            )
            results.extend(resp.get("Responses", {}).get(EMPLOYEE_TABLE_NAME, []))
            unprocessed_table = cast(
                "dict[str, Any]",
                resp.get("UnprocessedKeys", {}).get(EMPLOYEE_TABLE_NAME, {}),
            )
            request_keys = cast(
                "list[dict[str, str]]",
                unprocessed_table.get("Keys", []),
            )
    return results


# --- Projection ----------------------------------------------------------


def _project_target(employee: dict[str, Any]) -> dict[str, str]:
    """Return ``{employeeId, name, phoneNumber}`` for the SFN ``CallMap``.

    Callers must have already filtered by ``is_visible`` so this function
    assumes the three attributes are present and well-typed.
    """
    return {
        "employeeId": employee["employeeId"],
        "name": employee["name"],
        "phoneNumber": employee["phoneNumber"],
    }


# --- Entry point ---------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Resolve the target employee list for the current cycle.

    See module docstring for the input contract, mode semantics, and
    failure modes.
    """
    cycle_id, mode, referenced_cycle_id = _parse_event(event)
    LOGGER.info(
        "LoadTargets start cycleId=%s mode=%s referencedCycleId=%s",
        cycle_id,
        mode,
        referenced_cycle_id,
    )

    if mode == "ALL":
        candidates = _scan_all_employees()
    else:
        # mode == "UNREACHABLE_ONLY"; _parse_event guarantees referenced_cycle_id is a non-empty str.
        assert referenced_cycle_id is not None  # for type checkers
        employee_ids = _query_unreachable_response_ids(referenced_cycle_id)
        if not employee_ids:
            raise NoTargetsError(
                f"No UNREACHABLE/OTHER responses found in referencedCycleId={referenced_cycle_id}"
            )
        candidates = _batch_get_employees(employee_ids)

    visible = [emp for emp in candidates if is_visible(emp)]
    if not visible:
        raise NoTargetsError(
            f"No visible employees for cycleId={cycle_id} mode={mode}"
        )

    targets = [_project_target(emp) for emp in visible]
    LOGGER.info(
        "LoadTargets done cycleId=%s mode=%s targetCount=%d",
        cycle_id,
        mode,
        len(targets),
    )
    return {
        "cycleId": cycle_id,
        "mode": mode,
        "targetCount": len(targets),
        "targets": targets,
    }
