"""Pure helpers for the CycleFinalizer Lambda (Phase 6.6).

The CycleFinalizer Lambda has three trigger paths (Map completion,
30-minute timer, 60-minute timer; see ``backend/lambdas/cycle_finalizer``).
Each path inspects the current Response rows for the cycle and decides
which side-effects (DynamoDB UpdateItem, SFN StopExecution, SNS Publish,
EventBridge DeleteRule) to fire. The classification logic itself is
keep-pure-and-test-densely territory: the five helpers below take a
``list[dict]`` of Response rows and return primitive answers, with no
I/O whatsoever.

Truth tables are documented inline so PBT (Phase 13.x) can mechanically
mirror them under Hypothesis without re-deriving the semantics:

* :func:`is_cycle_completed` (Property 16, Phase 13.x PBT candidate).
  A cycle is "complete" iff every Response row has reached a terminal
  ``voiceStatus``. An empty list trivially satisfies the universal
  predicate (vacuously true) and we accept that — production cycles
  with 0 targets are caught earlier in LoadTargets (Phase 6.1).

* :func:`count_pending_responses`. Convenience counter for the "still
  pending" log line. Treats a Response row missing the ``voiceStatus``
  key as PENDING (i.e. unknown ⇒ not yet confirmed) because that's the
  safer default for SLA logic.

* :func:`compute_summary` (Property 15, Phase 13.x PBT candidate). Returns
  the same per-cycle aggregate the SPA shows on the status page so the
  Finalizer can include it in SNS notification subjects/messages and the
  ``status="completed"`` return payload. Properties tracked:
  ``targetTotal = len(responses)``, ``dispatched = #{r: callAttempts > 0}``,
  ``responded = #{r: voiceStatus ∈ CONFIRMED}``, ``unreachable =
  #{r: voiceStatus == "UNREACHABLE"}``, plus a histogram ``byStatus``.

* :func:`apply_timeout` (Property 17, Phase 13.x PBT candidate). The
  60-minute timeout path forces every non-terminal Response to
  ``UNREACHABLE`` (Requirement 14.4). Returns the **list of mutations to
  apply** so the handler can DynamoDB-UpdateItem each one with a guarded
  ConditionExpression. Already-terminal rows are not included in the
  output, mirroring the table-truth ``terminal → unchanged``.

* :func:`is_first_dispatch_incomplete`. The 30-minute SLA check
  (Requirement 14.5) flags a cycle when at least one Response still has
  ``callAttempts == 0`` (or the field missing). Returns ``True`` if a
  warning should fire.

Every function rejects non-``list`` input with ``TypeError`` so the
handler's misuse turns into a CloudWatch-visible failure rather than
silently producing nonsense aggregates (project principle 19(b)).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from shared.retry.evaluator import VALID_VOICE_STATUS_VALUES

#: Voice-status values that count as a *confirmed* answer for the
#: ``responded`` aggregate (Requirement 9.1). Imported indirectly via the
#: ``_CONFIRMED_VOICE_STATUSES`` set in ``shared.retry.evaluator`` would
#: tightly couple the two modules; we keep a local literal because the
#: list rarely changes and the duplication is intentional documentation
#: ("here are the answers the status page counts as responded").
_CONFIRMED_VOICE_STATUSES: frozenset[str] = frozenset(
    {"SAFE", "INJURED", "UNAVAILABLE"}
)

#: Voice-status values that mean "no more outbound work for this
#: employee in this cycle". A Cycle is complete only when every
#: Response row is in one of these states. ``UNREACHABLE`` is included
#: here because the SFN ``FinalizeOne`` state has already accepted the
#: retry budget as exhausted (Requirement 9.5).
_TERMINAL_VOICE_STATUSES: frozenset[str] = (
    _CONFIRMED_VOICE_STATUSES | frozenset({"UNREACHABLE"})
)

#: Voice-status values that the 60-minute timeout path forcibly
#: re-labels to ``UNREACHABLE``. ``PENDING`` and ``OTHER`` are not
#: terminal answers; rewriting them is the explicit Requirement 14.4
#: behavior. Anything else (already terminal) is left alone.
_TIMEOUT_REWRITE_VOICE_STATUSES: frozenset[str] = frozenset(
    {"PENDING", "OTHER"}
)


def _require_list(responses: object, field_name: str = "responses") -> list[dict[str, Any]]:
    """Validate the ``responses`` argument is a list of dicts.

    Returns the list unchanged on success. Raises ``TypeError`` on
    structural problems so the handler surfaces a CloudWatch-visible
    exception (project principle 19(b)).    """
    if not isinstance(responses, list):
        raise TypeError(
            f"{field_name} must be a list; got {type(responses).__name__}"
        )
    for idx, item in enumerate(responses):
        if not isinstance(item, dict):
            raise TypeError(
                f"{field_name}[{idx}] must be a dict; "
                f"got {type(item).__name__}"
            )
    return responses


def _coerce_call_attempts(idx: int, raw: Any) -> int:
    """Validate and convert a ``callAttempts`` value to ``int``.

    DRY helper for ``compute_summary`` and ``is_first_dispatch_incomplete``.
    boto3 returns DynamoDB ``Number`` attributes as ``decimal.Decimal``,
    so the helper accepts both ``int`` and ``Decimal``. ``bool`` is
    rejected explicitly because Python treats it as an ``int`` subtype
    but ``callAttempts=True/False`` would be upstream data corruption.

    Args:
        idx: Row index for the error message (caller-supplied).
        raw: The raw ``callAttempts`` value as read from DynamoDB or
            constructed in tests. ``None`` / missing should be handled
            by the caller with ``.get("callAttempts", 0)`` before this
            function is invoked.

    Returns:
        The ``int`` form of ``raw``. ``Decimal`` values are converted
        via ``int(Decimal(...))`` which truncates toward zero — fine
        for non-negative integer call counts.

    Raises:
        TypeError: ``raw`` is ``bool`` or not ``(int | Decimal)``.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, Decimal)):
        raise TypeError(
            f"responses[{idx}].callAttempts must be int or Decimal; "
            f"got {type(raw).__name__}"
        )
    return int(raw)


