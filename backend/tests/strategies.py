"""Shared Hypothesis strategies for Phase 13 PBT tests (DRY).

Re-used by Property 22 (mask_phone), Property 3 (isValidE164),
Property 5 (findDuplicatePhone) and other phone-number-related properties.
"""

from __future__ import annotations

from hypothesis import strategies as st

# E.164 spec: "+" followed by 1..15 decimal digits.
E164_MIN_DIGITS = 1
E164_MAX_DIGITS = 15

#: Strategy generating valid E.164 phone strings: "+" + 1..15 digits.
e164_phone = st.integers(
    min_value=E164_MIN_DIGITS, max_value=E164_MAX_DIGITS
).flatmap(
    lambda n: st.text(alphabet="0123456789", min_size=n, max_size=n).map(
        lambda body: "+" + body
    )
)

#: Strategy generating valid Japanese domestic phone numbers: "0" + 9..10 digits.
#: Covers mobile (090/080/070 = 11 digits total) and landline (03/06 etc = 10 digits total).
domestic_jp_phone = st.integers(
    min_value=9, max_value=10
).flatmap(
    lambda n: st.text(alphabet="0123456789", min_size=n, max_size=n).map(
        lambda body: "0" + body
    )
)

#: Strategy generating valid E.164 strings whose body is long enough (>=5)
#: that mask_phone actually masks (n>=5 means body has >=5 digits, but the
#: contract masks when len(body) > 4, i.e. body length 5..15).
e164_phone_maskable = st.integers(min_value=5, max_value=E164_MAX_DIGITS).flatmap(
    lambda n: st.text(alphabet="0123456789", min_size=n, max_size=n).map(
        lambda body: "+" + body
    )
)

#: Strategy generating short E.164 strings whose body is 1..4 digits
#: (mask_phone returns these unchanged by contract).
e164_phone_short = st.integers(min_value=1, max_value=4).flatmap(
    lambda n: st.text(alphabet="0123456789", min_size=n, max_size=n).map(
        lambda body: "+" + body
    )
)

#: Strategy generating arbitrary strings that do NOT start with "+".
#: Used for the non-E.164 best-effort branch of mask_phone.
non_e164_string = st.text(min_size=0, max_size=20).filter(
    lambda s: not s.startswith("+")
)


# ---------------------------------------------------------------------------
# Property 3 — is_valid_e164 negative-side strategies (Phase 13.3).
# ---------------------------------------------------------------------------

#: Strategy generating "+" + (16..30 ASCII digits) — body exceeds the E.164
#: maximum of 15 digits, so is_valid_e164 must return False.
plus_too_many_digits = st.integers(min_value=E164_MAX_DIGITS + 1, max_value=30).flatmap(
    lambda n: st.text(alphabet="0123456789", min_size=n, max_size=n).map(
        lambda body: "+" + body
    )
)

#: ASCII characters that are NOT decimal digits and NOT "+", used to inject
#: invalid characters into an otherwise digit-only body.  Restricting to
#: ASCII avoids Python's `\d` matching Unicode digit codepoints (e.g. "１",
#: "٠"), which would create accidental successes against the strategy intent.
_ASCII_NON_DIGIT_NON_PLUS = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "!@#$%^&*()-_=[]{}|;:,./<>?\\\"' "
)

#: Strategy generating "+" + (ASCII string of length 1..20 that contains at
#: least one non-digit character).  Body may also contain digits, but the
#: presence of any non-digit guarantees is_valid_e164 returns False.
plus_non_digit_body = (
    st.text(
        alphabet=_ASCII_NON_DIGIT_NON_PLUS + "0123456789",
        min_size=1,
        max_size=20,
    )
    .filter(lambda body: any(c not in "0123456789" for c in body))
    .map(lambda body: "+" + body)
)

#: Strategy generating values that are not `str` — covers the
#: `isinstance(phone, str)` guard branch in is_valid_e164.  Includes
#: int, float, bool, None, bytes, list, dict, tuple, and set.
non_string_value = st.one_of(
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.booleans(),
    st.none(),
    st.binary(max_size=20),
    st.lists(st.integers(), max_size=5),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=3),
    st.tuples(st.integers()),
    st.sets(st.integers(), max_size=3),
)


# ---------------------------------------------------------------------------
# Property 8 (and reusable for Property 23) — epoch-seconds strategies.
# ---------------------------------------------------------------------------

#: Default lockout window in seconds (mirrors shared/auth/lockout.py).
DEFAULT_WINDOW_SEC = 30 * 60  # 30 minutes

