"""InboundHandler Lambda — inbound callback voice flow (Phase 9.2).

Invoked twice per inbound Connect contact from
``infrastructure/contact-flows/inbound.json`` (Phase 9.1):

1. ``step = "identify"`` — immediately after the call is answered.
   Looks up the caller-ID against the Employee_Master
   ``PhoneNumberIndex`` GSI, scans recent Cycles, runs the pure
   Cycle-selection function from :mod:`shared.inbound.cycle_selection`,
   writes a provisional Inbound_Contact row, and returns the resulting
   ``flow`` attribute (one of
   ``ACTIVE_CYCLE`` / ``NO_CYCLE`` / ``NOT_REGISTERED`` /
   ``CYCLE_TERMINATED``).

2. ``step = "finalize"`` — after the 30 s recording window. Finalises
   the Inbound_Contact row (recordingS3Key, transcriptS3Key,
   callResultCode); for ``ACTIVE_CYCLE`` it also appends ``INBOUND`` to
   the matched Cycle's ``Response.callResultCodes`` list while leaving
   ``callAttempts`` unchanged (Requirement 13.5).

Inputs (canonical Amazon Connect ``ContactFlowEvent`` shape):

    {
        "Details": {
            "ContactData": {
                "ContactId":        "<connect-contact-id>",
                "CustomerEndpoint": {"Address": "<E.164>", "Type": "TELEPHONE_NUMBER"},
                "Attributes":       {"flow": "...", "employeeId": "...", "cycleId": "..."},
                ...
            },
            "Parameters": {"step": "identify" | "finalize"}
        },
        "Name": "ContactFlowEvent"
    }

The same handler also accepts a flat legacy event shape (test fixtures
and direct callers):

    {"step": "identify", "contactId": "...", "callerNumber": "+819...", ...}
    {"step": "finalize", "contactId": "...", "flow": "ACTIVE_CYCLE",
     "cycleId": "...", "employeeId": "..."}

Connect requires Lambda return values to be a *flat string-keyed* dict
(per the contact flow's ``ResponseValidation.ResponseType =
STRING_MAP``). The handler always returns at least ``{"flow": "<flow>"}``;
``employeeId`` and ``cycleId`` are added when the flow is
``ACTIVE_CYCLE`` so step=finalize can read them out of
``$.Attributes`` after the Phase 9.1 ``UpdateContactAttributes`` block.

Phase 9.2 design choices:

* The Cycle-selection logic lives in :mod:`shared.inbound.cycle_selection`
  as a pure function so Phase 9.3 can Hypothesis-test Property 11
  without DynamoDB mocks. This handler is the I/O wrapper only.

* Inbound_Contact is written twice for an ACTIVE_CYCLE contact:
  - ``identify`` writes a provisional row with the flow classification.
    Idempotency is enforced via ``attribute_not_exists(contactId)`` so a
    re-invocation of identify (Connect occasionally retries on transient
    network blips, mirrors Phase 6.3 CallEndHandler) cannot overwrite
    the first writer's row.
  - ``finalize`` ``UpdateItem`` s the recording / transcript / call
    result fields onto the existing row.

  For non-ACTIVE_CYCLE flows the row is finalised at identify time
  (recording/transcript fields are NULL because no recording was
  taken) and ``finalize`` is never invoked on those branches
  (Phase 9.1 inbound.json wires the non-ACTIVE branches straight to
  ``DisconnectParticipant``).

* The Response update for ``ACTIVE_CYCLE`` uses
  ``list_append(if_not_exists(callResultCodes, :empty), :inbound)`` so
  the operation is correct whether or not Phase 5.3 CycleApi had
  initialised the attribute. ``callAttempts`` is intentionally NOT
  touched — Requirement 13.5 explicitly preserves it because the
  inbound call is the *employee's* outbound (from our system's
  perspective it is an unanswered outbound followed by a callback).

* ``voiceStatus`` on Inbound_Contact starts as ``PENDING`` at
  ``finalize`` time; KeywordMatcher (extended in a Phase 9.x follow-up)
  will overwrite it once the transcript classification completes.
  Setting a placeholder here makes the row queryable for operators
  even before the asynchronous pipeline finishes.

* ``callerNumber`` is stored in plain E.164 form. The
  InboundContactTable is SSE-KMS encrypted at the table level
  (Phase 2.8); column-level encryption per design.md D8 is deferred
  to a future security-hardening phase.

Failure semantics follow project principle 19(b): no silent fallbacks
for input-shape errors — they raise ``ValueError``.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.audit.logger import write_audit_log
from shared.audit.mask import mask_phone
from shared.employee.visibility import is_visible
from shared.inbound.cycle_selection import (
    FLOW_ACTIVE_CYCLE,
    VALID_FLOWS,
    decide_inbound_flow,
)

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

EMPLOYEE_TABLE_NAME = os.environ["EMPLOYEE_TABLE_NAME"]
CYCLE_TABLE_NAME = os.environ["CYCLE_TABLE_NAME"]
RESPONSE_TABLE_NAME = os.environ["RESPONSE_TABLE_NAME"]
INBOUND_CONTACT_TABLE_NAME = os.environ["INBOUND_CONTACT_TABLE_NAME"]

#: Inbound reception window in days. Injected from CFn Parameter
#: ``InboundReceptionWindowDays`` (range 1–90, default 30; Requirements
#: 13.5 / 13.6). A missing env var is a deploy-time configuration bug,
#: so we let ``KeyError`` propagate at import time rather than falling
#: back to a hard-coded default (project principle 19(b)). ``int(...)``
#: also propagates ``ValueError`` on a malformed value for the same
#: reason. The pure function :func:`shared.inbound.cycle_selection.decide_inbound_flow`
#: enforces the [1, 365] runtime range guard on the parsed value.
INBOUND_RECEPTION_WINDOW_DAYS = int(os.environ["INBOUND_RECEPTION_WINDOW_DAYS"])

#: Number of recent terminal-status Cycles to consider for
#: CYCLE_TERMINATED classification. The eligibility window in
#: :mod:`shared.inbound.cycle_selection` is 30 days, so we fetch a
#: bounded number per status (RUNNING / COMPLETED / TIMEOUT /
#: START_FAILED) and rely on the pure function to discard out-of-window
#: records. 10 per status is well above the steady-state load (1 Cycle
#: per day, plus occasional manual re-runs) and still cheap as a Query.
_CYCLE_QUERY_LIMIT_PER_STATUS = 10

#: Statuses queried at identify time. The pure decision function
#: applies the eligibility-window filter; we just need to surface all
#: four buckets to it.
_CYCLE_QUERY_STATUSES: tuple[str, ...] = (
    "RUNNING",
    "COMPLETED",
    "TIMEOUT",
    "START_FAILED",
)

#: Recording / Transcript S3 key shape for inbound contacts. See
#: design.md / Recording_Store / S3 オブジェクトキー命名.
#: Phase 9.x will introduce RecordingRelocator's inbound branch that
#: actually renames the Connect-native key into this shape; the
#: Inbound_Contact row records the *target* key so downstream
#: consumers (RecordingApi, Status_Viewer) can resolve it without
#: another DB lookup.
_INBOUND_RECORDING_KEY_FMT = "inbound/{yyyymm}/{employee_id}/{contact_id}.wav"
_INBOUND_TRANSCRIPT_KEY_FMT = "inbound/{yyyymm}/{employee_id}/{contact_id}.json"

_DDB = boto3.resource("dynamodb")
_EMPLOYEE_TABLE = _DDB.Table(EMPLOYEE_TABLE_NAME)
_CYCLE_TABLE = _DDB.Table(CYCLE_TABLE_NAME)
_RESPONSE_TABLE = _DDB.Table(RESPONSE_TABLE_NAME)
_INBOUND_CONTACT_TABLE = _DDB.Table(INBOUND_CONTACT_TABLE_NAME)


# --- Input parsing ------------------------------------------------------


def _normalize_connect_event(event: dict[str, Any]) -> dict[str, Any]:
    """Flatten Amazon Connect's nested ``Invoke Lambda`` payload.

    Mirrors the same normalisation pattern Phase 6.3 CallEndHandler
    introduced. When the ``Details.ContactData`` sub-tree is absent the
    event is returned unchanged so flat test fixtures keep working.

    The flat form produced here contains the *union* of fields needed
    by both steps:

        * ``step``         — from ``Details.Parameters.step``
        * ``contactId``    — from ``Details.ContactData.ContactId``
        * ``callerNumber`` — from ``Details.ContactData.CustomerEndpoint.Address``
        * ``flow``         — from ``Details.ContactData.Attributes.flow``
        * ``employeeId``   — from ``Details.ContactData.Attributes.employeeId``
        * ``cycleId``      — from ``Details.ContactData.Attributes.cycleId``

    Per-step validation downstream surfaces missing required fields as
    ``ValueError`` (project principle 19(b)).
    """
    details = event.get("Details")
    if not isinstance(details, dict):
        return event
    contact_data = details.get("ContactData")
    if not isinstance(contact_data, dict):
        return event

    attributes = contact_data.get("Attributes") or {}
    parameters = details.get("Parameters") or {}
    customer_endpoint = contact_data.get("CustomerEndpoint") or {}
    flat: dict[str, Any] = {
        "step": parameters.get("step"),
        "contactId": contact_data.get("ContactId"),
        "callerNumber": customer_endpoint.get("Address"),
        "flow": attributes.get("flow"),
        "employeeId": attributes.get("employeeId"),
        "cycleId": attributes.get("cycleId"),
    }
    return flat


def _parse_identify_event(event: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate identify-step inputs.

    Required: ``contactId``, ``callerNumber``. ``callerNumber`` must
    be in E.164 form (validated only loosely here — a leading ``+`` —
    because Amazon Connect's CustomerEndpoint.Address always carries
    a normalised E.164 string and stricter validation belongs in
    :func:`shared.employee.validate.is_valid_e164` which the GSI
    lookup will indirectly enforce by failing to match).
    """
    contact_id = event.get("contactId")
    caller_number = event.get("callerNumber")
    if not isinstance(contact_id, str) or not contact_id:
        raise ValueError(
            f"identify: contactId is required (non-empty str); got {contact_id!r}"
        )
    if not isinstance(caller_number, str) or not caller_number:
        raise ValueError(
            f"identify: callerNumber is required (non-empty str); got {caller_number!r}"
        )
    if not caller_number.startswith("+"):
        raise ValueError(
            f"identify: callerNumber must start with '+' (E.164); got {caller_number!r}"
        )
    return {"contactId": contact_id, "callerNumber": caller_number}


