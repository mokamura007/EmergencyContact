"""Unit tests for ``shared/recording/s3_keys.py`` (Phase 6.4).

Covers the parse_recording_key and derive_transcribe_job_name pure
functions consumed by TranscribeStarter (Phase 6.4). These are flagged
in the module docstring as Phase 13.x PBT candidates against the
path-shape invariant of Property 24; the cases here serve as the
example-based baseline that will anchor any future Hypothesis tests.

Validates: Requirements 6.1, 6.2, 6.6
"""

from __future__ import annotations

import pytest

from shared.recording.s3_keys import (
    RecordingKeyInfo,
    derive_transcribe_job_name,
    parse_recording_key,
    parse_transcript_key,
)

# --- parse_recording_key: outbound positive ---------------------------


def test_parse_outbound_basic() -> None:
    info = parse_recording_key("recordings/cycle-1/emp-1/0.wav")
    assert info == RecordingKeyInfo(
        kind="outbound",
        cycle_id_or_inbound_prefix="cycle-1",
        employee_id="emp-1",
        seq_or_contact="0",
        transcript_s3_key="transcripts/cycle-1/emp-1/0.json",
        meta_pk="cycle-1",
        meta_sk="emp-1#0",
    )


def test_parse_outbound_multi_digit_seq() -> None:
    info = parse_recording_key("recordings/cycle-uuid/emp-uuid/123.wav")
    assert info is not None
    assert info.kind == "outbound"
    assert info.seq_or_contact == "123"
    assert info.transcript_s3_key == "transcripts/cycle-uuid/emp-uuid/123.json"
    assert info.meta_sk == "emp-uuid#123"


def test_parse_outbound_uuid_ids() -> None:
    """Real-world UUID v4 cycleId and employeeId."""
    cycle = "550e8400-e29b-41d4-a716-446655440000"
    emp = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    info = parse_recording_key(f"recordings/{cycle}/{emp}/9.wav")
    assert info is not None
    assert info.cycle_id_or_inbound_prefix == cycle
    assert info.employee_id == emp
    assert info.meta_pk == cycle


# --- parse_recording_key: inbound positive ----------------------------


def test_parse_inbound_basic() -> None:
    info = parse_recording_key("inbound/202606/emp-2/contact-abc.wav")
    assert info == RecordingKeyInfo(
        kind="inbound",
        cycle_id_or_inbound_prefix="INBOUND#contact-abc",
        employee_id="emp-2",
        seq_or_contact="contact-abc",
        transcript_s3_key="inbound/202606/emp-2/contact-abc.json",
        meta_pk="INBOUND#contact-abc",
        meta_sk="emp-2#0",
    )


def test_parse_inbound_uuid_contact() -> None:
    info = parse_recording_key(
        "inbound/202612/employee-99/c8d6a4b2-1234-5678-9abc-def012345678.wav"
    )
    assert info is not None
    assert info.kind == "inbound"
    assert info.meta_pk == "INBOUND#c8d6a4b2-1234-5678-9abc-def012345678"
    assert info.meta_sk == "employee-99#0"


# --- parse_recording_key: negative cases ------------------------------


def test_parse_returns_none_for_random_key() -> None:
    assert parse_recording_key("random/key.mp3") is None


def test_parse_returns_none_for_wrong_extension() -> None:
    """``.mp3`` instead of ``.wav`` must not match."""
    assert parse_recording_key("recordings/cycle-1/emp-1/0.mp3") is None


def test_parse_returns_none_for_uppercase_wav() -> None:
    """``.WAV`` uppercase must not match (extension is case-sensitive)."""
    assert parse_recording_key("recordings/cycle-1/emp-1/0.WAV") is None


def test_parse_returns_none_for_outbound_non_numeric_seq() -> None:
    """``seq`` must be all digits."""
    assert parse_recording_key("recordings/cycle-1/emp-1/abc.wav") is None


def test_parse_returns_none_for_extra_path_components() -> None:
    """Extra path components break the [^/]+ groups."""
    assert parse_recording_key("recordings/cycle-1/emp-1/sub/0.wav") is None


def test_parse_returns_none_for_inbound_short_yyyymm() -> None:
    """yyyymm must be exactly 6 digits."""
    assert parse_recording_key("inbound/20266/emp-2/contact-abc.wav") is None


def test_parse_returns_none_for_inbound_long_yyyymm() -> None:
    """yyyymm must be exactly 6 digits (7 rejected)."""
    assert parse_recording_key("inbound/2026066/emp-2/contact-abc.wav") is None


def test_parse_returns_none_for_inbound_non_digit_yyyymm() -> None:
    assert parse_recording_key("inbound/2026-6/emp-2/contact-abc.wav") is None


def test_parse_returns_none_for_empty_string() -> None:
    assert parse_recording_key("") is None


def test_parse_returns_none_for_non_string() -> None:
    """Defensive: callers might pass None / int by mistake."""
    assert parse_recording_key(None) is None  # type: ignore[arg-type]
    assert parse_recording_key(123) is None  # type: ignore[arg-type]


def test_parse_returns_none_for_missing_prefix() -> None:
    """The string must start with ``recordings/`` or ``inbound/``."""
    assert parse_recording_key("cycle-1/emp-1/0.wav") is None


# --- derive_transcribe_job_name: positive cases -----------------------


