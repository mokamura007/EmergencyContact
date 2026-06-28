"""Property 16 - Cycle 完了判定 PBT (Phase 13.16).

Validates: Requirements 11.4
    "WHEN Cycle に含まれるすべての対象者について、応答受領または最大発信回数
     到達のいずれかにより最終ステータスが確定する, THE Cycle_Manager SHALL
     Cycle のステータスを「完了」に更新する。"

------------------------------------------------------------------------
本ファイルは ``shared/cycle/finalize.py::is_cycle_completed`` (Phase 6.6
実装済) の挙動を Hypothesis により網羅的に検証する。

真の仕様 ("A 採用" - ユーザー確定済)
------------------------------------------------------------------------
既存実装が定義する TERMINAL 集合を真の仕様とする::

    TERMINAL = {SAFE, INJURED, UNAVAILABLE, UNREACHABLE}

    is_cycle_completed(rows)
        == all(row.get("voiceStatus") in TERMINAL for row in rows)

すなわち:

* 空リストは vacuous-true で ``True``.
* 全要素の ``voiceStatus`` が TERMINAL 集合に含まれる iff ``True``.
* 1 要素でも ``voiceStatus`` が TERMINAL に含まれない (OTHER / PENDING /
  欠損キー / 不正な文字列値) iff ``False``.

これは Requirement 11.4 の「最終ステータスが確定」を「TERMINAL に到達」
として運用する (応答受領 = SAFE/INJURED/UNAVAILABLE, 最大発信回数到達 =
UNREACHABLE) 解釈であり, Phase 6.6 truth table と整合する.

文言ズレに関する脚注
------------------------------------------------------------------------
design.md Property 16 および tasks.md 13.16 Done When は ``!= PENDING``
という表現で書かれているが, これは厳密には OTHER を「完了」に含めて
しまうため既存実装と齟齬がある. ユーザー確定方針として:

* **実装を正とする (A 採用)**
* **design.md / tasks.md の文言修正は別タスクで起票**

本 PBT は実装側の仕様 (TERMINAL 集合) に従う.
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.cycle.finalize import is_cycle_completed
from shared.retry.evaluator import VALID_VOICE_STATUS_VALUES

# ---------------------------------------------------------------------------
# Hypothesis settings — match Phase 13.x convention (>= 200 examples).
# ---------------------------------------------------------------------------

PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

# ---------------------------------------------------------------------------
# Reference vocabulary — kept in this file (not imported from finalize) so
# the test acts as an independent oracle. If finalize.py's TERMINAL set
# silently drifts, this set diverges and the equivalence property (P3)
# pins the regression.
# ---------------------------------------------------------------------------

#: voiceStatus values that count as "最終ステータス確定" per Requirement 11.4.
TERMINAL_VOICE_STATUSES: frozenset[str] = frozenset(
    {"SAFE", "INJURED", "UNAVAILABLE", "UNREACHABLE"}
)

#: Non-terminal voiceStatus values defined in the project vocabulary
#: (PENDING / OTHER). Used to seed "non-completed" rows.
_NON_TERMINAL_KNOWN: frozenset[str] = (
    VALID_VOICE_STATUS_VALUES - TERMINAL_VOICE_STATUSES
)

# ---------------------------------------------------------------------------
# Hypothesis strategies.
# ---------------------------------------------------------------------------

# A "terminal" row: only voiceStatus matters for is_cycle_completed, but we
# include a stable employeeId so the row resembles real ResponseTable data.
_terminal_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, status: {"employeeId": eid, "voiceStatus": status},
    eid=st.text(
        alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
        min_size=1,
        max_size=8,
    ),
    status=st.sampled_from(sorted(TERMINAL_VOICE_STATUSES)),
)

# A "non-terminal" row covers the four classes we care about:
#   (a) Known non-terminal vocabulary  -> PENDING / OTHER
#   (b) Missing voiceStatus key entirely
#   (c) voiceStatus is None
#   (d) voiceStatus is an unrecognised arbitrary string (e.g. "FOO")
_non_terminal_known_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, status: {"employeeId": eid, "voiceStatus": status},
    eid=st.text(min_size=1, max_size=8),
    status=st.sampled_from(sorted(_NON_TERMINAL_KNOWN)),
)

_missing_key_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid: {"employeeId": eid},
    eid=st.text(min_size=1, max_size=8),
)

_none_status_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid: {"employeeId": eid, "voiceStatus": None},
    eid=st.text(min_size=1, max_size=8),
)

# An "unrecognised string" row: any string NOT in the documented vocabulary
# (including TERMINAL). Using filter() rather than complex alphabets keeps
# the generator readable; Hypothesis settles fast because the rejection
# rate is negligible.
_unknown_string_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, status: {"employeeId": eid, "voiceStatus": status},
    eid=st.text(min_size=1, max_size=8),
    status=st.text(min_size=0, max_size=12).filter(
        lambda s: s not in VALID_VOICE_STATUS_VALUES
    ),
)

_non_terminal_row: st.SearchStrategy[dict[str, Any]] = st.one_of(
    _non_terminal_known_row,
    _missing_key_row,
    _none_status_row,
    _unknown_string_row,
)

# Mixed rows: any of the eight cases above.
_any_row: st.SearchStrategy[dict[str, Any]] = st.one_of(
    _terminal_row, _non_terminal_row
)


# ---------------------------------------------------------------------------
# Helper: oracle expression of the contract.
# ---------------------------------------------------------------------------


def _oracle(rows: list[dict[str, Any]]) -> bool:
    """Independent re-derivation of the contract.

    Used by P3 as a property-level equivalence check against the
    implementation. Intentionally written in the most literal form
    possible — ``all`` over ``voiceStatus ∈ TERMINAL``.
    """
    return all(row.get("voiceStatus") in TERMINAL_VOICE_STATUSES for row in rows)


# ===========================================================================
# P1 forward — 全要素 TERMINAL → True
# ===========================================================================


@PBT_SETTINGS
@example(rows=[])  # vacuous-true boundary
@example(rows=[{"employeeId": "e1", "voiceStatus": "SAFE"}])
@example(rows=[{"employeeId": "e1", "voiceStatus": "UNREACHABLE"}])
@given(rows=st.lists(_terminal_row, min_size=0, max_size=20))
def test_property16_all_terminal_returns_true(
    rows: list[dict[str, Any]],
) -> None:
    """∀ r ∈ rows : r.voiceStatus ∈ TERMINAL ⇒ is_cycle_completed == True.

    Validates: Requirements 11.4 (全対象者の最終ステータス確定 → 完了)
    """
    assert is_cycle_completed(rows) is True, (
        f"expected True for all-terminal rows, got False; rows={rows!r}"
    )


# ===========================================================================
# P2 backward — 1 件でも非 TERMINAL → False
# ===========================================================================


@PBT_SETTINGS
@example(terminals=[], non_terminal={"voiceStatus": "OTHER"}, extras=[])
@example(terminals=[], non_terminal={"voiceStatus": "PENDING"}, extras=[])
@example(terminals=[], non_terminal={}, extras=[])  # missing voiceStatus key
@example(terminals=[], non_terminal={"voiceStatus": None}, extras=[])
@example(
    terminals=[],
    non_terminal={"voiceStatus": "UNKNOWN_VALUE"},
    extras=[],
)
@example(
    terminals=[{"employeeId": "e1", "voiceStatus": "SAFE"}],
    non_terminal={"employeeId": "e2", "voiceStatus": "PENDING"},
    extras=[{"employeeId": "e3", "voiceStatus": "INJURED"}],
)
@given(
    terminals=st.lists(_terminal_row, min_size=0, max_size=10),
    non_terminal=_non_terminal_row,
    extras=st.lists(_any_row, min_size=0, max_size=10),
)
def test_property16_any_non_terminal_returns_false(
    terminals: list[dict[str, Any]],
    non_terminal: dict[str, Any],
    extras: list[dict[str, Any]],
) -> None:
    """∃ r ∈ rows : r.voiceStatus ∉ TERMINAL ⇒ is_cycle_completed == False.

    Covers the four non-terminal classes:
        (a) known non-terminal value (PENDING / OTHER)
        (b) missing 'voiceStatus' key
        (c) voiceStatus == None
        (d) arbitrary unrecognised string

    Validates: Requirements 11.4 (1 件でも未確定なら未完了)
    """
    rows = [*terminals, non_terminal, *extras]
    assert is_cycle_completed(rows) is False, (
        f"expected False when at least one non-terminal row present; "
        f"non_terminal={non_terminal!r}, rows={rows!r}"
    )


# ===========================================================================
# P3 equivalence - 対称性推論 (第17原則): implementation <=> oracle
# ===========================================================================


@PBT_SETTINGS
@example(rows=[])
@example(rows=[{"voiceStatus": "OTHER"}])
@example(rows=[{"voiceStatus": "PENDING"}])
@example(rows=[{"voiceStatus": "SAFE"}, {"voiceStatus": "INJURED"}])
@example(
    rows=[
        {"voiceStatus": "SAFE"},
        {"voiceStatus": "UNREACHABLE"},
        {"voiceStatus": "OTHER"},
    ]
)
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property16_equivalent_to_all_terminal_oracle(
    rows: list[dict[str, Any]],
) -> None:
    """is_cycle_completed(R) == all(r.get(voiceStatus) ∈ TERMINAL for r in R).

    This is the bi-directional invariant — guarantees neither false
    positives nor false negatives. P1 and P2 together imply this
    equivalence, but encoding it explicitly catches regressions where
    the implementation drifts on a class we did not enumerate in P2's
    one_of(...) (e.g. a future "PARTIAL" status).

    Validates: Requirements 11.4 (完了判定の必要十分条件)
    """
    actual = is_cycle_completed(rows)
    expected = _oracle(rows)
    assert actual == expected, (
        f"contract drift: oracle={expected} impl={actual} rows={rows!r}"
    )
