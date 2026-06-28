"""Unit tests for ``shared.audit.logger`` (Phase 12.3).

Validates the contract documented in :mod:`shared.audit.logger`:
    * every record carries the 5 reserved top-level fields,
    * ``phoneMasked`` is present iff ``phone`` is supplied,
    * ``extra`` merges at the top level but cannot overwrite reserved
      keys,
    * stream name is ``<function>/<YYYY-MM-DD>``,
    * ``create_log_stream`` is called once per stream then cached, and
      ``ResourceAlreadyExistsException`` is treated as benign,
    * ``AUDIT_LOG_GROUP_NAME`` env-var absence raises ``KeyError``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from shared.audit import logger as audit_logger


# --- Fixtures ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Reset module-level caches before every test."""
    audit_logger._CREATED_STREAMS.clear()
    audit_logger._LOGS_CLIENT = None


@pytest.fixture
def mock_logs_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="LogsClient")
    monkeypatch.setattr(audit_logger, "_LOGS_CLIENT", client)
    return client


@pytest.fixture(autouse=True)
def _seed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_LOG_GROUP_NAME", "/aws/safety-confirmation/audit-test")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "test-fn")


# --- Helpers -----------------------------------------------------------


def _extract_put_log_events_call(client: MagicMock) -> dict[str, Any]:
    """Return the kwargs of the most recent put_log_events call."""
    client.put_log_events.assert_called_once()
    return client.put_log_events.call_args.kwargs


