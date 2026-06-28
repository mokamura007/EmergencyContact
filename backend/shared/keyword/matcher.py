"""Voice_Status classification (Phase 8.1, Property 10).

PURE function. No I/O. Phase 13.10 PBT target.

Implements the keyword-matching priority described in design.md
Property 10 / requirements.md Requirement 7:

    INJURED > UNAVAILABLE > SAFE > OTHER

Matching is case-insensitive and uses raw substring containment
(no morphological analysis — the design opts for substring matching
because the language is Japanese; see design.md Keyword_Matcher /
マッチング判定の擬似コード).

The function returns BOTH the resolved Voice_Status AND the list of
matched keywords from the winning category. Returning a tuple keeps
the priority logic centralised; KeywordMatcher.lambda_handler is then
a thin orchestrator that does nothing but I/O.

Failure semantics follow project principle 19(b): no silent fallbacks.
``classify_voice_status`` raises ``ValueError`` on input-shape errors
(non-str text, non-dict dictionary, malformed category lists). Empty
text and empty dictionaries are valid inputs — they yield ``"OTHER"``
with an empty matched-keywords list.
"""

from __future__ import annotations

from typing import Final

#: The four Voice_Status values, in priority order (highest first).
#: Property 10 fixes the priority as INJURED > UNAVAILABLE > SAFE > OTHER;
#: this tuple is the single source of truth so any future re-ordering
#: discussion touches one line.
PRIORITY_ORDER: Final[tuple[str, ...]] = ("INJURED", "UNAVAILABLE", "SAFE")

#: ``OTHER`` is the catch-all fallback when none of the three priority
#: categories matches. Exported for callers that need to compare.
DEFAULT_STATUS: Final[str] = "OTHER"


def classify_voice_status(
    text: str,
    dictionary: dict[str, list[str]],
) -> tuple[str, list[str]]:
    """Classify ``text`` into a Voice_Status using ``dictionary``.

    Args:
        text: Transcript text body. Empty string is valid and yields
            ``("OTHER", [])`` directly. Comparison is case-insensitive.
        dictionary: Snapshot dictionary as returned by
            :func:`shared.dictionary.snapshot.get_dictionary_snapshot`.
            Shape: ``{"SAFE": [...], "INJURED": [...], "UNAVAILABLE": [...]}``.
            Missing categories are treated as empty lists; extra
            categories are silently ignored (forward-compat with
            hypothetical future Voice_Status values).

    Returns:
        ``(status, matched_keywords)`` where ``status`` is one of
        ``"INJURED"`` / ``"UNAVAILABLE"`` / ``"SAFE"`` / ``"OTHER"``
        and ``matched_keywords`` is the (de-duplicated, sort-stable)
        list of keywords from the *winning* category that occur in
        ``text``. When ``status == "OTHER"``, the list is empty.

    Raises:
        ValueError: ``text`` is not a string, or ``dictionary`` is not
            a dict, or any category's keyword list is not a list of
            strings.
    """
    if not isinstance(text, str):
        raise ValueError(f"text must be a str; got {type(text).__name__}")
    if not isinstance(dictionary, dict):
        raise ValueError(
            f"dictionary must be a dict; got {type(dictionary).__name__}"
        )

    text_lower = text.lower()

    for category in PRIORITY_ORDER:
        keywords = dictionary.get(category, [])
        if not isinstance(keywords, list):
            raise ValueError(
                f"dictionary[{category!r}] must be a list of str; "
                f"got {type(keywords).__name__}"
            )
        matched: list[str] = []
        seen: set[str] = set()
        for kw in keywords:
            if not isinstance(kw, str):
                raise ValueError(
                    f"dictionary[{category!r}] contains non-str entry: {kw!r}"
                )
            if not kw:
                # Empty string is a degenerate keyword that would match
                # every text including empty text. Skip — the dictionary
                # writer (Phase 4.1 DictionaryApi) already enforces
                # length 1-64 (Requirement 8.3), so this branch is
                # defensive against corrupted history rows.
                continue
            if kw in seen:
                continue
            if kw.lower() in text_lower:
                matched.append(kw)
                seen.add(kw)
        if matched:
            return category, matched

    return DEFAULT_STATUS, []