#: Strategy generating realistic Unix epoch seconds.  Lower bound is
#: DEFAULT_WINDOW_SEC so that `now - window_sec` never underflows below
#: zero in lockout / expiry property tests.  Upper bound is well within
#: signed 32-bit to avoid pathological large-int behaviour.
epoch_sec = st.integers(min_value=DEFAULT_WINDOW_SEC, max_value=2**31 - 1)


# ---------------------------------------------------------------------------
# Property 23 (and reusable for Property 17 / 24 等) — ISO 8601 UTC strategies.
#
# Re-used by `shared.recording.expiry.can_issue_url` / `compute_expiry`,
# which accept either `+00:00` suffix or `Z` suffix (the implementation
# performs `s.replace("Z", "+00:00")` before `datetime.fromisoformat`).
# ---------------------------------------------------------------------------

from datetime import datetime, timezone

#: Number of seconds in a single day. `_parse_iso` round-trips to second
#: precision via `datetime.fromisoformat`, so all generators below are
#: second-accurate (sub-second precision is unnecessary for Property 23).
SECONDS_PER_DAY = 86_400

#: Default retention window enforced by `shared.recording.expiry`
#: (Requirement 10.7: recording / transcript 90-day retention).
DEFAULT_MAX_DAYS = 90


def _epoch_to_iso(sec: int, use_z_suffix: bool) -> str:
    """Convert epoch seconds to an ISO 8601 UTC string.

    Both representations are valid inputs to `can_issue_url` / `compute_expiry`:
      - `2026-06-25T12:34:56+00:00`  (Python `datetime.isoformat` default)
      - `2026-06-25T12:34:56Z`       (RFC 3339 zulu suffix; produced by
                                      `_parse_iso` after `replace("Z", "+00:00")`)
    """
    iso = datetime.fromtimestamp(sec, tz=timezone.utc).isoformat()
    if use_z_suffix:
        iso = iso.replace("+00:00", "Z")
    return iso


#: Strategy generating ISO 8601 UTC strings.  Suffix style (`Z` vs `+00:00`)
#: is randomly chosen so callers can verify both forms behave identically.
iso8601_utc = st.builds(
    _epoch_to_iso,
    epoch_sec,
    st.booleans(),
)


@st.composite
def iso_pair_within_days(
    draw: st.DrawFn, max_days: int = DEFAULT_MAX_DAYS
) -> tuple[str, str]:
    """Generate (ref_iso, now_iso) with `(now - ref)` in [0, max_days days].

    Guarantees `can_issue_url(ref_iso, now_iso, max_days)` is True under the
    contract `(now - ref) <= max_days days` (inclusive on both ends, since
    delta == 0 and delta == max_days*86400 are both within the inclusive
    upper bound).
    """
    window_sec = max_days * SECONDS_PER_DAY
    # `now` must be large enough that `now - window_sec >= 0` and the
    # `epoch_sec` lower-bound stays above 0 for `ref`.
    now_sec = draw(
        st.integers(min_value=window_sec + DEFAULT_WINDOW_SEC, max_value=2**31 - 1)
    )
    delta_sec = draw(st.integers(min_value=0, max_value=window_sec))
    ref_sec = now_sec - delta_sec
    ref_z = draw(st.booleans())
    now_z = draw(st.booleans())
    return _epoch_to_iso(ref_sec, ref_z), _epoch_to_iso(now_sec, now_z)


@st.composite
def iso_pair_over_days(
    draw: st.DrawFn, max_days: int = DEFAULT_MAX_DAYS
) -> tuple[str, str]:
    """Generate (ref_iso, now_iso) with `(now - ref)` strictly greater than
    `max_days * 86400` seconds.

    Guarantees `can_issue_url(ref_iso, now_iso, max_days)` is False under the
    contract `(now - ref) <= max_days days` (strict `>` on the negative side).
    """
    window_sec = max_days * SECONDS_PER_DAY
    # `now` must leave room for `delta >= window_sec + 1` while keeping `ref >= 0`.
    now_sec = draw(
        st.integers(min_value=window_sec + 1, max_value=2**31 - 1)
    )
    delta_sec = draw(st.integers(min_value=window_sec + 1, max_value=now_sec))
    ref_sec = now_sec - delta_sec
    ref_z = draw(st.booleans())
    now_z = draw(st.booleans())
    return _epoch_to_iso(ref_sec, ref_z), _epoch_to_iso(now_sec, now_z)
