"""RecordingApi Lambda — S3 presigned-URL issuance with 90-day expiry guard.

Routes:
    GET /cycles/{id}/recordings/{employeeId}/{seq}
        Outbound-call recording (S3 RecordingsBucket).
    GET /cycles/{id}/transcripts/{employeeId}/{seq}
        Outbound-call transcript JSON (S3 TranscriptsBucket).
    GET /inbound/{contactId}/recording
        Inbound-call recording.
    GET /inbound/{contactId}/transcript
        Inbound-call transcript JSON.

Expiry policy (Requirement 10.7 / 13.7, Property 23):
    For cycle-based URLs the reference timestamp is `cycle.startedAt`.
    For inbound URLs it is `inboundContact.receivedAt`. If
    (now - reference) exceeds 90 days, return 410 Gone. Otherwise
    return a 15-minute presigned URL.

Authorization (Requirement 1.4):
    Administrator-only is enforced by the API Gateway Cognito
    Authorizer + group claim. This Lambda assumes the request has
    already passed authorization.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.api.cors import with_cors_headers
from shared.recording.expiry import (
    PRESIGNED_URL_TTL_SECONDS,
    can_issue_url,
    now_iso_utc,
)

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

CYCLE_TABLE_NAME = os.environ["CYCLE_TABLE_NAME"]
INBOUND_TABLE_NAME = os.environ["INBOUND_TABLE_NAME"]
RECORDINGS_BUCKET = os.environ["RECORDINGS_BUCKET_NAME"]
TRANSCRIPTS_BUCKET = os.environ["TRANSCRIPTS_BUCKET_NAME"]

_DDB = boto3.resource("dynamodb")
_CYCLE_TABLE = _DDB.Table(CYCLE_TABLE_NAME)
_INBOUND_TABLE = _DDB.Table(INBOUND_TABLE_NAME)
_S3 = boto3.client("s3")


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": with_cors_headers(
            {"Content-Type": "application/json; charset=utf-8"}
        ),
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def _presigned_url(bucket: str, key: str) -> str:
    """Generate a 15-minute presigned GET URL for the given S3 object."""
    return _S3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PRESIGNED_URL_TTL_SECONDS,
    )


def _cycle_artifact(
    path_params: dict[str, Any], bucket: str, key_template: str, ext: str
) -> dict[str, Any]:
    """Handle the cycle-based recording/transcript routes.

    Args:
        path_params: id (cycleId), employeeId, seq.
        bucket: S3 bucket name (RecordingsBucket or TranscriptsBucket).
        key_template: Format string yielding the S3 object key, e.g.
            "cycles/{cycle_id}/{employee_id}#{seq}.{ext}".
        ext: File extension (wav / json).
    """
    cycle_id = path_params.get("id")
    employee_id = path_params.get("employeeId")
    seq = path_params.get("seq")
    if not all(isinstance(x, str) and x for x in (cycle_id, employee_id, seq)):
        raise ValueError("id, employeeId, seq path parameters are required")
    assert isinstance(cycle_id, str)
    assert isinstance(employee_id, str)
    assert isinstance(seq, str)

    cycle = _CYCLE_TABLE.get_item(Key={"cycleId": cycle_id}).get("Item")
    if cycle is None:
        return _response(404, {"error": f"Cycle not found: {cycle_id}"})
    started_at = cycle.get("startedAt")
    if not isinstance(started_at, str):
        return _response(500, {"error": "Cycle has no startedAt timestamp"})

    if not can_issue_url(started_at, now_iso_utc()):
        return _response(
            410,
            {
                "error": "Recording / transcript has been deleted by 90-day lifecycle policy",
                "cycleId": cycle_id,
                "startedAt": started_at,
            },
        )

    s3_key = key_template.format(
        cycle_id=cycle_id, employee_id=employee_id, seq=seq, ext=ext
    )
    url = _presigned_url(bucket, s3_key)
    return _response(
        200,
        {
            "url": url,
            "expiresInSeconds": PRESIGNED_URL_TTL_SECONDS,
            "bucket": bucket,
            "key": s3_key,
        },
    )


def _inbound_artifact(
    path_params: dict[str, Any], bucket: str, key_template: str, ext: str
) -> dict[str, Any]:
    contact_id = path_params.get("contactId")
    if not isinstance(contact_id, str) or not contact_id:
        raise ValueError("contactId path parameter is required")

    inbound = _INBOUND_TABLE.get_item(Key={"contactId": contact_id}).get("Item")
    if inbound is None:
        return _response(404, {"error": f"Inbound contact not found: {contact_id}"})
    received_at = inbound.get("receivedAt")
    if not isinstance(received_at, str):
        return _response(500, {"error": "InboundContact has no receivedAt timestamp"})

    if not can_issue_url(received_at, now_iso_utc()):
        return _response(
            410,
            {
                "error": "Recording / transcript has been deleted by 90-day lifecycle policy",
                "contactId": contact_id,
                "receivedAt": received_at,
            },
        )

    s3_key = key_template.format(contact_id=contact_id, ext=ext)
    url = _presigned_url(bucket, s3_key)
    return _response(
        200,
        {
            "url": url,
            "expiresInSeconds": PRESIGNED_URL_TTL_SECONDS,
            "bucket": bucket,
            "key": s3_key,
        },
    )


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}

    try:
        if method == "GET" and resource == "/cycles/{id}/recordings/{employeeId}/{seq}":
            return _cycle_artifact(
                path_params,
                RECORDINGS_BUCKET,
                "cycles/{cycle_id}/{employee_id}#{seq}.{ext}",
                "wav",
            )
        if method == "GET" and resource == "/cycles/{id}/transcripts/{employeeId}/{seq}":
            return _cycle_artifact(
                path_params,
                TRANSCRIPTS_BUCKET,
                "cycles/{cycle_id}/{employee_id}#{seq}.{ext}",
                "json",
            )
        if method == "GET" and resource == "/inbound/{contactId}/recording":
            return _inbound_artifact(
                path_params, RECORDINGS_BUCKET, "inbound/{contact_id}.{ext}", "wav"
            )
        if method == "GET" and resource == "/inbound/{contactId}/transcript":
            return _inbound_artifact(
                path_params, TRANSCRIPTS_BUCKET, "inbound/{contact_id}.{ext}", "json"
            )
        return _response(405, {"error": f"Method {method} not allowed on {resource}"})
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except ClientError as exc:
        LOGGER.error("ClientError on %s %s: %s", method, resource, exc)
        raise
