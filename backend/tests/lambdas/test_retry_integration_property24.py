"""Property 24 (Handler-integration half) — recording / transcribe / meta
write retry PBT (Phase 13.24).

Validates: Requirements 6.6 (再試行回数上限), 10.8 (録音保存の再試行戦略),
    10.9 (Transcribe 起動の再試行戦略 / TRANSCRIBE_FAILED への遷移).

Targets (the three retry surfaces named abstractly in tasks.md L1077-1078):
    * ``uploadRecordingWithRetry``    →
      ``lambdas.recording_relocator.handler._lookup_response_by_contact_id``
      (Phase 7.2 — the GSI Query retry surface of the Connect-native →
      design-layout S3 rename pipeline. The S3 ``copy_object`` /
      ``delete_object`` calls themselves rely on the boto3 default retry
      stack and are not re-attempted by application code.)
    * ``startTranscribeWithRetry``    →
      ``lambdas.transcribe_starter.handler._start_transcribe_job_with_retry``
      (Phase 6.4).
    * ``writeMetadataWithRetry``      →
      ``lambdas.recording_metadata_writer.handler._write_metadata_with_retry``
      (Phase 6.7).

Why an integration file (Option C from the task brief):
    The three retry loops live in three Lambdas but share one shape:
    boto3 client raises ``ClientError``, the handler maps the error
    code to *retryable / non-retryable / idempotency-success*, sleeps
    via ``compute_backoff_delay`` between attempts (cap == 3 calls),
    and either returns success or raises a domain-specific
    ``…ExhaustedError``. The shared retry semantics is what Property
    24 actually pins, so a single integration file lets one Hypothesis
    strategy drive all three handlers and prove the contract is
    consistent across them. The pure-function half (``compute_backoff_
    delay``) is in ``tests/shared/connect/test_backoff_property24.py``.

Named properties (5; 1:1 with the task brief "Done When"):
    P1 (attempt_cap_at_three):
        Each handler invokes its AWS client AT MOST ``_MAX_*_ATTEMPTS``
        times (== 3 for all three). Holds even when the underlying
        client keeps raising retryable errors forever.

    P2 (success_when_failures_le_two):
        For ``f`` retryable failures with ``0 <= f <= 2``, the handler
        eventually succeeds and returns its happy-path sentinel
        (``status=="ok"`` for transcribe / metadata, the rename payload
        for relocator). The number of client calls equals ``f + 1``.

    P3 (failure_when_failures_ge_three):
        For ``f >= 3`` retryable failures, the handler raises its
        domain-specific exhaustion exception (or, for transcribe,
        flips Response.callResultCode to TRANSCRIBE_FAILED and returns
        the error sentinel). The number of client calls equals 3
        (cap), and exactly 2 sleeps happen (between the 3 tries).

    P4 (meta_write_skipped_on_upload_failure):
        When ``recording_relocator`` exhausts GSI lookup retries, no
        S3 ``copy_object`` / ``delete_object`` is issued — so the
        downstream ``recording_metadata_writer`` (driven by the
        EventBridge ObjectCreated event of the renamed key) never
        sees a trigger. This is the "保存失敗時のメタ書込スキップ"
        invariant from the task brief, validated at the handler
        boundary (the no-S3-write side).
        Companion invariant: when ``transcribe_starter`` exhausts
        StartJob retries, ``TranscriptMeta.put_item`` is NOT called
        — the meta write is conditional on Transcribe success.

    P5 (transcribe_failure_writes_transcribe_failed_to_response):
        On ``f >= 3`` transcribe failures for an *outbound* recording,
        ``Response.update_item`` is invoked exactly once with
        ``UpdateExpression`` setting ``callResultCode =
        :TRANSCRIBE_FAILED`` under the ``RECORDED``-only
        ``ConditionExpression``. Inbound recordings do not touch
        Response (Phase 9 InboundContactTable owns that bookkeeping).

Anchored examples (``@example`` pin, exercised in addition to random draws):
    - f = 0   (immediate success — no retry)
    - f = 1   (one retry)
    - f = 2   (two retries — last attempt succeeds)  ← the success/failure boundary
    - f = 3   (three retries — exhaustion)
    - f = 4   (extra failures past the cap; behaviour must still match f == 3)

Mock surface:
    Each handler module exposes its boto3 globals (e.g.
    ``handler._TRANSCRIBE``) which ``monkeypatch`` replaces with a
    ``MagicMock`` per test. ``time.sleep`` is also replaced with a
    counter so the suite stays fast and the sleep-count assertion is
    cheap. This is identical to the example-based tests already in
    ``tests/lambdas/{transcribe_starter,recording_metadata_writer,
    recording_relocator}/test_handler.py`` — Property 24 is the
    Hypothesis-driven dual of those example tests, not a replacement.
"""

