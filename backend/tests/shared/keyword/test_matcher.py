"""Unit tests for ``shared/keyword/matcher.py`` (Phase 8.1).

Example-based tests for the pure ``classify_voice_status`` function.
Property 10 (Phase 13.10) will add Hypothesis-driven property tests
that drive the same function across thousands of inputs.

Validates: Requirements 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8
"""

from __future__ import annotations

import pytest

from shared.keyword.matcher import (
    DEFAULT_STATUS,
    PRIORITY_ORDER,
    classify_voice_status,
)

# --- Priority order constants are immutable single source of truth -----


def test_priority_order_is_injured_unavailable_safe() -> None:
    """Requirement 7.8: INJURED > UNAVAILABLE > SAFE > OTHER."""
    assert PRIORITY_ORDER == ("INJURED", "UNAVAILABLE", "SAFE")
    assert DEFAULT_STATUS == "OTHER"


# --- Single-category matches ------------------------------------------


def test_safe_only_returns_safe() -> None:
    """Requirement 7.3: SAFE alone yields SAFE."""
    dictionary = {
        "SAFE": ["無事", "大丈夫"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["手が離せません"],
    }
    status, matched = classify_voice_status("私は無事です", dictionary)
    assert status == "SAFE"
    assert matched == ["無事"]


def test_injured_alone_returns_injured() -> None:
    """Requirement 7.4: INJURED match yields INJURED regardless of others."""
    dictionary = {
        "SAFE": ["無事"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["手が離せません"],
    }
    status, matched = classify_voice_status("足を怪我しました", dictionary)
    assert status == "INJURED"
    assert matched == ["怪我"]


def test_unavailable_alone_returns_unavailable() -> None:
    """Requirement 7.5: UNAVAILABLE with no INJURED yields UNAVAILABLE."""
    dictionary = {
        "SAFE": ["無事"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["対応できません"],
    }
    status, matched = classify_voice_status(
        "今は対応できません", dictionary
    )
    assert status == "UNAVAILABLE"
    assert matched == ["対応できません"]


def test_no_match_returns_other() -> None:
    """Requirement 7.6: no match in any category yields OTHER."""
    dictionary = {
        "SAFE": ["無事"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["対応できません"],
    }
    status, matched = classify_voice_status("こんにちは", dictionary)
    assert status == "OTHER"
    assert matched == []


# --- Priority resolution (multi-match) --------------------------------


def test_injured_beats_unavailable_and_safe() -> None:
    """Requirement 7.4 / 7.8: INJURED wins over UNAVAILABLE and SAFE."""
    dictionary = {
        "SAFE": ["無事"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["対応"],
    }
    text = "無事ですが少し怪我があり対応できません"
    status, matched = classify_voice_status(text, dictionary)
    assert status == "INJURED"
    assert matched == ["怪我"]


def test_unavailable_beats_safe_when_no_injured() -> None:
    """Requirement 7.5 / 7.8: UNAVAILABLE wins over SAFE."""
    dictionary = {
        "SAFE": ["無事"],
        "INJURED": ["怪我"],
        "UNAVAILABLE": ["手が離せません"],
    }
    text = "無事ですが今は手が離せません"
    status, matched = classify_voice_status(text, dictionary)
    assert status == "UNAVAILABLE"
    assert matched == ["手が離せません"]


# --- Multiple keywords in winning category ----------------------------


def test_multiple_keywords_returned_in_order() -> None:
    """Matched keywords list contains every distinct keyword that hit."""
    dictionary = {
        "SAFE": ["無事", "元気"],
        "INJURED": [],
        "UNAVAILABLE": [],
    }
    status, matched = classify_voice_status("私は無事で元気です", dictionary)
    assert status == "SAFE"
    assert matched == ["無事", "元気"]


def test_duplicate_keyword_in_dictionary_deduplicated() -> None:
    """A keyword listed twice in the dictionary still produces one match."""
    dictionary = {
        "SAFE": ["無事", "無事"],
        "INJURED": [],
        "UNAVAILABLE": [],
    }
    status, matched = classify_voice_status("無事です", dictionary)
    assert status == "SAFE"
    assert matched == ["無事"]


# --- Case-insensitivity -----------------------------------------------


def test_case_insensitive_match() -> None:
    """Design.md: 大文字小文字は区別しない."""
    dictionary = {"SAFE": ["OK"], "INJURED": [], "UNAVAILABLE": []}
    status, matched = classify_voice_status("everything is ok here", dictionary)
    assert status == "SAFE"
    assert matched == ["OK"]


def test_case_insensitive_match_japanese_mixed() -> None:
    dictionary = {"SAFE": ["SAFE"], "INJURED": [], "UNAVAILABLE": []}
    status, _ = classify_voice_status("私は Safe です", dictionary)
    assert status == "SAFE"


# --- Empty / degenerate inputs ----------------------------------------


def test_empty_text_returns_other() -> None:
    dictionary = {"SAFE": ["無事"], "INJURED": ["怪我"], "UNAVAILABLE": ["不在"]}
    status, matched = classify_voice_status("", dictionary)
    assert status == "OTHER"
    assert matched == []


def test_empty_dictionary_returns_other() -> None:
    status, matched = classify_voice_status("無事です", {})
    assert status == "OTHER"
    assert matched == []


def test_all_empty_lists_returns_other() -> None:
    status, matched = classify_voice_status(
        "無事です", {"SAFE": [], "INJURED": [], "UNAVAILABLE": []}
    )
    assert status == "OTHER"
    assert matched == []


def test_empty_keyword_in_list_is_skipped() -> None:
    """Empty-string keywords are degenerate and would match every text."""
    dictionary = {"SAFE": ["", "無事"], "INJURED": [], "UNAVAILABLE": []}
    # Without skipping "", an empty text would match — verify SAFE still
    # requires the non-empty keyword.
    status_empty, _ = classify_voice_status("", dictionary)
    assert status_empty == "OTHER"
    # And the non-empty keyword still matches normally.
    status_ok, matched = classify_voice_status("私は無事", dictionary)
    assert status_ok == "SAFE"
    assert matched == ["無事"]


# --- Missing categories (forward-compat) ------------------------------


def test_missing_category_treated_as_empty() -> None:
    """Dictionary missing one of the three categories yields no match for it."""
    dictionary = {"SAFE": ["無事"]}
    status, _ = classify_voice_status("無事です", dictionary)
    assert status == "SAFE"


def test_extra_category_silently_ignored() -> None:
    """Extra (future) categories don't cause an error."""
    dictionary = {
        "SAFE": ["無事"],
        "INJURED": [],
        "UNAVAILABLE": [],
        "FUTURE_CATEGORY": ["xyz"],
    }
    status, _ = classify_voice_status("xyz", dictionary)
    # FUTURE_CATEGORY doesn't satisfy the priority order, so OTHER.
    assert status == "OTHER"


# --- Validation -------------------------------------------------------


def test_non_str_text_raises() -> None:
    with pytest.raises(ValueError, match="text must be a str"):
        classify_voice_status(123, {})  # type: ignore[arg-type]


def test_non_dict_dictionary_raises() -> None:
    with pytest.raises(ValueError, match="dictionary must be a dict"):
        classify_voice_status("foo", ["bar"])  # type: ignore[arg-type]


def test_non_list_category_raises() -> None:
    with pytest.raises(ValueError, match="must be a list of str"):
        classify_voice_status("foo", {"SAFE": "not-a-list"})  # type: ignore[dict-item]


def test_non_str_keyword_raises() -> None:
    with pytest.raises(ValueError, match="non-str entry"):
        classify_voice_status("foo", {"SAFE": [123]})  # type: ignore[list-item]
