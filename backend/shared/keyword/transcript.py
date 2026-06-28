"""Amazon Transcribe JSON output parsing (Phase 8.1).

PURE function. No I/O.

Amazon Transcribe writes a documented JSON payload to the configured
``OutputBucketName`` / ``OutputKey`` when a transcription job
completes. The relevant fields for KeywordMatcher (Phase 8.1) are:

    results.transcripts[0].transcript        — the full text body
    results.items[*].alternatives[0].confidence  — per-token confidence
                                                   for ``type == "pronunciation"``

Average confidence is the arithmetic mean of every pronunciation
item's top-alternative confidence (punctuation items carry no
confidence). It is recorded on TranscriptMetaTable for the SPA's
status view (Phase 5.4 ResponseApi reads it for transcripts that
fall under the operator's "review" threshold).

The function is deliberately tolerant of partial JSON: missing
``items`` collapses to ``avg_confidence = 0.0`` rather than raising,
because some Transcribe jobs (e.g. silence-only recordings) emit a
non-empty payload with an empty transcript and no items, and the
project's design (Property 10) classifies those as ``OTHER`` —
forcing a raise here would convert that legitimate outcome into a
Lambda error.

Failure semantics follow project principle 19(b) at the *structural*
level: when the input is not a dict, or when ``results.transcripts``
is shaped wrong (not a list, or its first element lacks a
``transcript`` string), the function raises ``ValueError``. Only the
*confidence aggregation* is tolerant.
"""

from __future__ import annotations

from typing import Any


def _iter_pronunciation_confidences(items: list[Any]) -> list[float]:
    """Extract ``[0,1]``-bounded confidences from pronunciation items.

    Helper to keep :func:`extract_transcript_payload` within Ruff's
    branch-count budget. Returns the list of valid confidence floats
    in input order; malformed items are silently skipped.
    """
    out: list[float] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "pronunciation":
            # Punctuation items carry no meaningful confidence.
            continue
        alternatives = item.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            continue
        first_alt = alternatives[0]
        if not isinstance(first_alt, dict):
            continue
        conf_raw = first_alt.get("confidence")
        if conf_raw is None:
            continue
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            continue
        # Confidence values outside [0,1] are nonsense; skip them so a
        # single corrupted token doesn't blow up the mean.
        if 0.0 <= conf <= 1.0:
            out.append(conf)
    return out


def extract_transcript_payload(body: dict[str, Any]) -> tuple[str, float]:
    """Extract ``(transcript_text, avg_confidence)`` from a Transcribe JSON.

    Args:
        body: The decoded JSON dictionary written by Amazon Transcribe
            to ``OutputKey``. Expected to have a ``results`` top-level
            key with ``transcripts`` and (optionally) ``items``.

    Returns:
        ``(text, avg_confidence)`` where ``text`` is the full
        transcript body (empty string is valid) and ``avg_confidence``
        is the arithmetic mean of every pronunciation item's
        top-alternative confidence, in ``[0.0, 1.0]``. When there are
        no pronunciation items, ``avg_confidence`` is ``0.0``.

    Raises:
        ValueError: ``body`` is not a dict, ``results`` is missing or
            not a dict, ``results.transcripts`` is missing / not a
            list / has no first element, or the first element's
            ``transcript`` field is not a string.
    """
    if not isinstance(body, dict):
        raise ValueError(f"transcript body must be a dict; got {type(body).__name__}")

    results = body.get("results")
    if not isinstance(results, dict):
        raise ValueError(
            f"transcript.results must be a dict; got {type(results).__name__}"
        )

    transcripts = results.get("transcripts")
    if not isinstance(transcripts, list) or not transcripts:
        raise ValueError(
            "transcript.results.transcripts must be a non-empty list; "
            f"got {transcripts!r}"
        )
    first = transcripts[0]
    if not isinstance(first, dict):
        raise ValueError(
            f"transcript.results.transcripts[0] must be a dict; "
            f"got {type(first).__name__}"
        )
    text = first.get("transcript")
    if not isinstance(text, str):
        raise ValueError(
            f"transcript.results.transcripts[0].transcript must be str; "
            f"got {type(text).__name__}"
        )

    items = results.get("items")
    if not isinstance(items, list):
        # Tolerant: silence-only jobs may omit ``items`` entirely.
        return text, 0.0

    confidences = _iter_pronunciation_confidences(items)
    if not confidences:
        return text, 0.0
    avg = sum(confidences) / len(confidences)
    return text, avg
