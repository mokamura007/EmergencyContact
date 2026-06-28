"""Property 10 - キーワードマッチング判定優先順位 PBT (Phase 13.10).

Validates: Requirements 7.3, 7.4, 7.5, 7.6, 7.8
    7.3 "WHEN テキスト中に SAFE カテゴリのキーワードが 1 個以上含まれ、
         かつ INJURED および UNAVAILABLE カテゴリのキーワードがいずれも
         含まれていない, THE Keyword_Matcher SHALL Voice_Status を SAFE と判定する。"
    7.4 "WHEN テキスト中に INJURED カテゴリのキーワードが 1 個以上含まれて
         いる, THE Keyword_Matcher SHALL Voice_Status を INJURED と判定する。"
    7.5 "WHEN テキスト中に INJURED カテゴリのキーワードが含まれず、かつ
         UNAVAILABLE カテゴリのキーワードが 1 個以上含まれている,
         THE Keyword_Matcher SHALL Voice_Status を UNAVAILABLE と判定する。"
    7.6 "IF テキスト中にいずれのカテゴリのキーワードも含まれていない,
         THEN THE Keyword_Matcher SHALL Voice_Status を OTHER と判定する。"
    7.8 "THE System SHALL INJURED を UNAVAILABLE よりも優先するステータス
         判定優先順位 (INJURED > UNAVAILABLE > SAFE > OTHER) を採用する。"

------------------------------------------------------------------------
本ファイルは ``shared/keyword/matcher.py::classify_voice_status`` (Phase
8.1 実装済) の挙動を Hypothesis により網羅的に検証する。

真の仕様 ("A 採用" — 13.12 / 13.14 / 13.15 / 13.16 / 13.17 と同じ方針)
------------------------------------------------------------------------
既存実装が定義する判定ロジックを真の仕様とする::

    text_lower = text.lower()
    for category in (INJURED, UNAVAILABLE, SAFE):    # ← 優先順
        matched = [kw for kw in dictionary[category]
                   if kw and kw.lower() in text_lower]
        if matched:
            return (category, dedup(matched))
    return ("OTHER", [])

帰結:

* 大文字小文字を区別しない (text / keyword 両方 .lower() 比較).
* 部分文字列一致 (形態素解析なし — 日本語のため).
* 空文字列キーワードはスキップ (degenerate match 回避).
* マッチ済みキーワードは挿入順を保ちつつ dedup.
* 優先順位: INJURED > UNAVAILABLE > SAFE > OTHER (高→低).
* 同時マッチ時の解決は高優先カテゴリで確定し、低優先カテゴリの
  matched list は返却しない (= 勝者カテゴリの matched のみ返す).

これは design.md "Keyword_Matcher / マッチング判定の擬似コード" および
matcher.py docstring と完全に一致する.

既存 example-based テストとの分業
------------------------------------------------------------------------
本 PBT は **valid input 集合** に対する挙動を網羅検証する.
以下の負経路は既存 ``backend/tests/shared/keyword/test_matcher.py`` の
example test がカバー済のため、本ファイルでは生成しない (DRY 原則):

* ``text`` が str 以外 → ValueError
* ``dictionary`` が dict 以外 → ValueError
* ``dictionary[category]`` が list 以外 → ValueError
* キーワード要素が str 以外 → ValueError
* 文言検証 (match パターン)

また以下の「優先カテゴリ matched と非優先カテゴリ keyword の dedup
詳細」も既存 example test の責務:

* 同一キーワードを辞書に重複記載した時の dedup
* 勝者カテゴリ内で複数キーワードが順序保持される挙動

本 PBT は「カテゴリ単位の優先順位確定」に集中する.

入力空間設計の意図
------------------------------------------------------------------------
3 カテゴリのキーワードと "neutral filler" を文字レベルで disjoint な
アルファベットに割り当てることで、

* "P1 (INJURED only) の生成テキストには UNAVAILABLE / SAFE キーワードが
  絶対に部分文字列として現れない"

を **生成時点で構造的に保証** する. これにより property が
"matched/not-matched の論理を正しく表現できているか" を検証ロジックの
責務にして、strategy の filter で精度を出す形を避ける. Hypothesis の
filter_too_much を回避し、200 examples ぶん高速に走らせる狙い.

副次的発見
------------------------------------------------------------------------
* 実装は ``dictionary[category]`` 内の **重複キーワード**, **空文字列**,
  **欠落カテゴリ** をそれぞれ "skip / 空 list として扱う" 形で
  graceful に受ける. Property は valid input に絞るため重複や空文字列
  キーワードは strategy 側で生成しない. これらの境界は example test
  側で検証済.
* design.md / tasks.md / requirements.md に **戻り値型のズレ** がある
  (実装は ``(status, matched)`` の tuple, 仕様文は "voiceStatus 文字列"
  と書くものもある). 13.10 では実装側を正 (A 採用) とし、文言修正は
  別タスクで起票する方針.
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.keyword.matcher import (
    DEFAULT_STATUS,
    PRIORITY_ORDER,
    classify_voice_status,
)

# ---------------------------------------------------------------------------
# Hypothesis settings — match Phase 13.x convention (>= 200 examples).
# ---------------------------------------------------------------------------

PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

# ---------------------------------------------------------------------------
# Reference vocabulary — kept locally so this test acts as an independent
# oracle. If matcher.py's PRIORITY_ORDER silently drifts, the equivalence
# property (P7) catches the regression.
# ---------------------------------------------------------------------------

#: Priority order used by the oracle (must match PRIORITY_ORDER in matcher.py).
CATEGORIES_PRIORITY: tuple[str, str, str] = ("INJURED", "UNAVAILABLE", "SAFE")
CATEGORY_OTHER: str = "OTHER"

# ---------------------------------------------------------------------------
# Disjoint alphabets — STRUCTURAL guarantee of category exclusivity.
#
# Each category's keywords are drawn from its own alphabet. The filler
# alphabet is also disjoint from all three. As a consequence:
#
#   "text containing INJURED keyword K + filler chars" CANNOT contain any
#   SAFE or UNAVAILABLE keyword as a substring, because those keywords
#   are built from entirely different characters.
#
# This is the key trick that makes the strategies fast: no filter retry,
# no rejection sampling, deterministic-time generation.
# ---------------------------------------------------------------------------

#: Alphabet for INJURED keywords. ASCII letters only — case-insensitivity
#: of the implementation is exercised by mixing upper/lower at the text
#: generation step (P6).
_ALPHABET_INJURED: str = "abcdef"

#: Alphabet for UNAVAILABLE keywords — disjoint from INJURED.
_ALPHABET_UNAVAILABLE: str = "ghijkl"

#: Alphabet for SAFE keywords — disjoint from INJURED / UNAVAILABLE.
_ALPHABET_SAFE: str = "mnopqr"

#: Filler alphabet — disjoint from all keyword alphabets. Used to pad
#: the text around the embedded keyword(s). Includes a space and a digit
#: to exercise non-letter chars.
_ALPHABET_FILLER: str = "stuvwxyz0123456789 "

# Sanity assertion at module load — defends against future maintainers
# accidentally introducing alphabet overlap.
assert not (set(_ALPHABET_INJURED) & set(_ALPHABET_UNAVAILABLE))
assert not (set(_ALPHABET_INJURED) & set(_ALPHABET_SAFE))
assert not (set(_ALPHABET_UNAVAILABLE) & set(_ALPHABET_SAFE))
assert not (set(_ALPHABET_INJURED) & set(_ALPHABET_FILLER))
assert not (set(_ALPHABET_UNAVAILABLE) & set(_ALPHABET_FILLER))
assert not (set(_ALPHABET_SAFE) & set(_ALPHABET_FILLER))

# ---------------------------------------------------------------------------
# Keyword strategies — generate non-empty keyword lists per category.
# ---------------------------------------------------------------------------


def _keyword_st(alphabet: str) -> st.SearchStrategy[str]:
    """Single keyword from ``alphabet`` of length 1..5.

    Length 1 is the smallest meaningful keyword; 5 keeps the search
    space tractable while exercising multi-char substring matching.
    """
    return st.text(alphabet=alphabet, min_size=1, max_size=5)


def _keyword_list_st(
    alphabet: str, min_size: int, max_size: int
) -> st.SearchStrategy[list[str]]:
    """List of distinct keywords from ``alphabet``.

    ``unique=True`` ensures the input list has no duplicates so that
    matched-list dedup logic is not exercised here (that is the
    responsibility of the example test ``test_duplicate_keyword_in_
    dictionary_deduplicated``).
    """
    return st.lists(
        _keyword_st(alphabet), min_size=min_size, max_size=max_size, unique=True
    )


_injured_kws_nonempty = _keyword_list_st(_ALPHABET_INJURED, 1, 3)
_unavailable_kws_nonempty = _keyword_list_st(_ALPHABET_UNAVAILABLE, 1, 3)
_safe_kws_nonempty = _keyword_list_st(_ALPHABET_SAFE, 1, 3)

# Variants that allow an empty list (used for negative-category slots).
_injured_kws_any = _keyword_list_st(_ALPHABET_INJURED, 0, 3)
_unavailable_kws_any = _keyword_list_st(_ALPHABET_UNAVAILABLE, 0, 3)
_safe_kws_any = _keyword_list_st(_ALPHABET_SAFE, 0, 3)

# ---------------------------------------------------------------------------
# Text generation strategies.
# ---------------------------------------------------------------------------

# A filler-only string. Cannot contain any keyword substring because the
# filler alphabet is disjoint from all keyword alphabets.
_filler_text: st.SearchStrategy[str] = st.text(
    alphabet=_ALPHABET_FILLER, min_size=0, max_size=20
)


def _maybe_swap_case(s: str, swap: bool) -> str:
    """Optionally swap the case of ``s`` to exercise case-insensitivity.

    The implementation compares ``text.lower()`` against ``kw.lower()``,
    so swapping the case of either side must not change the result. The
    test rotates this knob at the text-generation step (P6).
    """
    return s.swapcase() if swap else s


@st.composite
def _text_embedding_keyword(
    draw: st.DrawFn,
    keyword_pool: list[str],
) -> str:
    """Build a text that contains at least one keyword from ``keyword_pool``.

    The chosen keyword is embedded between two filler segments, with an
    optional case swap on the keyword to exercise case-insensitivity.
    """
    assert keyword_pool, "keyword_pool must be non-empty"
    keyword = draw(st.sampled_from(keyword_pool))
    prefix = draw(_filler_text)
    suffix = draw(_filler_text)
    swap = draw(st.booleans())
    return prefix + _maybe_swap_case(keyword, swap) + suffix


@st.composite
def _text_no_keywords(draw: st.DrawFn) -> str:
    """Filler-only text — by construction contains no keyword substring."""
    return draw(_filler_text)


# ---------------------------------------------------------------------------
# Dictionary strategies — composed scenario generators.
# ---------------------------------------------------------------------------


@st.composite
def _scenario_injured_only(
    draw: st.DrawFn,
) -> tuple[str, dict[str, list[str]]]:
    """Build (text, dictionary) where only INJURED matches.

    Text is built from INJURED-alphabet (one embedded keyword) + filler.
    SAFE / UNAVAILABLE keywords from their disjoint alphabets cannot
    appear by construction.
    """
    inj = draw(_injured_kws_nonempty)
    una = draw(_unavailable_kws_any)
    safe = draw(_safe_kws_any)
    text = draw(_text_embedding_keyword(inj))
    dictionary: dict[str, list[str]] = {
        "INJURED": inj,
        "UNAVAILABLE": una,
        "SAFE": safe,
    }
    return text, dictionary


@st.composite
def _scenario_unavailable_only(
    draw: st.DrawFn,
) -> tuple[str, dict[str, list[str]]]:
    """Build (text, dictionary) where only UNAVAILABLE matches.

    Text is built from UNAVAILABLE-alphabet + filler. INJURED keywords
    can be present in the dictionary but cannot appear in text.
    """
    inj = draw(_injured_kws_any)
    una = draw(_unavailable_kws_nonempty)
    safe = draw(_safe_kws_any)
    text = draw(_text_embedding_keyword(una))
    dictionary: dict[str, list[str]] = {
        "INJURED": inj,
        "UNAVAILABLE": una,
        "SAFE": safe,
    }
    return text, dictionary


@st.composite
def _scenario_safe_only(
    draw: st.DrawFn,
) -> tuple[str, dict[str, list[str]]]:
    """Build (text, dictionary) where only SAFE matches."""
    inj = draw(_injured_kws_any)
    una = draw(_unavailable_kws_any)
    safe = draw(_safe_kws_nonempty)
    text = draw(_text_embedding_keyword(safe))
    dictionary: dict[str, list[str]] = {
        "INJURED": inj,
        "UNAVAILABLE": una,
        "SAFE": safe,
    }
    return text, dictionary


@st.composite
def _scenario_no_match(
    draw: st.DrawFn,
) -> tuple[str, dict[str, list[str]]]:
    """Build (text, dictionary) where no category matches.

    Text consists only of filler chars. Dictionaries may carry keywords
    in any category but none can appear in the filler-only text.
    """
    inj = draw(_injured_kws_any)
    una = draw(_unavailable_kws_any)
    safe = draw(_safe_kws_any)
    text = draw(_text_no_keywords())
    dictionary: dict[str, list[str]] = {
        "INJURED": inj,
        "UNAVAILABLE": una,
        "SAFE": safe,
    }
    return text, dictionary


@st.composite
def _scenario_priority_resolution(
    draw: st.DrawFn,
) -> tuple[str, dict[str, list[str]], frozenset[str]]:
    """Build a scenario where >= 2 categories simultaneously match.

    Returns ``(text, dictionary, matching_categories)``:

    * ``text`` is built by concatenating one keyword from each of the
      chosen-to-match categories, padded by filler. Disjoint alphabets
      guarantee that *only* the chosen categories match.
    * ``matching_categories`` is the frozenset of categories whose
      keywords are embedded — the oracle uses this to compute the
      expected winner via priority order.

    The size of ``matching_categories`` is 2 or 3, so single-category
    scenarios are handled by the dedicated _scenario_*_only generators.
    """
    # Pick which categories will simultaneously match (2 or 3 of them).
    n_categories = draw(st.integers(min_value=2, max_value=3))
    # Sort so the strategy is reproducible across shrinks.
    matching = sorted(
        draw(
            st.lists(
                st.sampled_from(CATEGORIES_PRIORITY),
                min_size=n_categories,
                max_size=n_categories,
                unique=True,
            )
        )
    )
    matching_set = frozenset(matching)

    # Generate a non-empty keyword list for every category that must
    # match, and possibly-empty for the rest.
    inj = (
        draw(_injured_kws_nonempty)
        if "INJURED" in matching_set
        else draw(_injured_kws_any)
    )
    una = (
        draw(_unavailable_kws_nonempty)
        if "UNAVAILABLE" in matching_set
        else draw(_unavailable_kws_any)
    )
    safe = (
        draw(_safe_kws_nonempty)
        if "SAFE" in matching_set
        else draw(_safe_kws_any)
    )

    pools_by_category: dict[str, list[str]] = {
        "INJURED": inj,
        "UNAVAILABLE": una,
        "SAFE": safe,
    }

    # Build the text by concatenating one keyword from each matching
    # category, each wrapped in fresh filler. Order doesn't matter — the
    # priority rule depends only on which categories match, not on
    # positional ordering.
    pieces: list[str] = []
    for cat in matching:
        kw = draw(st.sampled_from(pools_by_category[cat]))
        swap = draw(st.booleans())
        pieces.append(draw(_filler_text))
        pieces.append(_maybe_swap_case(kw, swap))
    pieces.append(draw(_filler_text))
    text = "".join(pieces)

    dictionary: dict[str, list[str]] = {
        "INJURED": inj,
        "UNAVAILABLE": una,
        "SAFE": safe,
    }
    return text, dictionary, matching_set


@st.composite
def _scenario_any(
    draw: st.DrawFn,
) -> tuple[str, dict[str, list[str]]]:
    """Arbitrary in-domain scenario — superset of all the above.

    Used by P7 (oracle equivalence) so the property covers the full
    state space without partition. Picks one of the dedicated scenario
    generators uniformly.
    """
    choice = draw(st.integers(min_value=0, max_value=4))
    if choice == 0:
        return draw(_scenario_injured_only())
    if choice == 1:
        return draw(_scenario_unavailable_only())
    if choice == 2:
        return draw(_scenario_safe_only())
    if choice == 3:
        return draw(_scenario_no_match())
    text, dictionary, _ = draw(_scenario_priority_resolution())
    return text, dictionary


# ---------------------------------------------------------------------------
# Helper: oracle expression of the contract.
# ---------------------------------------------------------------------------


def _oracle(text: str, dictionary: dict[str, list[str]]) -> str:
    """Independent re-derivation of classify_voice_status's status.

    Returns only the resolved status — the matched-keyword list is not
    re-derived here because:

    * The status alone fully captures the priority resolution contract
      that this PBT validates (Property 10).
    * The matched-list semantics (insertion order, dedup, empty-kw skip)
      are already covered by example tests in test_matcher.py.

    The oracle is written in the most literal form possible: check each
    category in priority order, return the first one that has any
    substring hit.
    """
    text_lower = text.lower()
    for category in CATEGORIES_PRIORITY:
        keywords = dictionary.get(category, [])
        for kw in keywords:
            if not kw:
                continue
            if kw.lower() in text_lower:
                return category
    return CATEGORY_OTHER


# ===========================================================================
# P1 — INJURED キーワードのみマッチ → INJURED
# ===========================================================================


@PBT_SETTINGS
@example(
    scenario=(
        "abc",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
@example(
    scenario=(
        "stuv abc wxyz",
        {"INJURED": ["abc"], "UNAVAILABLE": [], "SAFE": []},
    )
)
@given(scenario=_scenario_injured_only())
def test_property10_injured_only_returns_injured(
    scenario: tuple[str, dict[str, list[str]]],
) -> None:
    """INJURED キーワードのみが text に含まれる ⇒ status == INJURED.

    Validates: Requirements 7.4
    """
    text, dictionary = scenario
    status, matched = classify_voice_status(text, dictionary)
    assert status == "INJURED", (
        f"INJURED-only scenario must yield INJURED; "
        f"text={text!r}, dictionary={dictionary!r}, got status={status!r} "
        f"matched={matched!r}"
    )
    # 勝者カテゴリの matched は非空であるべき (少なくとも 1 件)。
    assert matched, (
        f"matched must be non-empty when status==INJURED; "
        f"text={text!r}, dictionary={dictionary!r}"
    )


# ===========================================================================
# P2 — UNAVAILABLE キーワードのみマッチ → UNAVAILABLE
# ===========================================================================


@PBT_SETTINGS
@example(
    scenario=(
        "ghi",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
@example(
    scenario=(
        "stuv ghi wxyz",
        {"INJURED": [], "UNAVAILABLE": ["ghi"], "SAFE": []},
    )
)
@given(scenario=_scenario_unavailable_only())
def test_property10_unavailable_only_returns_unavailable(
    scenario: tuple[str, dict[str, list[str]]],
) -> None:
    """UNAVAILABLE のみが text に含まれる ⇒ status == UNAVAILABLE.

    Validates: Requirements 7.5
    """
    text, dictionary = scenario
    status, matched = classify_voice_status(text, dictionary)
    assert status == "UNAVAILABLE", (
        f"UNAVAILABLE-only scenario must yield UNAVAILABLE; "
        f"text={text!r}, dictionary={dictionary!r}, got status={status!r} "
        f"matched={matched!r}"
    )
    assert matched, (
        f"matched must be non-empty when status==UNAVAILABLE; "
        f"text={text!r}, dictionary={dictionary!r}"
    )


# ===========================================================================
# P3 — SAFE キーワードのみマッチ → SAFE
# ===========================================================================


@PBT_SETTINGS
@example(
    scenario=(
        "mno",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
@example(
    scenario=(
        "stuv mno wxyz",
        {"INJURED": [], "UNAVAILABLE": [], "SAFE": ["mno"]},
    )
)
@given(scenario=_scenario_safe_only())
def test_property10_safe_only_returns_safe(
    scenario: tuple[str, dict[str, list[str]]],
) -> None:
    """SAFE のみが text に含まれる ⇒ status == SAFE.

    Validates: Requirements 7.3
    """
    text, dictionary = scenario
    status, matched = classify_voice_status(text, dictionary)
    assert status == "SAFE", (
        f"SAFE-only scenario must yield SAFE; "
        f"text={text!r}, dictionary={dictionary!r}, got status={status!r} "
        f"matched={matched!r}"
    )
    assert matched, (
        f"matched must be non-empty when status==SAFE; "
        f"text={text!r}, dictionary={dictionary!r}"
    )


# ===========================================================================
# P4 — 全カテゴリ未マッチ → OTHER
# ===========================================================================


@PBT_SETTINGS
@example(
    scenario=(
        "",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
@example(
    scenario=(
        "stuv wxyz 0123",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
@given(scenario=_scenario_no_match())
def test_property10_no_match_returns_other(
    scenario: tuple[str, dict[str, list[str]]],
) -> None:
    """どのカテゴリのキーワードも text に含まれない ⇒ status == OTHER.

    Validates: Requirements 7.6
    """
    text, dictionary = scenario
    status, matched = classify_voice_status(text, dictionary)
    assert status == DEFAULT_STATUS == "OTHER", (
        f"No-match scenario must yield OTHER; "
        f"text={text!r}, dictionary={dictionary!r}, got status={status!r} "
        f"matched={matched!r}"
    )
    assert matched == [], (
        f"matched must be empty when status==OTHER; "
        f"text={text!r}, dictionary={dictionary!r}, got matched={matched!r}"
    )


# ===========================================================================
# P5 — 複数カテゴリ同時マッチ時の優先順位 (INJURED > UNAVAILABLE > SAFE)
# ===========================================================================


@PBT_SETTINGS
# Pin: INJURED + UNAVAILABLE 同時マッチ → INJURED
@example(
    scenario=(
        "abc ghi",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
        frozenset({"INJURED", "UNAVAILABLE"}),
    )
)
# Pin: UNAVAILABLE + SAFE 同時マッチ → UNAVAILABLE
@example(
    scenario=(
        "ghi mno",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
        frozenset({"UNAVAILABLE", "SAFE"}),
    )
)
# Pin: INJURED + SAFE 同時マッチ → INJURED
@example(
    scenario=(
        "abc mno",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
        frozenset({"INJURED", "SAFE"}),
    )
)
# Pin: 全カテゴリ同時マッチ → INJURED
@example(
    scenario=(
        "abc ghi mno",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
        frozenset({"INJURED", "UNAVAILABLE", "SAFE"}),
    )
)
@given(scenario=_scenario_priority_resolution())
def test_property10_priority_resolution(
    scenario: tuple[str, dict[str, list[str]], frozenset[str]],
) -> None:
    """複数カテゴリが同時にマッチする ⇒ 高優先順カテゴリで status 確定.

    優先順位: INJURED > UNAVAILABLE > SAFE.

    検証:

    * 期待 status は ``matching_categories`` の中で PRIORITY_ORDER 順に
      最も先に現れる category — つまり INJURED が含まれれば INJURED,
      含まれず UNAVAILABLE があれば UNAVAILABLE, それ以外で SAFE.
    * 実装の PRIORITY_ORDER 定数が ("INJURED", "UNAVAILABLE", "SAFE")
      であることも assert (ローカル参照とのドリフト検出).

    Validates: Requirements 7.4, 7.5, 7.8
    """
    text, dictionary, matching_set = scenario

    # 期待値計算 — ローカル CATEGORIES_PRIORITY と実装の PRIORITY_ORDER
    # の整合性も同時に検証する.
    assert PRIORITY_ORDER == CATEGORIES_PRIORITY, (
        f"PRIORITY_ORDER drift detected: impl={PRIORITY_ORDER}, "
        f"oracle={CATEGORIES_PRIORITY}"
    )

    expected: str | None = None
    for cat in CATEGORIES_PRIORITY:
        if cat in matching_set:
            expected = cat
            break
    assert expected is not None, (
        f"matching_set must not be empty in priority scenario; "
        f"matching_set={matching_set}"
    )

    status, matched = classify_voice_status(text, dictionary)
    assert status == expected, (
        f"priority resolution failed; "
        f"text={text!r}, dictionary={dictionary!r}, "
        f"matching_categories={sorted(matching_set)!r}, "
        f"expected={expected!r}, got={status!r}, matched={matched!r}"
    )
    # 勝者カテゴリの matched は非空 (∵ そのカテゴリが matching_set に
    # 含まれており、テキストにキーワードが少なくとも 1 つ埋め込まれている).
    assert matched, (
        f"matched must be non-empty for winning category; "
        f"text={text!r}, dictionary={dictionary!r}, status={status!r}"
    )


# ===========================================================================
# P6 — 大文字小文字無視の不変性
# ===========================================================================


@PBT_SETTINGS
@example(
    scenario=(
        "ABC",
        {"INJURED": ["abc"], "UNAVAILABLE": [], "SAFE": []},
    )
)
@example(
    scenario=(
        "abc",
        {"INJURED": ["ABC"], "UNAVAILABLE": [], "SAFE": []},
    )
)
@example(
    scenario=(
        "AbC",
        {"INJURED": ["aBc"], "UNAVAILABLE": [], "SAFE": []},
    )
)
@given(scenario=_scenario_any())
def test_property10_case_insensitive_invariance(
    scenario: tuple[str, dict[str, list[str]]],
) -> None:
    """text / dictionary 双方の case 変換は status 判定を変えない.

    検証:

    * ``classify_voice_status(text.lower(), dictionary)`` の status と
      ``classify_voice_status(text.upper(), dictionary)`` の status は
      一致する.
    * dictionary 側のキーワードを upper / lower に変換しても status は
      一致する.

    Validates: Requirements 7.3, 7.4, 7.5, 7.6, 7.8 (大文字小文字の
        差異が判定結果に影響しないこと — design.md "大文字小文字は区別しない")
    """
    text, dictionary = scenario

    status_orig, _ = classify_voice_status(text, dictionary)
    status_text_lower, _ = classify_voice_status(text.lower(), dictionary)
    status_text_upper, _ = classify_voice_status(text.upper(), dictionary)
    assert (
        status_orig == status_text_lower == status_text_upper
    ), (
        f"case of text must not affect status; "
        f"text={text!r}, dictionary={dictionary!r}, "
        f"orig={status_orig!r}, lower={status_text_lower!r}, "
        f"upper={status_text_upper!r}"
    )

    # キーワード側の case 変換も同様。
    dict_upper: dict[str, list[str]] = {
        cat: [kw.upper() for kw in kws]
        for cat, kws in dictionary.items()
    }
    dict_lower: dict[str, list[str]] = {
        cat: [kw.lower() for kw in kws]
        for cat, kws in dictionary.items()
    }
    status_kw_upper, _ = classify_voice_status(text, dict_upper)
    status_kw_lower, _ = classify_voice_status(text, dict_lower)
    assert status_orig == status_kw_upper == status_kw_lower, (
        f"case of dictionary keywords must not affect status; "
        f"text={text!r}, dictionary={dictionary!r}, "
        f"orig={status_orig!r}, kw_upper={status_kw_upper!r}, "
        f"kw_lower={status_kw_lower!r}"
    )


# ===========================================================================
# P7 — 対称性推論 (第17原則): implementation status == oracle status
# ===========================================================================


@PBT_SETTINGS
# Pin: 単一 INJURED マッチ
@example(
    scenario=(
        "abc",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
# Pin: 単一 UNAVAILABLE マッチ
@example(
    scenario=(
        "ghi",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
# Pin: 単一 SAFE マッチ
@example(
    scenario=(
        "mno",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
# Pin: 未マッチ → OTHER
@example(
    scenario=(
        "wxyz",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
# Pin: INJURED + UNAVAILABLE 同時 → INJURED
@example(
    scenario=(
        "abc ghi",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
# Pin: UNAVAILABLE + SAFE 同時 → UNAVAILABLE
@example(
    scenario=(
        "ghi mno",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
# Pin: INJURED + SAFE 同時 → INJURED
@example(
    scenario=(
        "abc mno",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
# Pin: 全カテゴリ同時 → INJURED
@example(
    scenario=(
        "abc ghi mno",
        {"INJURED": ["abc"], "UNAVAILABLE": ["ghi"], "SAFE": ["mno"]},
    )
)
@given(scenario=_scenario_any())
def test_property10_equivalent_to_oracle(
    scenario: tuple[str, dict[str, list[str]]],
) -> None:
    """classify_voice_status の status は独立 oracle と等価.

    P1〜P5 を合わせると暗黙的にこの等価性が導かれるが、明示的に encode
    しておくことで:

    * P1〜P5 の partition が将来未網羅クラスを生んだ場合の検出
    * 実装の優先順位ループ実装の入れ替えなどの回帰検出
    * "AならB" と "BならA" の双方向検証 (第17原則 対称性推論):
      - "実装が INJURED と判定" ⇔ "oracle が INJURED と判定"
      - 全 status 値について同様

    を担保する.

    matched-keyword リストは比較しない (insertion-order / dedup の
    詳細は example test の責務であり, status の優先順位確定が本
    Property 10 の関心事).

    Validates: Requirements 7.3, 7.4, 7.5, 7.6, 7.8 (優先順位の必要十分条件)
    """
    text, dictionary = scenario
    expected = _oracle(text, dictionary)
    actual, _matched = classify_voice_status(text, dictionary)
    assert actual == expected, (
        f"oracle/impl status drift; "
        f"text={text!r}, dictionary={dictionary!r}, "
        f"oracle={expected!r}, impl={actual!r}"
    )
