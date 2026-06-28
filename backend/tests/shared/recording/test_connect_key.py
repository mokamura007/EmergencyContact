"""Unit tests for ``shared/recording/connect_key.py`` (Phase 7.2).

Covers the ``parse_connect_native_key`` and ``derive_target_outbound_key``
pure functions consumed by the ``RecordingRelocator`` Lambda (Phase
7.2). These functions form the parsing/projection halves of the
Connect-native-key → design-mandated-key transformation.

Validates: Requirements 10.1, 10.2, D5
"""

from __future__ import annotations

import pytest

from shared.recording.connect_key import (
    ConnectNativeKeyInfo,
    derive_target_outbound_key,
    parse_connect_native_key,
)


# --- parse_connect_native_key: positive cases ----------------------------


def test_parse_connect_key_basic_with_alias_segment() -> None:
    """The typical layout includes an instance-alias path component."""
    info = parse_connect_native_key(
        "connect-raw/my-alias/CallRecordings/2026/06/25/"
        "11111111-1111-1111-1111-111111111111_2026-06-25T07:00:00_UTC.wav"
    )
    assert info == ConnectNativeKeyInfo(
        contact_id="11111111-1111-1111-1111-111111111111",
        yyyy="2026",
        mm="06",
        dd="25",
        timestamp="2026-06-25T07:00:00",
    )


def test_parse_connect_key_without_alias_segment() -> None:
    """A flat prefix (no alias segment) is still parseable."""
    info = parse_connect_native_key(
        "connect-raw/CallRecordings/2026/06/25/"
        "abc123_2026-06-25T07:00:00_UTC.wav"
    )
    assert info is not None
    assert info.contact_id == "abc123"
    assert info.yyyy == "2026"
    assert info.mm == "06"
    assert info.dd == "25"


def test_parse_connect_key_multi_channel_export() -> None:
    """Multi-channel exports append ``_<channel>`` before ``.wav``."""
    info = parse_connect_native_key(
        "connect-raw/my-alias/CallRecordings/2026/06/25/"
        "contact-uuid_2026-06-25T07:00:00_UTC_FROM-CUSTOMER.wav"
    )
    assert info is not None
    assert info.contact_id == "contact-uuid"


def test_parse_connect_key_deep_prefix() -> None:
    """A prefix with multiple path components still resolves."""
    info = parse_connect_native_key(
        "deep/nested/prefix/instance-x/CallRecordings/2027/01/02/"
        "ctid_2027-01-02T00:00:00_UTC.wav"
    )
    assert info is not None
    assert info.contact_id == "ctid"
    assert info.yyyy == "2027"


# --- parse_connect_native_key: negative cases ----------------------------


def test_parse_connect_key_returns_none_for_design_path() -> None:
    """The project's own ``recordings/{...}.wav`` is NOT a Connect key."""
    info = parse_connect_native_key("recordings/cycle-1/emp-1/0.wav")
    assert info is None


def test_parse_connect_key_returns_none_for_inbound_design_path() -> None:
    info = parse_connect_native_key("inbound/202606/emp-2/contact-abc.wav")
    assert info is None


def test_parse_connect_key_returns_none_for_empty_string() -> None:
    assert parse_connect_native_key("") is None


def test_parse_connect_key_returns_none_for_non_string() -> None:
    assert parse_connect_native_key(None) is None  # type: ignore[arg-type]
    assert parse_connect_native_key(123) is None  # type: ignore[arg-type]


def test_parse_connect_key_returns_none_when_date_not_2_digit_month() -> None:
    """Single-digit month should not match."""
    info = parse_connect_native_key(
        "connect-raw/CallRecordings/2026/6/25/cid_ts_UTC.wav"
    )
    assert info is None


def test_parse_connect_key_returns_none_without_utc_suffix() -> None:
    info = parse_connect_native_key(
        "connect-raw/CallRecordings/2026/06/25/cid_2026-06-25T07-00-00.wav"
    )
    assert info is None


def test_parse_connect_key_returns_none_for_non_wav_extension() -> None:
    info = parse_connect_native_key(
        "connect-raw/CallRecordings/2026/06/25/cid_ts_UTC.mp3"
    )
    assert info is None


# --- derive_target_outbound_key: positive cases --------------------------


def test_derive_target_outbound_key_basic() -> None:
    assert (
        derive_target_outbound_key("cycle-1", "emp-1", 1)
        == "recordings/cycle-1/emp-1/1.wav"
    )


def test_derive_target_outbound_key_accepts_zero_seq() -> None:
    """``parse_recording_key`` regex permits ``\\d+`` which includes 0."""
    assert (
        derive_target_outbound_key("cycle-x", "emp-x", 0)
        == "recordings/cycle-x/emp-x/0.wav"
    )


def test_derive_target_outbound_key_uuid_round_trip() -> None:
    """Round-trip with parse_recording_key (Phase 6.4 helper)."""
    from shared.recording.s3_keys import parse_recording_key

    cycle = "550e8400-e29b-41d4-a716-446655440000"
    emp = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    key = derive_target_outbound_key(cycle, emp, 5)
    info = parse_recording_key(key)
    assert info is not None
    assert info.cycle_id_or_inbound_prefix == cycle
    assert info.employee_id == emp
    assert info.seq_or_contact == "5"


# --- derive_target_outbound_key: negative cases --------------------------


def test_derive_target_outbound_key_rejects_empty_cycle_id() -> None:
    with pytest.raises(ValueError, match="cycle_id"):
        derive_target_outbound_key("", "emp-1", 0)


def test_derive_target_outbound_key_rejects_slash_in_cycle_id() -> None:
    with pytest.raises(ValueError, match="cycle_id"):
        derive_target_outbound_key("cycle/1", "emp-1", 0)


def test_derive_target_outbound_key_rejects_empty_employee_id() -> None:
    with pytest.raises(ValueError, match="employee_id"):
        derive_target_outbound_key("cycle-1", "", 0)


def test_derive_target_outbound_key_rejects_slash_in_employee_id() -> None:
    with pytest.raises(ValueError, match="employee_id"):
        derive_target_outbound_key("cycle-1", "emp/1", 0)


def test_derive_target_outbound_key_rejects_negative_seq() -> None:
    with pytest.raises(ValueError, match="seq"):
        derive_target_outbound_key("cycle-1", "emp-1", -1)


def test_derive_target_outbound_key_rejects_bool_seq() -> None:
    """``True`` is technically int but we reject it as a typo."""
    with pytest.raises(ValueError, match="seq"):
        derive_target_outbound_key("cycle-1", "emp-1", True)


def test_derive_target_outbound_key_rejects_non_int_seq() -> None:
    with pytest.raises(ValueError, match="seq"):
        derive_target_outbound_key(
            "cycle-1", "emp-1", "1"  # type: ignore[arg-type]
        )
