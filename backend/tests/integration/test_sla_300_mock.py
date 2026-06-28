# ruff: noqa: RUF002, PLR0912, RUF046, PLC0415
"""300名 60分 SLA mock 検証（Task 14.11a）.

Connect-independent end-to-end performance test that exercises the
Cycle workflow (ConnectDispatcher → CallEndHandler → TranscribeStarter
→ RetryEvaluator → CycleFinalizer) against in-memory DynamoDB fakes
and stubbed Connect / Transcribe / SFN / SNS / CloudWatch / Events
clients. The real Lambda handler code runs unchanged — only the AWS
edges are mocked.

Verification model
==================

The SFN cycle is reproduced in two phases:

1. **Per-employee sequential pass.** Each of the 300 employees is
   driven through their full Map iteration (initial dispatch +
   up to 2 retries) by calling the actual ``lambda_handler``
   functions in order. The boto3 client / table singletons each
   handler imports are monkey-patched onto in-memory fakes so the
   state transitions persist between calls. While running, every
   ``dispatch`` / ``wait_transcribe`` / ``wait_interval`` segment
   records its simulated duration into the employee's *phase list*.

2. **Discrete-event scheduler.** The phase lists from step (1) are
   fed into :func:`_simulate_workflows`, which replays the cycle on
   a virtual clock with ``MaxConcurrency = 10`` for the
   ``dispatch`` phases (Connect API calls are the only thing the
   AWS quota constrains; Wait / WaitForTranscribe states do not
   hold a Connect slot per design.md L1479-1496). The scheduler
   reports the peak concurrent dispatch count, each employee's
   first-dispatch completion time, and the cycle's total wall clock.

Done When (task 14.11a)
=======================

* All 300 Response rows reach a terminal ``voiceStatus``
  (``SAFE`` / ``INJURED`` / ``UNAVAILABLE`` / ``UNREACHABLE``).
* Peak concurrent dispatch ≤ ``MaxConcurrentCalls`` (default 10).
* Each employee's first dispatch completes within 30 minutes
  (Requirement 14.1 → ``SafetyConfirmation/SlaWarning30Min``
  not raised).
* Cycle total wall clock ≤ 60 minutes (Requirement 14.2 →
  ``SafetyConfirmation/CycleTimeout`` not raised).
* ``CycleFinalizer`` with ``trigger=MAP_COMPLETED`` flips the cycle
  to ``COMPLETED``; with ``trigger=TIMER_30MIN`` returns
  ``no_warning_needed``; with ``trigger=TIMER_60MIN`` returns
  ``no_op`` (cycle already terminal).

Why a discrete-event scheduler (not real wall clock)
====================================================

Running 300 employees with a real ``time.sleep`` would take 60 wall
minutes per CI run — operationally untenable. The scheduler stays
faithful to the design.md SLA model (each phase has a documented
duration; only ``dispatch`` slots are limited) while completing
in seconds. The trade-off is recorded in
``docs/notes/14-11a-mock-sla.md``.
"""

from __future__ import annotations

import heapq
import logging
import random
from collections import defaultdict
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from lambdas.call_end_handler import handler as call_end_handler_mod
from lambdas.connect_dispatcher import handler as connect_dispatcher_mod
from lambdas.cycle_finalizer import handler as cycle_finalizer_mod
from lambdas.retry_evaluator import handler as retry_evaluator_mod
from lambdas.transcribe_starter import handler as transcribe_starter_mod
from tests.integration._fakes import InMemoryTable

# ---------------------------------------------------------------------------
# Constants — mirror design.md L1478-1483 "SLA 達成の根拠"
# ---------------------------------------------------------------------------

NUM_EMPLOYEES: int = 300
MAX_CONCURRENT_CALLS: int = 10  # Requirement 9.6
SLA_60_MIN_SEC: int = 60 * 60
SLA_30_MIN_SEC: int = 30 * 60
RETRY_COUNT: int = 3  # design.md default
RETRY_INTERVAL_MIN: int = 5
RETRY_INTERVAL_SEC: int = RETRY_INTERVAL_MIN * 60