from __future__ import annotations

import os

# Each handler reads its environment at module-import time (the production
# Lambda runtime injects them via the function configuration). The
# per-handler conftests under ``tests/lambdas/{handler}/`` already set
# these, but this integration file lives one level up so it needs to seed
# them itself BEFORE the ``handler`` imports below.
os.environ.setdefault(
    "RECORDINGS_BUCKET_NAME",
    "safety-confirmation-recordings-test-111122223333-ap-northeast-1",
)
os.environ.setdefault(
    "TRANSCRIPTS_BUCKET_NAME",
    "safety-confirmation-transcripts-test-111122223333-ap-northeast-1",
)
os.environ.setdefault("TRANSCRIPT_META_TABLE_NAME", "TranscriptMetadata-test")
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault("RECORDING_META_TABLE_NAME", "RecordingMetadata-test")
os.environ.setdefault(
    "KMS_CMK_ARN",
    "arn:aws:kms:ap-northeast-1:111122223333:key/00000000-0000-0000-0000-000000000000",
)

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from lambdas.recording_metadata_writer import handler as meta_handler
from lambdas.recording_relocator import handler as relocator_handler
from lambdas.transcribe_starter import handler as transcribe_handler

# Hypothesis settings: handler invocations are heavier than pure-function
# calls (MagicMock + side-effect lists), so cap at 100 examples per
# property. ``HealthCheck.function_scoped_fixture`` is suppressed because
# the boto3-mock fixtures are intentionally function-scoped (one fresh
# MagicMock per Hypothesis draw), which Hypothesis flags by default.
PBT_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.function_scoped_fixture,
    ],
)

# Cap shared by all three handlers (Requirements 6.6 / 10.8 / 10.9).
_MAX_ATTEMPTS = 3

# Bucket name used by all three handlers' conftest fixtures.
_BUCKET = "safety-confirmation-recordings-test-111122223333-ap-northeast-1"

# A single outbound recording key reused across handlers — keeps the
# (cycleId, employeeId, seq) tuple in lockstep so cross-handler property
# assertions (e.g. P4) compare apples to apples.
_OUTBOUND_KEY = "recordings/cycle-1/emp-1/0.wav"
_OUTBOUND_CONNECT_CONTACT_ID = "11111111-1111-1111-1111-111111111111"
_OUTBOUND_CONNECT_KEY = (
    "connect-raw/my-alias/CallRecordings/2026/06/25/"
    f"{_OUTBOUND_CONNECT_CONTACT_ID}_2026-06-25T07:00:00_UTC.wav"
)
_INBOUND_KEY = "inbound/202606/emp-2/contact-abc.wav"


# ---------------------------------------------------------------------------
# Strategies (local — strong contextual coupling to Property 24).
# ---------------------------------------------------------------------------

# Pre-cap failure counts: 0..2 retryable failures means eventual success.
_failures_le_two = st.integers(min_value=0, max_value=_MAX_ATTEMPTS - 1)

# At-or-past-cap failure counts: 3..6 retryable failures means exhaustion.
# Capped at 6 because behaviour past ``_MAX_ATTEMPTS`` is identical (the
# handler stops trying after 3 calls regardless), so wider draws add no
# new coverage.
_failures_ge_three = st.integers(
    min_value=_MAX_ATTEMPTS, max_value=_MAX_ATTEMPTS * 2
)

# Full domain — 0..6 retryable failures.
_failures_any = st.integers(min_value=0, max_value=_MAX_ATTEMPTS * 2)

