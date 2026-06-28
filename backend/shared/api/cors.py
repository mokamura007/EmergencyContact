"""CORS response header helpers (Phase 15.2a hotfix).

All six API Lambda handlers
(dictionary_api / cycle_api / employee_api / response_api / recording_api
/ auth_failure_reporter) used to return responses without any
``Access-Control-Allow-*`` headers, so the CloudFront-hosted SPA at
``https://dn8bulnup9krf.cloudfront.net`` could not invoke them from
the browser. This module centralises the CORS headers so the fix is
DRY (Principle 19 (a)) and the allowed origin is configurable via
environment variable for the future move from ``*`` to a specific
CloudFront / custom domain.

Headers returned (matching the API Gateway OPTIONS Mock Integration
and GatewayResponse definitions in ``infrastructure/template.yaml``):

    Access-Control-Allow-Origin:  <origin>        (default ``*``)
    Access-Control-Allow-Headers: Content-Type,Authorization,X-Idempotency-Key
    Access-Control-Allow-Methods: GET,POST,PUT,DELETE,PATCH,OPTIONS

Environment variable:
    ``CORS_ALLOWED_ORIGIN`` — overrides the default when set. Unset or
    empty -> falls back to ``*``.
"""

from __future__ import annotations

import os

ALLOW_HEADERS = "Content-Type,Authorization,X-Idempotency-Key"
ALLOW_METHODS = "GET,POST,PUT,DELETE,PATCH,OPTIONS"


def _default_origin() -> str:
    """Return the configured allowed origin, defaulting to ``*``."""
    value = os.environ.get("CORS_ALLOWED_ORIGIN", "")
    return value if value else "*"


def build_cors_headers(allowed_origin: str | None = None) -> dict[str, str]:
    """Return the three CORS response headers as a plain dict.

    Args:
        allowed_origin: Explicit ``Access-Control-Allow-Origin`` value.
            When ``None`` (the default), reads ``CORS_ALLOWED_ORIGIN``
            from the environment, falling back to ``"*"``.

    Returns:
        A fresh ``dict[str, str]`` so callers may mutate it freely.
    """
    origin = allowed_origin if allowed_origin is not None else _default_origin()
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": ALLOW_HEADERS,
        "Access-Control-Allow-Methods": ALLOW_METHODS,
    }


def with_cors_headers(
    headers: dict[str, str], allowed_origin: str | None = None
) -> dict[str, str]:
    """Return a new dict that merges CORS headers into ``headers``.

    Caller-provided keys take precedence over the CORS defaults so a
    handler can override ``Access-Control-Allow-Origin`` per-request if
    ever needed. The input dict is not mutated.

    Args:
        headers: Existing response headers (e.g. ``Content-Type``).
        allowed_origin: See :func:`build_cors_headers`.
    """
    merged = build_cors_headers(allowed_origin)
    merged.update(headers)
    return merged