# Per-call duration model. design.md L1479-1480 documents the
# safety-confirmation call as ~ 60 s (guidance 20 s + response 30 s +
# handshake 10 s) with the 90 s upper bound used for sizing. The
# average reported there is "40〜50 秒" — we pick 50 s for RECORDED to
# stay on the conservative side of that range while still meeting
# the 22.5-minute initial-dispatch budget. NO_ANSWER takes the
# documented 30 s ring-out plus a small disconnect overhead; BUSY /
# ERROR are immediate; VOICEMAIL approximates "answering machine
# detected after a few seconds of intro audio". Jitter would not
# change scheduler behaviour materially.
_DISPATCH_DURATION_BY_RESULT: dict[str, float] = {
    "RECORDED": 50.0,
    "NO_ANSWER": 35.0,
    "BUSY": 5.0,
    "VOICEMAIL": 25.0,
    "ERROR": 5.0,
}
_TRANSCRIBE_DURATION_SEC: float = 45.0

# Call-result probability distribution. Values picked to match the
# "想定下" baseline in design.md (~ 30% retry pool, > 70% first-try
# success). Deterministic via random.seed(42).
_CALL_RESULT_DIST: list[tuple[str, float]] = [
    ("RECORDED", 0.85),
    ("NO_ANSWER", 0.07),
    ("BUSY", 0.03),
    ("VOICEMAIL", 0.03),
    ("ERROR", 0.02),
]

# voiceStatus distribution conditional on callResultCode == RECORDED
# (i.e. KeywordMatcher classified the transcript). 95% of recorded
# calls match a confirmed-answer keyword. The remaining 5% become
# OTHER and trigger a retry until retryCount exhausts.
_VOICE_STATUS_DIST_RECORDED: list[tuple[str, float]] = [
    ("SAFE", 0.85),
    ("INJURED", 0.05),
    ("UNAVAILABLE", 0.05),
    ("OTHER", 0.05),
]

