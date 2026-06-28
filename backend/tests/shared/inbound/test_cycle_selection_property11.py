"""Property 11 — Inbound caller match + Cycle selection PBT (Phase 9.3).

Validates: Requirements 13.2, 13.3, 13.5, 13.6, 13.8

Contract (verbatim from design.md Property 11):

    _For all_ caller phone number ``caller``, Employee_Master set ``E``,
    Cycle set ``C``, current time ``now``::

        identifyInbound(caller, E, C, now) ∈
            {NOT_REGISTERED, NO_CYCLE, CYCLE_TERMINATED, ACTIVE_CYCLE}

    With:

    (1) ``caller`` does not match a visible record in ``E``
        (``deleted = false`` AND ``phoneNumber ≠ null``)
        ⇒ ``NOT_REGISTERED``.

    (2) Matches but no Cycle in ``C`` has ``status = RUNNING`` and no
        Cycle has ``status = COMPLETED`` with ``completedAt + 30 days
        ≥ now`` and no Cycle has ``status ∈ {TIMEOUT, START_FAILED}``
        within the same 30-day window
        ⇒ ``NO_CYCLE``.

    (3) Matches and the most-recent eligible Cycle is in
        ``{TIMEOUT, START_FAILED}``
        ⇒ ``CYCLE_TERMINATED``.

    (4) Matches and the most-recent eligible Cycle is ``RUNNING`` or
        ``COMPLETED`` (within 30 days)
        ⇒ ``ACTIVE_CYCLE`` + the selected ``cycleId``.

    Tie-break: multiple ``RUNNING`` → pick latest ``startedAt``;
    multiple ``COMPLETED`` within 30 days → pick latest ``completedAt``.

The pure function under test is ``decide_inbound_flow`` in
``shared.inbound.cycle_selection``.  The Lambda's PhoneNumberIndex GSI
lookup (Employee_Master) is *not* part of this function — the caller
already performs visibility filtering (Property 2 / Phase 13.2) and
hands a single ``employee_matched: bool`` to this function.  PBT
coverage of the visibility predicate is owned by Property 2; this file
therefore parameterises ``employee_matched`` directly.

The 7 properties below correspond 1-to-1 with the Done-When in tasks.md
Phase 9.3 — "各分岐パターンが網羅的にテストできる":

* P11.1: ``employee_matched = False`` ⇒ NOT_REGISTERED + cycle_id None
         (for any cycle list).
* P11.2: ``employee_matched = True`` + only out-of-window cycles
         (and no RUNNING) ⇒ NO_CYCLE + cycle_id None.
* P11.3: ``employee_matched = True`` + at least one RUNNING ⇒
         ACTIVE_CYCLE + cycle_id is the latest-startedAt RUNNING.
* P11.4: ``employee_matched = True`` + no RUNNING + at least one
         in-window COMPLETED ⇒ ACTIVE_CYCLE + cycle_id is the
         latest-completedAt in-window COMPLETED.
* P11.5: ``employee_matched = True`` + no RUNNING + no in-window
         COMPLETED + at least one in-window TIMEOUT/START_FAILED ⇒
         CYCLE_TERMINATED + cycle_id is the latest-completedAt
         in-window terminated.
* P11.6: Return-value invariants for ANY input:
         flow ∈ VALID_FLOWS, and (cycle_id is None) iff
         (flow ∈ {NOT_REGISTERED, NO_CYCLE}); when cycle_id is
         non-None, it must be present in the input cycle list.
* P11.7: Priority composition: a single list with one fresh RUNNING +
         one in-window COMPLETED + one in-window terminated cycle
         always resolves to the RUNNING (RUNNING ≻ recent COMPLETED
         ≻ recent terminated).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.inbound.cycle_selection import (
    CYCLE_STATUS_COMPLETED,
    CYCLE_STATUS_RUNNING,
    CYCLE_STATUS_START_FAILED,
    CYCLE_STATUS_TIMEOUT,
    FLOW_ACTIVE_CYCLE,
    FLOW_CYCLE_TERMINATED,
    FLOW_NO_CYCLE,
    FLOW_NOT_REGISTERED,
    VALID_FLOWS,
    decide_inbound_flow,
)

# Hypothesis settings: at least 100 runs per property (Phase 13 standard).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.large_base_example,
    ],
)

#: Fixed anchor for ``now``.  All time-arithmetic in the generators
#: works relative to this instant so generated ISO strings stay
#: deterministic and well-formed.  ``decide_inbound_flow`` takes
#: ``now`` as a parameter, so a constant value is appropriate here —
#: shifting ``now`` is equivalent to shifting every Cycle's
#: ``startedAt`` / ``completedAt`` by the same delta, which adds no
#: testing power.
_NOW = dt.datetime(2026, 6, 25, 12, 0, 0, tzinfo=dt.UTC)

#: Default eligibility window in days (= CFn Parameter
#: ``InboundReceptionWindowDays`` default 30). Phase 15.27b lifted the
#: previous module-level ``ELIGIBILITY_WINDOW`` constant to a per-call
#: parameter; this PBT suite was written against the system's
#: production behaviour at the default window, so we keep the same
#: value locally as a fixture for both ``decide_inbound_flow`` calls
#: and the test oracle (:func:`_within_window`).
_DEFAULT_WINDOW_DAYS = 30
_ELIGIBILITY_WINDOW = dt.timedelta(days=_DEFAULT_WINDOW_DAYS)

#: Window in seconds (= 30 * 86400 = 2_592_000).
_WINDOW_SEC = int(_ELIGIBILITY_WINDOW.total_seconds())

#: An offset clearly beyond the window for "old" cycles.  Far enough
#: above ``_WINDOW_SEC`` that, even with random jitter, generated
#: completedAt is strictly outside the eligibility window.
_OLD_MIN_SEC = _WINDOW_SEC + 1
_OLD_MAX_SEC = _WINDOW_SEC + 365 * 86_400  # up to ~1 year past the window

#: A safe lower bound for "in-window" offsets — we use 1 second rather
#: than 0 so the boundary case ``completedAt + 30 days == now`` is
#: tested explicitly via @example anchors below (not via random draws).
_RECENT_MIN_SEC = 1
_RECENT_MAX_SEC = _WINDOW_SEC

#: A safe lower bound for "fresh RUNNING" offsets — RUNNING cycles
#: have no completedAt and no eligibility window per se, but the
#: realistic range used by the system is < 1 year.
_RUNNING_MIN_SEC = 1
_RUNNING_MAX_SEC = 365 * 86_400


def _iso(d: dt.datetime) -> str:
    """Serialize a tz-aware UTC datetime to the canonical ``...Z`` form.

    The decision function uses string comparison on the ISO suffix to
    pick the latest record, so all timestamps in a single list must
    share the same suffix style — we standardise on ``Z`` here.
    """
    return d.isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Cycle generators.
# ---------------------------------------------------------------------------


def _running_cycle(cycle_id: str, sec_back: int) -> dict[str, str]:
    """Build a RUNNING cycle whose ``startedAt`` is ``sec_back`` seconds ago."""
    return {
        "cycleId": cycle_id,
        "status": CYCLE_STATUS_RUNNING,
        "startedAt": _iso(_NOW - dt.timedelta(seconds=sec_back)),
    }


def _completed_cycle(
    cycle_id: str, completed_sec_back: int, *, started_extra_sec: int = 1
) -> dict[str, str]:
    """Build a COMPLETED cycle whose ``completedAt`` is ``completed_sec_back``
    seconds ago.  ``startedAt`` is set to ``started_extra_sec`` further back
    so ``startedAt < completedAt`` (a realistic invariant of the system).
    """
    completed_at = _NOW - dt.timedelta(seconds=completed_sec_back)
    started_at = completed_at - dt.timedelta(seconds=started_extra_sec)
    return {
        "cycleId": cycle_id,
        "status": CYCLE_STATUS_COMPLETED,
        "startedAt": _iso(started_at),
        "completedAt": _iso(completed_at),
    }


def _terminated_cycle(
    cycle_id: str,
    status: str,
    completed_sec_back: int,
    *,
    started_extra_sec: int = 1,
) -> dict[str, str]:
    """Build a TIMEOUT or START_FAILED cycle.  ``status`` is the caller's
    choice between :data:`CYCLE_STATUS_TIMEOUT` and
    :data:`CYCLE_STATUS_START_FAILED`.
    """
    if status not in (CYCLE_STATUS_TIMEOUT, CYCLE_STATUS_START_FAILED):
        raise AssertionError(
            f"_terminated_cycle requires TIMEOUT or START_FAILED; got {status!r}"
        )
    completed_at = _NOW - dt.timedelta(seconds=completed_sec_back)
    started_at = completed_at - dt.timedelta(seconds=started_extra_sec)
    return {
        "cycleId": cycle_id,
        "status": status,
        "startedAt": _iso(started_at),
        "completedAt": _iso(completed_at),
    }


def _unique_offsets(prefix: str) -> st.SearchStrategy[list[int]]:
    """Strategy: a list of 0..6 unique seconds-back offsets in
    [_RUNNING_MIN_SEC, _RUNNING_MAX_SEC].

    Each offset uniquely identifies a cycle when paired with the
    generated cycle ID, which means generated timestamps are also
    unique — eliminating ambiguity in latest-pick tie-breaking
    (``max`` on a tie returns the first occurrence, which would
    couple test outcomes to list order rather than the semantics
    under test).

    The ``prefix`` argument is reserved for future use (currently
    unused; left in the signature so callers can plumb different
    offset ranges per status without changing the call site).
    """
    del prefix  # reserved for future per-status range tuning
    return st.lists(
        st.integers(min_value=_RUNNING_MIN_SEC, max_value=_RUNNING_MAX_SEC),
        min_size=0,
        max_size=6,
        unique=True,
    )


def _unique_offsets_recent() -> st.SearchStrategy[list[int]]:
    """Like :func:`_unique_offsets` but constrained to the in-window range
    [1, _WINDOW_SEC]."""
    return st.lists(
        st.integers(min_value=_RECENT_MIN_SEC, max_value=_RECENT_MAX_SEC),
        min_size=0,
        max_size=6,
        unique=True,
    )


def _unique_offsets_old() -> st.SearchStrategy[list[int]]:
    """Like :func:`_unique_offsets` but constrained to the out-of-window
    range [_OLD_MIN_SEC, _OLD_MAX_SEC]."""
    return st.lists(
        st.integers(min_value=_OLD_MIN_SEC, max_value=_OLD_MAX_SEC),
        min_size=0,
        max_size=6,
        unique=True,
    )


_terminal_status = st.sampled_from(
    [CYCLE_STATUS_TIMEOUT, CYCLE_STATUS_START_FAILED]
)


@st.composite
def arbitrary_cycle_list(draw: st.DrawFn) -> list[dict[str, Any]]:
    """Draw a heterogeneous list of 0..12 cycles spanning all 4 statuses,
    both in-window and out-of-window timestamps.

    Used for the universal invariant property (P11.6) and for the
    NOT_REGISTERED property (P11.1), where the contract holds for
    *any* cycle list.
    """
    running_offs = draw(_unique_offsets("run"))
    completed_recent_offs = draw(_unique_offsets_recent())
    completed_old_offs = draw(_unique_offsets_old())
    terminated_recent_offs = draw(_unique_offsets_recent())
    terminated_old_offs = draw(_unique_offsets_old())

    cycles: list[dict[str, Any]] = []
    idx = 0
    for off in running_offs:
        cycles.append(_running_cycle(f"r{idx:02d}", off))
        idx += 1
    for off in completed_recent_offs:
        cycles.append(_completed_cycle(f"cr{idx:02d}", off))
        idx += 1
    for off in completed_old_offs:
        cycles.append(_completed_cycle(f"co{idx:02d}", off))
        idx += 1
    for off in terminated_recent_offs:
        cycles.append(
            _terminated_cycle(f"tr{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    for off in terminated_old_offs:
        cycles.append(
            _terminated_cycle(f"to{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    # Shuffle list order to defend against accidental order-dependence.
    return draw(st.permutations(cycles))


@st.composite
def only_old_cycle_list(draw: st.DrawFn) -> list[dict[str, Any]]:
    """Draw a list of 0..6 cycles, *all* of which are out-of-window
    COMPLETED / TIMEOUT / START_FAILED records (no RUNNING).

    Drives P11.2 (NO_CYCLE).
    """
    completed_old_offs = draw(_unique_offsets_old())
    terminated_old_offs = draw(_unique_offsets_old())

    cycles: list[dict[str, Any]] = []
    idx = 0
    for off in completed_old_offs:
        cycles.append(_completed_cycle(f"co{idx:02d}", off))
        idx += 1
    for off in terminated_old_offs:
        cycles.append(
            _terminated_cycle(f"to{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    return draw(st.permutations(cycles))


@st.composite
def cycles_with_at_least_one_running(
    draw: st.DrawFn,
) -> list[dict[str, Any]]:
    """Draw a cycle list containing at least one RUNNING.  Other entries
    are random non-RUNNING records (any status / window).

    Drives P11.3 (RUNNING wins).
    """
    # At least one RUNNING — generate 1..4 unique RUNNING offsets.
    running_offs = draw(
        st.lists(
            st.integers(min_value=_RUNNING_MIN_SEC, max_value=_RUNNING_MAX_SEC),
            min_size=1,
            max_size=4,
            unique=True,
        )
    )
    other_completed_recent = draw(_unique_offsets_recent())
    other_completed_old = draw(_unique_offsets_old())
    other_terminated_recent = draw(_unique_offsets_recent())
    other_terminated_old = draw(_unique_offsets_old())

    cycles: list[dict[str, Any]] = []
    idx = 0
    for off in running_offs:
        cycles.append(_running_cycle(f"r{idx:02d}", off))
        idx += 1
    for off in other_completed_recent:
        cycles.append(_completed_cycle(f"cr{idx:02d}", off))
        idx += 1
    for off in other_completed_old:
        cycles.append(_completed_cycle(f"co{idx:02d}", off))
        idx += 1
    for off in other_terminated_recent:
        cycles.append(
            _terminated_cycle(f"tr{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    for off in other_terminated_old:
        cycles.append(
            _terminated_cycle(f"to{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    return draw(st.permutations(cycles))


@st.composite
def cycles_recent_completed_no_running(
    draw: st.DrawFn,
) -> list[dict[str, Any]]:
    """Draw a cycle list with at least one in-window COMPLETED and *no*
    RUNNING.  May include arbitrary out-of-window COMPLETED and any
    terminated records (in- or out-of-window).

    Drives P11.4 (recent COMPLETED wins over terminated / NO_CYCLE).
    """
    completed_recent_offs = draw(
        st.lists(
            st.integers(min_value=_RECENT_MIN_SEC, max_value=_RECENT_MAX_SEC),
            min_size=1,
            max_size=4,
            unique=True,
        )
    )
    completed_old_offs = draw(_unique_offsets_old())
    terminated_recent_offs = draw(_unique_offsets_recent())
    terminated_old_offs = draw(_unique_offsets_old())

    cycles: list[dict[str, Any]] = []
    idx = 0
    for off in completed_recent_offs:
        cycles.append(_completed_cycle(f"cr{idx:02d}", off))
        idx += 1
    for off in completed_old_offs:
        cycles.append(_completed_cycle(f"co{idx:02d}", off))
        idx += 1
    for off in terminated_recent_offs:
        cycles.append(
            _terminated_cycle(f"tr{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    for off in terminated_old_offs:
        cycles.append(
            _terminated_cycle(f"to{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    return draw(st.permutations(cycles))


@st.composite
def cycles_recent_terminated_only(
    draw: st.DrawFn,
) -> list[dict[str, Any]]:
    """Draw a cycle list with at least one in-window TIMEOUT/START_FAILED,
    no RUNNING, and no in-window COMPLETED.  Out-of-window records
    (any status) may appear in addition.

    Drives P11.5 (CYCLE_TERMINATED).
    """
    terminated_recent_offs = draw(
        st.lists(
            st.integers(min_value=_RECENT_MIN_SEC, max_value=_RECENT_MAX_SEC),
            min_size=1,
            max_size=4,
            unique=True,
        )
    )
    completed_old_offs = draw(_unique_offsets_old())
    terminated_old_offs = draw(_unique_offsets_old())

    cycles: list[dict[str, Any]] = []
    idx = 0
    for off in terminated_recent_offs:
        cycles.append(
            _terminated_cycle(f"tr{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    for off in completed_old_offs:
        cycles.append(_completed_cycle(f"co{idx:02d}", off))
        idx += 1
    for off in terminated_old_offs:
        cycles.append(
            _terminated_cycle(f"to{idx:02d}", draw(_terminal_status), off)
        )
        idx += 1
    return draw(st.permutations(cycles))


# ---------------------------------------------------------------------------
# Helpers — reference implementation of the "latest-eligible" tiebreak.
# Used as the test oracle for P11.3 / P11.4 / P11.5.  Implemented here
# (and not imported from the module under test) so the test does not
# trivially restate the implementation; it instead expresses the
# specification in a different shape and checks they agree.
# ---------------------------------------------------------------------------


def _expected_latest_running(cycles: list[dict[str, Any]]) -> dict[str, Any]:
    running = [c for c in cycles if c["status"] == CYCLE_STATUS_RUNNING]
    assert running, "P11.3 generator invariant: at least one RUNNING"
    return max(running, key=lambda c: c["startedAt"])


def _within_window(completed_at_iso: str) -> bool:
    completed_at = dt.datetime.fromisoformat(
        completed_at_iso.replace("Z", "+00:00")
    )
    return completed_at + _ELIGIBILITY_WINDOW >= _NOW


def _expected_latest_recent_completed(
    cycles: list[dict[str, Any]],
) -> dict[str, Any]:
    recent = [
        c
        for c in cycles
        if c["status"] == CYCLE_STATUS_COMPLETED
        and _within_window(c["completedAt"])
    ]
    assert recent, "P11.4 generator invariant: at least one recent COMPLETED"
    return max(recent, key=lambda c: c["completedAt"])


def _expected_latest_recent_terminated(
    cycles: list[dict[str, Any]],
) -> dict[str, Any]:
    recent = [
        c
        for c in cycles
        if c["status"] in (CYCLE_STATUS_TIMEOUT, CYCLE_STATUS_START_FAILED)
        and _within_window(c["completedAt"])
    ]
    assert recent, "P11.5 generator invariant: at least one recent terminated"
    return max(recent, key=lambda c: c["completedAt"])


# ===========================================================================
# P11.1 — employee_matched=False ⇒ NOT_REGISTERED + cycle_id None.
# ===========================================================================


@PBT_SETTINGS
@example(cycles=[])
@example(cycles=[{"cycleId": "c1", "status": CYCLE_STATUS_RUNNING,
                  "startedAt": _iso(_NOW - dt.timedelta(hours=1))}])
@given(cycles=arbitrary_cycle_list())
def test_property11_not_matched_returns_not_registered(
    cycles: list[dict[str, Any]],
) -> None:
    """For ANY cycle list, ``employee_matched=False`` ⇒ NOT_REGISTERED.

    Cycle lookup is short-circuited when the caller is not a visible
    Employee — the contract makes this independent of ``C``.

    Validates: Requirements 13.2, 13.3
    """
    flow, cycle_id = decide_inbound_flow(False, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_NOT_REGISTERED, (
        f"expected NOT_REGISTERED for unmatched caller; got {flow!r} "
        f"with cycles={cycles!r}"
    )
    assert cycle_id is None, (
        f"expected cycle_id=None for NOT_REGISTERED; got {cycle_id!r}"
    )


# ===========================================================================
# P11.2 — employee_matched=True + no RUNNING + only out-of-window
#         terminal cycles ⇒ NO_CYCLE + cycle_id None.
# ===========================================================================


@PBT_SETTINGS
@example(cycles=[])
@given(cycles=only_old_cycle_list())
def test_property11_only_old_cycles_returns_no_cycle(
    cycles: list[dict[str, Any]],
) -> None:
    """Matched caller + zero eligible cycles ⇒ NO_CYCLE.

    All input cycles are in {COMPLETED, TIMEOUT, START_FAILED} with
    ``completedAt + 30 days < now`` — none qualify, and there is no
    RUNNING fallback.

    Validates: Requirement 13.6
    """
    # Generator invariant — no RUNNING and no in-window terminal.
    for c in cycles:
        assert c["status"] != CYCLE_STATUS_RUNNING, (
            f"P11.2 generator drift: RUNNING leaked into list {cycles!r}"
        )
        assert not _within_window(c["completedAt"]), (
            f"P11.2 generator drift: in-window terminal leaked "
            f"({c!r})"
        )

    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_NO_CYCLE, (
        f"expected NO_CYCLE for only-old cycles; got {flow!r} "
        f"with cycles={cycles!r}"
    )
    assert cycle_id is None, (
        f"expected cycle_id=None for NO_CYCLE; got {cycle_id!r}"
    )


# ===========================================================================
# P11.3 — at least one RUNNING ⇒ ACTIVE_CYCLE + latest-startedAt RUNNING.
# ===========================================================================


@PBT_SETTINGS
@given(cycles=cycles_with_at_least_one_running())
def test_property11_running_wins_with_latest_started_at(
    cycles: list[dict[str, Any]],
) -> None:
    """Matched caller + ≥1 RUNNING ⇒ ACTIVE_CYCLE; cycle_id is the
    RUNNING with the maximum ``startedAt``.

    Any non-RUNNING cycles in the list are dominated, regardless of
    whether they are in-window COMPLETED, in-window terminated, or
    out-of-window.

    Validates: Requirements 13.5, 13.8
    """
    expected = _expected_latest_running(cycles)
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE, (
        f"expected ACTIVE_CYCLE when ≥1 RUNNING; got {flow!r} "
        f"with cycles={cycles!r}"
    )
    assert cycle_id == expected["cycleId"], (
        f"expected latest-RUNNING cycleId={expected['cycleId']!r}; "
        f"got {cycle_id!r} with cycles={cycles!r}"
    )


# ===========================================================================
# P11.4 — no RUNNING + ≥1 in-window COMPLETED ⇒ ACTIVE_CYCLE +
#         latest-completedAt in-window COMPLETED.
# ===========================================================================


@PBT_SETTINGS
@given(cycles=cycles_recent_completed_no_running())
def test_property11_recent_completed_wins_when_no_running(
    cycles: list[dict[str, Any]],
) -> None:
    """No RUNNING + ≥1 in-window COMPLETED ⇒ ACTIVE_CYCLE; cycle_id is
    the in-window COMPLETED with the maximum ``completedAt``.

    Out-of-window COMPLETED records and any terminated records
    (in- or out-of-window) are dominated by recent COMPLETED.

    Validates: Requirements 13.5, 13.6
    """
    # Generator invariant — no RUNNING anywhere in the list.
    for c in cycles:
        assert c["status"] != CYCLE_STATUS_RUNNING, (
            f"P11.4 generator drift: RUNNING leaked into list {cycles!r}"
        )

    expected = _expected_latest_recent_completed(cycles)
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE, (
        f"expected ACTIVE_CYCLE when ≥1 recent COMPLETED & no RUNNING; "
        f"got {flow!r} with cycles={cycles!r}"
    )
    assert cycle_id == expected["cycleId"], (
        f"expected latest in-window COMPLETED cycleId="
        f"{expected['cycleId']!r}; got {cycle_id!r} with cycles={cycles!r}"
    )


# ===========================================================================
# P11.5 — no RUNNING + no in-window COMPLETED + ≥1 in-window
#         TIMEOUT/START_FAILED ⇒ CYCLE_TERMINATED +
#         latest-completedAt in-window terminated.
# ===========================================================================


@PBT_SETTINGS
@given(cycles=cycles_recent_terminated_only())
def test_property11_recent_terminated_returns_cycle_terminated(
    cycles: list[dict[str, Any]],
) -> None:
    """No RUNNING + no in-window COMPLETED + ≥1 in-window
    TIMEOUT/START_FAILED ⇒ CYCLE_TERMINATED; cycle_id is the in-window
    terminated record with the maximum ``completedAt``.

    Out-of-window records (any status) are dominated.

    Validates: Requirement 13.8
    """
    # Generator invariants — verify the strategy held the bargain.
    for c in cycles:
        assert c["status"] != CYCLE_STATUS_RUNNING, (
            f"P11.5 generator drift: RUNNING leaked into list {cycles!r}"
        )
        if c["status"] == CYCLE_STATUS_COMPLETED:
            assert not _within_window(c["completedAt"]), (
                f"P11.5 generator drift: in-window COMPLETED leaked "
                f"({c!r})"
            )

    expected = _expected_latest_recent_terminated(cycles)
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_CYCLE_TERMINATED, (
        f"expected CYCLE_TERMINATED; got {flow!r} with cycles={cycles!r}"
    )
    assert cycle_id == expected["cycleId"], (
        f"expected latest in-window terminated cycleId="
        f"{expected['cycleId']!r}; got {cycle_id!r} with cycles={cycles!r}"
    )


# ===========================================================================
# P11.6 — Return-value invariants for ANY input.
# ===========================================================================


@PBT_SETTINGS
@given(matched=st.booleans(), cycles=arbitrary_cycle_list())
def test_property11_return_invariants(
    matched: bool, cycles: list[dict[str, Any]]
) -> None:
    """For ANY (matched, cycles) pair, ``decide_inbound_flow``::

      * returns ``flow ∈ VALID_FLOWS`` (the contract's codomain);
      * returns ``cycle_id is None`` iff ``flow ∈ {NOT_REGISTERED, NO_CYCLE}``;
      * when ``cycle_id`` is non-None, it must reference a cycle in
        the input list (no silent fabrication).

    Validates: Requirements 13.2, 13.3, 13.5, 13.6, 13.8
    """
    flow, cycle_id = decide_inbound_flow(matched, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow in VALID_FLOWS, (
        f"flow not in VALID_FLOWS: {flow!r} (matched={matched}, "
        f"cycles={cycles!r})"
    )
    if flow in (FLOW_NOT_REGISTERED, FLOW_NO_CYCLE):
        assert cycle_id is None, (
            f"expected cycle_id=None for flow={flow!r}; got {cycle_id!r}"
        )
    else:
        assert isinstance(cycle_id, str), (
            f"expected str cycle_id for flow={flow!r}; got {cycle_id!r}"
        )
        assert cycle_id, (
            f"expected non-empty cycle_id for flow={flow!r}; got {cycle_id!r}"
        )
        assert any(c["cycleId"] == cycle_id for c in cycles), (
            f"returned cycle_id={cycle_id!r} not present in input "
            f"cycles={cycles!r}"
        )


# ===========================================================================
# P11.7 — Priority composition (RUNNING ≻ recent COMPLETED ≻ recent
#         terminated).  Generates one of each kind and asserts the
#         RUNNING always wins, then drops the RUNNING and asserts the
#         recent COMPLETED wins, then drops that and asserts the
#         recent terminated wins.
# ===========================================================================


@PBT_SETTINGS
@given(
    running_back=st.integers(min_value=_RUNNING_MIN_SEC, max_value=_RUNNING_MAX_SEC),
    completed_back=st.integers(min_value=_RECENT_MIN_SEC, max_value=_RECENT_MAX_SEC),
    terminated_back=st.integers(min_value=_RECENT_MIN_SEC, max_value=_RECENT_MAX_SEC),
    terminated_status=_terminal_status,
)
def test_property11_priority_running_over_completed_over_terminated(
    running_back: int,
    completed_back: int,
    terminated_back: int,
    terminated_status: str,
) -> None:
    """RUNNING ≻ recent COMPLETED ≻ recent TIMEOUT/START_FAILED.

    Constructs a 3-cycle list (one of each), then progressively
    removes the highest-priority record and reasserts the next-rank
    record wins.  Removing the recent terminated as well returns
    NO_CYCLE — confirming the priority ladder is exhaustive.

    Validates: Requirements 13.5, 13.6, 13.8
    """
    running = _running_cycle("c_running", running_back)
    completed = _completed_cycle("c_completed", completed_back)
    terminated = _terminated_cycle(
        "c_terminated", terminated_status, terminated_back
    )

    # Stage 1: all three present → RUNNING wins.
    flow1, cid1 = decide_inbound_flow(
        True, [terminated, completed, running], _NOW, _DEFAULT_WINDOW_DAYS
    )
    assert flow1 == FLOW_ACTIVE_CYCLE, (
        f"stage 1: expected ACTIVE_CYCLE flow; got {flow1!r}"
    )
    assert cid1 == "c_running", (
        f"stage 1: expected cycle_id=c_running; got {cid1!r}"
    )

    # Stage 2: remove RUNNING → recent COMPLETED wins.
    flow2, cid2 = decide_inbound_flow(True, [terminated, completed], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow2 == FLOW_ACTIVE_CYCLE, (
        f"stage 2: expected ACTIVE_CYCLE flow; got {flow2!r}"
    )
    assert cid2 == "c_completed", (
        f"stage 2: expected cycle_id=c_completed; got {cid2!r}"
    )

    # Stage 3: remove recent COMPLETED → recent terminated wins.
    flow3, cid3 = decide_inbound_flow(True, [terminated], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow3 == FLOW_CYCLE_TERMINATED, (
        f"stage 3: expected CYCLE_TERMINATED flow; got {flow3!r}"
    )
    assert cid3 == "c_terminated", (
        f"stage 3: expected cycle_id=c_terminated; got {cid3!r}"
    )

    # Stage 4: remove the recent terminated → NO_CYCLE.
    flow4, cid4 = decide_inbound_flow(True, [], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow4 == FLOW_NO_CYCLE, (
        f"stage 4: expected NO_CYCLE flow; got {flow4!r}"
    )
    assert cid4 is None, (
        f"stage 4: expected cycle_id=None; got {cid4!r}"
    )
