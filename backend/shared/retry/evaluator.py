"""Pure retry-evaluation helpers (Phase 6.5).

The RetryEvaluator Lambda is invoked from the Step Functions
``EvaluateRetry`` state (Phase 6.8) after each outbound dispatch
attempt. It must answer two questions:

1. **Should this employee be redialed?**
   - ``voiceStatus ∈ {SAFE, INJURED, UNAVAILABLE}`` is a confirmed
     answer (Requirement 9.1) — no further dispatch.
   - ``voiceStatus == UNREACHABLE`` is a terminal state already
     (Requirement 9.5) — no further dispatch.
   - Otherwise (``PENDING`` or ``OTHER``, Requirements 9.3 / 9.4) the
     decision depends on how many dispatches have already been made.

2. **When should the next dispatch fire?**
   - The previous dispatch ended at ``prevEndAt``. The next dispatch
     is permitted ``retryIntervalMinutes`` later (Requirement 9.4).
   - The SFN ``Wait`` state needs the delay in **seconds**, so
     :func:`compute_retry_wait_seconds` converts the absolute future
     timestamp into a relative seconds value (clipped to zero when the
     interval has already elapsed).

All four functions below are pure (no clocks, no I/O, no module-level
state). The clock is dependency-injected via the ``now_iso`` argument
on :func:`compute_retry_wait_seconds` so callers and tests can pass a
deterministic value. The handler in
``backend/lambdas/retry_evaluator`` is a thin wrapper that supplies
``datetime.now(UTC)`` and the event payload.

Phase 13.12 (Property 12 ``shouldRetry``) and Phase 13.13 (Property 13
``computeNextDispatchAt``) will drive Hypothesis property tests
against the functions here without further code changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: All ``voiceStatus`` values the system tracks. Mirrors the column
#: vocabulary documented in design.md / Data Models / D3 Response.
#:
#: * ``PENDING``      — Response row exists but no call placed / answer
#:                      received yet (initial state, Requirement 9.3
#:                      considers it equivalent to ``OTHER`` for retry).
#: * ``SAFE`` / ``INJURED`` / ``UNAVAILABLE`` — Confirmed answers via
#:                      keyword match on the transcript (Requirement
#:                      9.1).
#: * ``OTHER``        — A response was recorded but did not match any
#:                      dictionary keyword (Requirement 9.3).
#: * ``UNREACHABLE``  — Terminal state set after the retry budget is
#:                      exhausted (Requirement 9.5).
VALID_VOICE_STATUS_VALUES: frozenset[str] = frozenset(
    {
        "PENDING",
        "SAFE",
        "INJURED",
        "UNAVAILABLE",
        "OTHER",
        "UNREACHABLE",
    }
)

#: Confirmed answers — no further dispatch (Requirement 9.1).
_CONFIRMED_VOICE_STATUSES: frozenset[str] = frozenset(
    {"SAFE", "INJURED", "UNAVAILABLE"}
)

#: Statuses that may still be retried subject to the retry-count budget
#: (Requirements 9.3 / 9.4). ``UNREACHABLE`` is intentionally NOT here:
#: it is terminal even when ``attempts < retryCount`` because the SFN
#: has already finalized that branch.
_RETRYABLE_VOICE_STATUSES: frozenset[str] = frozenset(
    {"PENDING", "OTHER"}
)

#: Lower / upper bounds for ``retryIntervalMinutes`` as defined by the
#: Cycle creation API (Requirements 4.7 / Parameter
#: ``DefaultRetryIntervalMinutes`` AllowedRange).
_MIN_INTERVAL_MINUTES = 1
_MAX_INTERVAL_MINUTES = 60


# --- shouldRetry (Property 12 PBT candidate) -----------------------------


def should_retry(
    voice_status: str,
    call_result_code: str | None,
    attempts: int,
    retry_count: int,
) -> bool:
    """Return ``True`` iff another outbound dispatch should be scheduled.

    Pure function. Phase 13.12 PBT candidate (Property 12).

    Decision table (Requirements 9.1 / 9.3 / 9.4 / 9.5):

        ============================  =================  ===============
        voiceStatus                   attempts<retryCnt  result
        ============================  =================  ===============
        SAFE / INJURED / UNAVAILABLE  *any*              False (Req 9.1)
        UNREACHABLE                   *any*              False (Req 9.5)
        PENDING / OTHER               True               True  (Req 9.4)
        PENDING / OTHER               False              False (Req 9.4)
        ============================  =================  ===============

    ``call_result_code`` is accepted for symmetry with the SFN input
    payload (and to allow future extensions to differentiate e.g.
    ``BUSY`` vs ``NO_ANSWER`` without changing the signature), but the
    current Requirements 9.x text does not have the retry decision
    depend on it — ``voiceStatus`` already encodes the answer.

    Args:
        voice_status: One of :data:`VALID_VOICE_STATUS_VALUES`.
        call_result_code: One of
            :data:`shared.connect.call_result.VALID_CALL_RESULT_CODES`
            or ``None`` (no call placed yet). Currently informational
            only; see note above.
        attempts: Cumulative dispatch count for this employee in this
            cycle. ``>= 0``.
        retry_count: Cycle-wide retry budget set by the operator at
            ``POST /cycles`` time. ``0`` means no retries — one
            dispatch then finalize.

    Returns:
        ``True`` iff the SFN should branch to ``WaitInterval`` →
        ``Dispatch`` for another attempt.

    Raises:
        ValueError: ``voice_status`` not in
            :data:`VALID_VOICE_STATUS_VALUES`, ``attempts`` not a
            non-negative ``int``, or ``retry_count`` not a non-negative
            ``int``.
    """
    if voice_status not in VALID_VOICE_STATUS_VALUES:
        raise ValueError(
            "voice_status must be one of "
            f"{sorted(VALID_VOICE_STATUS_VALUES)}; got {voice_status!r}"
        )
    # ``isinstance(True, int)`` is ``True`` in Python, but a boolean here
    # is almost certainly a caller mistake — refuse it explicitly.
    if not isinstance(attempts, int) or isinstance(attempts, bool):
        raise ValueError(f"attempts must be int; got {type(attempts).__name__}")
    if not isinstance(retry_count, int) or isinstance(retry_count, bool):
        raise ValueError(
            f"retry_count must be int; got {type(retry_count).__name__}"
        )
    if attempts < 0:
        raise ValueError(f"attempts must be >= 0; got {attempts}")
    if retry_count < 0:
        raise ValueError(f"retry_count must be >= 0; got {retry_count}")

    if voice_status in _CONFIRMED_VOICE_STATUSES:
        return False
    if voice_status == "UNREACHABLE":
        return False
    # voice_status ∈ {PENDING, OTHER}
    return attempts < retry_count


# --- computeNextDispatchAt (Property 13 PBT candidate) -------------------


def _parse_iso_utc(value: str, field_name: str) -> datetime:
    """Parse an ISO 8601 timestamp into a timezone-aware UTC ``datetime``.

    Accepts both ``Z`` and ``+00:00`` suffixes (the two forms emitted
    by AWS services and Python's ``datetime.isoformat``).
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty ISO 8601 string")
    # ``fromisoformat`` accepts ``+00:00`` natively but not the ``Z``
    # short-form, so normalize first.
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} is not a valid ISO 8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} must include a timezone offset: {value!r}"
        )
    return parsed.astimezone(timezone.utc)