def _parse_finalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate finalize-step inputs.

    Required: ``contactId``, ``flow``. For ``ACTIVE_CYCLE`` flow,
    ``employeeId`` and ``cycleId`` are additionally required (they
    are passed through the Contact Flow Attribute store from the
    identify step's return value).
    """
    contact_id = event.get("contactId")
    flow = event.get("flow")
    if not isinstance(contact_id, str) or not contact_id:
        raise ValueError(
            f"finalize: contactId is required (non-empty str); got {contact_id!r}"
        )
    if flow not in VALID_FLOWS:
        raise ValueError(
            f"finalize: flow must be one of {sorted(VALID_FLOWS)}; got {flow!r}"
        )
    parsed: dict[str, Any] = {"contactId": contact_id, "flow": flow}
    if flow == FLOW_ACTIVE_CYCLE:
        employee_id = event.get("employeeId")
        cycle_id = event.get("cycleId")
        if not isinstance(employee_id, str) or not employee_id:
            raise ValueError(
                f"finalize: employeeId is required for ACTIVE_CYCLE; got {employee_id!r}"
            )
        if not isinstance(cycle_id, str) or not cycle_id:
            raise ValueError(
                f"finalize: cycleId is required for ACTIVE_CYCLE; got {cycle_id!r}"
            )
        parsed["employeeId"] = employee_id
        parsed["cycleId"] = cycle_id
    return parsed


# --- Employee lookup ---------------------------------------------------


def _lookup_employee_by_phone(caller_number: str) -> dict[str, Any] | None:
    """Return the first visible Employee whose ``phoneNumber`` matches.

    Queries the PhoneNumberIndex GSI for exact E.164 equality, then
    filters out deleted / null-phone rows via
    :func:`shared.employee.visibility.is_visible` (Property 2). The
    GSI projects all attributes (template.yaml Phase 2.1), so the
    visibility check has everything it needs without a second GetItem.

    Returns ``None`` when no row matches OR when all matching rows
    fail the visibility predicate.
    """
    response = _EMPLOYEE_TABLE.query(
        IndexName="PhoneNumberIndex",
        KeyConditionExpression=Key("phoneNumber").eq(caller_number),
    )
    items = response.get("Items") or []
    for item in items:
        if is_visible(item):
            return item
    return None


# --- Cycle lookup ------------------------------------------------------


def _query_recent_cycles() -> list[dict[str, Any]]:
    """Query StatusStartedAtIndex for all four statuses, bounded.

    Returns at most ``_CYCLE_QUERY_LIMIT_PER_STATUS * 4`` records, all
    sorted by ``startedAt`` descending within each status group. The
    pure decision function applies the eligibility window itself, so
    we deliberately do not filter by ``completedAt`` here.
    """
    out: list[dict[str, Any]] = []
    for status in _CYCLE_QUERY_STATUSES:
        response = _CYCLE_TABLE.query(
            IndexName="StatusStartedAtIndex",
            KeyConditionExpression=Key("status").eq(status),
            ScanIndexForward=False,  # latest first
            Limit=_CYCLE_QUERY_LIMIT_PER_STATUS,
        )
        out.extend(response.get("Items") or [])
    return out


# --- Inbound_Contact writes -------------------------------------------


def _utc_now() -> dt.datetime:
    """Return the current UTC instant as a timezone-aware datetime."""
    return dt.datetime.now(tz=dt.UTC)


def _format_iso(now: dt.datetime) -> str:
    """Return ``now`` as an ISO 8601 ``Z`` string."""
    return now.isoformat(timespec="seconds").replace("+00:00", "Z")


def _format_yyyymm(now: dt.datetime) -> str:
    """Return ``now`` as a ``YYYYMM`` partition string for S3 keys."""
    return now.strftime("%Y%m")


def _put_provisional_inbound_contact(
    *,
    contact_id: str,
    received_at_iso: str,
    caller_number: str,
    flow: str,
    employee_id: str | None,
    cycle_id: str | None,
) -> None:
    """Write the initial Inbound_Contact row at identify time.

    Idempotent via ``attribute_not_exists(contactId)``: a re-invocation
    of identify for the same ContactId leaves the row untouched and
    swallows the conditional-check failure (mirrors Phase 6.3
    CallEndHandler pattern, design.md "InboundHandler: 同一 Connect
    ContactId に対する identify ステップは 1 回のみ実行").
    """
    item: dict[str, Any] = {
        "contactId": contact_id,
        "receivedAt": received_at_iso,
        "callerNumber": caller_number,
        "callerNumberMasked": mask_phone(caller_number),
        "flow": flow,
    }
    # DynamoDB rejects empty-string keys; omit unset linkage fields rather
    # than writing "" (which would otherwise pollute the GSI).
    if employee_id is not None:
        item["employeeId"] = employee_id
    if cycle_id is not None:
        item["cycleId"] = cycle_id
    try:
        _INBOUND_CONTACT_TABLE.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(contactId)",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            LOGGER.info(
                "Inbound_Contact identify put_item skipped (already exists) "
                "contactId=%s flow=%s",
                contact_id,
                flow,
            )
            return
        raise


def _finalize_inbound_contact(
    *,
    contact_id: str,
    flow: str,
    employee_id: str | None,
    recording_s3_key: str | None,
    transcript_s3_key: str | None,
    call_result_code: str,
    voice_status: str | None,
) -> None:
    """Add the recording / transcript / call-result fields to the row.

    Uses ``UpdateItem`` so we preserve the identify-step fields
    (callerNumber, callerNumberMasked, flow, receivedAt). The
    ``ConditionExpression`` asserts the row exists so a finalize for a
    never-identified contactId surfaces as a Lambda error rather than
    silently creating a half-formed row.

    For non-ACTIVE_CYCLE flows the recording / transcript fields are
    NULL (no recording was taken). ``call_result_code`` is then
    ``NO_RECORDING`` per design.md D8 vocabulary.
    """
    expr_parts = ["callResultCode = :rc"]
    expr_values: dict[str, Any] = {":rc": call_result_code}
    if recording_s3_key is not None:
        expr_parts.append("recordingS3Key = :rk")
        expr_values[":rk"] = recording_s3_key
    if transcript_s3_key is not None:
        expr_parts.append("transcriptS3Key = :tk")
        expr_values[":tk"] = transcript_s3_key
    if voice_status is not None:
        expr_parts.append("voiceStatus = :vs")
        expr_values[":vs"] = voice_status
    if employee_id is not None:
        # Identify already wrote employeeId when an Employee match was
        # found. For NOT_REGISTERED no row carries an employeeId, so the
        # caller passes None and we skip the SET.
        expr_parts.append("employeeId = :eid")
        expr_values[":eid"] = employee_id

    _INBOUND_CONTACT_TABLE.update_item(
        Key={"contactId": contact_id},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ConditionExpression="attribute_exists(contactId) AND #f = :f",
        ExpressionAttributeNames={"#f": "flow"},
        ExpressionAttributeValues={**expr_values, ":f": flow},
    )


# --- Response update (ACTIVE_CYCLE only) ------------------------------


def _append_inbound_to_response(*, cycle_id: str, employee_id: str) -> None:
    """Append ``INBOUND`` to ``Response.callResultCodes``.

    Requirement 13.5: callAttempts is unchanged; only callResultCodes
    grows. ``list_append(if_not_exists(callResultCodes, :empty),
    :inbound)`` handles the first-write case where the attribute does
    not yet exist (which can happen if the row was just created by an
    inbound contact on a Cycle whose outbound dispatch never reached
    this employee — design step 4: "存在しなければ Cycle に対応
    Response を新規追加").

    The Response row is created (PutItem with attribute_not_exists)
    before the UpdateItem so the design.md "Response を新規追加
    callAttempts=0 で初期化" branch is honoured. The PutItem's
    ConditionExpression makes that a no-op when the row already
    exists.
    """
    try:
        _RESPONSE_TABLE.put_item(
            Item={
                "cycleId": cycle_id,
                "employeeId": employee_id,
                "callAttempts": 0,
                "callResultCodes": [],
                "voiceStatus": "PENDING",
            },
            ConditionExpression="attribute_not_exists(cycleId)",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code != "ConditionalCheckFailedException":
            raise
        # Row already exists — expected steady-state path.

    _RESPONSE_TABLE.update_item(
        Key={"cycleId": cycle_id, "employeeId": employee_id},
        UpdateExpression=(
            "SET callResultCodes = list_append("
            "if_not_exists(callResultCodes, :empty), :inbound)"
        ),
        ExpressionAttributeValues={
            ":empty": [],
            ":inbound": ["INBOUND"],
        },
    )


# --- Step entry points -------------------------------------------------


def _handle_identify(event: dict[str, Any]) -> dict[str, Any]:
    """Run the identify step and return the ``flow`` attribute payload.

    Steps:
        1. Parse / validate the event.
        2. Look up Employee by caller number.
        3. Query recent Cycles + run the pure decision function.
        4. Write the provisional Inbound_Contact row.
        5. Return ``{"flow": ..., "employeeId": ..., "cycleId": ...}``
           (the last two fields only when ``ACTIVE_CYCLE``).
    """
    parsed = _parse_identify_event(event)
    contact_id = parsed["contactId"]
    caller_number = parsed["callerNumber"]
    now = _utc_now()

    employee = _lookup_employee_by_phone(caller_number)
    employee_matched = employee is not None
    employee_id: str | None = employee["employeeId"] if employee is not None else None

    if employee_matched:
        cycles = _query_recent_cycles()
        flow, cycle_id = decide_inbound_flow(
            True, cycles, now, INBOUND_RECEPTION_WINDOW_DAYS
        )
    else:
        flow, cycle_id = decide_inbound_flow(
            False, [], now, INBOUND_RECEPTION_WINDOW_DAYS
        )

    # Phase 12.3 removed the previous human-readable LOGGER.info() debug
    # line in favour of the structured audit record below — keeps a
    # single source of truth for inbound classifications.
    _put_provisional_inbound_contact(
        contact_id=contact_id,
        received_at_iso=_format_iso(now),
        caller_number=caller_number,
        flow=flow,
        employee_id=employee_id,
        cycle_id=cycle_id,
    )

    # Phase 12.3: emit one audit record per inbound contact. principal is
    # ``<connect-service>`` because Amazon Connect (not an authenticated
    # user) is the actor that drives identify-step invocation.
    write_audit_log(
        event_type="INBOUND_CONTACT_RECEIVED",
        principal="<connect-service>",
        target=contact_id,
        phone=caller_number,
        extra={
            "flow": flow,
            "employeeId": employee_id,
            "cycleId": cycle_id,
        },
    )

    result: dict[str, Any] = {"flow": flow}
    if flow == FLOW_ACTIVE_CYCLE:
        # Both fields are non-None by the decide_inbound_flow contract
        # when flow == ACTIVE_CYCLE; assert for the type checker.
        assert employee_id is not None
        assert cycle_id is not None
        result["employeeId"] = employee_id
        result["cycleId"] = cycle_id
    return result


def _handle_finalize(event: dict[str, Any]) -> dict[str, Any]:
    """Run the finalize step and return a status payload.

    Steps:
        1. Parse / validate the event.
        2. For ``ACTIVE_CYCLE``: derive expected recording / transcript
           keys, append ``INBOUND`` to the matched Cycle's Response,
           finalize the Inbound_Contact row with the resolved keys.
        3. For ``NO_CYCLE`` / ``NOT_REGISTERED`` / ``CYCLE_TERMINATED``:
           finalize the Inbound_Contact row with NULL recording /
           transcript references and ``callResultCode = NO_RECORDING``.
    """
    parsed = _parse_finalize_event(event)
    contact_id = parsed["contactId"]
    flow = parsed["flow"]
    now = _utc_now()

    if flow == FLOW_ACTIVE_CYCLE:
        employee_id = parsed["employeeId"]
        cycle_id = parsed["cycleId"]
        yyyymm = _format_yyyymm(now)
        recording_s3_key = _INBOUND_RECORDING_KEY_FMT.format(
            yyyymm=yyyymm, employee_id=employee_id, contact_id=contact_id
        )
        transcript_s3_key = _INBOUND_TRANSCRIPT_KEY_FMT.format(
            yyyymm=yyyymm, employee_id=employee_id, contact_id=contact_id
        )

        _append_inbound_to_response(cycle_id=cycle_id, employee_id=employee_id)
        _finalize_inbound_contact(
            contact_id=contact_id,
            flow=flow,
            employee_id=employee_id,
            recording_s3_key=recording_s3_key,
            transcript_s3_key=transcript_s3_key,
            call_result_code="RECORDED",
            voice_status="PENDING",
        )
        LOGGER.info(
            "Inbound finalize ACTIVE_CYCLE contactId=%s cycleId=%s employeeId=%s "
            "recordingS3Key=%s",
            contact_id,
            cycle_id,
            employee_id,
            recording_s3_key,
        )
        return {
            "status": "ok",
            "flow": flow,
            "contactId": contact_id,
            "cycleId": cycle_id,
            "employeeId": employee_id,
        }

    # Non-ACTIVE_CYCLE: no recording was taken; finalize with NULLs.
    _finalize_inbound_contact(
        contact_id=contact_id,
        flow=flow,
        employee_id=None,
        recording_s3_key=None,
        transcript_s3_key=None,
        call_result_code="NO_RECORDING",
        voice_status=None,
    )
    LOGGER.info(
        "Inbound finalize non-ACTIVE contactId=%s flow=%s",
        contact_id,
        flow,
    )
    return {"status": "ok", "flow": flow, "contactId": contact_id}


# --- Entry point -------------------------------------------------------


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Inbound Contact Flow ``Invoke Lambda`` entry point."""
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    flat = _normalize_connect_event(event)
    step = flat.get("step")
    if step == "identify":
        return _handle_identify(flat)
    if step == "finalize":
        return _handle_finalize(flat)
    raise ValueError(
        f"step must be 'identify' or 'finalize'; got {step!r}"
    )