# Retryable error codes vary per handler. The strategies below mirror
# each handler's ``_RETRYABLE_*_ERROR_CODES`` frozenset.
_meta_retryable_codes = st.sampled_from(
    ["ThrottlingException", "ProvisionedThroughputExceededException"]
)
_transcribe_retryable_codes = st.sampled_from(
    ["ThrottlingException", "LimitExceededException"]
)
_relocator_retryable_codes = st.sampled_from(
    ["ThrottlingException", "ProvisionedThroughputExceededException"]
)


# ---------------------------------------------------------------------------
# Helpers — build per-test side-effect lists and standard events.
# ---------------------------------------------------------------------------


def _client_error(code: str, op: str = "Op") -> ClientError:
    """Build a boto3 ClientError carrying the given Error.Code."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": f"{code} simulated"}},
        operation_name=op,
    )


def _build_side_effect(
    n_failures: int,
    error_code: str,
    success_value: Any,
) -> list[Any]:
    """Build a ``side_effect`` list for ``MagicMock``.

    Yields ``n_failures`` retryable ``ClientError`` raises followed by a
    single ``success_value``. After exhaustion (past ``_MAX_ATTEMPTS``)
    the trailing success is never consumed, which is the intended
    semantics: the handler stops calling the client.
    """
    return [_client_error(error_code) for _ in range(n_failures)] + [
        success_value
    ]


def _outbound_event(key: str = _OUTBOUND_KEY) -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-id",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {"name": _BUCKET},
            "object": {"key": key, "size": 320_000},
        },
    }


def _inbound_event(key: str = _INBOUND_KEY) -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-id-inbound",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {"name": _BUCKET},
            "object": {"key": key, "size": 256_000},
        },
    }


def _connect_relocator_event() -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-id-relocator",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {
            "bucket": {"name": _BUCKET},
            "object": {"key": _OUTBOUND_CONNECT_KEY, "size": 320_000},
        },
    }


def _response_row() -> dict[str, Any]:
    return {
        "cycleId": "cycle-1",
        "employeeId": "emp-1",
        "callAttempts": 1,
        "contactId": _OUTBOUND_CONNECT_CONTACT_ID,
    }


# ---------------------------------------------------------------------------
# Per-handler mock fixtures.
#
# Hypothesis re-uses the function-scoped fixture instance across all
# draws of a single ``@given`` test, so ``side_effect`` and ``call_count``
# would otherwise leak between draws. Each property body calls
# :func:`_reset` at the top to clear that state without recreating the
# monkeypatched globals.
# ---------------------------------------------------------------------------


def _reset(mocks: dict[str, MagicMock]) -> None:
    """Reset every MagicMock in the given fixture dict.

    ``MagicMock.reset_mock()`` clears ``call_count``, ``call_args``, and
    ``call_args_list`` but does NOT clear ``side_effect`` / ``return_
    value`` — both are re-assigned at the start of each draw anyway.
    """
    for mock in mocks.values():
        mock.reset_mock(return_value=False, side_effect=True)


@pytest.fixture
def meta_mocks(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Boto3 mocks for ``recording_metadata_writer``."""
    table = MagicMock(name="RecordingMetaTable")
    sleeper = MagicMock(name="meta-time.sleep")
    monkeypatch.setattr(meta_handler, "_RECORDING_META_TABLE", table)
    monkeypatch.setattr("lambdas.recording_metadata_writer.handler.time.sleep", sleeper)
    # Freeze ``_utc_now_iso`` so item shape is deterministic across draws.
    monkeypatch.setattr(meta_handler, "_utc_now_iso", lambda: "2026-06-25T12:34:56Z")
    return {"table": table, "sleeper": sleeper}


