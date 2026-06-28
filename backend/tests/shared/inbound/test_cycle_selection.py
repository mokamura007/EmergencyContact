"""Unit tests for the pure inbound flow-decision function (Phase 9.2).

Phase 9.3 will add Hypothesis property tests against this same module
(Property 11). These unit tests cover representative examples for each
branch so regressions surface during ordinary CI runs, independent of
Phase 9.3 PBT.
"""

from __future__ import annotations

import datetime as dt

import pytest

from shared.inbound.cycle_selection import (
    CYCLE_STATUS_COMPLETED,
    CYCLE_STATUS_RUNNING,
    CYCLE_STATUS_START_FAILED,
    CYCLE_STATUS_TIMEOUT,
    FLOW_ACTIVE_CYCLE,
    FLOW_CYCLE_TERMINATED,
    FLOW_NO_CYCLE,
    FLOW_NOT_REGISTERED,
    decide_inbound_flow,
)

_NOW = dt.datetime(2026, 6, 25, 12, 0, 0, tzinfo=dt.UTC)

#: The window value historically baked into the module as
#: ``ELIGIBILITY_WINDOW``. Phase 15.27b lifted this to a per-call
#: argument; the existing 22 tests in this file assert the system's
#: production behaviour at the default 30-day window, so we keep the
#: same constant locally for boundary-fixture computation.
_DEFAULT_WINDOW_DAYS = 30
_ELIGIBILITY_WINDOW = dt.timedelta(days=_DEFAULT_WINDOW_DAYS)


def _iso(d: dt.datetime) -> str:
    return d.isoformat(timespec="seconds").replace("+00:00", "Z")


def _running(cycle_id: str, started_at: dt.datetime) -> dict[str, str]:
    return {
        "cycleId": cycle_id,
        "status": CYCLE_STATUS_RUNNING,
        "startedAt": _iso(started_at),
    }


def _completed(
    cycle_id: str, started_at: dt.datetime, completed_at: dt.datetime
) -> dict[str, str]:
    return {
        "cycleId": cycle_id,
        "status": CYCLE_STATUS_COMPLETED,
        "startedAt": _iso(started_at),
        "completedAt": _iso(completed_at),
    }


def _timeout(
    cycle_id: str, started_at: dt.datetime, completed_at: dt.datetime
) -> dict[str, str]:
    return {
        "cycleId": cycle_id,
        "status": CYCLE_STATUS_TIMEOUT,
        "startedAt": _iso(started_at),
        "completedAt": _iso(completed_at),
    }


def _start_failed(
    cycle_id: str, started_at: dt.datetime, completed_at: dt.datetime
) -> dict[str, str]:
    return {
        "cycleId": cycle_id,
        "status": CYCLE_STATUS_START_FAILED,
        "startedAt": _iso(started_at),
        "completedAt": _iso(completed_at),
    }


# --- NOT_REGISTERED -----------------------------------------------------