def _format_iso_utc_z(dt: datetime) -> str:
    """Return ``dt`` as an ISO 8601 string with ``Z`` suffix."""
    # ``isoformat`` would produce ``+00:00``; replace with ``Z`` so the
    # output matches the rest of the system (SFN / DynamoDB strings).
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def compute_next_dispatch_at(
    prev_end_at_iso: str,
    interval_minutes: int,
) -> str:
    """Return the ISO 8601 timestamp for the next dispatch (UTC, ``Z``).

    Pure function. Phase 13.13 PBT candidate (Property 13).

    Args:
        prev_end_at_iso: ISO 8601 timestamp of when the previous
            outbound call ended. Accepts both ``Z`` and ``+00:00``
            suffixes; the timezone offset is mandatory.
        interval_minutes: Retry interval, in minutes, set by the
            operator at cycle-creation time. Must be in
            ``[1, 60]`` (Requirements 4.7 / 9.4).

    Returns:
        ``prev_end_at + interval_minutes`` minutes, formatted as
        ``YYYY-MM-DDTHH:MM:SS(.ffffff)Z``.

    Raises:
        ValueError: on malformed ``prev_end_at_iso`` or
            ``interval_minutes`` outside ``[1, 60]``.
    """
    if not isinstance(interval_minutes, int) or isinstance(
        interval_minutes, bool
    ):
        raise ValueError(
            "interval_minutes must be int; "
            f"got {type(interval_minutes).__name__}"
        )
    if not _MIN_INTERVAL_MINUTES <= interval_minutes <= _MAX_INTERVAL_MINUTES:
        raise ValueError(
            "interval_minutes must be in "
            f"[{_MIN_INTERVAL_MINUTES}, {_MAX_INTERVAL_MINUTES}]; "
            f"got {interval_minutes}"
        )
    prev_end_at = _parse_iso_utc(prev_end_at_iso, "prev_end_at_iso")
    next_dispatch_at = prev_end_at + timedelta(minutes=interval_minutes)
    return _format_iso_utc_z(next_dispatch_at)