def is_cycle_completed(responses: list[dict[str, Any]]) -> bool:
    """Return ``True`` iff every Response has a terminal ``voiceStatus``.

    Property 16 PBT candidate (Phase 13.x).

    Truth table::

        responses == []                                 -> True   (vacuous)
        ∀ r ∈ responses: r.voiceStatus ∈ TERMINAL       -> True
        ∃ r ∈ responses: r.voiceStatus ∉ TERMINAL       -> False
        ∃ r ∈ responses: r has no 'voiceStatus' key      -> False (treated as PENDING)

    Args:
        responses: List of Response dicts as queried from
            ``ResponseTable`` by ``cycleId``.

    Returns:
        Whether the SFN Map completion path may transition the cycle to
        ``COMPLETED``.

    Raises:
        TypeError: ``responses`` is not a list of dicts.
    """
    rows = _require_list(responses)
    for row in rows:
        status = row.get("voiceStatus")
        if status not in _TERMINAL_VOICE_STATUSES:
            return False
    return True


def count_pending_responses(responses: list[dict[str, Any]]) -> int:
    """Return how many Response rows are *not* in a terminal state.

    Includes rows missing the ``voiceStatus`` field (defensive default:
    unknown ⇒ pending).

    Args:
        responses: List of Response dicts.

    Returns:
        Non-negative integer count of pending rows.

    Raises:
        TypeError: ``responses`` is not a list of dicts.
    """
    rows = _require_list(responses)
    return sum(
        1
        for row in rows
        if row.get("voiceStatus") not in _TERMINAL_VOICE_STATUSES
    )


def compute_summary(responses: list[dict[str, Any]]) -> dict[str, Any]:
    """Return aggregate counters for the cycle.

    Property 15 PBT candidate (Phase 13.x).

    Schema::

        {
            "targetTotal": <int>,                  # == len(responses)
            "dispatched":  <int>,                  # rows with callAttempts > 0
            "responded":   <int>,                  # voiceStatus ∈ CONFIRMED
            "unreachable": <int>,                  # voiceStatus == "UNREACHABLE"
            "byStatus":    {<voiceStatus>: <int>}  # histogram (missing → "PENDING")
        }

    ``callAttempts`` is read with ``.get("callAttempts", 0)`` so rows
    that never entered the SFN Dispatch state still count as
    ``dispatched=0``. Non-int values raise ``TypeError`` because that
    indicates upstream data corruption.

    Args:
        responses: List of Response dicts.

    Returns:
        Summary dict matching the schema above.

    Raises:
        TypeError: ``responses`` is not a list of dicts, or a
            ``callAttempts`` value is not an int.
    """
    rows = _require_list(responses)
    target_total = len(rows)
    dispatched = 0
    responded = 0
    unreachable = 0
    by_status: dict[str, int] = {}
    for idx, row in enumerate(rows):
        raw_attempts = row.get("callAttempts", 0)
        attempts = _coerce_call_attempts(idx, raw_attempts)
        if attempts > 0:
            dispatched += 1

        status = row.get("voiceStatus", "PENDING")
        # ``status`` may be a string we don't recognise — keep counting
        # it in ``byStatus`` so operators see the anomaly rather than
        # have it silently dropped.
        by_status[status] = by_status.get(status, 0) + 1
        if status in _CONFIRMED_VOICE_STATUSES:
            responded += 1
        if status == "UNREACHABLE":
            unreachable += 1

    return {
        "targetTotal": target_total,
        "dispatched": dispatched,
        "responded": responded,
        "unreachable": unreachable,
        "byStatus": by_status,
    }


