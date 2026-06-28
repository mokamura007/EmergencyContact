"""Call-result code constants and classifier for Connect_Caller
(Phase 6.3 / Phase 7.4).

The Outbound Contact Flow (Phase 7.1) routes each completed call to
``CallEndHandler`` with one of a fixed set of result-code strings. The
classification logic itself (Connect ``DisconnectReason`` + Transcribe
status → code) lives in :func:`classify_call_result` (Phase 7.4,
Property 14 PBT candidate). Phase 6.3 only needs the **set** of valid
codes for input validation in ``CallEndHandler``.

Keeping the set and the classifier here (and not duplicated inside
each handler) prevents drift between CallEndHandler (Phase 6.3),
TranscribeStarter (Phase 6.4), the Outbound Contact Flow's decision
tree (Phase 7.1), and KeywordMatcher (Phase 8.1) — all of which need
to read or write these codes.

See design.md / Connect_Caller / 通話結果コード and Property 14 for
the meaning of each value and the full mapping table.

Phase 7.4 design choices:
    * The classifier is a **pure function** (no I/O) so Phase 13.14
      can drive Hypothesis property tests against it without mocks.
    * Inputs are normalised (uppercased, hyphens/spaces → underscores,
      surrounding whitespace stripped) so callers can pass Connect's
      raw ``DisconnectReason`` directly without preprocessing.
    * Unknown ``reason`` values raise ``ValueError`` rather than being
      silently bucketed (project principle 19(b) — no silent
      fallbacks). The set of recognised reasons is therefore a
      contract: extending it requires updating this module and the
      Property 14 strategy in lockstep.
    * The output set equals :data:`VALID_CALL_RESULT_CODES` exactly,
      which lets ``CallEndHandler`` reuse a single membership check
      against either a Contact-Flow-supplied code or a classifier
      result.
"""

from __future__ import annotations

#: All accepted ``callResultCode`` values from Outbound Contact Flow
#: (Phase 7.1) and downstream pipelines. ``frozenset`` because callers
#: should never mutate it.
#:
#: * ``RECORDED``          — Call connected and the recording uploaded.
#: * ``NO_ANSWER``         — 30s ring with no pickup.
#: * ``BUSY``              — Recipient line was busy.
#: * ``VOICEMAIL``         — Voicemail detected on pickup.
#: * ``ERROR``             — Connect API error (dispatch or in-call).
#: * ``TRANSCRIBE_FAILED`` — Transcribe job exhausted retries.
VALID_CALL_RESULT_CODES: frozenset[str] = frozenset(
    {
        "RECORDED",
        "NO_ANSWER",
        "BUSY",
        "VOICEMAIL",
        "ERROR",
        "TRANSCRIBE_FAILED",
    }
)


# --- Reason → category lookup tables -----------------------------------
#
# Reason strings are **normalised** before lookup (see ``_normalise``):
# uppercased, surrounding whitespace stripped, hyphens and spaces
# converted to underscores. Members of each set below must therefore be
# written in the canonical normalised form.
#
# The sets are deliberately exhaustive against the design.md mapping
# (Property 14) and Amazon Connect's documented ``DisconnectReason``
# vocabulary. New values added by Connect (or by future Contact Flow
# revisions) require an explicit code change here — there is no
# catch-all bucket.

#: Reasons that map to ``NO_ANSWER``: the call rang out without pickup.
_NO_ANSWER_REASONS: frozenset[str] = frozenset(
    {
        "NO_ANSWER",
        "NO_USER_RESPONSE",
        "EXPIRED",
        "TIMEOUT",
        "RING_TIMEOUT",
    }
)

#: Reasons that map to ``BUSY``: the recipient line was already in use.
_BUSY_REASONS: frozenset[str] = frozenset(
    {
        "BUSY",
        "LINE_BUSY",
        "USER_BUSY",
    }
)

#: Reasons that map to ``VOICEMAIL``: an answering machine was detected.
_VOICEMAIL_REASONS: frozenset[str] = frozenset(
    {
        "VOICEMAIL",
        "ANSWERING_MACHINE",
        "ANSWERING_MACHINE_DETECTED",
    }
)

#: Reasons that map to ``ERROR``: dispatch failure, telecom problem,
#: or any explicit API-level rejection. These are surfaced as ERROR so
#: Phase 6.5 RetryEvaluator schedules a retry within Retry_Count.
_ERROR_REASONS: frozenset[str] = frozenset(
    {
        "API_ERROR",
        "ERROR",
        "REJECT",
        "REJECTED",
        "FAILED",
        "TELECOM_PROBLEM",
        "ENDPOINT_ERROR",
        "DISPATCH_FAILED",
        "USER_NOT_AVAILABLE",
    }
)

