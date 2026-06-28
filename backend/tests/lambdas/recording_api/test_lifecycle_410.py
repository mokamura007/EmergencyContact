"""Boundary-condition 90-day lifecycle 410 tests for RecordingApi (Task 14.7a).

The handler implements (Requirements 10.7 / 12.3 / 13.7, Property 23):

    if (now - reference) > 90 days:
        return 410 Gone with {error: "...90-day...", cycleId|contactId, startedAt|receivedAt}
    else:
        return 200 OK with a 15-minute presigned URL.

Where ``reference`` is ``cycle.startedAt`` for outbound routes and
``inboundContact.receivedAt`` for inbound routes.

Per Task 14.7a, this file exercises the *four* boundary points enumerated
in the task body for each of the *four* routes:

    ref = now - 90d           → delta = 90d           → 200 OK   (inclusive `<=` upper bound)
    ref = now - 90d + 1 sec   → delta = 90d - 1 sec   → 200 OK   (just inside)
    ref = now - 90d - 1 sec   → delta = 90d + 1 sec   → 410 Gone (just outside)
    ref = now - 91d           → delta = 91d           → 410 Gone (clearly outside)

= 4 endpoints × 4 boundary points = **16 cases**.

Approach: handler's ``now_iso_utc`` and DynamoDB / S3 module-level singletons
are monkeypatched so the boundary arithmetic is deterministic and no AWS
calls are made (Bii: Connect-independent verification, matching tasks.md
14.7a option (i) "Local moto/boto3 stubber + handler 直接呼出").
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from lambdas.recording_api import handler

# --- Anchored "now" so 90-day boundary arithmetic is deterministic --------

_NOW_ISO = "2024-01-15T12:00:00Z"
_NOW_DT = dt.datetime.fromisoformat(_NOW_ISO.replace("Z", "+00:00"))


def _iso(d: dt.datetime) -> str:
    return d.isoformat(timespec="seconds").replace("+00:00", "Z")


def _ref_iso(delta: dt.timedelta) -> str:
    """Return ISO 8601 string for an anchored reference time ``now - delta``."""
    return _iso(_NOW_DT - delta)


# Boundary points: (age_of_reference_from_now, expected_status, label).
# Reading: ``ref = now - delta`` so larger delta = older reference = more likely 410.
_BOUNDARY_POINTS: list[tuple[dt.timedelta, int, str]] = [
    (dt.timedelta(days=90), 200, "exactly_90_days"),
    (dt.timedelta(days=90) - dt.timedelta(seconds=1), 200, "90_days_minus_1_second"),
    (dt.timedelta(days=90) + dt.timedelta(seconds=1), 410, "90_days_plus_1_second"),
    (dt.timedelta(days=91), 410, "91_days"),
]


# --- Fixtures: handler module-level singletons & frozen now ---------------


@pytest.fixture
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin handler's ``now_iso_utc`` to the anchored constant."""
    monkeypatch.setattr(handler, "now_iso_utc", lambda: _NOW_ISO)


@pytest.fixture
def mock_cycle_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="CycleTable")
    monkeypatch.setattr(handler, "_CYCLE_TABLE", table)
    return table


@pytest.fixture
def mock_inbound_table(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    table = MagicMock(name="InboundContactTable")
    monkeypatch.setattr(handler, "_INBOUND_TABLE", table)
    return table


@pytest.fixture
def mock_s3(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="S3")
    client.generate_presigned_url.return_value = (
        "https://example.s3.amazonaws.com/presigned"
    )
    monkeypatch.setattr(handler, "_S3", client)
    return client


# --- Event builders -------------------------------------------------------


def _cycle_recording_event() -> dict[str, Any]:
    return {
        "httpMethod": "GET",
        "resource": "/cycles/{id}/recordings/{employeeId}/{seq}",
        "pathParameters": {"id": "cycle-1", "employeeId": "emp-001", "seq": "1"},
    }


def _cycle_transcript_event() -> dict[str, Any]:
    return {
        "httpMethod": "GET",
        "resource": "/cycles/{id}/transcripts/{employeeId}/{seq}",
        "pathParameters": {"id": "cycle-1", "employeeId": "emp-001", "seq": "1"},
    }


def _inbound_recording_event() -> dict[str, Any]:
    return {
        "httpMethod": "GET",
        "resource": "/inbound/{contactId}/recording",
        "pathParameters": {"contactId": "contact-abc"},
    }


def _inbound_transcript_event() -> dict[str, Any]:
    return {
        "httpMethod": "GET",
        "resource": "/inbound/{contactId}/transcript",
        "pathParameters": {"contactId": "contact-abc"},
    }


# --- Cycle / outbound boundary tests --------------------------------------


@pytest.mark.parametrize(
    ("delta", "expected_status", "label"),
    _BOUNDARY_POINTS,
    ids=[p[2] for p in _BOUNDARY_POINTS],
)
def test_cycle_recording_boundary(
    freeze_now: None,
    mock_cycle_table: MagicMock,
    mock_s3: MagicMock,
    delta: dt.timedelta,
    expected_status: int,
    label: str,
) -> None:
    """GET /cycles/{id}/recordings/{employeeId}/{seq}: 90-day boundary.

    Validates: Requirements 10.7, 12.3, Property 23
    """
    started_at = _ref_iso(delta)
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "startedAt": started_at}
    }

    result = handler.lambda_handler(_cycle_recording_event(), None)

    assert result["statusCode"] == expected_status, (
        f"boundary {label}: expected {expected_status}, got {result['statusCode']}"
    )
    body = json.loads(result["body"])
    if expected_status == 410:
        assert "90-day" in body["error"]
        assert body["cycleId"] == "cycle-1"
        assert body["startedAt"] == started_at
        mock_s3.generate_presigned_url.assert_not_called()
    else:
        assert body["url"] == "https://example.s3.amazonaws.com/presigned"
        assert body["bucket"] == handler.RECORDINGS_BUCKET
        assert body["key"] == "cycles/cycle-1/emp-001#1.wav"
        assert body["expiresInSeconds"] == 900


