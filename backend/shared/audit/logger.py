"""Consolidated audit logger (Phase 12.3 / Requirement 16.3).

Writes a single-line JSON audit record per call to the
``AuditLogGroup`` CloudWatch Logs LogGroup, replacing the previous
per-Lambda ``LOGGER.info(json.dumps(...))`` pattern. Used by:

* ``auth_post_auth`` — AUTH_SUCCESS
* ``auth_failure_reporter`` — AUTH_FAILURE_RECORDED
* ``dictionary_api`` — DICTIONARY_ADD / DICTIONARY_UPDATE / DICTIONARY_DELETE
* ``employee_api`` — EMPLOYEE_ADD / EMPLOYEE_UPDATE / EMPLOYEE_DELETE / EMPLOYEE_CSV_IMPORT / EMPLOYEE_ANONYMIZE
* ``cycle_api`` — CYCLE_START / CYCLE_START_REJECTED
* ``inbound_handler`` — INBOUND_CONTACT_RECEIVED

Operating contract (Requirement 16.3 / Property 21):
    Every record carries (1) ``event``, (2) ``timestamp`` (ISO 8601 UTC
    with trailing ``Z``), (3) ``principal``, (4) ``target``, plus
    optional (5) ``phoneMasked`` (Property 22 via :func:`mask_phone`)
    and (6) ``extra`` fields merged at the top level.

Stream naming:
    ``<lambda-function-name>/<YYYY-MM-DD>`` — one stream per Lambda
    per UTC day. ``AWS_LAMBDA_FUNCTION_NAME`` is auto-populated by the
    Lambda runtime; falls back to ``"local"`` for unit-test contexts.

Failure policy (project principle 19(b)):
    Errors from ``put_log_events`` / ``create_log_stream`` are NOT
    swallowed. The single benign exception is
    ``ResourceAlreadyExistsException`` on ``create_log_stream``, which
    is the expected path on the second-and-subsequent call per stream.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .mask import mask_phone

LOGGER = logging.getLogger(__name__)

#: Reserved keys at the top level of the audit record. ``extra`` fields
#: that collide with these are silently ignored to keep the wire format
#: stable for downstream consumers (Athena / dashboards).
_RESERVED_KEYS: frozenset[str] = frozenset(
    {"event", "timestamp", "principal", "target", "outcome", "phoneMasked"}
)

#: Module-level cache for the boto3 ``logs`` client. Tests replace this
#: directly via ``monkeypatch.setattr(logger, "_LOGS_CLIENT", mock)``.
_LOGS_CLIENT: Any = None

#: Streams known to already exist (Lambda container persistence). Cache
#: avoids issuing one ``create_log_stream`` per audit emit.
_CREATED_STREAMS: set[str] = set()


def _get_client() -> Any:
    """Return the cached boto3 ``logs`` client, creating it on first use."""
    global _LOGS_CLIENT
    if _LOGS_CLIENT is None:
        _LOGS_CLIENT = boto3.client("logs")
    return _LOGS_CLIENT


def _now_iso() -> str:
    """Return the current instant as ISO 8601 UTC with trailing ``Z``."""
    return (
        dt.datetime.now(tz=dt.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _now_epoch_ms() -> int:
    """Return the current instant as milliseconds since the Unix epoch."""
    return int(dt.datetime.now(tz=dt.UTC).timestamp() * 1000)


def _function_name() -> str:
    """Return the active Lambda function name (or ``"local"`` for tests)."""
    return os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "local")


def _today_yyyymmdd() -> str:
    """Return today (UTC) as ``YYYY-MM-DD``."""
    return dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")


def _stream_name() -> str:
    """Return the canonical log-stream name for this Lambda / day."""
    return f"{_function_name()}/{_today_yyyymmdd()}"


def _ensure_log_stream(
    client: Any, log_group_name: str, log_stream_name: str
) -> None:
    """Create the log stream if it does not already exist.

    The CloudWatch Logs API rejects ``put_log_events`` with
    ``ResourceNotFoundException`` if the target stream is missing.
    ``ResourceAlreadyExistsException`` is the expected steady-state
    response after the first call per stream — we cache the stream
    name to avoid the redundant API call, but treat the exception as
    benign for cache misses (cold start in a new container).
    """
    cache_key = f"{log_group_name}::{log_stream_name}"
    if cache_key in _CREATED_STREAMS:
        return
    try:
        client.create_log_stream(
            logGroupName=log_group_name,
            logStreamName=log_stream_name,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code != "ResourceAlreadyExistsException":
            raise
    _CREATED_STREAMS.add(cache_key)


def write_audit_log(
    *,
    event_type: str,
    principal: str,
    target: str,
    outcome: str = "SUCCESS",
    timestamp: str | None = None,
    phone: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write one audit record to ``AuditLogGroup``.

    Args:
        event_type: Logical event identifier (e.g. ``"AUTH_SUCCESS"``).
        principal: The actor who triggered the event (Cognito sub,
            ``"<anonymous>"`` for public endpoints, ``"<connect-service>"``
            for Amazon Connect Contact Flow invocations).
        target: The entity acted upon (employee id, ``category#keyword``,
            cycle id, contact id, ...).
        outcome: One of ``"SUCCESS"`` (default) / ``"REJECTED"`` /
            ``"RECORDED"`` etc. Free-form short identifier.
        timestamp: ISO 8601 UTC string. If omitted, ``now()`` is used.
        phone: Optional E.164 phone number. When provided, it is masked
            via :func:`mask_phone` and stored as ``phoneMasked``.
        extra: Optional additional fields merged at the top level of
            the JSON record. Keys colliding with reserved fields are
            silently dropped.

    Raises:
        KeyError: ``AUDIT_LOG_GROUP_NAME`` env var is missing.
        ClientError: Any non-benign CloudWatch Logs API error.
    """
    log_group_name = os.environ["AUDIT_LOG_GROUP_NAME"]
    ts_iso = timestamp or _now_iso()
    stream_name = _stream_name()

    record: dict[str, Any] = {
        "event": event_type,
        "timestamp": ts_iso,
        "principal": principal,
        "target": target,
        "outcome": outcome,
    }
    if phone is not None:
        record["phoneMasked"] = mask_phone(phone)
    if extra:
        for key, value in extra.items():
            if key not in _RESERVED_KEYS:
                record[key] = value

    client = _get_client()
    _ensure_log_stream(client, log_group_name, stream_name)
    client.put_log_events(
        logGroupName=log_group_name,
        logStreamName=stream_name,
        logEvents=[
            {
                "timestamp": _now_epoch_ms(),
                "message": json.dumps(record, ensure_ascii=False),
            }
        ],
    )
