"""S3 recording key parsing and Transcribe job naming (Phase 6.4).

TranscribeStarter (Phase 6.4) receives S3 ``Object Created`` EventBridge
events for both outbound and inbound recordings. Each event must be
mapped to:

    * the resulting Transcript S3 key (= Transcribe output destination),
    * the TranscriptMetaTable primary key (``meta_pk`` / ``meta_sk``), and
    * an idempotent Transcribe job name.

This module isolates that mapping in pure functions so the handler is a
thin orchestrator. Both functions below are side-effect-free and are
flagged as Phase 13.x PBT candidates against Property 24 (再試行回数上限
不変条件) — specifically the path-shape invariant "any outbound recording
key uniquely determines its transcript key and meta keys".

Outbound key schema (per design.md / Recording_Store / S3 オブジェクト
キー命名):
    recordings/{cycleId}/{employeeId}/{seq}.wav
        → transcripts/{cycleId}/{employeeId}/{seq}.json
        → meta_pk = cycleId,                meta_sk = "{employeeId}#{seq}"

Inbound key schema:
    inbound/{yyyymm}/{employeeId}/{contactId}.wav
        → inbound/{yyyymm}/{employeeId}/{contactId}.json
        → meta_pk = "INBOUND#{contactId}",  meta_sk = "{employeeId}#0"

Anything that does not match either schema returns ``None``. Callers
(TranscribeStarter.lambda_handler) raise ``ValueError`` on ``None`` so
unexpected S3 keys surface as a Lambda error rather than a silent skip
(project principle 19(b): no silent fallbacks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# ``[^/]+`` for cycle / employee / contact components so a path like
# ``recordings/a/b/c/0.wav`` does NOT slip through with cycleId="a/b/c".
# ``\d+`` for seq enforces non-negative integer ordering used by Phase 7.2
# Connect rename mapping.
_OUTBOUND_RE = re.compile(r"^recordings/([^/]+)/([^/]+)/(\d+)\.wav$")
# ``\d{6}`` for yyyymm exactly matches the design.md inbound key shape
# (e.g. "202606"). Five- or seven-digit prefixes fail.
_INBOUND_RE = re.compile(r"^inbound/(\d{6})/([^/]+)/([^/]+)\.wav$")

# Phase 8.1: transcript-side key regexes. Mirrors of the recording regexes
# but with the ``.json`` extension. Transcribe writes its JSON output at
# these paths (see TranscribeStarter Phase 6.4 ``OutputKey`` assignment),
# and KeywordMatcher reverses the mapping on the S3 ObjectCreated event.
_OUTBOUND_TRANSCRIPT_RE = re.compile(r"^transcripts/([^/]+)/([^/]+)/(\d+)\.json$")
_INBOUND_TRANSCRIPT_RE = re.compile(r"^inbound/(\d{6})/([^/]+)/([^/]+)\.json$")

# AWS Transcribe job name constraints (per public API doc):
#   * Pattern ``^[0-9a-zA-Z._-]+$``  (length 1-200)
#   * Must be unique per AWS account / region.
# Our naming rule ``safety-confirm-{meta_pk}-{meta_sk}`` carries enough
# entropy from cycleId / contactId / employeeId to be unique. The pk/sk
# values can contain ``#`` (inbound prefix) and ``/`` / ``:`` (defensive
# coverage against future encoding choices) — all replaced with ``-``.
_JOB_NAME_SANITIZE_RE = re.compile(r"[:/#]")
_TRANSCRIBE_JOB_NAME_MAX = 200


@dataclass(frozen=True, slots=True)
class RecordingKeyInfo:
    """Parsed components of an S3 recording object key.

    Immutable so callers can pass it around without defensive copies.

    Attributes:
        kind: ``"outbound"`` for cycle-driven recordings,
            ``"inbound"`` for caller-initiated recordings.
        cycle_id_or_inbound_prefix: For outbound, the raw cycleId
            (e.g. ``"cycle-1"``). For inbound, the TranscriptMetaTable
            PK form ``"INBOUND#{contactId}"``.
        employee_id: The employee record's UUID.
        seq_or_contact: For outbound, the call sequence (string-typed
            because the SK uses string concatenation; the regex
            guarantees it's all digits). For inbound, the Connect
            contactId.
        transcript_s3_key: The S3 key to which Amazon Transcribe should
            write the JSON output.
        meta_pk: TranscriptMetaTable partition key.
        meta_sk: TranscriptMetaTable sort key.
    """

    kind: Literal["outbound", "inbound"]
    cycle_id_or_inbound_prefix: str
    employee_id: str
    seq_or_contact: str
    transcript_s3_key: str
    meta_pk: str
    meta_sk: str


def parse_recording_key(key: str) -> RecordingKeyInfo | None:
    """Parse an S3 recording object key into its components.

    Pure function. No I/O. Returns ``None`` for any key that does not
    match either the outbound or inbound schema documented above —
    callers turn ``None`` into a ``ValueError`` so the Lambda fails
    fast rather than silently swallow malformed events.

    Args:
        key: The S3 object key, e.g.
            ``"recordings/cycle-1/emp-1/0.wav"`` or
            ``"inbound/202606/emp-2/contact-abc.wav"``.

    Returns:
        :class:`RecordingKeyInfo` if the key matches either schema,
        otherwise ``None``.
    """
    if not isinstance(key, str) or not key:
        return None

    m = _OUTBOUND_RE.match(key)
    if m is not None:
        cycle_id, employee_id, seq = m.group(1), m.group(2), m.group(3)
        return RecordingKeyInfo(
            kind="outbound",
            cycle_id_or_inbound_prefix=cycle_id,
            employee_id=employee_id,
            seq_or_contact=seq,
            transcript_s3_key=f"transcripts/{cycle_id}/{employee_id}/{seq}.json",
            meta_pk=cycle_id,
            meta_sk=f"{employee_id}#{seq}",
        )

    m = _INBOUND_RE.match(key)
    if m is not None:
        yyyymm, employee_id, contact_id = m.group(1), m.group(2), m.group(3)
        return RecordingKeyInfo(
            kind="inbound",
            cycle_id_or_inbound_prefix=f"INBOUND#{contact_id}",
            employee_id=employee_id,
            seq_or_contact=contact_id,
            transcript_s3_key=f"inbound/{yyyymm}/{employee_id}/{contact_id}.json",
            meta_pk=f"INBOUND#{contact_id}",
            meta_sk=f"{employee_id}#0",
        )

    return None


def derive_transcribe_job_name(meta_pk: str, meta_sk: str) -> str:
    """Derive an idempotent Transcribe job name from meta keys.

    Pure function. Same ``(meta_pk, meta_sk)`` always produces the same
    job name so that re-delivered EventBridge events (S3 retries, Lambda
    at-least-once semantics) target the same Transcribe job — the
    second call returns ``ConflictException`` which the handler treats
    as success.

    The job name follows ``safety-confirm-{meta_pk}-{meta_sk}`` with
    ``:`` / ``/`` / ``#`` replaced with ``-`` (the Transcribe job name
    pattern is ``^[0-9a-zA-Z._-]+$``), then truncated to 200 chars
    (Transcribe's hard limit). Truncation by suffix preserves the
    ``safety-confirm-`` prefix which is useful for log filtering.

    Args:
        meta_pk: TranscriptMetaTable partition key, e.g. ``"cycle-1"``
            or ``"INBOUND#contact-abc"``.
        meta_sk: TranscriptMetaTable sort key, e.g. ``"emp-1#0"``.

    Returns:
        A Transcribe-job-name-safe string of length 1..200.

    Raises:
        ValueError: if either input is empty / non-string after
            sanitisation produces an empty result.
    """
    if not isinstance(meta_pk, str) or not isinstance(meta_sk, str):
        raise ValueError(
            f"meta_pk and meta_sk must be strings; "
            f"got meta_pk={type(meta_pk).__name__} meta_sk={type(meta_sk).__name__}"
        )
    if not meta_pk or not meta_sk:
        raise ValueError(
            f"meta_pk and meta_sk must be non-empty; got meta_pk={meta_pk!r} meta_sk={meta_sk!r}"
        )

    raw = f"safety-confirm-{meta_pk}-{meta_sk}"
    sanitized = _JOB_NAME_SANITIZE_RE.sub("-", raw)

    if len(sanitized) > _TRANSCRIBE_JOB_NAME_MAX:
        return sanitized[:_TRANSCRIBE_JOB_NAME_MAX]
    return sanitized


def parse_transcript_key(key: str) -> RecordingKeyInfo | None:
    """Parse an S3 transcript object key into its components (Phase 8.1).

    Pure function. No I/O. Mirror of :func:`parse_recording_key` but
    matches the ``.json`` extension that Amazon Transcribe writes to
    the TranscriptsBucket (Phase 6.4 ``TranscribeStarter`` configures
    ``OutputKey`` with the same shape, just with ``.json`` in place of
    ``.wav``).

    Outbound key schema:
        transcripts/{cycleId}/{employeeId}/{seq}.json
            → meta_pk = cycleId, meta_sk = "{employeeId}#{seq}"

    Inbound key schema:
        inbound/{yyyymm}/{employeeId}/{contactId}.json
            → meta_pk = "INBOUND#{contactId}", meta_sk = "{employeeId}#0"

    The returned :class:`RecordingKeyInfo` is the same shape used by
    Phase 6.4 / 7.2 — ``transcript_s3_key`` re-states ``key`` itself
    (KeywordMatcher already has the transcript key on the input event;
    the field exists for downstream callers that want the resolved
    output key without conditional handling).

    Returns ``None`` for any key that matches neither schema; the
    caller (KeywordMatcher.lambda_handler) raises ``ValueError`` so
    malformed events surface as a Lambda error rather than being
    silently swallowed (project principle 19(b): no silent fallbacks).
    """
    if not isinstance(key, str) or not key:
        return None

    m = _OUTBOUND_TRANSCRIPT_RE.match(key)
    if m is not None:
        cycle_id, employee_id, seq = m.group(1), m.group(2), m.group(3)
        return RecordingKeyInfo(
            kind="outbound",
            cycle_id_or_inbound_prefix=cycle_id,
            employee_id=employee_id,
            seq_or_contact=seq,
            transcript_s3_key=key,
            meta_pk=cycle_id,
            meta_sk=f"{employee_id}#{seq}",
        )

    m = _INBOUND_TRANSCRIPT_RE.match(key)
    if m is not None:
        _yyyymm, employee_id, contact_id = m.group(1), m.group(2), m.group(3)
        return RecordingKeyInfo(
            kind="inbound",
            cycle_id_or_inbound_prefix=f"INBOUND#{contact_id}",
            employee_id=employee_id,
            seq_or_contact=contact_id,
            transcript_s3_key=key,
            meta_pk=f"INBOUND#{contact_id}",
            meta_sk=f"{employee_id}#0",
        )

    return None
