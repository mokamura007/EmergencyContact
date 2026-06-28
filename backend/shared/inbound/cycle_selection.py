"""Inbound flow-decision pure function (Phase 9.2 placeholder, Phase 9.3 PBT target).

Property 11 (design.md):
    For all Cycle set ``C``, caller number ``caller``, valid Employee set
    ``E``, wall-clock ``now``, and eligibility window ``N`` (days), 
    ``decide_inbound_flow(...)`` returns a member of
    ``{NOT_REGISTERED, NO_CYCLE, CYCLE_TERMINATED, ACTIVE_CYCLE}``
    satisfying:

    - ``caller`` does not match any visible Employee in ``E``
      (``deleted = true`` or ``phoneNumber = null`` records are excluded
      per Property 2 / :mod:`shared.employee.visibility`)
      ⇒ ``NOT_REGISTERED``.

    - Otherwise, when there is no Cycle with ``status = RUNNING`` and no
      Cycle with ``status = COMPLETED`` whose ``completedAt + N days
      ≥ now`` and no Cycle with ``status ∈ {TIMEOUT, START_FAILED}``
      whose ``completedAt + N days ≥ now``
      ⇒ ``NO_CYCLE``.

    - Otherwise, when the *most-recent eligible* Cycle (by ``startedAt``
      for RUNNING; by ``completedAt`` for terminal statuses) has
      ``status ∈ {TIMEOUT, START_FAILED}``
      ⇒ ``CYCLE_TERMINATED``.

    - Else ⇒ ``ACTIVE_CYCLE`` and the selected Cycle ID is returned.

    ``N`` (``eligibility_window_days``) is sourced from the CFn
    Parameter ``InboundReceptionWindowDays`` (default 30, range 1–90;
    Requirements 13.5 / 13.6).

This module is intentionally pure (no boto3, no logging, no globals)
so Phase 9.3 can drive Hypothesis property tests directly against the
function. Phase 9.2 (InboundHandler Lambda) wraps this function with
DynamoDB I/O.

Design choices recorded for Phase 9.2:

* Single function ``decide_inbound_flow`` rather than separate query
  functions, because the four-way classification has cross-cutting
  ordering constraints (e.g. RUNNING wins over recent COMPLETED wins
  over recent TIMEOUT/START_FAILED) that are easier to reason about
  in one place — and a single boundary is the natural unit for
  Property 11.

* The caller is responsible for pre-filtering Employee records via
  :func:`shared.employee.visibility.is_visible`. This function only
  takes the boolean ``employee_matched`` because the Lambda already
  performs the Employee_Master lookup via the PhoneNumberIndex GSI
  and applies visibility there — duplicating the logic here would
  violate DRY (project principle 19(a)).

* Cycle records are passed as a list of dicts, not as a typed
  dataclass, because the Lambda reads them from DynamoDB as dicts
  and there is no other consumer that would benefit from a stronger
  type. Keys: ``cycleId`` (str), ``status`` (str), ``startedAt``
  (ISO 8601 str), and optionally ``completedAt`` (ISO 8601 str).

* All time arithmetic uses ``datetime.timedelta(days=eligibility_window_days)``
  against the supplied ``now`` (UTC-aware) — design-document phrasing
  "completedAt + N 日 ≥ now" is interpreted in UTC, with ``N`` injected
  as the ``eligibility_window_days`` argument. The boundary is *inclusive*
  (Requirement 13.5: "30 日以内" = ``completedAt + N days ≥ now``;
  Requirement 13.6: "30 日を超過" = ``completedAt + N days < now``).
  Phase 15.27b lifted the previous module-level ``ELIGIBILITY_WINDOW``
  constant to a per-call parameter so the value can flow from CFn
  Parameter ``InboundReceptionWindowDays`` via the Lambda's environment
  variable, keeping the pure function the single source of truth for
  the boundary rule.

* Unrecognised Cycle statuses raise ``ValueError`` (project principle
  19(b): no silent fallbacks).

* The function never mutates its arguments.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

#: Flow classification strings returned to the Inbound Contact Flow as
#: the ``flow`` contact attribute. The Contact Flow's Compare block
#: branches on these four exact values (Phase 9.1 inbound.json).
FLOW_NOT_REGISTERED = "NOT_REGISTERED"
FLOW_NO_CYCLE = "NO_CYCLE"
FLOW_CYCLE_TERMINATED = "CYCLE_TERMINATED"
FLOW_ACTIVE_CYCLE = "ACTIVE_CYCLE"

VALID_FLOWS: frozenset[str] = frozenset(
    {
        FLOW_NOT_REGISTERED,
        FLOW_NO_CYCLE,
        FLOW_CYCLE_TERMINATED,
        FLOW_ACTIVE_CYCLE,
    }
)

#: Cycle status values recognised by the decision function. The set
#: mirrors design.md D2 Cycle.status — any value outside this set
#: triggers ``ValueError`` rather than silent bucketing.
CYCLE_STATUS_RUNNING = "RUNNING"
CYCLE_STATUS_COMPLETED = "COMPLETED"
CYCLE_STATUS_TIMEOUT = "TIMEOUT"
CYCLE_STATUS_START_FAILED = "START_FAILED"

_VALID_CYCLE_STATUSES: frozenset[str] = frozenset(
    {
        CYCLE_STATUS_RUNNING,
        CYCLE_STATUS_COMPLETED,
        CYCLE_STATUS_TIMEOUT,
        CYCLE_STATUS_START_FAILED,
    }
)

_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {CYCLE_STATUS_TIMEOUT, CYCLE_STATUS_START_FAILED}
)

#: Inclusive lower / upper bounds for the ``eligibility_window_days``
#: argument. The lower bound matches the CFn Parameter
#: ``InboundReceptionWindowDays``'s Min (= 1). The upper bound is
#: deliberately looser than the CFn Parameter's Max (= 90) so unit
#: tests can exercise larger windows without duplicating the
#: Parameter constraint here — Parameter Min/Max enforcement is the
#: deploy-time gate; this is the runtime sanity gate.
_MIN_ELIGIBILITY_WINDOW_DAYS = 1
_MAX_ELIGIBILITY_WINDOW_DAYS = 365


def _parse_iso8601(value: str) -> dt.datetime:
    """Parse an ISO 8601 timestamp into a timezone-aware datetime.

    Accepts both ``...Z`` and ``...+00:00`` forms (Python's
    ``datetime.fromisoformat`` handles ``+00:00`` natively; ``Z`` is
    rewritten before parsing). All timestamps in the system are
    written by Lambdas via ``_utc_now_iso()`` helpers that emit the
    ``Z`` suffix, so callers should not see other timezones — but
    rejecting naive timestamps here keeps the function defensive.

    Raises:
        ValueError: when ``value`` is not a string, is empty, or
            cannot be parsed; also when the parsed result is naive
            (no tzinfo).
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"timestamp must be a non-empty string; got {value!r}")
    candidate = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            f"timestamp not ISO 8601: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"timestamp must be timezone-aware; got naive {value!r}"
        )
    return parsed