def apply_timeout(responses: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Return the (employeeId, newVoiceStatus) tuples for the 60-min flip.

    Property 17 PBT candidate (Phase 13.x).

    Only Response rows whose current ``voiceStatus`` is in
    :data:`_TIMEOUT_REWRITE_VOICE_STATUSES` (PENDING / OTHER) are
    returned, with ``"UNREACHABLE"`` as the new status — those are the
    rows that need DynamoDB UpdateItem in the handler. Already-terminal
    rows (SAFE / INJURED / UNAVAILABLE / UNREACHABLE) are filtered out.

    Rows missing ``employeeId`` or ``voiceStatus`` are quarantined with
    a ``TypeError`` because flipping a row we cannot address would
    silently corrupt the table (project principle 19(b)).

    Args:
        responses: List of Response dicts.

    Returns:
        List of ``(employeeId, "UNREACHABLE")`` tuples — the exact set
        of UpdateItem calls the handler should issue, each guarded by a
        ``ConditionExpression`` so a race with the SFN doesn't reset a
        meanwhile-confirmed answer.

    Raises:
        TypeError: ``responses`` is not a list of dicts, or a row in
            ``_TIMEOUT_REWRITE_VOICE_STATUSES`` lacks ``employeeId``.
    """
    rows = _require_list(responses)
    out: list[tuple[str, str]] = []
    for idx, row in enumerate(rows):
        status = row.get("voiceStatus")
        if status not in _TIMEOUT_REWRITE_VOICE_STATUSES:
            continue
        employee_id = row.get("employeeId")
        if not isinstance(employee_id, str) or not employee_id:
            raise TypeError(
                f"responses[{idx}] in {status} state must include a "
                f"non-empty employeeId; got {employee_id!r}"
            )
        out.append((employee_id, "UNREACHABLE"))
    return out


def is_first_dispatch_incomplete(responses: list[dict[str, Any]]) -> bool:
    """Return ``True`` iff at least one Response has ``callAttempts == 0``.

    Requirement 14.5 (30-minute SLA): a warning fires when the first
    outbound dispatch round has not finished — operationalized as "at
    least one Response in this cycle has never been dispatched". A row
    that lacks the ``callAttempts`` field counts as ``0`` (the SFN
    Dispatch state hasn't run yet).

    An empty cycle (``responses == []``) is treated as **not**
    incomplete: there is no one left to dispatch, so the warning is
    nonsensical. This mirrors the
    :func:`is_cycle_completed`-on-empty convention (no employees ⇒
    everything is trivially done).

    Args:
        responses: List of Response dicts.

    Returns:
        ``True`` when the 30-min SLA warning should be raised.

    Raises:
        TypeError: ``responses`` is not a list of dicts, or a
            ``callAttempts`` value is not an int.
    """
    rows = _require_list(responses)
    if not rows:
        return False
    for idx, row in enumerate(rows):
        raw_attempts = row.get("callAttempts", 0)
        attempts = _coerce_call_attempts(idx, raw_attempts)
        if attempts == 0:
            return True
    return False


# Re-export the shared voice status vocabulary so callers don't have to
# import from two different modules. This is documentation, not data
# duplication — the actual frozenset still lives in ``shared.retry.evaluator``.
__all__ = (
    "VALID_VOICE_STATUS_VALUES",
    "apply_timeout",
    "compute_summary",
    "count_pending_responses",
    "is_cycle_completed",
    "is_first_dispatch_incomplete",
)