@pytest.mark.parametrize(
    ("delta", "expected_status", "label"),
    _BOUNDARY_POINTS,
    ids=[p[2] for p in _BOUNDARY_POINTS],
)
def test_cycle_transcript_boundary(
    freeze_now: None,
    mock_cycle_table: MagicMock,
    mock_s3: MagicMock,
    delta: dt.timedelta,
    expected_status: int,
    label: str,
) -> None:
    """GET /cycles/{id}/transcripts/{employeeId}/{seq}: 90-day boundary.

    Validates: Requirements 10.7, 12.3, Property 23
    """
    started_at = _ref_iso(delta)
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "startedAt": started_at}
    }

    result = handler.lambda_handler(_cycle_transcript_event(), None)

    assert result["statusCode"] == expected_status, (
        f"boundary {label}: expected {expected_status}, got {result['statusCode']}"
    )
    body = json.loads(result["body"])
    if expected_status == 410:
        assert "90-day" in body["error"]
        assert body["cycleId"] == "cycle-1"
        assert body["startedAt"] == started_at
        mock_s3.generate_presigned_url.assert_not_called()
    else:
        assert body["url"] == "https://example.s3.amazonaws.com/presigned"
        assert body["bucket"] == handler.TRANSCRIPTS_BUCKET
        assert body["key"] == "cycles/cycle-1/emp-001#1.json"
        assert body["expiresInSeconds"] == 900


# --- Inbound boundary tests -----------------------------------------------


@pytest.mark.parametrize(
    ("delta", "expected_status", "label"),
    _BOUNDARY_POINTS,
    ids=[p[2] for p in _BOUNDARY_POINTS],
)
def test_inbound_recording_boundary(
    freeze_now: None,
    mock_inbound_table: MagicMock,
    mock_s3: MagicMock,
    delta: dt.timedelta,
    expected_status: int,
    label: str,
) -> None:
    """GET /inbound/{contactId}/recording: 90-day boundary.

    Validates: Requirements 13.7, Property 23 (inbound branch)
    """
    received_at = _ref_iso(delta)
    mock_inbound_table.get_item.return_value = {
        "Item": {"contactId": "contact-abc", "receivedAt": received_at}
    }

    result = handler.lambda_handler(_inbound_recording_event(), None)

    assert result["statusCode"] == expected_status, (
        f"boundary {label}: expected {expected_status}, got {result['statusCode']}"
    )
    body = json.loads(result["body"])
    if expected_status == 410:
        assert "90-day" in body["error"]
        assert body["contactId"] == "contact-abc"
        assert body["receivedAt"] == received_at
        mock_s3.generate_presigned_url.assert_not_called()
    else:
        assert body["url"] == "https://example.s3.amazonaws.com/presigned"
        assert body["bucket"] == handler.RECORDINGS_BUCKET
        assert body["key"] == "inbound/contact-abc.wav"
        assert body["expiresInSeconds"] == 900


@pytest.mark.parametrize(
    ("delta", "expected_status", "label"),
    _BOUNDARY_POINTS,
    ids=[p[2] for p in _BOUNDARY_POINTS],
)
def test_inbound_transcript_boundary(
    freeze_now: None,
    mock_inbound_table: MagicMock,
    mock_s3: MagicMock,
    delta: dt.timedelta,
    expected_status: int,
    label: str,
) -> None:
    """GET /inbound/{contactId}/transcript: 90-day boundary.

    Validates: Requirements 13.7, Property 23 (inbound branch)
    """
    received_at = _ref_iso(delta)
    mock_inbound_table.get_item.return_value = {
        "Item": {"contactId": "contact-abc", "receivedAt": received_at}
    }

    result = handler.lambda_handler(_inbound_transcript_event(), None)

    assert result["statusCode"] == expected_status, (
        f"boundary {label}: expected {expected_status}, got {result['statusCode']}"
    )
    body = json.loads(result["body"])
    if expected_status == 410:
        assert "90-day" in body["error"]
        assert body["contactId"] == "contact-abc"
        assert body["receivedAt"] == received_at
        mock_s3.generate_presigned_url.assert_not_called()
    else:
        assert body["url"] == "https://example.s3.amazonaws.com/presigned"
        assert body["bucket"] == handler.TRANSCRIPTS_BUCKET
        assert body["key"] == "inbound/contact-abc.json"
        assert body["expiresInSeconds"] == 900
