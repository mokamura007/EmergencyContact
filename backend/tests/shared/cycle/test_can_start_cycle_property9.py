"""Property 9 - 実行中サイクルの単一性(排他) PBT (Phase 13.9).

Validates: Requirements 4.8
    "IF Cycle ステータスが「実行中」のレコードが既に 1 件以上存在する状態で
     別の Cycle 起動が要求される, THEN THE Cycle_Manager SHALL 起動を拒否し、
     既存 Cycle の ID と重複起動である旨を示すエラーを返却し、新規 Cycle
     レコードを作成しない。"

------------------------------------------------------------------------
本ファイルは ``shared/cycle/exclusivity.py::can_start_cycle`` (Phase
13.9 本タスクで新規実装) の挙動を Hypothesis により網羅的に検証する。

真の仕様 ("A 採用" - 先例 13.15 / 13.16 / 13.17 と同方針)
------------------------------------------------------------------------
design.md Property 9 の数学的契約をそのまま実装した真の仕様::

    can_start_cycle(C) == True
        iff |{c ∈ C : c.get("status") == "RUNNING"}| == 0

具体例:

* 空リストは vacuous-true で ``True``.
* 全要素の ``status`` が ``"RUNNING"`` 以外 (``"COMPLETED"``,
  ``"TIMEOUT"``, ``"START_FAILED"``, ``None``, キー欠損, 未知文字列,
  小文字 ``"running"`` 等) => ``True``.
* 1 要素でも ``status == "RUNNING"`` ⇒ ``False``.

これは Requirement 4.8 の「『実行中』のレコードが 1 件以上 → 起動拒否」を
リテラル ``"RUNNING"`` 状態の有無に正規化した形であり, CycleApi handler
の RUNNING 判定 (Phase 15.7.5 以降は ``_query_running_cycles()`` の結果を
``can_start_cycle`` に委譲する形に統一済) と整合する.

入力前提
------------------------------------------------------------------------
タスク仕様上, ``cycles`` は DynamoDB ``StatusStartedAtIndex`` Query 結果
(つまり ``status == "RUNNING"`` で事前フィルタ済の list[dict]) を主用途と
する. しかし契約は混在 list でも成立する (status="RUNNING" 以外を含む list
でも内部フィルタが効く). P3 の独立 oracle で両ケースを同時に検証する.

文言ズレに関する脚注
------------------------------------------------------------------------
tasks.md 13.9 Done When は「RUNNING 0 件のときのみ true、1 件以上で false」
と書かれており, 事前フィルタ済 list を前提とした表現. design.md Property 9
は混在 list を許容する一般化形. ユーザー確定方針として:

* **実装を正とする (A 採用) — design.md 一般化形 (status="RUNNING"
  での内部フィルタ) を採用**
* 事前フィルタ済 list 入力下では両表現は同値 (list 全要素が
  status="RUNNING" -> 個数 == len(cycles) -> len(cycles)==0 <=> 個数==0)
* **design.md / tasks.md の文言修正は別タスクで起票**
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.cycle.exclusivity import can_start_cycle

# ---------------------------------------------------------------------------
# Hypothesis settings — match Phase 13.x convention (>= 200 examples).
# ---------------------------------------------------------------------------

PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

# ---------------------------------------------------------------------------
# Reference vocabulary — kept in this file (not imported from
# exclusivity.py) so the test acts as an independent oracle. If the
# implementation's RUNNING literal silently drifts (e.g. to lowercase
# or a Status enum), this constant diverges and P3 (equivalence) pins
# the regression.
# ---------------------------------------------------------------------------

#: Cycle status that triggers the exclusivity guard per Requirement 4.8.
RUNNING_STATUS: str = "RUNNING"

#: Other documented Cycle status values (design.md D2 Cycle).
_NON_RUNNING_KNOWN: frozenset[str] = frozenset(
    {"COMPLETED", "TIMEOUT", "START_FAILED"}
)

# ---------------------------------------------------------------------------
# Hypothesis strategies.
# ---------------------------------------------------------------------------

# A "running" row: status == "RUNNING" plus a stable cycleId so the row
# resembles real CycleTable data. Other attributes (mode / startedAt /
# retryCount …) are irrelevant to the predicate and intentionally omitted.
_running_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda cid: {"cycleId": cid, "status": RUNNING_STATUS},
    cid=st.text(
        alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
        min_size=1,
        max_size=12,
    ),
)

# A "non-running" row covers four classes that must NOT trigger the
# exclusivity guard:
#   (a) Known non-running status      -> COMPLETED / TIMEOUT / START_FAILED
#   (b) Missing 'status' key entirely
#   (c) status == None
#   (d) status is an unrecognised string (including the case-variant
#       "running", future statuses like "PAUSED", or arbitrary text)
_non_running_known_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda cid, s: {"cycleId": cid, "status": s},
    cid=st.text(min_size=1, max_size=12),
    s=st.sampled_from(sorted(_NON_RUNNING_KNOWN)),
)

_missing_status_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda cid: {"cycleId": cid},
    cid=st.text(min_size=1, max_size=12),
)

_none_status_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda cid: {"cycleId": cid, "status": None},
    cid=st.text(min_size=1, max_size=12),
)

# An "unknown string" row: any text strictly different from "RUNNING".
# Using filter() rather than constructing a complex alphabet keeps the
# generator readable; the rejection rate is negligible because
# Hypothesis rarely draws the exact literal "RUNNING".
_unknown_string_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda cid, s: {"cycleId": cid, "status": s},
    cid=st.text(min_size=1, max_size=12),
    s=st.text(min_size=0, max_size=12).filter(lambda v: v != RUNNING_STATUS),
)

_non_running_row: st.SearchStrategy[dict[str, Any]] = st.one_of(
    _non_running_known_row,
    _missing_status_row,
    _none_status_row,
    _unknown_string_row,
)

# Mixed: any of the above classes.
_any_row: st.SearchStrategy[dict[str, Any]] = st.one_of(
    _running_row, _non_running_row
)


# ---------------------------------------------------------------------------
# Helper: independent oracle expression of the contract.
# ---------------------------------------------------------------------------


def _oracle(cycles: list[dict[str, Any]]) -> bool:
    """Independent re-derivation of the contract.

    Written in the most literal form possible — ``no c has
    status=="RUNNING"`` — so P3 catches drift on any class of input
    the strategies might fail to enumerate explicitly.
    """
    return not any(c.get("status") == RUNNING_STATUS for c in cycles)


# ===========================================================================
# P1 forward — 空リスト & 全要素非 RUNNING → True
# ===========================================================================


@PBT_SETTINGS
@example(rows=[])  # vacuous-true boundary
@example(rows=[{"cycleId": "c1", "status": "COMPLETED"}])
@example(rows=[{"cycleId": "c1", "status": "TIMEOUT"}])
@example(rows=[{"cycleId": "c1", "status": "START_FAILED"}])
@example(rows=[{"cycleId": "c1"}])  # missing status key
@example(rows=[{"cycleId": "c1", "status": None}])
@example(rows=[{"cycleId": "c1", "status": "running"}])  # case-sensitive
@example(
    rows=[
        {"cycleId": "c1", "status": "COMPLETED"},
        {"cycleId": "c2", "status": "TIMEOUT"},
        {"cycleId": "c3", "status": "START_FAILED"},
    ]
)
@given(rows=st.lists(_non_running_row, min_size=0, max_size=20))
def test_property9_no_running_returns_true(
    rows: list[dict[str, Any]],
) -> None:
    """∀ c ∈ cycles : c.status != "RUNNING" ⇒ can_start_cycle == True.

    Validates: Requirements 4.8 (RUNNING 0 件 → 起動可)
    """
    assert can_start_cycle(rows) is True, (
        f"expected True when no row has status='RUNNING'; rows={rows!r}"
    )


# ===========================================================================
# P2 backward — 1 件でも RUNNING → False
# ===========================================================================


@PBT_SETTINGS
@example(non_runnings=[], one_running={"cycleId": "c1", "status": "RUNNING"}, extras=[])
@example(
    non_runnings=[{"cycleId": "c1", "status": "COMPLETED"}],
    one_running={"cycleId": "c2", "status": "RUNNING"},
    extras=[{"cycleId": "c3", "status": "TIMEOUT"}],
)
@example(
    non_runnings=[],
    one_running={"cycleId": "c1", "status": "RUNNING"},
    extras=[{"cycleId": "c2", "status": "RUNNING"}],  # multiple RUNNING
)
@example(
    non_runnings=[
        {"cycleId": "c1"},
        {"cycleId": "c2", "status": None},
        {"cycleId": "c3", "status": "running"},
    ],
    one_running={"cycleId": "c4", "status": "RUNNING"},
    extras=[],
)
@given(
    non_runnings=st.lists(_non_running_row, min_size=0, max_size=10),
    one_running=_running_row,
    extras=st.lists(_any_row, min_size=0, max_size=10),
)
def test_property9_any_running_returns_false(
    non_runnings: list[dict[str, Any]],
    one_running: dict[str, Any],
    extras: list[dict[str, Any]],
) -> None:
    """∃ c ∈ cycles : c.status == "RUNNING" ⇒ can_start_cycle == False.

    Covers the four non-running classes plus a guaranteed RUNNING row:
        (a) known non-running (COMPLETED / TIMEOUT / START_FAILED)
        (b) missing 'status' key
        (c) status == None
        (d) unrecognised / case-variant string

    Validates: Requirements 4.8 (RUNNING 1 件以上 → 起動拒否)
    """
    rows = [*non_runnings, one_running, *extras]
    assert can_start_cycle(rows) is False, (
        f"expected False when at least one RUNNING row present; "
        f"one_running={one_running!r}, rows={rows!r}"
    )


# ===========================================================================
# P3 equivalence - 対称性推論 (第 17 原則): implementation <=> oracle
# ===========================================================================


@PBT_SETTINGS
@example(rows=[])
@example(rows=[{"status": "RUNNING"}])
@example(rows=[{"status": "COMPLETED"}])
@example(rows=[{"status": "RUNNING"}, {"status": "COMPLETED"}])
@example(rows=[{"status": "COMPLETED"}, {"status": "RUNNING"}])  # order shuffle
@example(
    rows=[
        {"status": "RUNNING"},
        {"status": "RUNNING"},
        {"status": "RUNNING"},
    ]
)
@example(rows=[{}, {"status": None}, {"status": "running"}])  # edge non-RUNNING
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property9_equivalent_to_no_running_oracle(
    rows: list[dict[str, Any]],
) -> None:
    """can_start_cycle(C) == ¬∃ c ∈ C : c.status == "RUNNING".

    This is the bi-directional invariant — guarantees neither false
    positives (rejecting when no RUNNING) nor false negatives
    (admitting when RUNNING present). P1 / P2 together imply this
    equivalence, but encoding it explicitly catches regressions where
    the implementation drifts on a class we did not enumerate in P1 /
    P2's one_of(...).

    Validates: Requirements 4.8 (起動可否の必要十分条件)
    """
    actual = can_start_cycle(rows)
    expected = _oracle(rows)
    assert actual == expected, (
        f"contract drift: oracle={expected} impl={actual} rows={rows!r}"
    )


# ===========================================================================
# P4 length-independence — 件数 N が変化しても判定の符号は status のみ依存
# ===========================================================================


@PBT_SETTINGS
@example(n=1)
@example(n=2)
@example(n=10)
@given(n=st.integers(min_value=1, max_value=15))
def test_property9_all_running_any_length_is_false(n: int) -> None:
    """全要素 RUNNING の list は長さ N (>=1) に関わらず False.

    Validates: Requirements 4.8 (RUNNING の "件数" は 1 と 2 以上を区別しない)
    """
    rows = [{"cycleId": f"c{i}", "status": "RUNNING"} for i in range(n)]
    assert can_start_cycle(rows) is False, (
        f"expected False for N={n} all-RUNNING rows; rows={rows!r}"
    )


@PBT_SETTINGS
@example(rows=[])
@example(rows=[{"cycleId": "c1", "status": "COMPLETED"}])
@given(rows=st.lists(_non_running_row, min_size=0, max_size=15))
def test_property9_no_running_any_length_is_true(
    rows: list[dict[str, Any]],
) -> None:
    """RUNNING を含まない list は長さに関わらず True.

    Validates: Requirements 4.8 (非 RUNNING のみの list は常に起動可)
    """
    assert can_start_cycle(rows) is True, (
        f"expected True for N={len(rows)} non-RUNNING rows; rows={rows!r}"
    )


# ===========================================================================
# P5 purity — 同一入力で複数回呼出して同結果 (参照透過性)
# ===========================================================================


@PBT_SETTINGS
@example(rows=[])
@example(rows=[{"status": "RUNNING"}])
@example(rows=[{"status": "COMPLETED"}, {"status": "RUNNING"}])
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property9_pure_function_idempotent(
    rows: list[dict[str, Any]],
) -> None:
    """can_start_cycle(C) == can_start_cycle(C) — 副作用なし.

    Pure function 契約の最小チェック: 同一入力で 3 回連続呼出して全て
    同値. これは Phase 13.9 の Done When「PBT」項目には含まれないが,
    純関数化のメリットを test レベルで担保する.

    Validates: Requirements 4.8 (判定は履歴非依存)
    """
    first = can_start_cycle(rows)
    second = can_start_cycle(rows)
    third = can_start_cycle(rows)
    assert first == second == third, (
        f"non-deterministic: 1st={first}, 2nd={second}, 3rd={third}, "
        f"rows={rows!r}"
    )