# --- computeRetryWaitSeconds (helper for SFN Wait) -----------------------


def compute_retry_wait_seconds(
    prev_end_at_iso: str,
    interval_minutes: int,
    now_iso: str,
) -> int:
    """Return seconds to wait between now and the next dispatch.

    Pure function (clock injected via ``now_iso``).

    The SFN ``Wait`` state in :ref:`cycle state machine <Phase 6.8>`
    consumes a seconds count, so we convert the absolute next-dispatch
    timestamp into a relative delay. If the interval has already
    elapsed (the handler took longer than ``interval_minutes`` to be
    invoked), the result is clamped to ``0`` so the SFN proceeds
    immediately rather than scheduling a negative wait.

    Args:
        prev_end_at_iso: Same as :func:`compute_next_dispatch_at`.
        interval_minutes: Same as :func:`compute_next_dispatch_at`.
        now_iso: ISO 8601 timestamp representing "now" (the handler
            supplies ``datetime.now(UTC)`` rendered through
            :func:`_format_iso_utc_z`).

    Returns:
        Integer seconds in ``[0, +∞)``. Always rounded down with
        ``int()`` truncation (fractional seconds matter little for a
        retry interval in the minutes range).

    Raises:
        ValueError: propagated from :func:`compute_next_dispatch_at`
            or from a malformed ``now_iso``.
    """
    next_dispatch_at_iso = compute_next_dispatch_at(
        prev_end_at_iso, interval_minutes
    )
    next_dispatch_at = _parse_iso_utc(next_dispatch_at_iso, "next_dispatch_at")
    now = _parse_iso_utc(now_iso, "now_iso")
    delta = next_dispatch_at - now
    seconds = int(delta.total_seconds())
    return max(0, seconds)


# --- deriveFinalStatus ---------------------------------------------------


def derive_final_status(voice_status: str) -> str:
    """Resolve the terminal ``finalStatus`` for the FinalizeOne SFN state.

    Pure function.

    The Step Functions ``FinalizeOne`` state writes the resulting
    status into the Response row when ``should_retry`` returns
    ``False``. The mapping is:

        ===============================  ===============
        voice_status                     finalStatus
        ===============================  ===============
        SAFE / INJURED / UNAVAILABLE     <unchanged>
        UNREACHABLE                      ``"UNREACHABLE"``
        PENDING / OTHER                  ``"UNREACHABLE"``  (Req 9.4 — budget exhausted)
        ===============================  ===============

    The ``PENDING``/``OTHER`` → ``UNREACHABLE`` collapse is the
    correct behavior in this code path: callers only invoke
    ``derive_final_status`` after ``should_retry`` returned ``False``,
    which for ``PENDING``/``OTHER`` requires the retry budget to be
    exhausted (Requirement 9.5).

    Args:
        voice_status: One of :data:`VALID_VOICE_STATUS_VALUES`.

    Returns:
        The terminal ``finalStatus`` string the Response row should
        carry.

    Raises:
        ValueError: ``voice_status`` not in
            :data:`VALID_VOICE_STATUS_VALUES`.
    """
    if voice_status not in VALID_VOICE_STATUS_VALUES:
        raise ValueError(
            "voice_status must be one of "
            f"{sorted(VALID_VOICE_STATUS_VALUES)}; got {voice_status!r}"
        )
    if voice_status in _CONFIRMED_VOICE_STATUSES:
        return voice_status
    return "UNREACHABLE"
