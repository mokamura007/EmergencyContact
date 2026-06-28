"""Account-lockout predicate for Requirement 1.6 / Property 8.

Requirement 1.6:
    IF the same account has 5 consecutive auth failures THEN the
    Auth_Service SHALL lock the account for 30 minutes.

Property 8 (target of Hypothesis PBT in Phase 13.x):
    For all H = [t_1, ..., t_k] (each t_i is a failure timestamp, Unix
    epoch seconds) and now,
        is_locked(H, now) is True
        iff
        the last `threshold` elements of sorted(H) are strictly newer
        than (now - window_sec).

Design notes:
    - This module is pure: no I/O, no globals other than the constants.
    - Timestamps are Unix epoch seconds (int).
    - The list does NOT need to be sorted: `is_locked` sorts internally
      so attackers cannot evade detection by submitting out-of-order
      failure times.
"""

from __future__ import annotations

DEFAULT_THRESHOLD = 5
DEFAULT_WINDOW_SEC = 30 * 60  # 30 minutes


def is_locked(
    failed_ats: list[int],
    now_epoch_sec: int,
    threshold: int = DEFAULT_THRESHOLD,
    window_sec: int = DEFAULT_WINDOW_SEC,
) -> bool:
    """Return True iff the account is currently locked.

    The account is locked iff there exist at least `threshold` failure
    timestamps whose values are all strictly greater than
    `now_epoch_sec - window_sec` (i.e., the last `threshold` failures
    all happened within the window).

    Args:
        failed_ats: Past authentication-failure timestamps (Unix epoch
            seconds). Order does not matter — the function sorts a copy
            internally.
        now_epoch_sec: Current Unix epoch seconds.
        threshold: Number of failures within the window required to
            trigger the lock. Default 5 (Requirement 1.6).
        window_sec: Lock window in seconds. Default 1800 (30 minutes,
            Requirement 1.6).

    Returns:
        True iff `len(failed_ats) >= threshold` AND every one of the
        last `threshold` chronological entries is strictly newer than
        `now_epoch_sec - window_sec`.
    """
    if len(failed_ats) < threshold:
        return False
    cutoff = now_epoch_sec - window_sec
    tail = sorted(failed_ats)[-threshold:]
    return all(t > cutoff for t in tail)