def _validate_cycle(cycle: object) -> dict[str, Any]:
    """Validate a single Cycle record and return it.

    Required keys: ``cycleId`` (non-empty str), ``status`` (member of
    :data:`_VALID_CYCLE_STATUSES`), ``startedAt`` (ISO 8601 str).
    Terminal statuses additionally require ``completedAt`` (ISO 8601
    str) — RUNNING does not.

    Raises:
        ValueError: on any shape / type / value violation.
    """
    if not isinstance(cycle, dict):
        raise ValueError(f"cycle must be a dict; got {type(cycle).__name__}")
    cycle_id = cycle.get("cycleId")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError(f"cycle.cycleId must be a non-empty str; got {cycle_id!r}")
    status = cycle.get("status")
    if status not in _VALID_CYCLE_STATUSES:
        raise ValueError(
            f"cycle.status must be one of {sorted(_VALID_CYCLE_STATUSES)}; "
            f"got {status!r} for cycleId={cycle_id!r}"
        )
    started_at = cycle.get("startedAt")
    # Parse to assert format; the parsed value isn't needed by every branch
    # but the validation cost is minimal and the early failure is useful.
    _parse_iso8601(started_at) if isinstance(started_at, str) else _raise_started_at(
        cycle_id, started_at
    )
    if status in _TERMINAL_STATUSES or status == CYCLE_STATUS_COMPLETED:
        completed_at = cycle.get("completedAt")
        if not isinstance(completed_at, str) or not completed_at:
            raise ValueError(
                f"cycle.completedAt is required for status={status!r}; "
                f"got {completed_at!r} for cycleId={cycle_id!r}"
            )
        # Parse to assert format.
        _parse_iso8601(completed_at)
    return cycle


