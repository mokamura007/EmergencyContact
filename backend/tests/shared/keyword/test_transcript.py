"""Unit tests for ``shared/keyword/transcript.py`` (Phase 8.1).

Example-based tests for the pure ``extract_transcript_payload`` function.

Validates: Requirements 7.1, 7.7 (transcript reading + confidence extraction)
"""

from __future__ import annotations

import math

import pytest

from shared.keyword.transcript import extract_transcript_payload


def _job_body(
    transcript_text: str,
    items: list[dict] | None = None,
) -> dict:
    """Construct an Amazon Transcribe job-output-shape body."""
    body: dict = {
        "jobName": "safety-confirm-cycle-1-emp-1-0",
        "accountId": "111122223333",
        "status": "COMPLETED",
        "results": {
            "transcripts": [{"transcript": transcript_text}],
        },
    }
    if items is not None:
        body["results"]["items"] = items
    return body


def _pron(confidence: str | float, content: str = "x") -> dict:
    """Build a pronunciation-type item."""
    return {
        "start_time": "0.0",
        "end_time": "0.5",
        "alternatives": [{"confidence": str(confidence), "content": content}],
        "type": "pronunciation",
    }


def _punct(content: str = ".") -> dict:
    return {"alternatives": [{"confidence": "0.0", "content": content}], "type": "punctuation"}


# --- Happy paths ------------------------------------------------------


def test_extract_text_and_average_confidence() -> None:
    body = _job_body(
        "無事です",
        items=[_pron(0.9), _pron(0.8), _pron(0.7)],
    )
    text, conf = extract_transcript_payload(body)
    assert text == "無事です"
    assert math.isclose(conf, 0.8, rel_tol=1e-6)


def test_extract_skips_punctuation_when_averaging() -> None:
    """Punctuation items are not included in the mean."""
    body = _job_body(
        "無事です。",
        items=[_pron(0.9), _pron(0.7), _punct(".")],
    )
    _, conf = extract_transcript_payload(body)
    # Mean of [0.9, 0.7] = 0.8, NOT (0.9+0.7+0)/3.
    assert math.isclose(conf, 0.8, rel_tol=1e-6)


def test_extract_empty_items_yields_zero_confidence() -> None:
    body = _job_body("無事です", items=[])
    text, conf = extract_transcript_payload(body)
    assert text == "無事です"
    assert conf == 0.0


def test_extract_missing_items_yields_zero_confidence() -> None:
    """Silence-only jobs may omit ``items`` entirely."""
    body = _job_body("")
    text, conf = extract_transcript_payload(body)
    assert text == ""
    assert conf == 0.0


def test_extract_handles_single_token() -> None:
    body = _job_body("はい", items=[_pron(0.95)])
    text, conf = extract_transcript_payload(body)
    assert text == "はい"
    assert math.isclose(conf, 0.95, rel_tol=1e-6)


# --- Tolerant of malformed items --------------------------------------


def test_extract_skips_item_without_alternatives() -> None:
    body = _job_body(
        "x",
        items=[
            _pron(0.9),
            {"type": "pronunciation"},  # missing alternatives
            _pron(0.5),
        ],
    )
    _, conf = extract_transcript_payload(body)
    assert math.isclose(conf, 0.7, rel_tol=1e-6)


def test_extract_skips_item_with_non_numeric_confidence() -> None:
    body = _job_body(
        "x",
        items=[
            _pron(0.9),
            {
                "type": "pronunciation",
                "alternatives": [{"confidence": "NaN-not-a-number"}],
            },
            _pron(0.5),
        ],
    )
    _, conf = extract_transcript_payload(body)
    assert math.isclose(conf, 0.7, rel_tol=1e-6)


def test_extract_skips_out_of_range_confidence() -> None:
    """Confidence > 1.0 or < 0.0 is nonsense; skip rather than poison the mean."""
    body = _job_body(
        "x",
        items=[_pron(0.9), _pron(1.5), _pron(0.5), _pron(-0.1)],
    )
    _, conf = extract_transcript_payload(body)
    assert math.isclose(conf, 0.7, rel_tol=1e-6)


# --- Validation -------------------------------------------------------


def test_non_dict_body_raises() -> None:
    with pytest.raises(ValueError, match="transcript body must be a dict"):
        extract_transcript_payload("not-a-dict")  # type: ignore[arg-type]


def test_missing_results_raises() -> None:
    with pytest.raises(ValueError, match=r"transcript\.results must be a dict"):
        extract_transcript_payload({"jobName": "x"})


def test_missing_transcripts_raises() -> None:
    with pytest.raises(ValueError, match="transcripts must be a non-empty list"):
        extract_transcript_payload({"results": {}})


def test_empty_transcripts_list_raises() -> None:
    with pytest.raises(ValueError, match="transcripts must be a non-empty list"):
        extract_transcript_payload({"results": {"transcripts": []}})


def test_non_str_transcript_raises() -> None:
    with pytest.raises(ValueError, match=r"transcripts\[0\]\.transcript must be str"):
        extract_transcript_payload(
            {"results": {"transcripts": [{"transcript": 42}]}}
        )
