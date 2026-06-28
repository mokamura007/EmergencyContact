"""Amazon Connect native recording S3 key parsing (Phase 7.2).

Amazon Connect, when configured via ``AWS::Connect::InstanceStorageConfig``
with ``ResourceType=CALL_RECORDINGS``, writes call-recording WAV files
to the configured S3 location using a *fixed* path layout that the
service controls. The layout is::

    <configured-bucket-prefix>/<instance-alias-or-id>/CallRecordings/
        <yyyy>/<mm>/<dd>/<contactId>_<timestamp>_UTC.wav

(plus a parallel ``<contactId>_<timestamp>_UTC_<channel>.wav`` for
multi-channel exports when ``RecordingBehavior=Both`` is used).

The project design (``design.md`` / "Recording_Store / S3 オブジェクト
キー命名") expects a different layout::

    recordings/{cycleId}/{employeeId}/{seq}.wav

Phase 7.2 closes that gap with the ``RecordingRelocator`` Lambda: it
parses the Connect-native key, looks up the originating
``(cycleId, employeeId, callAttempts)`` from the Response table (via
the ``ContactIdIndex`` GSI), and ``CopyObject`` / ``DeleteObject`` the
recording into the design-mandated key. This module isolates the
*parsing* half of that pipeline in a pure, side-effect-free function so
it is trivially unit-testable and so Phase 13.x property tests can
exercise it across all syntactically-valid Connect outputs.

Design choices:
    * The prefix portion before ``/CallRecordings/`` is treated as
      opaque. Real-world deployments may include an instance-alias
      segment (``<prefix>/<alias>/CallRecordings/...``) or omit it
      depending on InstanceStorageConfig settings. We do not try to
      reconstruct it.
    * The ``contactId`` is the substring **before the first underscore**
      in the file-name component. Connect contact IDs are UUIDs and
      contain no underscores, so this rule is safe.
    * The trailing ``_<timestamp>_UTC.wav`` portion is matched
      defensively but its inner shape is not parsed — we do not depend
      on Connect's timestamp format because the relocator does not need
      it (the call's actual start/end times live on the Response row).
    * Multi-channel exports (``..._UTC_<channel>.wav``) are accepted as
      well: a trailing ``_<channel>`` between ``_UTC`` and ``.wav`` is
      ignored. The relocator's caller decides whether to keep one
      channel or both — outside the parser's responsibility.

Returns ``None`` for keys that don't match the Connect layout so the
caller can branch on it rather than catch an exception (Project
principle 19(b): no silent fallbacks, but ``None`` here is a
*classified* result rather than a swallow — the relocator turns
``None`` into a fatal ``ValueError`` so unexpected S3 keys surface).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Permissive prefix (``.+?`` non-greedy) plus a fixed ``/CallRecordings/``
# anchor plus ``yyyy/mm/dd`` plus the file-name. The ``contactId`` group
# matches ``[^/_]+`` so Connect's UUID-shaped ID (no underscores) is
# captured without consuming any of the ``_<timestamp>`` tail.
# ``\d{4}/\d{2}/\d{2}`` is enforced to reduce false positives from
# pathological prefixes that happen to contain ``/CallRecordings/``.
# An optional ``_<channel>`` segment is allowed after ``_UTC`` to
# accommodate multi-channel exports.
_CONNECT_RE = re.compile(
    r"^.+?/CallRecordings/(?P<yyyy>\d{4})/(?P<mm>\d{2})/(?P<dd>\d{2})/"
    r"(?P<contactId>[^/_]+)_(?P<timestamp>[^/]+?)_UTC(?:_[^/]+)?\.wav$"
)


@dataclass(frozen=True, slots=True)
class ConnectNativeKeyInfo:
    """Parsed components of a Connect-native call-recording S3 key.

    Attributes:
        contact_id: The Connect ContactId carried in the filename. The
            relocator uses this to query the Response table's
            ``ContactIdIndex`` GSI.
        yyyy: 4-digit year segment of the recording date.
        mm: 2-digit month segment.
        dd: 2-digit day segment.
        timestamp: Raw Connect-provided timestamp string (e.g.
            ``"2026-06-25T07:00:00"``). Preserved for log correlation
            but not used to compute the target key.
    """

    contact_id: str
    yyyy: str
    mm: str
    dd: str
    timestamp: str


def parse_connect_native_key(key: str) -> ConnectNativeKeyInfo | None:
    """Parse an Amazon Connect native recording key.

    Pure function. No I/O.

    Args:
        key: The S3 object key, e.g.
            ``"connect-raw/myinstance/CallRecordings/2026/06/25/<contactId>_2026-06-25T07:00:00_UTC.wav"``.

    Returns:
        :class:`ConnectNativeKeyInfo` if the key matches the
        Connect-native layout, otherwise ``None``.
    """
    if not isinstance(key, str) or not key:
        return None
    m = _CONNECT_RE.match(key)
    if m is None:
        return None
    return ConnectNativeKeyInfo(
        contact_id=m.group("contactId"),
        yyyy=m.group("yyyy"),
        mm=m.group("mm"),
        dd=m.group("dd"),
        timestamp=m.group("timestamp"),
    )


def derive_target_outbound_key(
    cycle_id: str, employee_id: str, seq: int
) -> str:
    """Return the design-mandated outbound recording key.

    Pure function. Mirrors the inverse of :func:`shared.recording.s3_keys.
    parse_recording_key` — ``parse_recording_key`` consumes
    ``recordings/{cycleId}/{employeeId}/{seq}.wav`` while this function
    produces it. Phase 13.x can pair the two in a round-trip property
    test (``parse_recording_key(derive_target_outbound_key(c, e, s))``
    yields ``(c, e, str(s))``).

    Args:
        cycle_id: Owning cycle UUID. Must be a non-empty string without
            ``/`` (would corrupt the key shape).
        employee_id: Employee record UUID. Same constraints.
        seq: Per-employee call sequence number (1-based per Phase 6.2
            ``ConnectDispatcher`` ``ADD callAttempts :one``). Must be
            ``>= 0`` — the project's ``parse_recording_key`` accepts
            ``\\d+`` which includes ``0``, so we mirror that.

    Returns:
        The S3 object key string.

    Raises:
        ValueError: if any input violates the documented constraints.
            Per project principle 19(b), input-shape errors raise
            rather than silently fall back.
    """
    if not isinstance(cycle_id, str) or not cycle_id or "/" in cycle_id:
        raise ValueError(
            f"cycle_id must be a non-empty string without '/'; got {cycle_id!r}"
        )
    if (
        not isinstance(employee_id, str)
        or not employee_id
        or "/" in employee_id
    ):
        raise ValueError(
            f"employee_id must be a non-empty string without '/'; "
            f"got {employee_id!r}"
        )
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise ValueError(f"seq must be a non-negative int; got {seq!r}")
    return f"recordings/{cycle_id}/{employee_id}/{seq}.wav"