def _raise_started_at(cycle_id: str, started_at: object) -> None:
    """Defensive: surface a clear error when startedAt is malformed."""
    raise ValueError(
        f"cycle.startedAt must be an ISO 8601 string; "
        f"got {started_at!r} for cycleId={cycle_id!r}"
    )


def _is_within_window(
    timestamp_iso: str, now: dt.datetime, window: dt.timedelta
) -> bool:
    """True iff ``timestamp_iso + window >= now`` (boundary inclusive).

    The ``>=`` comparison realises Requirement 13.5 ("30 日以内" — i.e.
    completedAt plus the configured window is at least ``now``). The
    complementary Requirement 13.6 ("30 日を超過") is the strict ``<``
    negation. Phase 15.27b parameterised the window so callers can
    inject the value sourced from CFn Parameter
    ``InboundReceptionWindowDays``.
    """
    return _parse_iso8601(timestamp_iso) + window >= now


def _select_latest(
    cycles: list[dict[str, Any]], *, key_attr: str
) -> dict[str, Any] | None:
    """Return the Cycle with the maximum ``key_attr`` value, or ``None``.

    Sort key is the ISO 8601 string itself — string comparison on the
    canonical ``YYYY-MM-DDTHH:MM:SSZ`` form is order-preserving with
    chronological time, which lets us avoid parsing every record into a
    datetime when only the ordering matters.
    """
    if not cycles:
        return None
    return max(cycles, key=lambda c: c[key_attr])


