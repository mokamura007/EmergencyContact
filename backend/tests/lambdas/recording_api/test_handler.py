"""Unit tests for the RecordingApi Lambda (Phase 5.5).

The handler issues a 15-minute S3 presigned URL when a recording /
transcript is requested within the 90-day retention window
(Requirements 10.7 / 13.7, Property 23). Past that boundary it must
return 410 Gone.

Routes covered:
    GET /cycles/{id}/recordings/{employeeId}/{seq}
    GET /cycles/{id}/transcripts/{employeeId}/{seq}
    GET /inbound/{contactId}/recording
    GET /inbound/{contactId}/transcript

Authorization note (Requirement 1.4):
    Administrator-group enforcement happens at the API Gateway Cognito
    Authorizer layer — the Lambda itself trusts that the request has
    already passed authorization (see handler.py module docstring).
    No 403-branch unit test exists here because there is no 403 code
    path in the Lambda; that scenario is covered by API Gateway-level
    integration tests.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from lambdas.recording_api import handler

# --- Fixtures ----------------------------------------------------------


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


# --- Helpers -----------------------------------------------------------


def _iso(d: dt.datetime) -> str:
    return d.isoformat(timespec="seconds").replace("+00:00", "Z")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _days_ago(days: int) -> str:
    return _iso(_now() - dt.timedelta(days=days))


def _cycle_recording_event(
    cycle_id: str = "cycle-1",
    employee_id: str = "emp-001",
    seq: str = "1",
) -> dict[str, Any]:
    return {
        "httpMethod": "GET",
        "resource": "/cycles/{id}/recordings/{employeeId}/{seq}",
        "pathParameters": {
            "id": cycle_id,
            "employeeId": employee_id,
            "seq": seq,
        },
    }


def _cycle_transcript_event(
    cycle_id: str = "cycle-1",
    employee_id: str = "emp-001",
    seq: str = "1",
) -> dict[str, Any]:
    return {
        "httpMethod": "GET",
        "resource": "/cycles/{id}/transcripts/{employeeId}/{seq}",
        "pathParameters": {
            "id": cycle_id,
            "employeeId": employee_id,
            "seq": seq,
        },
    }


def _inbound_recording_event(
    contact_id: str = "contact-abc",
) -> dict[str, Any]:
    return {
        "httpMethod": "GET",
        "resource": "/inbound/{contactId}/recording",
        "pathParameters": {"contactId": contact_id},
    }


def _inbound_transcript_event(
    contact_id: str = "contact-abc",
) -> dict[str, Any]:
    return {
        "httpMethod": "GET",
        "resource": "/inbound/{contactId}/transcript",
        "pathParameters": {"contactId": contact_id},
    }


# --- 1) Cycle recording happy path ------------------------------------


def test_outbound_recording_within_90_days_returns_presigned_url(
    mock_cycle_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "startedAt": _days_ago(10)}
    }

    result = handler.lambda_handler(_cycle_recording_event(), None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["url"] == "https://example.s3.amazonaws.com/presigned"
    assert body["bucket"] == handler.RECORDINGS_BUCKET
    assert body["key"] == "cycles/cycle-1/emp-001#1.wav"
    assert body["expiresInSeconds"] == 900

    mock_cycle_table.get_item.assert_called_once_with(
        Key={"cycleId": "cycle-1"}
    )
    mock_s3.generate_presigned_url.assert_called_once()
    s3_kwargs = mock_s3.generate_presigned_url.call_args.kwargs
    assert s3_kwargs["ClientMethod"] == "get_object"
    assert s3_kwargs["Params"] == {
        "Bucket": handler.RECORDINGS_BUCKET,
        "Key": "cycles/cycle-1/emp-001#1.wav",
    }


# --- 2) Cycle transcript happy path -----------------------------------


def test_outbound_transcript_within_90_days_returns_presigned_url(
    mock_cycle_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "startedAt": _days_ago(30)}
    }

    result = handler.lambda_handler(_cycle_transcript_event(), None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["bucket"] == handler.TRANSCRIPTS_BUCKET
    assert body["key"] == "cycles/cycle-1/emp-001#1.json"
    assert body["expiresInSeconds"] == 900

    s3_kwargs = mock_s3.generate_presigned_url.call_args.kwargs
    assert s3_kwargs["Params"]["Bucket"] == handler.TRANSCRIPTS_BUCKET
    assert s3_kwargs["Params"]["Key"] == "cycles/cycle-1/emp-001#1.json"


# --- 3) Inbound recording happy path ----------------------------------


def test_inbound_recording_within_90_days_returns_presigned_url(
    mock_inbound_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    mock_inbound_table.get_item.return_value = {
        "Item": {
            "contactId": "contact-abc",
            "receivedAt": _days_ago(5),
        }
    }

    result = handler.lambda_handler(_inbound_recording_event(), None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["bucket"] == handler.RECORDINGS_BUCKET
    assert body["key"] == "inbound/contact-abc.wav"
    assert body["expiresInSeconds"] == 900

    mock_inbound_table.get_item.assert_called_once_with(
        Key={"contactId": "contact-abc"}
    )


# --- 4) Inbound transcript happy path ---------------------------------


def test_inbound_transcript_within_90_days_returns_presigned_url(
    mock_inbound_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    mock_inbound_table.get_item.return_value = {
        "Item": {
            "contactId": "contact-abc",
            "receivedAt": _days_ago(45),
        }
    }

    result = handler.lambda_handler(_inbound_transcript_event(), None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["bucket"] == handler.TRANSCRIPTS_BUCKET
    assert body["key"] == "inbound/contact-abc.json"
    assert body["expiresInSeconds"] == 900


# --- 5a) Cycle artifact past 90-day expiry → 410 Gone -----------------


def test_outbound_recording_past_90_days_returns_410(
    mock_cycle_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "startedAt": _days_ago(91)}
    }

    result = handler.lambda_handler(_cycle_recording_event(), None)

    assert result["statusCode"] == 410
    body = json.loads(result["body"])
    assert "90-day" in body["error"]
    assert body["cycleId"] == "cycle-1"
    # No S3 call when the URL is past expiry.
    mock_s3.generate_presigned_url.assert_not_called()


# --- 5b) Inbound artifact past 90-day expiry → 410 Gone ---------------


def test_inbound_transcript_past_90_days_returns_410(
    mock_inbound_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    mock_inbound_table.get_item.return_value = {
        "Item": {
            "contactId": "contact-abc",
            "receivedAt": _days_ago(95),
        }
    }

    result = handler.lambda_handler(_inbound_transcript_event(), None)

    assert result["statusCode"] == 410
    body = json.loads(result["body"])
    assert "90-day" in body["error"]
    assert body["contactId"] == "contact-abc"
    mock_s3.generate_presigned_url.assert_not_called()


# --- 6a) Missing cycle row → 404 --------------------------------------


def test_missing_cycle_returns_404(
    mock_cycle_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    mock_cycle_table.get_item.return_value = {}

    result = handler.lambda_handler(
        _cycle_recording_event(cycle_id="missing"), None
    )

    assert result["statusCode"] == 404
    body = json.loads(result["body"])
    assert "missing" in body["error"]
    mock_s3.generate_presigned_url.assert_not_called()


# --- 6b) Missing inbound contact row → 404 ----------------------------


def test_missing_inbound_contact_returns_404(
    mock_inbound_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    mock_inbound_table.get_item.return_value = {}

    result = handler.lambda_handler(
        _inbound_recording_event(contact_id="nope"), None
    )

    assert result["statusCode"] == 404
    body = json.loads(result["body"])
    assert "nope" in body["error"]
    mock_s3.generate_presigned_url.assert_not_called()


# --- 8) Presigned URL TTL is exactly 900 seconds ----------------------


def test_presigned_url_uses_900_second_ttl(
    mock_cycle_table: MagicMock,
    mock_s3: MagicMock,
) -> None:
    """Property 23 / Requirement 10.7: the URL is valid for 15 minutes.

    Verified by inspecting the ``ExpiresIn`` parameter passed to
    ``generate_presigned_url``. The constant lives in
    ``shared.recording.expiry.PRESIGNED_URL_TTL_SECONDS`` (= 900).
    """
    mock_cycle_table.get_item.return_value = {
        "Item": {"cycleId": "cycle-1", "startedAt": _days_ago(1)}
    }

    handler.lambda_handler(_cycle_recording_event(), None)

    s3_kwargs = mock_s3.generate_presigned_url.call_args.kwargs
    assert s3_kwargs["ExpiresIn"] == 900
