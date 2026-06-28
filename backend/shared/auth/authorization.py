"""Administrator role authorisation predicate for Requirement 1 / Property 1.

Requirement 1.3:
    IF 認証トークンに「管理者」ロールが含まれている, THEN THE Admin_Console
    SHALL 管理者向け機能を表示する。

Requirement 1.4:
    IF 認証トークンに「管理者」ロールが含まれていない, THEN THE Admin_Console
    SHALL 全機能へのアクセスを拒否する。

Requirement 1.9:
    THE Auth_Service SHALL 一般社員ロールおよび一般社員向け画面・API を提供しない。
    → 本予測関数は唯一のロール `Administrator` を判定する関数として運用される。

Property 1 (target of Hypothesis PBT in Phase 13.1):
    For all (claims, required_group):
        is_authorized(claims, required_group) is True
        iff
        (i)   claims is a dict, AND
        (ii)  required_group is a non-empty str, AND
        (iii) claims["cognito:groups"] (parsed) is a non-empty list that
              contains `required_group` (case-sensitive, exact match).

Design notes:
    - This module is pure: no I/O, no globals.
    - The `cognito:groups` claim is delivered by Cognito as either a list[str]
      (when API Gateway deserialises the JSON identity payload) or a
      whitespace-separated string (when read directly from the JWT payload).
      Both shapes are accepted.  Leading / trailing / consecutive whitespace
      is stripped via `str.split()` (no argument).
    - Group name comparison is case-sensitive. Cognito treats group names as
      case-sensitive, and so does this predicate.
    - Any deviation from the contract (claims not a dict, required_group not a
      non-empty str, `cognito:groups` missing / wrong type / empty) returns
      False without raising — fallback per project principle 19(b) is forbidden,
      but here False is the *defined* answer for unauthorised inputs, not a
      fallback for an error.
"""

from __future__ import annotations

from typing import Any

#: Canonical group name for the sole administrator role (Req 1.9).
ADMINISTRATOR_GROUP = "Administrator"

#: JWT claim key carrying Cognito group membership.
COGNITO_GROUPS_CLAIM = "cognito:groups"


def is_authorized(claims: Any, required_group: Any) -> bool:
    """Return True iff `claims` grants access to `required_group`.

    Args:
        claims: Decoded Cognito ID/Access token claims. Typically passed
            through `event["requestContext"]["authorizer"]["claims"]` by
            API Gateway Cognito Authorizer. Must be a `dict` for the
            function to return True.
        required_group: Group name that the caller must belong to (e.g.
            ``"Administrator"``). Must be a non-empty `str` for the
            function to return True.

    Returns:
        True iff all of the following hold:
          * `claims` is a `dict`,
          * `required_group` is a non-empty `str`,
          * `claims[COGNITO_GROUPS_CLAIM]` parses to a non-empty list of
            strings (either a `list[str]` directly, or a whitespace-
            separated `str`),
          * that list contains `required_group` (case-sensitive, exact
            match after whitespace trimming).

        False in every other case.
    """
    # Guard: required_group must be a non-empty str.
    if not isinstance(required_group, str) or required_group == "":
        return False

    # Guard: claims must be a dict.  Note: `bool` is a subclass of `int`,
    # not of `dict`, so we do not need an extra `not isinstance(..., bool)`
    # check here.
    if not isinstance(claims, dict):
        return False

    # Lookup: `cognito:groups` may be absent, None, str, or list.
    if COGNITO_GROUPS_CLAIM not in claims:
        return False
    raw = claims[COGNITO_GROUPS_CLAIM]
    if raw is None:
        return False

    # Normalise to list[str].
    if isinstance(raw, str):
        # `str.split()` (no argument) splits on any run of whitespace AND
        # drops empty leading/trailing/internal tokens.  This handles
        # "Administrator", " Administrator ", "X  Administrator  Y" etc.
        groups: list[str] = raw.split()
    elif isinstance(raw, list):
        # Filter out non-str members defensively; only str members are
        # eligible for the exact-match check.  Cognito itself only emits
        # str members, so a non-str element indicates a malformed claim
        # and must not grant access.
        groups = [g for g in raw if isinstance(g, str)]
    else:
        # Any other type (int, float, dict, tuple, bool, ...) is rejected.
        return False

    return required_group in groups