def decide_inbound_flow(
    employee_matched: bool,
    cycles: list[dict[str, Any]],
    now: dt.datetime,
    eligibility_window_days: int,
) -> tuple[str, str | None]:
    """Classify an inbound caller into one of four flows (Property 11).

    Pure function. No I/O.

    Args:
        employee_matched: ``True`` when the Lambda's
            PhoneNumberIndex GSI lookup against Employee_Master
            returned a visible record
            (:func:`shared.employee.visibility.is_visible`); ``False``
            otherwise.
        cycles: All Cycle records considered for selection. Each
            element is a DynamoDB-shaped dict with keys ``cycleId``,
            ``status`` (∈ :data:`_VALID_CYCLE_STATUSES`),
            ``startedAt`` (ISO 8601, tz-aware), and — for non-RUNNING
            statuses — ``completedAt`` (ISO 8601, tz-aware). The list
            may be empty.
        now: The wall-clock instant. Must be timezone-aware. Caller
            supplies it explicitly so the function stays a pure
            mapping (``datetime.now`` would inject hidden state).
        eligibility_window_days: Length of the receipt window in days
            (Requirements 13.5 / 13.6). Must be an ``int`` in
            ``[_MIN_ELIGIBILITY_WINDOW_DAYS, _MAX_ELIGIBILITY_WINDOW_DAYS]``
            (= [1, 365]). The CFn Parameter
            ``InboundReceptionWindowDays`` enforces a tighter Min=1 /
            Max=90 at deploy time; the looser runtime bound exists so
            unit tests can exercise larger windows without duplicating
            the deploy-time constraint here. ``bool`` is explicitly
            rejected even though ``isinstance(True, int)`` is true in
            Python — passing a bool here is almost certainly a caller
            bug.

    Returns:
        A 2-tuple ``(flow, cycleId_or_None)``. ``cycleId_or_None`` is
        the selected ``cycleId`` string for ``ACTIVE_CYCLE`` and
        ``CYCLE_TERMINATED`` flows; ``None`` for ``NOT_REGISTERED``
        and ``NO_CYCLE``.

    Raises:
        ValueError: when ``employee_matched`` is not a bool, ``cycles``
            is not a list, ``now`` is not a timezone-aware datetime,
            ``eligibility_window_days`` is not an int in the accepted
            range (project principle 19(b): no silent fallback to a
            default value), or any Cycle record fails
            :func:`_validate_cycle`.
    """
    if not isinstance(employee_matched, bool):
        raise ValueError(
            f"employee_matched must be a bool; got {type(employee_matched).__name__}"
        )
    if not isinstance(cycles, list):
        raise ValueError(f"cycles must be a list; got {type(cycles).__name__}")
    if not isinstance(now, dt.datetime) or now.tzinfo is None:
        raise ValueError(
            f"now must be a timezone-aware datetime; got {now!r}"
        )
    if (
        not isinstance(eligibility_window_days, int)
        or isinstance(eligibility_window_days, bool)
        or eligibility_window_days < _MIN_ELIGIBILITY_WINDOW_DAYS
        or eligibility_window_days > _MAX_ELIGIBILITY_WINDOW_DAYS
    ):
        raise ValueError(
            "eligibility_window_days must be an int in "
            f"[{_MIN_ELIGIBILITY_WINDOW_DAYS}, {_MAX_ELIGIBILITY_WINDOW_DAYS}]; "
            f"got {eligibility_window_days!r}"
        )

    if not employee_matched:
        # NOT_REGISTERED takes precedence: no Cycle lookup is even needed.
        return FLOW_NOT_REGISTERED, None

    # Construct the timedelta once per call rather than per cycle —
    # the value is constant within a single decision pass.
    window = dt.timedelta(days=eligibility_window_days)

    # Validate all records up-front so the rest of the function can
    # assume well-formed shapes. Validation cost is O(n) and ``n`` is
    # tiny (recent Cycles only).
    for cycle in cycles:
        _validate_cycle(cycle)

    # Step 1: any RUNNING Cycle wins. Multiple RUNNING records pick the
    # latest startedAt (Property 11 last paragraph).
    running = [c for c in cycles if c["status"] == CYCLE_STATUS_RUNNING]
    latest_running = _select_latest(running, key_attr="startedAt")
    if latest_running is not None:
        return FLOW_ACTIVE_CYCLE, latest_running["cycleId"]

    # Step 2: COMPLETED within the eligibility window wins next.
    completed_recent = [
        c
        for c in cycles
        if c["status"] == CYCLE_STATUS_COMPLETED
        and _is_within_window(c["completedAt"], now, window)
    ]
    latest_completed = _select_latest(completed_recent, key_attr="completedAt")
    if latest_completed is not None:
        return FLOW_ACTIVE_CYCLE, latest_completed["cycleId"]

    # Step 3: TIMEOUT / START_FAILED within the eligibility window
    # surface as CYCLE_TERMINATED. Older terminal Cycles do not
    # qualify — see the eligibility-window rationale above.
    terminated_recent = [
        c
        for c in cycles
        if c["status"] in _TERMINAL_STATUSES
        and _is_within_window(c["completedAt"], now, window)
    ]
    latest_terminated = _select_latest(terminated_recent, key_attr="completedAt")
    if latest_terminated is not None:
        return FLOW_CYCLE_TERMINATED, latest_terminated["cycleId"]

    # Step 4: nothing eligible.
    return FLOW_NO_CYCLE, None
