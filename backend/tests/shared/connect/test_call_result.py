"""Unit tests for ``classify_call_result`` pure function (Phase 7.4).

Property 14 (Hypothesis) will live in
``tests/shared/connect/test_call_result_property14.py`` once Phase
13.14 is in scope. These tests pin specific cases from the design.md
Property 14 mapping table and exercise the normalisation /
validation contract.
"""

from __future__ import annotations

import pytest

from shared.connect.call_result import (
    VALID_CALL_RESULT_CODES,
    classify_call_result,
)

# --- NO_ANSWER bucket ---------------------------------------------------


class TestNoAnswerBucket:
    """Reasons that map to ``NO_ANSWER`` regardless of recorded/status."""

    @pytest.mark.parametrize(
        "reason",
        [
            "NO_ANSWER",
            "NO_USER_RESPONSE",
            "EXPIRED",
            "TIMEOUT",
            "RING_TIMEOUT",
        ],
    )
    def test_no_answer_reasons(self, reason: str) -> None:
        assert (
            classify_call_result(
                reason=reason, transcribe_status=None, recorded=False
            )
            == "NO_ANSWER"
        )

    def test_no_answer_ignores_recorded_flag(self) -> None:
        # Even with recorded=True (which should be impossible for a
        # no-answer call but the function still has to be total),
        # NO_ANSWER wins.
        assert (
            classify_call_result(
                reason="NO_ANSWER", transcribe_status="COMPLETED", recorded=True
            )
            == "NO_ANSWER"
        )


# --- BUSY bucket --------------------------------------------------------


class TestBusyBucket:
    """Reasons that map to ``BUSY``."""

    @pytest.mark.parametrize("reason", ["BUSY", "LINE_BUSY", "USER_BUSY"])
    def test_busy_reasons(self, reason: str) -> None:
        assert (
            classify_call_result(
                reason=reason, transcribe_status=None, recorded=False
            )
            == "BUSY"
        )


# --- VOICEMAIL bucket ---------------------------------------------------


class TestVoicemailBucket:
    """Reasons that map to ``VOICEMAIL``."""

    @pytest.mark.parametrize(
        "reason",
        ["VOICEMAIL", "ANSWERING_MACHINE", "ANSWERING_MACHINE_DETECTED"],
    )
    def test_voicemail_reasons(self, reason: str) -> None:
        assert (
            classify_call_result(
                reason=reason, transcribe_status=None, recorded=False
            )
            == "VOICEMAIL"
        )


# --- ERROR bucket -------------------------------------------------------


class TestErrorBucket:
    """Reasons that map to ``ERROR`` (API error / dispatch failure)."""

    @pytest.mark.parametrize(
        "reason",
        [
            "API_ERROR",
            "ERROR",
            "REJECT",
            "REJECTED",
            "FAILED",
            "TELECOM_PROBLEM",
            "ENDPOINT_ERROR",
            "DISPATCH_FAILED",
            "USER_NOT_AVAILABLE",
        ],
    )
    def test_error_reasons(self, reason: str) -> None:
        assert (
            classify_call_result(
                reason=reason, transcribe_status=None, recorded=False
            )
            == "ERROR"
        )


# --- CONNECTED bucket: depends on recorded + transcribe_status ---------


class TestConnectedRecorded:
    """Connected reasons + recorded=True → depends on transcribe_status."""

    @pytest.mark.parametrize(
        "reason",
        [
            "CUSTOMER_DISCONNECT",
            "CONTACT_FLOW_DISCONNECT",
            "AGENT_DISCONNECT",
            "NORMAL_HANGUP",
            "HANGUP",
            "OK",
            "NORMAL",
        ],
    )
    def test_recorded_with_no_transcribe_yet_is_recorded(
        self, reason: str
    ) -> None:
        # Canonical CallEndHandler-time state: Transcribe not yet started.
        assert (
            classify_call_result(
                reason=reason, transcribe_status=None, recorded=True
            )
            == "RECORDED"
        )

    def test_recorded_with_transcribe_completed_is_recorded(self) -> None:
        assert (
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status="COMPLETED",
                recorded=True,
            )
            == "RECORDED"
        )

    def test_recorded_with_transcribe_failed_is_transcribe_failed(
        self,
    ) -> None:
        # Canonical TranscribeStarter-after-retries state.
        assert (
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status="FAILED",
                recorded=True,
            )
            == "TRANSCRIBE_FAILED"
        )

    @pytest.mark.parametrize(
        "ts", ["QUEUED", "IN_PROGRESS"]
    )
    def test_recorded_with_transcribe_pending_is_recorded(
        self, ts: str
    ) -> None:
        # Job submitted but not yet finished: the recording itself is OK.
        assert (
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status=ts,
                recorded=True,
            )
            == "RECORDED"
        )

    def test_empty_transcribe_status_treated_as_none(self) -> None:
        # Empty string normalises to the None case — useful when a
        # caller forwards a missing field as "".
        assert (
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status="",
                recorded=True,
            )
            == "RECORDED"
        )