@pytest.fixture
def transcribe_mocks(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Boto3 mocks for ``transcribe_starter``."""
    client = MagicMock(name="TranscribeClient")
    meta_table = MagicMock(name="TranscriptMetaTable")
    response_table = MagicMock(name="ResponseTable")
    sleeper = MagicMock(name="transcribe-time.sleep")
    monkeypatch.setattr(transcribe_handler, "_TRANSCRIBE", client)
    monkeypatch.setattr(transcribe_handler, "_TRANSCRIPT_META_TABLE", meta_table)
    monkeypatch.setattr(transcribe_handler, "_RESPONSE_TABLE", response_table)
    monkeypatch.setattr("lambdas.transcribe_starter.handler.time.sleep", sleeper)
    return {
        "client": client,
        "meta_table": meta_table,
        "response_table": response_table,
        "sleeper": sleeper,
    }


@pytest.fixture
def relocator_mocks(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Boto3 mocks for ``recording_relocator``."""
    response_table = MagicMock(name="ResponseTable")
    s3 = MagicMock(name="S3")
    sleeper = MagicMock(name="relocator-time.sleep")
    monkeypatch.setattr(relocator_handler, "_RESPONSE_TABLE", response_table)
    monkeypatch.setattr(relocator_handler, "_S3", s3)
    monkeypatch.setattr("lambdas.recording_relocator.handler.time.sleep", sleeper)
    return {"response_table": response_table, "s3": s3, "sleeper": sleeper}


# ===========================================================================
# P1 — attempt_cap_at_three (per handler).
# ===========================================================================


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="ThrottlingException", n_failures=4)
@example(error_code="ProvisionedThroughputExceededException", n_failures=6)
@given(error_code=_meta_retryable_codes, n_failures=_failures_any)
def test_property24_meta_attempt_cap_at_three(
    meta_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """RecordingMetadataWriter calls put_item at most 3 times.

    Even with infinite retryable failures, the handler caps at
    ``_MAX_DDB_ATTEMPTS == 3``. Holds across both retryable error
    codes (Throttling, ProvisionedThroughputExceeded).

    Validates: Requirements 6.6 (再試行 ≤ 3), 10.8 (録音メタ書込の
    再試行上限).
    """
    _reset(meta_mocks)
    table = meta_mocks["table"]
    table.put_item.side_effect = _build_side_effect(n_failures, error_code, {})

    if n_failures >= _MAX_ATTEMPTS:
        with pytest.raises(meta_handler._DdbWriteExhaustedError):
            meta_handler.lambda_handler(_outbound_event(), None)
        assert table.put_item.call_count == _MAX_ATTEMPTS, (
            f"call_count={table.put_item.call_count} expected={_MAX_ATTEMPTS} "
            f"(n_failures={n_failures}, code={error_code})"
        )
    else:
        result = meta_handler.lambda_handler(_outbound_event(), None)
        assert result["status"] == "ok"
        assert table.put_item.call_count == n_failures + 1, (
            f"call_count={table.put_item.call_count} expected={n_failures + 1} "
            f"(n_failures={n_failures}, code={error_code})"
        )
    # In every case, call_count never exceeds the cap.
    assert table.put_item.call_count <= _MAX_ATTEMPTS


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="LimitExceededException", n_failures=4)
@given(error_code=_transcribe_retryable_codes, n_failures=_failures_any)
def test_property24_transcribe_attempt_cap_at_three(
    transcribe_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """TranscribeStarter calls start_transcription_job at most 3 times.

    Validates: Requirements 6.6 (再試行 ≤ 3), 10.9 (Transcribe 起動の
    再試行上限).
    """
    _reset(transcribe_mocks)
    client = transcribe_mocks["client"]
    client.start_transcription_job.side_effect = _build_side_effect(
        n_failures, error_code, {}
    )

    result = transcribe_handler.lambda_handler(_outbound_event(), None)

    if n_failures >= _MAX_ATTEMPTS:
        # Exhausted: handler returns error sentinel, not raises.
        assert result["status"] == "error"
        assert result["reason"] == "TRANSCRIBE_FAILED"
        assert client.start_transcription_job.call_count == _MAX_ATTEMPTS
    else:
        assert result["status"] == "ok"
        assert client.start_transcription_job.call_count == n_failures + 1
    assert client.start_transcription_job.call_count <= _MAX_ATTEMPTS


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="ProvisionedThroughputExceededException", n_failures=6)
@given(error_code=_relocator_retryable_codes, n_failures=_failures_any)
def test_property24_relocator_attempt_cap_at_three(
    relocator_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """RecordingRelocator calls Response.query at most 3 times.

    The relocator's retry surface is the GSI Query (its S3 ops rely on
    the boto3 default retry stack). Cap is ``_MAX_GSI_ATTEMPTS == 3``.

    Validates: Requirements 6.6 (再試行 ≤ 3), 10.8 (録音保存 — relocator
    の rename 前段リトライ上限).
    """
    _reset(relocator_mocks)
    response_table = relocator_mocks["response_table"]
    s3 = relocator_mocks["s3"]
    response_table.query.side_effect = _build_side_effect(
        n_failures, error_code, {"Items": [_response_row()]}
    )

    if n_failures >= _MAX_ATTEMPTS:
        with pytest.raises(relocator_handler._GsiLookupExhaustedError):
            relocator_handler.lambda_handler(_connect_relocator_event(), None)
        assert response_table.query.call_count == _MAX_ATTEMPTS
        # S3 ops never fired on exhaustion (P4 companion).
        s3.copy_object.assert_not_called()
        s3.delete_object.assert_not_called()
    else:
        result = relocator_handler.lambda_handler(_connect_relocator_event(), None)
        assert result["status"] == "ok"
        assert response_table.query.call_count == n_failures + 1
    assert response_table.query.call_count <= _MAX_ATTEMPTS


# ===========================================================================
# P2 — success_when_failures_le_two (per handler).
# ===========================================================================


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=0)
@example(error_code="ThrottlingException", n_failures=1)
@example(error_code="ProvisionedThroughputExceededException", n_failures=2)
@given(error_code=_meta_retryable_codes, n_failures=_failures_le_two)
def test_property24_meta_success_when_failures_le_two(
    meta_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Meta write succeeds when failures <= 2; call_count == failures + 1.

    Validates: Requirements 6.6, 10.8 (f<=2 で成功).
    """
    _reset(meta_mocks)
    table = meta_mocks["table"]
    sleeper = meta_mocks["sleeper"]
    table.put_item.side_effect = _build_side_effect(n_failures, error_code, {})

    result = meta_handler.lambda_handler(_outbound_event(), None)

    assert result["status"] == "ok"
    assert result["metaPk"] == "cycle-1"
    assert table.put_item.call_count == n_failures + 1
    # One sleep per failure (no sleep after the final success).
    assert sleeper.call_count == n_failures


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=0)
@example(error_code="LimitExceededException", n_failures=2)
@given(error_code=_transcribe_retryable_codes, n_failures=_failures_le_two)
def test_property24_transcribe_success_when_failures_le_two(
    transcribe_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Transcribe job starts when failures <= 2; TranscriptMeta is written.

    Validates: Requirements 6.6, 10.9 (f<=2 で成功).
    """
    _reset(transcribe_mocks)
    client = transcribe_mocks["client"]
    meta_table = transcribe_mocks["meta_table"]
    response_table = transcribe_mocks["response_table"]
    sleeper = transcribe_mocks["sleeper"]

    client.start_transcription_job.side_effect = _build_side_effect(
        n_failures, error_code, {}
    )

    result = transcribe_handler.lambda_handler(_outbound_event(), None)

    assert result["status"] == "ok"
    assert client.start_transcription_job.call_count == n_failures + 1
    # TranscriptMeta is written exactly once on the success path.
    meta_table.put_item.assert_called_once()
    # Response is NOT touched on success.
    response_table.update_item.assert_not_called()
    assert sleeper.call_count == n_failures


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=0)
@example(error_code="ProvisionedThroughputExceededException", n_failures=2)
@given(error_code=_relocator_retryable_codes, n_failures=_failures_le_two)
def test_property24_relocator_success_when_failures_le_two(
    relocator_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Relocator renames the file when GSI failures <= 2.

    Validates: Requirements 6.6, 10.8 (f<=2 で成功 — 録音保存パイプライン).
    """
    _reset(relocator_mocks)
    response_table = relocator_mocks["response_table"]
    s3 = relocator_mocks["s3"]
    sleeper = relocator_mocks["sleeper"]

    response_table.query.side_effect = _build_side_effect(
        n_failures, error_code, {"Items": [_response_row()]}
    )

    result = relocator_handler.lambda_handler(_connect_relocator_event(), None)

    assert result["status"] == "ok"
    assert result["targetKey"] == "recordings/cycle-1/emp-1/1.wav"
    assert response_table.query.call_count == n_failures + 1
    # S3 ops fire exactly once each on the success path.
    s3.copy_object.assert_called_once()
    s3.delete_object.assert_called_once()
    assert sleeper.call_count == n_failures


# ===========================================================================
# P3 — failure_when_failures_ge_three (per handler).
# ===========================================================================


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="ProvisionedThroughputExceededException", n_failures=4)
@example(error_code="ThrottlingException", n_failures=6)
@given(error_code=_meta_retryable_codes, n_failures=_failures_ge_three)
def test_property24_meta_failure_when_failures_ge_three(
    meta_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Meta write raises _DdbWriteExhaustedError on >= 3 failures.

    Exactly 3 client calls, exactly 2 sleeps (between calls 1↔2 and
    2↔3, no sleep after the final failure).

    Validates: Requirements 6.6, 10.8 (f>=3 で失敗).
    """
    _reset(meta_mocks)
    table = meta_mocks["table"]
    sleeper = meta_mocks["sleeper"]
    table.put_item.side_effect = _build_side_effect(n_failures, error_code, {})

    with pytest.raises(meta_handler._DdbWriteExhaustedError):
        meta_handler.lambda_handler(_outbound_event(), None)

    assert table.put_item.call_count == _MAX_ATTEMPTS
    assert sleeper.call_count == _MAX_ATTEMPTS - 1


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="LimitExceededException", n_failures=5)
@given(error_code=_transcribe_retryable_codes, n_failures=_failures_ge_three)
def test_property24_transcribe_failure_when_failures_ge_three(
    transcribe_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Transcribe returns error sentinel on >= 3 failures (no raise).

    The handler differs from the other two: it returns the
    ``{"status": "error", "reason": "TRANSCRIBE_FAILED"}`` sentinel
    rather than re-raising, because the Phase 6.4 design routes
    exhaustion to a Response-table side-effect (P5) rather than to the
    Lambda async DLQ. Inbound recordings still get the same sentinel
    but without the Response side-effect.

    Validates: Requirements 6.6, 10.9 (f>=3 で失敗).
    """
    _reset(transcribe_mocks)
    client = transcribe_mocks["client"]
    sleeper = transcribe_mocks["sleeper"]
    client.start_transcription_job.side_effect = _build_side_effect(
        n_failures, error_code, {}
    )

    result = transcribe_handler.lambda_handler(_outbound_event(), None)

    assert result == {
        "status": "error",
        "kind": "outbound",
        "reason": "TRANSCRIBE_FAILED",
    }
    assert client.start_transcription_job.call_count == _MAX_ATTEMPTS
    assert sleeper.call_count == _MAX_ATTEMPTS - 1


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="ProvisionedThroughputExceededException", n_failures=6)
@given(error_code=_relocator_retryable_codes, n_failures=_failures_ge_three)
def test_property24_relocator_failure_when_failures_ge_three(
    relocator_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Relocator raises _GsiLookupExhaustedError on >= 3 failures.

    Validates: Requirements 6.6, 10.8 (f>=3 で失敗 — 録音保存パイプライン).
    """
    _reset(relocator_mocks)
    response_table = relocator_mocks["response_table"]
    sleeper = relocator_mocks["sleeper"]
    response_table.query.side_effect = _build_side_effect(
        n_failures, error_code, {"Items": [_response_row()]}
    )

    with pytest.raises(relocator_handler._GsiLookupExhaustedError):
        relocator_handler.lambda_handler(_connect_relocator_event(), None)

    assert response_table.query.call_count == _MAX_ATTEMPTS
    assert sleeper.call_count == _MAX_ATTEMPTS - 1


# ===========================================================================
# P4 — meta_write_skipped_on_upload_failure.
#
# Two complementary surfaces:
#   (a) Relocator-side: GSI exhaustion ⇒ no S3 copy/delete ⇒ no
#       ObjectCreated event ⇒ recording_metadata_writer NEVER triggered
#       downstream. Validated by asserting copy_object/delete_object
#       are never called.
#   (b) Transcribe-side: StartJob exhaustion ⇒ TranscriptMeta.put_item
#       is NOT called. The transcribe meta write is conditional on
#       Transcribe success and the task brief lists "TRANSCRIBE_FAILED
#       が Response に書込" as the alternative side-effect.
# ===========================================================================


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="ProvisionedThroughputExceededException", n_failures=5)
@given(error_code=_relocator_retryable_codes, n_failures=_failures_ge_three)
def test_property24_relocator_failure_skips_s3_rename(
    relocator_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Upload-side skip: GSI exhaustion ⇒ no S3 copy/delete.

    When the upload pipeline (relocator) cannot complete the rename,
    the design-layout key is never written, so EventBridge's downstream
    ``recording_metadata_writer`` rule does not fire — the meta write
    is implicitly skipped at the system boundary. We validate the
    no-S3-rename side directly because that is the deterministic part
    we can observe in a unit-test mock.

    Validates: Requirements 6.6, 10.8 (保存失敗時のメタ書込スキップ —
    relocator boundary).
    """
    _reset(relocator_mocks)
    response_table = relocator_mocks["response_table"]
    s3 = relocator_mocks["s3"]
    response_table.query.side_effect = _build_side_effect(
        n_failures, error_code, {"Items": [_response_row()]}
    )

    with pytest.raises(relocator_handler._GsiLookupExhaustedError):
        relocator_handler.lambda_handler(_connect_relocator_event(), None)

    s3.copy_object.assert_not_called()
    s3.delete_object.assert_not_called()


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="LimitExceededException", n_failures=4)
@given(error_code=_transcribe_retryable_codes, n_failures=_failures_ge_three)
def test_property24_transcribe_failure_skips_transcript_meta_write(
    transcribe_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Transcribe failure ⇒ TranscriptMeta.put_item is NOT called.

    The transcribe meta write is conditional on Transcribe success;
    on exhaustion the handler flips Response.callResultCode to
    TRANSCRIBE_FAILED (P5) but the TranscriptMeta row is left absent.

    Validates: Requirements 6.6, 10.9 (Transcribe 失敗時のメタ書込スキップ).
    """
    _reset(transcribe_mocks)
    client = transcribe_mocks["client"]
    meta_table = transcribe_mocks["meta_table"]
    client.start_transcription_job.side_effect = _build_side_effect(
        n_failures, error_code, {}
    )

    result = transcribe_handler.lambda_handler(_outbound_event(), None)

    assert result["status"] == "error"
    meta_table.put_item.assert_not_called()


# ===========================================================================
# P5 — transcribe_failure_writes_transcribe_failed_to_response.
# ===========================================================================


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="LimitExceededException", n_failures=4)
@example(error_code="ThrottlingException", n_failures=6)
@given(error_code=_transcribe_retryable_codes, n_failures=_failures_ge_three)
def test_property24_outbound_transcribe_failure_writes_transcribe_failed(
    transcribe_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Outbound TRANSCRIBE_FAILED ⇒ Response.update_item invoked exactly once.

    The ``UpdateExpression`` sets ``callResultCode = :TRANSCRIBE_FAILED``
    and the ``ConditionExpression`` restricts the overwrite to rows
    where the prior ``callResultCode`` was ``RECORDED`` (so non-recorded
    outcomes like BUSY / NO_ANSWER are not mis-labelled).

    Validates: Requirements 10.9 (Transcribe 失敗で TRANSCRIBE_FAILED が
    Response に書込), 6.6 (再試行上限後の side-effect 仕様).
    """
    _reset(transcribe_mocks)
    client = transcribe_mocks["client"]
    response_table = transcribe_mocks["response_table"]
    client.start_transcription_job.side_effect = _build_side_effect(
        n_failures, error_code, {}
    )

    result = transcribe_handler.lambda_handler(_outbound_event(), None)

    assert result == {
        "status": "error",
        "kind": "outbound",
        "reason": "TRANSCRIBE_FAILED",
    }
    response_table.update_item.assert_called_once()
    upd_kwargs = response_table.update_item.call_args.kwargs
    assert upd_kwargs["Key"] == {"cycleId": "cycle-1", "employeeId": "emp-1"}
    assert upd_kwargs["UpdateExpression"] == "SET callResultCode = :code"
    assert upd_kwargs["ConditionExpression"] == (
        "attribute_exists(callResultCode) AND callResultCode = :recorded"
    )
    assert upd_kwargs["ExpressionAttributeValues"] == {
        ":code": "TRANSCRIBE_FAILED",
        ":recorded": "RECORDED",
    }


@PBT_SETTINGS
@example(error_code="ThrottlingException", n_failures=3)
@example(error_code="LimitExceededException", n_failures=5)
@given(error_code=_transcribe_retryable_codes, n_failures=_failures_ge_three)
def test_property24_inbound_transcribe_failure_does_not_touch_response(
    transcribe_mocks: dict[str, MagicMock],
    error_code: str,
    n_failures: int,
) -> None:
    """Inbound TRANSCRIBE_FAILED ⇒ Response is NOT touched.

    第17原則 (対称性推論): the outbound path writes TRANSCRIBE_FAILED.
    The inbound path is the opposite: Response is owned by Phase 6
    outbound bookkeeping, while inbound contacts live in
    InboundContactTable (Phase 9). Asserting the inbound path
    *doesn't* touch Response is the negative pin that makes the
    outbound positive pin meaningful.

    Validates: Requirements 10.9 (Response への書込は outbound 限定).
    """
    _reset(transcribe_mocks)
    client = transcribe_mocks["client"]
    response_table = transcribe_mocks["response_table"]
    client.start_transcription_job.side_effect = _build_side_effect(
        n_failures, error_code, {}
    )

    result = transcribe_handler.lambda_handler(_inbound_event(), None)

    assert result == {
        "status": "error",
        "kind": "inbound",
        "reason": "TRANSCRIBE_FAILED",
    }
    response_table.update_item.assert_not_called()


# ===========================================================================
# Symmetric / regression anchors — non-Hypothesis pins for highest-signal
# cases that cross-check the property bodies above.
# ===========================================================================


def test_unit_property24_three_handlers_share_max_attempts_constant() -> None:
    """All three handlers cap at the same number of attempts (== 3).

    第17原則 (対称性推論): the Hypothesis bodies all hard-code
    ``_MAX_ATTEMPTS = 3``. If any handler module ever raises its own
    cap above 3, the Hypothesis tests would silently start under-
    asserting (they'd succeed at ``n_failures==3`` instead of raising).
    This regression detector pins the three caps to a single literal.

    Validates: Requirements 6.6 (一貫した再試行上限 3 回).
    """
    assert meta_handler._MAX_DDB_ATTEMPTS == _MAX_ATTEMPTS
    assert transcribe_handler._MAX_TRANSCRIBE_ATTEMPTS == _MAX_ATTEMPTS
    assert relocator_handler._MAX_GSI_ATTEMPTS == _MAX_ATTEMPTS


def test_unit_property24_meta_immediate_success_uses_no_sleeps(
    meta_mocks: dict[str, MagicMock],
) -> None:
    """f == 0 anchor: success on the first attempt issues zero sleeps.

    This is the ``n_failures == 0`` shoulder of P2 pinned as a unit.
    """
    meta_mocks["table"].put_item.return_value = {}
    result = meta_handler.lambda_handler(_outbound_event(), None)
    assert result["status"] == "ok"
    assert meta_mocks["table"].put_item.call_count == 1
    meta_mocks["sleeper"].assert_not_called()


def test_unit_property24_transcribe_immediate_success_writes_meta(
    transcribe_mocks: dict[str, MagicMock],
) -> None:
    """f == 0 anchor for transcribe: success ⇒ TranscriptMeta written, no Response."""
    transcribe_mocks["client"].start_transcription_job.return_value = {}
    result = transcribe_handler.lambda_handler(_outbound_event(), None)
    assert result["status"] == "ok"
    transcribe_mocks["meta_table"].put_item.assert_called_once()
    transcribe_mocks["response_table"].update_item.assert_not_called()
    transcribe_mocks["sleeper"].assert_not_called()


def test_unit_property24_relocator_immediate_success_renames(
    relocator_mocks: dict[str, MagicMock],
) -> None:
    """f == 0 anchor for relocator: success ⇒ copy+delete, no sleeps."""
    relocator_mocks["response_table"].query.return_value = {
        "Items": [_response_row()]
    }
    result = relocator_handler.lambda_handler(_connect_relocator_event(), None)
    assert result["status"] == "ok"
    relocator_mocks["s3"].copy_object.assert_called_once()
    relocator_mocks["s3"].delete_object.assert_called_once()
    relocator_mocks["sleeper"].assert_not_called()
