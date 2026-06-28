"""90-day expiry predicate for recording / transcript URL issuance (Property 23).

Requirement 10.7 / 13.7: Recording and transcript objects are retained
for 90 days. Beyond that the API must return 410 Gone instead of a
presigned URL.

Property 23 (target of Hypothesis PBT in Phase 13.x):
    For all reference timestamps t_ref and current timestamps t_now,
        can_issue_url(t_ref, t_now, max_days=90) is True
        iff
        (t_now - t_ref) <= 90 days.

    Additionally, compute_expiry(t_now) returns the maximum-acceptable
    reference time: any older value yields can_issue_url == False.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PRESIGNED_URL_TTL_SECONDS = 15 * 60  # 15 minutes


def _parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 timestamp. Accepts both `+00:00` and `Z` suffix."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def can_issue_url(reference_iso: str, now_iso: str, max_days: int = 90) -> bool:
    """Return True iff the reference time is within `max_days` of now.

    Pure function. No I/O. Both parameters are ISO 8601 strings.
    """
    ref = _parse_iso(reference_iso)
    now = _parse_iso(now_iso)
    return (now - ref) <= timedelta(days=max_days)


def compute_expiry(now_iso: str, max_days: int = 90) -> datetime:
    """Return the earliest reference time still eligible for URL issuance."""
    now = _parse_iso(now_iso)
    return now - timedelta(days=max_days)


def now_iso_utc() -> str:
    """Return the current UTC time as an ISO 8601 string with 'Z' suffix."""
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