class TestConnectedNotRecorded:
    """Connected reasons + recorded=False → operational ``ERROR``."""

    @pytest.mark.parametrize(
        "reason",
        [
            "CUSTOMER_DISCONNECT",
            "CONTACT_FLOW_DISCONNECT",
            "AGENT_DISCONNECT",
            "NORMAL_HANGUP",
            "HANGUP",
            "OK",
            "NORMAL",
        ],
    )
    def test_connected_but_not_recorded_is_error(self, reason: str) -> None:
        assert (
            classify_call_result(
                reason=reason, transcribe_status=None, recorded=False
            )
            == "ERROR"
        )

    def test_connected_not_recorded_ignores_transcribe_status(self) -> None:
        # If there is no recording there cannot be a real Transcribe job,
        # but the function still has to be total: the result is ERROR.
        assert (
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status="COMPLETED",
                recorded=False,
            )
            == "ERROR"
        )


# --- Normalisation ------------------------------------------------------


class TestNormalisation:
    """Input strings are case- and separator-insensitive."""

    def test_lowercase_reason_accepted(self) -> None:
        assert (
            classify_call_result(
                reason="no_answer", transcribe_status=None, recorded=False
            )
            == "NO_ANSWER"
        )

    def test_hyphenated_reason_accepted(self) -> None:
        assert (
            classify_call_result(
                reason="no-answer", transcribe_status=None, recorded=False
            )
            == "NO_ANSWER"
        )

    def test_space_separated_reason_accepted(self) -> None:
        assert (
            classify_call_result(
                reason="no answer", transcribe_status=None, recorded=False
            )
            == "NO_ANSWER"
        )

    def test_surrounding_whitespace_stripped(self) -> None:
        assert (
            classify_call_result(
                reason="  BUSY  ", transcribe_status=None, recorded=False
            )
            == "BUSY"
        )

    def test_transcribe_status_lowercase_accepted(self) -> None:
        assert (
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status="failed",
                recorded=True,
            )
            == "TRANSCRIBE_FAILED"
        )


# --- Output contract ----------------------------------------------------


class TestOutputContract:
    """The classifier output is always a member of VALID_CALL_RESULT_CODES."""

    @pytest.mark.parametrize(
        ("reason", "ts", "recorded"),
        [
            ("NO_ANSWER", None, False),
            ("BUSY", None, False),
            ("VOICEMAIL", None, False),
            ("API_ERROR", None, False),
            ("CUSTOMER_DISCONNECT", None, True),
            ("CUSTOMER_DISCONNECT", "COMPLETED", True),
            ("CUSTOMER_DISCONNECT", "FAILED", True),
            ("CUSTOMER_DISCONNECT", None, False),
        ],
    )
    def test_output_is_a_valid_call_result_code(
        self, reason: str, ts: str | None, recorded: bool
    ) -> None:
        result = classify_call_result(
            reason=reason, transcribe_status=ts, recorded=recorded
        )
        assert result in VALID_CALL_RESULT_CODES


# --- Validation ---------------------------------------------------------


class TestValidation:
    """Invalid inputs raise ``ValueError`` (no silent fallback)."""

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            classify_call_result(
                reason="", transcribe_status=None, recorded=False
            )

    def test_whitespace_only_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            classify_call_result(
                reason="   ", transcribe_status=None, recorded=False
            )

    def test_non_string_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            classify_call_result(
                reason=None,  # type: ignore[arg-type]
                transcribe_status=None,
                recorded=False,
            )

    def test_integer_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            classify_call_result(
                reason=42,  # type: ignore[arg-type]
                transcribe_status=None,
                recorded=False,
            )

    def test_unknown_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="unrecognised reason"):
            classify_call_result(
                reason="UFO_INTERFERENCE",
                transcribe_status=None,
                recorded=False,
            )

    def test_unknown_transcribe_status_raises(self) -> None:
        with pytest.raises(ValueError, match="transcribe_status"):
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status="MAYBE_COMPLETED",
                recorded=True,
            )

    def test_non_string_non_none_transcribe_status_raises(self) -> None:
        with pytest.raises(ValueError, match="transcribe_status"):
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status=123,  # type: ignore[arg-type]
                recorded=True,
            )

    def test_non_bool_recorded_raises(self) -> None:
        with pytest.raises(ValueError, match="recorded"):
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status=None,
                recorded="yes",  # type: ignore[arg-type]
            )

    def test_int_recorded_raises(self) -> None:
        with pytest.raises(ValueError, match="recorded"):
            classify_call_result(
                reason="CUSTOMER_DISCONNECT",
                transcribe_status=None,
                recorded=1,  # type: ignore[arg-type]
            )