def test_derive_job_name_outbound_basic() -> None:
    name = derive_transcribe_job_name("cycle-1", "emp-1#0")
    # ``#`` in meta_sk replaced with ``-``.
    assert name == "safety-confirm-cycle-1-emp-1-0"


def test_derive_job_name_inbound_basic() -> None:
    name = derive_transcribe_job_name("INBOUND#contact-abc", "emp-2#0")
    # ``#`` in both meta_pk and meta_sk replaced.
    assert name == "safety-confirm-INBOUND-contact-abc-emp-2-0"


def test_derive_job_name_is_idempotent() -> None:
    """Identical inputs always produce identical outputs."""
    a = derive_transcribe_job_name("cycle-x", "emp-y#3")
    b = derive_transcribe_job_name("cycle-x", "emp-y#3")
    assert a == b


def test_derive_job_name_sanitises_slash_and_colon() -> None:
    """``/`` and ``:`` are also replaced even if not currently emitted."""
    name = derive_transcribe_job_name("cycle:1/region", "emp/x#5")
    assert name == "safety-confirm-cycle-1-region-emp-x-5"
    # Sanity: the result matches the Transcribe-allowed character class.
    import re

    assert re.fullmatch(r"^[0-9a-zA-Z._-]+$", name) is not None


def test_derive_job_name_truncates_at_200() -> None:
    """200-char hard limit from Transcribe API."""
    long_pk = "a" * 300
    long_sk = "b" * 300
    name = derive_transcribe_job_name(long_pk, long_sk)
    assert len(name) == 200
    # Prefix preserved so log filtering still works.
    assert name.startswith("safety-confirm-")


def test_derive_job_name_under_200_unchanged_length() -> None:
    """Inputs producing names <=200 chars are NOT truncated."""
    name = derive_transcribe_job_name("cycle-1", "emp-1#0")
    assert len(name) < 200
    # Specifically, the documented length.
    assert len(name) == len("safety-confirm-cycle-1-emp-1-0")


# --- derive_transcribe_job_name: negative cases -----------------------


def test_derive_job_name_rejects_empty_pk() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        derive_transcribe_job_name("", "emp-1#0")


def test_derive_job_name_rejects_empty_sk() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        derive_transcribe_job_name("cycle-1", "")


def test_derive_job_name_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="must be strings"):
        derive_transcribe_job_name(None, "emp-1#0")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be strings"):
        derive_transcribe_job_name("cycle-1", 0)  # type: ignore[arg-type]



# === parse_transcript_key (Phase 8.1) =================================


def test_parse_transcript_outbound_basic() -> None:
    info = parse_transcript_key("transcripts/cycle-1/emp-1/0.json")
    assert info == RecordingKeyInfo(
        kind="outbound",
        cycle_id_or_inbound_prefix="cycle-1",
        employee_id="emp-1",
        seq_or_contact="0",
        transcript_s3_key="transcripts/cycle-1/emp-1/0.json",
        meta_pk="cycle-1",
        meta_sk="emp-1#0",
    )


def test_parse_transcript_outbound_multi_digit_seq() -> None:
    info = parse_transcript_key("transcripts/cycle-uuid/emp-uuid/42.json")
    assert info is not None
    assert info.kind == "outbound"
    assert info.seq_or_contact == "42"
    assert info.meta_sk == "emp-uuid#42"


def test_parse_transcript_inbound_basic() -> None:
    info = parse_transcript_key("inbound/202606/emp-2/contact-abc.json")
    assert info == RecordingKeyInfo(
        kind="inbound",
        cycle_id_or_inbound_prefix="INBOUND#contact-abc",
        employee_id="emp-2",
        seq_or_contact="contact-abc",
        transcript_s3_key="inbound/202606/emp-2/contact-abc.json",
        meta_pk="INBOUND#contact-abc",
        meta_sk="emp-2#0",
    )


def test_parse_transcript_uuid_inbound() -> None:
    info = parse_transcript_key(
        "inbound/202612/employee-99/c8d6a4b2-1234-5678-9abc-def012345678.json"
    )
    assert info is not None
    assert info.kind == "inbound"
    assert info.meta_pk == "INBOUND#c8d6a4b2-1234-5678-9abc-def012345678"


def test_parse_transcript_wrong_extension_returns_none() -> None:
    """``.wav`` instead of ``.json`` must not match the transcript regex."""
    assert parse_transcript_key("transcripts/cycle-1/emp-1/0.wav") is None


def test_parse_transcript_recordings_prefix_returns_none() -> None:
    """``recordings/`` belongs to the recording regex, not transcript."""
    assert parse_transcript_key("recordings/cycle-1/emp-1/0.json") is None


def test_parse_transcript_random_key_returns_none() -> None:
    assert parse_transcript_key("random/key.json") is None


def test_parse_transcript_non_digit_seq_returns_none() -> None:
    """``seq`` must be digits-only for outbound transcripts."""
    assert parse_transcript_key("transcripts/cycle-1/emp-1/abc.json") is None


def test_parse_transcript_inbound_short_yyyymm_returns_none() -> None:
    """Inbound regex requires exactly 6 digits for yyyymm."""
    assert parse_transcript_key("inbound/20260/emp-2/contact-abc.json") is None


def test_parse_transcript_empty_string_returns_none() -> None:
    assert parse_transcript_key("") is None


def test_parse_transcript_non_str_returns_none() -> None:
    assert parse_transcript_key(None) is None  # type: ignore[arg-type]