#: Reasons that mean the call **connected** and went to natural end.
#: For these, the actual outcome (RECORDED vs TRANSCRIBE_FAILED) depends
#: on the ``recorded`` flag and the Transcribe job status.
_CONNECTED_REASONS: frozenset[str] = frozenset(
    {
        "CUSTOMER_DISCONNECT",
        "CONTACT_FLOW_DISCONNECT",
        "AGENT_DISCONNECT",
        "NORMAL_HANGUP",
        "HANGUP",
        "OK",
        "NORMAL",
    }
)

#: All recognised reasons. Sanity-checked at import time below to catch
#: copy-paste mistakes (e.g. the same reason landing in two buckets).
_ALL_REASONS: frozenset[str] = (
    _NO_ANSWER_REASONS
    | _BUSY_REASONS
    | _VOICEMAIL_REASONS
    | _ERROR_REASONS
    | _CONNECTED_REASONS
)

# Bucket overlap check (defensive — would indicate a coding error).
_BUCKETS: tuple[frozenset[str], ...] = (
    _NO_ANSWER_REASONS,
    _BUSY_REASONS,
    _VOICEMAIL_REASONS,
    _ERROR_REASONS,
    _CONNECTED_REASONS,
)
_BUCKET_NAMES: tuple[str, ...] = (
    "NO_ANSWER",
    "BUSY",
    "VOICEMAIL",
    "ERROR",
    "CONNECTED",
)
for _i in range(len(_BUCKETS)):
    for _j in range(_i + 1, len(_BUCKETS)):
        _overlap = _BUCKETS[_i] & _BUCKETS[_j]
        if _overlap:
            raise RuntimeError(
                f"reason bucket overlap between {_BUCKET_NAMES[_i]} and "
                f"{_BUCKET_NAMES[_j]}: {sorted(_overlap)}"
            )
del _i, _j, _overlap


#: Recognised Transcribe job-status values (normalised). ``None`` is
#: also accepted by the classifier and means "Transcribe has not been
#: invoked yet" — the canonical state when ``CallEndHandler`` runs.
_TRANSCRIBE_STATUS_COMPLETED: frozenset[str] = frozenset({"COMPLETED"})
_TRANSCRIBE_STATUS_FAILED: frozenset[str] = frozenset({"FAILED"})
_TRANSCRIBE_STATUS_PENDING: frozenset[str] = frozenset(
    {"QUEUED", "IN_PROGRESS"}
)
_ALL_TRANSCRIBE_STATUSES: frozenset[str] = (
    _TRANSCRIBE_STATUS_COMPLETED
    | _TRANSCRIBE_STATUS_FAILED
    | _TRANSCRIBE_STATUS_PENDING
)


def _normalise(s: str) -> str:
    """Return the canonical uppercase form used by the lookup tables."""
    return s.strip().upper().replace("-", "_").replace(" ", "_")


def _normalise_transcribe_status(transcribe_status: str | None) -> str | None:
    """Normalise ``transcribe_status``; raise ``ValueError`` if unrecognised.

    Returns:
        The canonical normalised value when ``transcribe_status`` is a
        recognised string. ``None`` for ``transcribe_status is None`` or
        for an empty / whitespace-only string (which we treat as the
        "Transcribe has not been invoked yet" case used by
        ``CallEndHandler``).

    Raises:
        ValueError: when ``transcribe_status`` is neither ``None`` nor a
            ``str``, or when it is a non-empty string that does not map
            into :data:`_ALL_TRANSCRIBE_STATUSES`.
    """
    if transcribe_status is None:
        return None
    if not isinstance(transcribe_status, str):
        raise ValueError(
            f"transcribe_status must be a str or None; "
            f"got {type(transcribe_status).__name__}"
        )
    if transcribe_status.strip() == "":
        return None
    candidate = _normalise(transcribe_status)
    if candidate not in _ALL_TRANSCRIBE_STATUSES:
        raise ValueError(
            f"transcribe_status must be one of "
            f"{sorted(_ALL_TRANSCRIBE_STATUSES)} or None; "
            f"got {transcribe_status!r}"
        )
    return candidate


def _classify_non_connected(reason_n: str) -> str | None:
    """Return the code for a non-connected ``reason``, or ``None``.

    ``None`` means the reason did not match any non-connected bucket
    (so the caller should consult the connected-bucket logic instead).
    """
    if reason_n in _NO_ANSWER_REASONS:
        return "NO_ANSWER"
    if reason_n in _BUSY_REASONS:
        return "BUSY"
    if reason_n in _VOICEMAIL_REASONS:
        return "VOICEMAIL"
    if reason_n in _ERROR_REASONS:
        return "ERROR"
    return None