def test_not_matched_employee_returns_not_registered() -> None:
    flow, cycle_id = decide_inbound_flow(False, [], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_NOT_REGISTERED
    assert cycle_id is None


def test_not_matched_ignores_running_cycles() -> None:
    """Even with running cycles, an unmatched caller is NOT_REGISTERED."""
    cycles = [_running("c1", _NOW - dt.timedelta(hours=1))]
    flow, cycle_id = decide_inbound_flow(False, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_NOT_REGISTERED
    assert cycle_id is None


# --- NO_CYCLE -----------------------------------------------------------


def test_no_cycles_returns_no_cycle() -> None:
    flow, cycle_id = decide_inbound_flow(True, [], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_NO_CYCLE
    assert cycle_id is None


def test_only_old_completed_returns_no_cycle() -> None:
    """A COMPLETED cycle that finished > 30 days ago does not qualify."""
    old_completed = _NOW - dt.timedelta(days=31)
    cycles = [
        _completed("c1", old_completed - dt.timedelta(hours=1), old_completed),
    ]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_NO_CYCLE
    assert cycle_id is None


def test_only_old_timeout_returns_no_cycle() -> None:
    """A TIMEOUT cycle older than the eligibility window does not qualify."""
    old = _NOW - dt.timedelta(days=31)
    cycles = [_timeout("c1", old - dt.timedelta(hours=1), old)]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_NO_CYCLE
    assert cycle_id is None


# --- ACTIVE_CYCLE -------------------------------------------------------


def test_running_cycle_wins() -> None:
    cycles = [_running("c1", _NOW - dt.timedelta(hours=2))]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c1"


def test_running_picks_latest_when_multiple() -> None:
    older = _running("c_old", _NOW - dt.timedelta(hours=5))
    newer = _running("c_new", _NOW - dt.timedelta(hours=1))
    cycles = [older, newer]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c_new"


def test_recent_completed_returns_active_cycle() -> None:
    started = _NOW - dt.timedelta(days=5, hours=1)
    completed = _NOW - dt.timedelta(days=5)
    cycles = [_completed("c1", started, completed)]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c1"


def test_completed_picks_latest_by_completed_at() -> None:
    older = _completed(
        "c_old",
        _NOW - dt.timedelta(days=10, hours=1),
        _NOW - dt.timedelta(days=10),
    )
    newer = _completed(
        "c_new",
        _NOW - dt.timedelta(days=2, hours=1),
        _NOW - dt.timedelta(days=2),
    )
    flow, cycle_id = decide_inbound_flow(True, [older, newer], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c_new"


def test_running_wins_over_recent_completed() -> None:
    running = _running("c_running", _NOW - dt.timedelta(hours=10))
    completed = _completed(
        "c_completed",
        _NOW - dt.timedelta(days=1, hours=1),
        _NOW - dt.timedelta(hours=1),
    )
    flow, cycle_id = decide_inbound_flow(True, [completed, running], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c_running"


def test_completed_at_window_edge_is_eligible() -> None:
    """``completedAt + 30 days == now`` qualifies as ACTIVE_CYCLE."""
    completed = _NOW - _ELIGIBILITY_WINDOW
    cycles = [
        _completed("c1", completed - dt.timedelta(hours=1), completed),
    ]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c1"


# --- CYCLE_TERMINATED ---------------------------------------------------


def test_recent_timeout_returns_cycle_terminated() -> None:
    started = _NOW - dt.timedelta(days=2, hours=1)
    completed = _NOW - dt.timedelta(days=2)
    cycles = [_timeout("c1", started, completed)]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_CYCLE_TERMINATED
    assert cycle_id == "c1"


def test_recent_start_failed_returns_cycle_terminated() -> None:
    started = _NOW - dt.timedelta(hours=2)
    completed = _NOW - dt.timedelta(hours=1, minutes=30)
    cycles = [_start_failed("c1", started, completed)]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_CYCLE_TERMINATED
    assert cycle_id == "c1"


def test_active_cycle_wins_over_recent_timeout() -> None:
    """A RUNNING cycle should beat any recent terminal cycle."""
    running = _running("c_running", _NOW - dt.timedelta(hours=3))
    timeout = _timeout(
        "c_timeout",
        _NOW - dt.timedelta(hours=2, minutes=10),
        _NOW - dt.timedelta(hours=2),
    )
    flow, cycle_id = decide_inbound_flow(True, [timeout, running], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c_running"


def test_recent_completed_wins_over_recent_timeout() -> None:
    completed = _completed(
        "c_completed",
        _NOW - dt.timedelta(days=1, hours=1),
        _NOW - dt.timedelta(hours=4),
    )
    timeout = _timeout(
        "c_timeout",
        _NOW - dt.timedelta(hours=2, minutes=10),
        _NOW - dt.timedelta(hours=2),
    )
    flow, cycle_id = decide_inbound_flow(True, [completed, timeout], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c_completed"


def test_terminated_picks_latest_by_completed_at() -> None:
    older = _timeout(
        "c_old",
        _NOW - dt.timedelta(days=10, hours=1),
        _NOW - dt.timedelta(days=10),
    )
    newer = _start_failed(
        "c_new",
        _NOW - dt.timedelta(days=2, hours=1),
        _NOW - dt.timedelta(days=2),
    )
    flow, cycle_id = decide_inbound_flow(True, [older, newer], _NOW, _DEFAULT_WINDOW_DAYS)
    assert flow == FLOW_CYCLE_TERMINATED
    assert cycle_id == "c_new"


# --- Input validation ---------------------------------------------------


def test_non_bool_employee_matched_raises() -> None:
    with pytest.raises(ValueError, match="employee_matched"):
        decide_inbound_flow("yes", [], _NOW, _DEFAULT_WINDOW_DAYS)  # type: ignore[arg-type]


def test_non_list_cycles_raises() -> None:
    with pytest.raises(ValueError, match="cycles must be a list"):
        decide_inbound_flow(True, "not a list", _NOW, _DEFAULT_WINDOW_DAYS)  # type: ignore[arg-type]


def test_naive_now_raises() -> None:
    naive = dt.datetime(2026, 6, 25, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        decide_inbound_flow(True, [], naive, _DEFAULT_WINDOW_DAYS)


def test_unrecognised_cycle_status_raises() -> None:
    cycles = [
        {
            "cycleId": "c1",
            "status": "UNKNOWN_STATUS",
            "startedAt": _iso(_NOW - dt.timedelta(hours=1)),
        }
    ]
    with pytest.raises(ValueError, match=r"cycle\.status"):
        decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)


def test_completed_without_completed_at_raises() -> None:
    cycles = [
        {
            "cycleId": "c1",
            "status": CYCLE_STATUS_COMPLETED,
            "startedAt": _iso(_NOW - dt.timedelta(hours=2)),
        }
    ]
    with pytest.raises(ValueError, match="completedAt"):
        decide_inbound_flow(True, cycles, _NOW, _DEFAULT_WINDOW_DAYS)


# --- eligibility_window_days parameterisation (Phase 15.27b) -----------


def test_eligibility_window_60_days_extends_active() -> None:
    """A 60-day window keeps a 35-day-old COMPLETED in ACTIVE_CYCLE.

    Requirement 13.5: the receipt window is configurable via the CFn
    Parameter ``InboundReceptionWindowDays`` (1–90). At ``N = 60``,
    a COMPLETED finished 35 days ago has ``completedAt + 60 days =
    NOW + 25 days ≥ NOW``, so the boundary inclusivity rule keeps it
    eligible.
    """
    completed = _NOW - dt.timedelta(days=35)
    cycles = [_completed("c1", completed - dt.timedelta(hours=1), completed)]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, 60)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c1"


def test_eligibility_window_7_days_shortens_active() -> None:
    """A 7-day window pushes a 10-day-old COMPLETED to NO_CYCLE.

    Requirement 13.6 (complementary): at ``N = 7`` a COMPLETED finished
    10 days ago has ``completedAt + 7 days = NOW - 3 days < NOW`` and
    therefore exits the window — the caller falls through to NO_CYCLE.
    """
    completed = _NOW - dt.timedelta(days=10)
    cycles = [_completed("c1", completed - dt.timedelta(hours=1), completed)]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, 7)
    assert flow == FLOW_NO_CYCLE
    assert cycle_id is None


@pytest.mark.parametrize(
    "bad_value",
    [
        0,
        -1,
        -30,
        366,
        1.5,
        "30",
        None,
        True,  # bool is a subclass of int in Python; explicitly rejected.
        False,
    ],
)
def test_eligibility_window_invalid_raises(bad_value: object) -> None:
    """Out-of-range / non-int ``eligibility_window_days`` ⇒ ValueError.

    Principle 19(b): no silent fallback. The pure function enforces a
    runtime range guard of ``[1, 365]`` (the CFn Parameter's Min/Max =
    1/90 is the tighter deploy-time gate). Floats, strings, ``None``,
    and bools are rejected. Bools are explicitly listed because
    ``isinstance(True, int)`` is true in Python.
    """
    with pytest.raises(ValueError, match="eligibility_window_days"):
        decide_inbound_flow(True, [], _NOW, bad_value)  # type: ignore[arg-type]


def test_eligibility_window_boundary_at_window_size() -> None:
    """Boundary inclusivity at ``N = 30``: ``completedAt + 30 days == now``
    qualifies as ACTIVE_CYCLE.

    Requirement 13.5 phrasing "30 日以内" is interpreted as the
    inclusive ``>=`` boundary in :func:`_is_within_window`. The unit
    test :func:`test_completed_at_window_edge_is_eligible` covers the
    same boundary at the default window; this test re-asserts the rule
    via the new parameter path so any future refactor that accidentally
    converts ``>=`` to ``>`` is caught here too.
    """
    completed = _NOW - dt.timedelta(days=30)
    cycles = [_completed("c1", completed - dt.timedelta(hours=1), completed)]
    flow, cycle_id = decide_inbound_flow(True, cycles, _NOW, 30)
    assert flow == FLOW_ACTIVE_CYCLE
    assert cycle_id == "c1"