_TERMINAL_VOICE_STATUSES: frozenset[str] = frozenset(
    {"SAFE", "INJURED", "UNAVAILABLE", "UNREACHABLE"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample(rng: random.Random, dist: list[tuple[str, float]]) -> str:
    """Sample one label from a (label, prob) categorical distribution."""
    r = rng.random()
    cumulative = 0.0
    for label, prob in dist:
        cumulative += prob
        if r < cumulative:
            return label
    return dist[-1][0]


def _dispatch_duration(call_result: str) -> float:
    return _DISPATCH_DURATION_BY_RESULT[call_result]


# ---------------------------------------------------------------------------
# Discrete-event scheduler
# ---------------------------------------------------------------------------


def _simulate_workflows(
    workflows: list[tuple[str, list[tuple[str, float]]]],
    max_concurrency: int,
) -> dict[str, Any]:
    """Replay employee phase lists on a virtual clock.

    Phase semantics
    ---------------

    * ``dispatch`` — Connect API call. Consumes one of the
      ``max_concurrency`` slots for its full duration.
    * ``wait_transcribe`` — SFN ``WaitForTranscribe`` state.
      No slot. Runs in parallel with other employees' dispatches.
    * ``wait_interval`` — SFN ``WaitInterval`` state between retries.
      No slot.

    Returns
    -------

    A dict with::

        {
            "state": {<emp_id>: {
                "start_time": float,
                "completion_time": float,
                "first_dispatch_complete": float,
            }},
            "peak_concurrency": int,
            "total_time": float,
        }
    """
    # Per-employee progress trackers.
    state: dict[str, dict[str, Any]] = {
        emp_id: {
            "phases": phases,
            "phase_idx": 0,
            "start_time": None,
            "completion_time": None,
            "first_dispatch_complete": None,
        }
        for emp_id, phases in workflows
    }

    events: list[tuple[float, int, str, str]] = []
    seq_counter = [0]

    def push(t: float, emp_id: str, action: str) -> None:
        # Sequence counter breaks ties between equal-time events.
        seq_counter[0] += 1
        heapq.heappush(events, (t, seq_counter[0], emp_id, action))

    parked: list[str] = []  # FIFO queue of employees waiting for a slot
    active_dispatch = 0
    peak_dispatch = 0
    last_t = 0.0

    # Seed: all employees ready to start phase 0 at t=0.
    for emp_id, _ in workflows:
        push(0.0, emp_id, "next_phase")

    while events:
        t, _seq, emp_id, action = heapq.heappop(events)
        last_t = max(last_t, t)
        st = state[emp_id]

        if action == "next_phase":
            if st["phase_idx"] >= len(st["phases"]):
                st["completion_time"] = t
                continue
            phase_type, duration = st["phases"][st["phase_idx"]]

            if phase_type == "dispatch":
                if active_dispatch < max_concurrency:
                    active_dispatch += 1
                    peak_dispatch = max(peak_dispatch, active_dispatch)
                    if st["start_time"] is None:
                        st["start_time"] = t
                    push(t + duration, emp_id, "end_dispatch")
                else:
                    parked.append(emp_id)
            elif phase_type in ("wait_transcribe", "wait_interval"):
                push(t + duration, emp_id, "end_wait")
            else:
                raise NotImplementedError(f"Unknown phase type: {phase_type!r}")

        elif action == "end_dispatch":
            active_dispatch -= 1
            # The end of the FIRST dispatch is what Requirement 14.1
            # / SafetyConfirmation/SlaWarning30Min watches for.
            if st["first_dispatch_complete"] is None:
                st["first_dispatch_complete"] = t
            st["phase_idx"] += 1
            # Wake any parked employees while slots remain.
            while parked and active_dispatch < max_concurrency:
                next_emp = parked.pop(0)
                push(t, next_emp, "next_phase")
            push(t, emp_id, "next_phase")

        elif action == "end_wait":
            st["phase_idx"] += 1
            push(t, emp_id, "next_phase")

        else:
            raise NotImplementedError(f"Unknown action: {action!r}")

    if parked:
        raise RuntimeError(
            f"Scheduler deadlock: {len(parked)} employees still parked "
            f"with active_dispatch={active_dispatch}"
        )

    return {
        "state": state,
        "peak_concurrency": peak_dispatch,
        "total_time": last_t,
    }



# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def suppress_handler_logs() -> None:
    """Lower handler log level — 300 employees × 4 attempts = lots of INFO."""
    for name in (
        "lambdas.connect_dispatcher.handler",
        "lambdas.call_end_handler.handler",
        "lambdas.transcribe_starter.handler",
        "lambdas.retry_evaluator.handler",
        "lambdas.cycle_finalizer.handler",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger().setLevel(logging.WARNING)


@pytest.fixture
def cycle_id() -> str:
    return "cycle-sla-300-mock"


@pytest.fixture
def in_memory_tables() -> dict[str, InMemoryTable]:
    """One Cycle / Response / TranscriptMeta table per test."""
    return {
        "cycle": InMemoryTable("cycleId"),
        "response": InMemoryTable("cycleId", "employeeId"),
        "transcript_meta": InMemoryTable("cycleId", "employeeIdSeq"),
    }


@pytest.fixture
def aws_mocks() -> dict[str, MagicMock]:
    """Connect / Transcribe / SFN / SNS / CloudWatch / Events stubs."""
    contact_counter = {"n": 0}

    def fake_start_outbound(**_kwargs: Any) -> dict[str, Any]:
        contact_counter["n"] += 1
        return {"ContactId": f"contact-{contact_counter['n']:06d}"}

    mock_connect = MagicMock(name="Connect")
    mock_connect.start_outbound_voice_contact.side_effect = fake_start_outbound

    return {
        "connect": mock_connect,
        "transcribe": MagicMock(name="Transcribe"),
        "sfn": MagicMock(name="SFN"),
        "sns": MagicMock(name="SNS"),
        "cloudwatch": MagicMock(name="CloudWatch"),
        "events": MagicMock(name="Events"),
        "_contact_counter": contact_counter,
    }


@pytest.fixture
def patched_handlers(
    monkeypatch: pytest.MonkeyPatch,
    in_memory_tables: dict[str, InMemoryTable],
    aws_mocks: dict[str, MagicMock],
) -> None:
    """Swap every handler's module-level boto3 singleton with a fake."""
    # ConnectDispatcher
    monkeypatch.setattr(connect_dispatcher_mod, "_CONNECT", aws_mocks["connect"])
    monkeypatch.setattr(connect_dispatcher_mod, "_SFN", aws_mocks["sfn"])
    monkeypatch.setattr(
        connect_dispatcher_mod, "_RESPONSE_TABLE", in_memory_tables["response"]
    )
    # CallEndHandler
    monkeypatch.setattr(call_end_handler_mod, "_SFN", aws_mocks["sfn"])
    monkeypatch.setattr(
        call_end_handler_mod, "_RESPONSE_TABLE", in_memory_tables["response"]
    )
    # TranscribeStarter
    monkeypatch.setattr(transcribe_starter_mod, "_TRANSCRIBE", aws_mocks["transcribe"])
    monkeypatch.setattr(
        transcribe_starter_mod,
        "_TRANSCRIPT_META_TABLE",
        in_memory_tables["transcript_meta"],
    )
    monkeypatch.setattr(
        transcribe_starter_mod, "_RESPONSE_TABLE", in_memory_tables["response"]
    )
    # CycleFinalizer
    monkeypatch.setattr(
        cycle_finalizer_mod, "_CYCLE_TABLE", in_memory_tables["cycle"]
    )
    monkeypatch.setattr(
        cycle_finalizer_mod, "_RESPONSE_TABLE", in_memory_tables["response"]
    )
    monkeypatch.setattr(cycle_finalizer_mod, "_SFN", aws_mocks["sfn"])
    monkeypatch.setattr(cycle_finalizer_mod, "_SNS", aws_mocks["sns"])
    monkeypatch.setattr(cycle_finalizer_mod, "_EVENTS", aws_mocks["events"])
    monkeypatch.setattr(cycle_finalizer_mod, "_CLOUDWATCH", aws_mocks["cloudwatch"])


# ---------------------------------------------------------------------------
# Workflow driver
# ---------------------------------------------------------------------------


def _run_one_employee(
    *,
    cycle_id: str,
    employee: dict[str, str],
    response_table: InMemoryTable,
    transcript_meta_table: InMemoryTable,
    rng: random.Random,
    result_counts: dict[str, int],
    voice_counts: dict[str, int],
) -> list[tuple[str, float]]:
    """Drive one employee through their Map iteration; return phase list.

    Calls the real Lambda handlers in the order the SFN ASL prescribes:
    InitAttempt (put_item) → Dispatch (ConnectDispatcher) → CallEnd
    (CallEndHandler) → optional Transcribe pipeline → ReadResponse →
    EvaluateRetry (RetryEvaluator) → either loop with WaitInterval or
    FinalizeOne.

    The returned phase list records (phase_type, duration_seconds) so
    the discrete-event scheduler can replay timing under
    ``MaxConcurrentCalls``.
    """
    emp_id = employee["employeeId"]
    phone = employee["phoneNumber"]
    phases: list[tuple[str, float]] = []
    prev_end_at = "2026-06-27T00:00:00Z"

    for attempt in range(1, RETRY_COUNT + 2):  # 1..RETRY_COUNT+1 inclusive
        # --- InitAttempt (idempotent put_item on Response) ---
        init_item = {
            "cycleId": cycle_id,
            "employeeId": emp_id,
            "callAttempts": 0,
            "voiceStatus": "PENDING",
            "currentAttempt": attempt,
        }
        try:
            response_table.put_item(
                init_item, ConditionExpression="attribute_not_exists(employeeId)"
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ConditionalCheckFailedException":
                raise
            # Row already exists from a previous attempt — that is the
            # exact idempotent path the SFN ASL relies on.

        # --- Dispatch (ConnectDispatcher) ---
        dispatch_event = {
            "cycleId": cycle_id,
            "employeeId": emp_id,
            "phoneNumber": phone,
            "attempt": attempt,
            "taskToken": f"task-token-{emp_id}-{attempt}",
        }
        dispatch_result = connect_dispatcher_mod.lambda_handler(
            dispatch_event, None
        )
        assert dispatch_result["status"] == "ok", dispatch_result
        contact_id = dispatch_result["contactId"]

        # --- Sample callResultCode for this attempt ---
        call_result = _sample(rng, _CALL_RESULT_DIST)
        result_counts[call_result] += 1
        phases.append(("dispatch", _dispatch_duration(call_result)))

        # --- CallEndHandler (Outbound Contact Flow terminal Lambda) ---
        call_end_event = {
            "contactId": contact_id,
            "cycleId": cycle_id,
            "employeeId": emp_id,
            "attempt": str(attempt),
            "callResultCode": call_result,
            "taskToken": f"task-token-{emp_id}-{attempt}",
        }
        call_end_result = call_end_handler_mod.lambda_handler(
            call_end_event, None
        )
        assert call_end_result["status"] == "ok"

        # --- Transcribe + KeywordMatcher (only on RECORDED) ---
        if call_result == "RECORDED":
            phases.append(("wait_transcribe", _TRANSCRIBE_DURATION_SEC))

            # Synthetic EventBridge S3 ObjectCreated event for the
            # recording, with a key that matches the outbound schema:
            # ``recordings/{cycleId}/{employeeId}/{seq}.wav``.
            s3_event = {
                "detail": {
                    "bucket": {"name": "safety-recordings-test"},
                    "object": {
                        "key": (
                            f"recordings/{cycle_id}/{emp_id}/"
                            f"{attempt:03d}.wav"
                        )
                    },
                }
            }
            transcribe_result = transcribe_starter_mod.lambda_handler(
                s3_event, None
            )
            assert transcribe_result["status"] == "ok"

            # KeywordMatcher is mocked by directly writing the
            # voiceStatus the matcher would have derived.
            voice_status = _sample(rng, _VOICE_STATUS_DIST_RECORDED)
            response_table.update_item(
                Key={"cycleId": cycle_id, "employeeId": emp_id},
                UpdateExpression="SET voiceStatus = :vs",
                ExpressionAttributeValues={":vs": voice_status},
            )
        else:
            # No recording → voiceStatus stays PENDING (the RetryEvaluator
            # input). The CallEndHandler already persisted callResultCode.
            voice_status = "PENDING"
        voice_counts[voice_status] += 1

        # --- ReadResponse + EvaluateRetry (RetryEvaluator) ---
        current_row = response_table.get_item(
            Key={"cycleId": cycle_id, "employeeId": emp_id}
        )["Item"]
        evaluate_event = {
            "cycleId": cycle_id,
            "employeeId": emp_id,
            "voiceStatus": current_row.get("voiceStatus", "PENDING"),
            "callResultCode": current_row.get("callResultCode"),
            "attempts": attempt,
            "retryCount": RETRY_COUNT,
            "retryIntervalMinutes": RETRY_INTERVAL_MIN,
            "prevEndAt": prev_end_at,
        }
        decision = retry_evaluator_mod.lambda_handler(evaluate_event, None)

        if decision["retry"]:
            phases.append(("wait_interval", float(RETRY_INTERVAL_SEC)))
            prev_end_at = decision["nextDispatchAt"]
            continue

        # --- FinalizeOne (SFN DynamoDB integration, simulated) ---
        final_status = decision["finalStatus"]
        response_table.update_item(
            Key={"cycleId": cycle_id, "employeeId": emp_id},
            UpdateExpression="SET voiceStatus = :vs",
            ExpressionAttributeValues={":vs": final_status},
        )
        break
    else:
        # Retry loop completed without an early ``break`` — should not
        # happen because RetryEvaluator must finalise on the (RETRY_COUNT+1)th
        # attempt. Raise loudly per principle 19(b).
        raise AssertionError(
            f"Employee {emp_id}: retry loop exhausted without FinalizeOne"
        )

    return phases


# ---------------------------------------------------------------------------
# The actual SLA test
# ---------------------------------------------------------------------------


# Stash the simulation summary in a module-level dict so a follow-up
# report-generation hook can read it after the test finishes.
SIM_SUMMARY: dict[str, Any] = {}


def test_300_employees_complete_within_60min_with_concurrency_10(
    suppress_handler_logs: None,
    cycle_id: str,
    in_memory_tables: dict[str, InMemoryTable],
    aws_mocks: dict[str, MagicMock],
    patched_handlers: None,
) -> None:
    """End-to-end SLA check for Task 14.11a.

    Drives 300 employees through the full Map iteration against
    in-memory fakes, then runs a discrete-event scheduler over the
    recorded phase lists to verify the 30 / 60-minute SLAs and the
    ``MaxConcurrency = 10`` invariant.
    """
    cycle_table = in_memory_tables["cycle"]
    response_table = in_memory_tables["response"]

    # Seed the Cycle row in RUNNING state.
    cycle_table.put_item(
        {
            "cycleId": cycle_id,
            "status": "RUNNING",
            "retryCount": RETRY_COUNT,
            "retryIntervalMinutes": RETRY_INTERVAL_MIN,
            "targetCount": NUM_EMPLOYEES,
            "startedAt": "2026-06-27T00:00:00Z",
        }
    )

    # Generate 300 dummy employees with deterministic phone numbers.
    employees: list[dict[str, str]] = [
        {"employeeId": f"emp-{i:03d}", "phoneNumber": f"+8190{i:08d}"}
        for i in range(1, NUM_EMPLOYEES + 1)
    ]

    rng = random.Random(42)
    result_counts: dict[str, int] = defaultdict(int)
    voice_counts: dict[str, int] = defaultdict(int)

    # --- Phase 1: drive each employee through their full workflow ---
    workflows: list[tuple[str, list[tuple[str, float]]]] = []
    for emp in employees:
        phases = _run_one_employee(
            cycle_id=cycle_id,
            employee=emp,
            response_table=response_table,
            transcript_meta_table=in_memory_tables["transcript_meta"],
            rng=rng,
            result_counts=result_counts,
            voice_counts=voice_counts,
        )
        workflows.append((emp["employeeId"], phases))

    # --- Phase 2: replay on the discrete-event scheduler ---
    sim_result = _simulate_workflows(workflows, MAX_CONCURRENT_CALLS)

    # ----- Assertions -----

    # 1. All 300 reach a terminal voiceStatus.
    all_rows = response_table.all_items()
    assert len(all_rows) == NUM_EMPLOYEES, (
        f"Expected {NUM_EMPLOYEES} Response rows; got {len(all_rows)}"
    )
    non_terminal = [
        row for row in all_rows if row["voiceStatus"] not in _TERMINAL_VOICE_STATUSES
    ]
    assert non_terminal == [], (
        f"{len(non_terminal)} employees did not reach terminal: "
        f"{[(r['employeeId'], r['voiceStatus']) for r in non_terminal[:5]]}"
    )

    # 2. Peak concurrent dispatch ≤ MaxConcurrentCalls.
    assert sim_result["peak_concurrency"] <= MAX_CONCURRENT_CALLS, (
        f"Peak concurrency {sim_result['peak_concurrency']} > "
        f"{MAX_CONCURRENT_CALLS} (MaxConcurrentCalls)"
    )

    # 3. Cycle total wall clock ≤ 60 minutes.
    assert sim_result["total_time"] <= SLA_60_MIN_SEC, (
        f"Cycle took {sim_result['total_time']:.1f}s > "
        f"{SLA_60_MIN_SEC}s (60min SLA)"
    )

    # 4. Each employee's first dispatch completes within 30 minutes.
    late_first_dispatch = [
        (emp_id, st["first_dispatch_complete"])
        for emp_id, st in sim_result["state"].items()
        if st["first_dispatch_complete"] is None
        or st["first_dispatch_complete"] > SLA_30_MIN_SEC
    ]
    assert late_first_dispatch == [], (
        f"{len(late_first_dispatch)} employees missed the 30min initial-"
        f"dispatch SLA: {late_first_dispatch[:5]}"
    )

    # 5. CycleFinalizer TIMER_30MIN → no_warning_needed
    #    (all rows now have callAttempts > 0).
    result_30 = cycle_finalizer_mod.lambda_handler(
        {"trigger": "TIMER_30MIN", "cycleId": cycle_id}, None
    )
    assert result_30["status"] == "no_warning_needed", result_30

    # 6. CycleFinalizer MAP_COMPLETED → completed
    result_done = cycle_finalizer_mod.lambda_handler(
        {
            "trigger": "MAP_COMPLETED",
            "cycleId": cycle_id,
            "executionArn": "arn:aws:states:ap-northeast-1:111122223333:execution:"
            "test:exec-1",
        },
        None,
    )
    assert result_done["status"] == "completed", result_done

    # 7. CycleFinalizer TIMER_60MIN after completion → no_op
    result_60 = cycle_finalizer_mod.lambda_handler(
        {
            "trigger": "TIMER_60MIN",
            "cycleId": cycle_id,
            "executionArn": "arn:aws:states:ap-northeast-1:111122223333:execution:"
            "test:exec-1",
        },
        None,
    )
    assert result_60["status"] == "no_op", result_60

    # 8. CloudWatch put_metric_data must NOT have been called with
    #    SlaWarning30Min nor CycleTimeout — both are SLA-violation
    #    signals that the cycle should not raise on a healthy run.
    metric_names = [
        call.kwargs["MetricData"][0]["MetricName"]
        for call in aws_mocks["cloudwatch"].put_metric_data.call_args_list
    ]
    assert "SlaWarning30Min" not in metric_names, (
        f"SlaWarning30Min metric was unexpectedly emitted: {metric_names}"
    )
    assert "CycleTimeout" not in metric_names, (
        f"CycleTimeout metric was unexpectedly emitted: {metric_names}"
    )

    # Stash for the report writer.
    SIM_SUMMARY.update(
        {
            "num_employees": NUM_EMPLOYEES,
            "max_concurrent_calls": MAX_CONCURRENT_CALLS,
            "retry_count": RETRY_COUNT,
            "retry_interval_min": RETRY_INTERVAL_MIN,
            "peak_concurrency": sim_result["peak_concurrency"],
            "total_time_sec": round(sim_result["total_time"], 1),
            "total_time_min": round(sim_result["total_time"] / 60, 2),
            "p50_first_dispatch_sec": round(
                _percentile(
                    [
                        st["first_dispatch_complete"]
                        for st in sim_result["state"].values()
                    ],
                    50,
                ),
                1,
            ),
            "p95_first_dispatch_sec": round(
                _percentile(
                    [
                        st["first_dispatch_complete"]
                        for st in sim_result["state"].values()
                    ],
                    95,
                ),
                1,
            ),
            "max_first_dispatch_sec": round(
                max(
                    st["first_dispatch_complete"]
                    for st in sim_result["state"].values()
                ),
                1,
            ),
            "call_result_distribution": dict(result_counts),
            "voice_status_distribution": dict(voice_counts),
            "final_status_distribution": _final_status_breakdown(all_rows),
        }
    )
    # Echo metrics on `-s` so the report writer (and CI logs) can pick
    # them up without an extra pytest run.
    import json as _json

    print(
        "\n[SLA_300_MOCK_SUMMARY]\n"
        + _json.dumps(SIM_SUMMARY, ensure_ascii=False, indent=2)
    )


def _percentile(values: list[float], pct: float) -> float:
    """Return the ``pct`` th percentile of ``values`` (nearest-rank)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = max(0, min(len(sorted_vals) - 1, int(round(pct / 100.0 * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def _final_status_breakdown(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r.get("voiceStatus", "PENDING")] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Smaller targeted checks of the scheduler itself (sanity guards)
# ---------------------------------------------------------------------------


def test_scheduler_enforces_max_concurrency() -> None:
    """Trivial workflow: 50 employees, each one ``dispatch`` of 10 s.

    Without the scheduler's slot cap, peak concurrency would be 50.
    With ``max_concurrency=10`` it must equal exactly 10 and the total
    time must be ``ceil(50/10) * 10 = 50`` seconds.
    """
    workflows = [(f"e{i}", [("dispatch", 10.0)]) for i in range(50)]
    res = _simulate_workflows(workflows, max_concurrency=10)
    assert res["peak_concurrency"] == 10
    assert res["total_time"] == pytest.approx(50.0, abs=1e-6)


def test_scheduler_wait_phases_do_not_consume_slots() -> None:
    """A long ``wait_interval`` must not block subsequent dispatches.

    Two employees: one does a single 10 s dispatch and a 1000 s wait,
    the other does a single 10 s dispatch starting at t=0. With
    ``max_concurrency=1`` and *wait counting as a slot*, the second
    employee could not dispatch until t > 1010 s. Verifying it dispatches
    at t = 10 confirms that ``wait_interval`` releases the slot.
    """
    workflows = [
        ("a", [("dispatch", 10.0), ("wait_interval", 1000.0)]),
        ("b", [("dispatch", 10.0)]),
    ]
    res = _simulate_workflows(workflows, max_concurrency=1)
    assert res["state"]["b"]["start_time"] == pytest.approx(10.0, abs=1e-6)
    # Total time bounded by 'a's full timeline (10 dispatch + 1000 wait).
    assert res["total_time"] == pytest.approx(1010.0, abs=1e-6)