def _classify_connected(ts_n: str | None, recorded: bool) -> str:
    """Return the code for a connected call, given Transcribe status.

    Pre-condition: the caller has confirmed ``reason_n`` is in
    :data:`_CONNECTED_REASONS`. The result depends only on
    ``recorded`` and the (already-validated) ``ts_n``.
    """
    if not recorded:
        # Connected but no audio captured — operational failure.
        return "ERROR"
    if ts_n is None or ts_n in _TRANSCRIBE_STATUS_PENDING:
        # Phase 6.3 CallEndHandler call site: Transcribe has not yet
        # been invoked. The Transcribe pipeline may later downgrade
        # this to TRANSCRIBE_FAILED, but as far as the call itself is
        # concerned the recording exists.
        return "RECORDED"
    if ts_n in _TRANSCRIBE_STATUS_COMPLETED:
        return "RECORDED"
    if ts_n in _TRANSCRIBE_STATUS_FAILED:
        # Phase 6.4 TranscribeStarter call site after retries.
        return "TRANSCRIBE_FAILED"
    # Unreachable: ts_n was validated against _ALL_TRANSCRIBE_STATUSES
    # in _normalise_transcribe_status; keep the explicit raise so any
    # future addition to that set without updating this dispatch is
    # caught loudly.
    raise ValueError(  # pragma: no cover - defensive
        f"unhandled transcribe_status bucket: {ts_n!r}"
    )


def classify_call_result(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> str:
    """Classify a single completed call into a ``callResultCode``.

    Pure function. Maps Connect's ``DisconnectReason`` plus the
    Transcribe job state plus whether a recording was captured into
    one of the six ``callResultCode`` values defined by Property 14
    (design.md line 1005-1014).

    The mapping rules, in evaluation order:

    1. ``reason`` indicates the call never connected
       (``NO_ANSWER`` / ``BUSY`` / ``VOICEMAIL`` / ``ERROR`` bucket)
       → return the corresponding code. ``recorded`` and
       ``transcribe_status`` are ignored because no recording could
       have been produced.

    2. ``reason`` indicates the call connected and ended naturally
       (``_CONNECTED_REASONS``):

       * ``recorded`` is ``False`` → ``ERROR`` (the call connected
         but no audio file was captured, which is an operational
         failure).
       * ``recorded`` is ``True`` and Transcribe has not been
         started yet (``transcribe_status`` is ``None`` or in the
         pending set) → ``RECORDED``. This is the canonical state
         at ``CallEndHandler`` invocation time, before
         ``TranscribeStarter`` runs.
       * ``recorded`` is ``True`` and ``transcribe_status`` is
         ``COMPLETED`` → ``RECORDED``.
       * ``recorded`` is ``True`` and ``transcribe_status`` is
         ``FAILED`` → ``TRANSCRIBE_FAILED``. This is the canonical
         state at the tail end of ``TranscribeStarter`` when it has
         exhausted retries.

    3. ``reason`` is not in any recognised bucket →
       :class:`ValueError`. Project principle 19(b) bans silent
       fallback bucketing.

    Args:
        reason: Connect ``DisconnectReason`` string (any casing,
            hyphens or spaces are accepted via normalisation).
        transcribe_status: Transcribe job status (``"COMPLETED"`` /
            ``"FAILED"`` / ``"QUEUED"`` / ``"IN_PROGRESS"``) or
            ``None`` when Transcribe has not yet been invoked. Case
            insensitive.
        recorded: ``True`` when a recording WAV was uploaded to the
            RecordingsBucket for this call; ``False`` otherwise.

    Returns:
        A member of :data:`VALID_CALL_RESULT_CODES`.

    Raises:
        ValueError: if ``reason`` is empty / not a string / not in any
            recognised bucket, or if ``transcribe_status`` is a
            non-empty string that is not a recognised Transcribe
            status, or if ``recorded`` is not a bool.
    """
    if not isinstance(reason, str):
        raise ValueError(f"reason must be a str; got {type(reason).__name__}")
    if not reason.strip():
        raise ValueError("reason must be a non-empty string")
    if not isinstance(recorded, bool):
        raise ValueError(
            f"recorded must be a bool; got {type(recorded).__name__}"
        )

    reason_n = _normalise(reason)
    ts_n = _normalise_transcribe_status(transcribe_status)

    # Rule 1: non-connected reasons short-circuit.
    non_connected = _classify_non_connected(reason_n)
    if non_connected is not None:
        return non_connected

    # Rule 2: connected reasons depend on recorded + transcribe_status.
    if reason_n in _CONNECTED_REASONS:
        return _classify_connected(ts_n, recorded)

    # Rule 3: unrecognised reason → no silent fallback.
    raise ValueError(
        f"unrecognised reason {reason!r} (normalised={reason_n!r}); "
        f"recognised values: {sorted(_ALL_REASONS)}"
    )