def _record_from_call(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON ``message`` from the put_log_events payload."""
    events = kwargs["logEvents"]
    assert len(events) == 1
    message = events[0]["message"]
    return json.loads(message)


# --- Tests -------------------------------------------------------------


def test_write_audit_log_records_reserved_fields(
    mock_logs_client: MagicMock,
) -> None:
    """A minimal call emits event/timestamp/principal/target/outcome."""
    audit_logger.write_audit_log(
        event_type="AUTH_SUCCESS",
        principal="user-123",
        target="user-123",
    )

    kwargs = _extract_put_log_events_call(mock_logs_client)
    record = _record_from_call(kwargs)
    assert record["event"] == "AUTH_SUCCESS"
    assert record["principal"] == "user-123"
    assert record["target"] == "user-123"
    assert record["outcome"] == "SUCCESS"
    assert "timestamp" in record


def test_write_audit_log_default_timestamp_iso_utc(
    mock_logs_client: MagicMock,
) -> None:
    """Default timestamp is ISO 8601 UTC with trailing ``Z``."""
    audit_logger.write_audit_log(
        event_type="X", principal="p", target="t"
    )

    record = _record_from_call(_extract_put_log_events_call(mock_logs_client))
    ts = record["timestamp"]
    assert ts.endswith("Z")
    # Parseable as ISO 8601 UTC (Python 3.11+ accepts the Z form).
    parsed = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_write_audit_log_explicit_timestamp_passthrough(
    mock_logs_client: MagicMock,
) -> None:
    """Explicit timestamp argument is preserved verbatim."""
    explicit = "2026-06-26T12:34:56Z"
    audit_logger.write_audit_log(
        event_type="X",
        principal="p",
        target="t",
        timestamp=explicit,
    )

    record = _record_from_call(_extract_put_log_events_call(mock_logs_client))
    assert record["timestamp"] == explicit


def test_write_audit_log_outcome_override(
    mock_logs_client: MagicMock,
) -> None:
    """Outcome argument overrides the default ``"SUCCESS"``."""
    audit_logger.write_audit_log(
        event_type="CYCLE_START_REJECTED",
        principal="p",
        target="dictionary_empty",
        outcome="REJECTED",
    )

    record = _record_from_call(_extract_put_log_events_call(mock_logs_client))
    assert record["outcome"] == "REJECTED"


def test_write_audit_log_phone_is_masked(
    mock_logs_client: MagicMock,
) -> None:
    """Phone numbers are masked via :func:`mask_phone`."""
    audit_logger.write_audit_log(
        event_type="EMPLOYEE_ADD",
        principal="p",
        target="emp-001",
        phone="+819012345678",
    )

    record = _record_from_call(_extract_put_log_events_call(mock_logs_client))
    assert record["phoneMasked"] == "+********5678"


def test_write_audit_log_phone_none_omits_field(
    mock_logs_client: MagicMock,
) -> None:
    """phone=None leaves ``phoneMasked`` absent."""
    audit_logger.write_audit_log(
        event_type="X", principal="p", target="t", phone=None
    )

    record = _record_from_call(_extract_put_log_events_call(mock_logs_client))
    assert "phoneMasked" not in record


def test_write_audit_log_extra_merged_top_level(
    mock_logs_client: MagicMock,
) -> None:
    """``extra`` fields appear at the top level of the JSON record."""
    audit_logger.write_audit_log(
        event_type="DICTIONARY_ADD",
        principal="admin-1",
        target="SAFE#無事",
        extra={"category": "SAFE", "keyword": "無事", "newVersion": 7},
    )

    record = _record_from_call(_extract_put_log_events_call(mock_logs_client))
    assert record["category"] == "SAFE"
    assert record["keyword"] == "無事"
    assert record["newVersion"] == 7


def test_write_audit_log_extra_cannot_overwrite_reserved(
    mock_logs_client: MagicMock,
) -> None:
    """Reserved-key collisions in ``extra`` are silently ignored."""
    audit_logger.write_audit_log(
        event_type="X",
        principal="p",
        target="t",
        extra={"event": "TAMPERED", "principal": "TAMPERED", "newField": "kept"},
    )

    record = _record_from_call(_extract_put_log_events_call(mock_logs_client))
    assert record["event"] == "X"
    assert record["principal"] == "p"
    assert record["newField"] == "kept"


def test_write_audit_log_uses_audit_log_group_env(
    mock_logs_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``logGroupName`` is read from ``AUDIT_LOG_GROUP_NAME`` env var."""
    monkeypatch.setenv("AUDIT_LOG_GROUP_NAME", "/aws/test-group")

    audit_logger.write_audit_log(event_type="X", principal="p", target="t")

    kwargs = _extract_put_log_events_call(mock_logs_client)
    assert kwargs["logGroupName"] == "/aws/test-group"


def test_write_audit_log_missing_audit_log_group_env_raises(
    mock_logs_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing ``AUDIT_LOG_GROUP_NAME`` env var raises ``KeyError``.

    Project principle 19(b): no silent fallback for required config.
    """
    monkeypatch.delenv("AUDIT_LOG_GROUP_NAME", raising=False)

    with pytest.raises(KeyError):
        audit_logger.write_audit_log(event_type="X", principal="p", target="t")


def test_write_audit_log_stream_name_format(
    mock_logs_client: MagicMock,
) -> None:
    """Stream name format is ``<function-name>/<YYYY-MM-DD>``."""
    audit_logger.write_audit_log(event_type="X", principal="p", target="t")

    kwargs = _extract_put_log_events_call(mock_logs_client)
    stream_name = kwargs["logStreamName"]
    # ``test-fn`` is set in autouse fixture.
    assert stream_name.startswith("test-fn/")
    suffix = stream_name.split("/", 1)[1]
    parsed = dt.datetime.strptime(suffix, "%Y-%m-%d")  # noqa: DTZ007
    assert parsed.year >= 2025


def test_write_audit_log_local_fallback_when_no_function_name(
    mock_logs_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falls back to ``"local/..."`` when AWS_LAMBDA_FUNCTION_NAME absent."""
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)

    audit_logger.write_audit_log(event_type="X", principal="p", target="t")

    kwargs = _extract_put_log_events_call(mock_logs_client)
    assert kwargs["logStreamName"].startswith("local/")


def test_write_audit_log_creates_stream_once_then_caches(
    mock_logs_client: MagicMock,
) -> None:
    """Repeated emits in one process call create_log_stream only once."""
    for _ in range(3):
        audit_logger.write_audit_log(event_type="X", principal="p", target="t")

    # create_log_stream invoked once on the first call; cached thereafter.
    assert mock_logs_client.create_log_stream.call_count == 1
    assert mock_logs_client.put_log_events.call_count == 3


def test_write_audit_log_swallows_resource_already_exists(
    mock_logs_client: MagicMock,
) -> None:
    """Existing stream (cold-start race) yields no error."""
    mock_logs_client.create_log_stream.side_effect = ClientError(
        {"Error": {"Code": "ResourceAlreadyExistsException", "Message": "exists"}},
        "CreateLogStream",
    )

    # No exception should escape.
    audit_logger.write_audit_log(event_type="X", principal="p", target="t")
    mock_logs_client.put_log_events.assert_called_once()


def test_write_audit_log_propagates_other_client_errors(
    mock_logs_client: MagicMock,
) -> None:
    """Non-benign CloudWatch Logs errors propagate to the caller."""
    mock_logs_client.create_log_stream.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
        "CreateLogStream",
    )

    with pytest.raises(ClientError):
        audit_logger.write_audit_log(event_type="X", principal="p", target="t")
    mock_logs_client.put_log_events.assert_not_called()


def test_write_audit_log_event_timestamp_is_epoch_ms(
    mock_logs_client: MagicMock,
) -> None:
    """logEvents[].timestamp is epoch milliseconds (int)."""
    audit_logger.write_audit_log(event_type="X", principal="p", target="t")

    kwargs = _extract_put_log_events_call(mock_logs_client)
    epoch_ms = kwargs["logEvents"][0]["timestamp"]
    assert isinstance(epoch_ms, int)
    # Plausibility check: 2025-01-01 < value < 2100-01-01 in ms.
    assert 1_735_689_600_000 < epoch_ms < 4_102_444_800_000


def test_write_audit_log_message_is_valid_json_with_japanese(
    mock_logs_client: MagicMock,
) -> None:
    """JSON message preserves Japanese characters (ensure_ascii=False)."""
    audit_logger.write_audit_log(
        event_type="DICTIONARY_ADD",
        principal="admin",
        target="SAFE#無事",
        extra={"keyword": "無事"},
    )

    kwargs = _extract_put_log_events_call(mock_logs_client)
    raw_message = kwargs["logEvents"][0]["message"]
    assert "無事" in raw_message
    parsed = json.loads(raw_message)
    assert parsed["keyword"] == "無事"
