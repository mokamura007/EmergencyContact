"""Property 17 - タイムアウト処理 PBT (Phase 13.17).

Validates: Requirements 14.4, 14.5
    14.4 "WHEN サイクル開始から 60 分が経過, THE Cycle_Manager SHALL 未確定の
         対象者を「未到達」(UNREACHABLE) として確定し、サイクルを「タイムアウト
         終了」(TIMEOUT) に遷移させる。"
    14.5 "WHEN タイムアウト終了が発生, THE Cycle_Manager SHALL StepFunctions
         実行を停止し、運用担当者へ通知する。"

------------------------------------------------------------------------
本ファイルは ``shared/cycle/finalize.py::apply_timeout`` (Phase 6.6
実装済) の挙動を Hypothesis により網羅的に検証する。

真の仕様 ("A 採用" - 13.15 / 13.16 と同じ方針)
------------------------------------------------------------------------
既存実装が定義する書換セマンティクスを真の仕様とする::

    REWRITE_FROM = {PENDING, OTHER}
    REWRITE_TO   = "UNREACHABLE"

    apply_timeout(rows)
        == [(r["employeeId"], "UNREACHABLE")
            for r in rows if r.get("voiceStatus") in REWRITE_FROM]

すなわち:

* 空リストは ``[]`` を返す.
* 入力中の PENDING / OTHER 行のみが対象、入力順序を保持して出力.
* 既に TERMINAL (SAFE / INJURED / UNAVAILABLE / UNREACHABLE) の行は
  出力に含まれない (Requirement 14.4 「未確定の対象者を」 — 確定済は不変).
* 新ステータスは常に ``"UNREACHABLE"`` の一意.

これは Requirement 14.4 の「未確定の対象者を UNREACHABLE として確定」を
そのまま実装した形であり、Phase 6.6 docstring の truth table と整合する.

既存 example-based テストとの分業
------------------------------------------------------------------------
本 PBT は **valid input 集合** に限定して挙動を網羅検証する.
``employeeId`` 欠落時の ``TypeError`` 経路や非 list / 非 dict 入力に
対する ``TypeError`` 経路は既存 ``backend/tests/shared/cycle/test_finalize.py``
の example test 5 件 (``test_apply_timeout_*``) でカバー済のため、
本ファイルでは生成しない (DRY 原則).

スコープ外 — handler 側 SFN StopExecution
------------------------------------------------------------------------
tasks.md 13.17 Done When には「Cycle TIMEOUT」「StopExecution 呼出」
が記載されているが、これらは ``backend/lambdas/cycle_finalizer/handler.py``
の責務であり、純粋関数 ``apply_timeout`` には I/O が一切無い (Phase 6.6
モジュール docstring 参照). 本 PBT は純粋関数 ``apply_timeout`` の挙動
網羅に集中し、SFN クライアントモック / Cycle ステータス遷移 / SNS 通知
の検証は handler 側既存テスト ``backend/tests/lambdas/cycle_finalizer/``
が担う (タスク責務の分離).

文言ズレに関する脚注
------------------------------------------------------------------------
tasks.md 13.17 の Done When「PENDING を UNREACHABLE に更新」は厳密に
書けば「PENDING および OTHER を UNREACHABLE に更新」(実装は OTHER も
flip 対象 — Phase 6.6 ``_TIMEOUT_REWRITE_VOICE_STATUSES`` 参照).
ユーザー確定方針として:

* **実装を正とする (A 採用)**
* **tasks.md / design.md の文言修正は別タスクで起票**

本 PBT は実装側の仕様 ({PENDING, OTHER} → UNREACHABLE) に従う.
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.cycle.finalize import apply_timeout

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
# the test acts as an independent oracle. If finalize.py's REWRITE_FROM
# set silently drifts, this set diverges and the equivalence property
# (P5) pins the regression.
# ---------------------------------------------------------------------------

#: voiceStatus values that the 60-min timeout path forcibly relabels.
REWRITE_FROM_VOICE_STATUSES: frozenset[str] = frozenset({"PENDING", "OTHER"})

#: The single target voiceStatus after timeout flip.
REWRITE_TO_VOICE_STATUS: str = "UNREACHABLE"

#: voiceStatus values that are already terminal — must be left unchanged
#: (filtered out of the apply_timeout output).
TERMINAL_VOICE_STATUSES: frozenset[str] = frozenset(
    {"SAFE", "INJURED", "UNAVAILABLE", "UNREACHABLE"}
)

# ---------------------------------------------------------------------------
# Hypothesis strategies.
# ---------------------------------------------------------------------------

#: employeeId domain — printable ASCII, non-empty (apply_timeout requires
#: a non-empty str for any row in REWRITE_FROM_VOICE_STATUSES).
_employee_id: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=8,
)

# A "rewrite-target" row: voiceStatus ∈ {PENDING, OTHER}, employeeId
# always present and non-empty (so TypeError branch is not exercised
# in PBT — see "scope" note in module docstring).
_rewrite_target_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, status: {"employeeId": eid, "voiceStatus": status},
    eid=_employee_id,
    status=st.sampled_from(sorted(REWRITE_FROM_VOICE_STATUSES)),
)

# A "terminal" row: voiceStatus ∈ TERMINAL. Per the implementation,
# already-terminal rows are filtered before any employeeId validation,
# so employeeId may be omitted — but we keep one to look like real
# ResponseTable data.
_terminal_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, status: {"employeeId": eid, "voiceStatus": status},
    eid=_employee_id,
    status=st.sampled_from(sorted(TERMINAL_VOICE_STATUSES)),
)

# Any row — mixed: rewrite-target or terminal.
_any_row: st.SearchStrategy[dict[str, Any]] = st.one_of(
    _rewrite_target_row, _terminal_row
)

# Real-world ResponseTable rows share a primary key ``(cycleId, employeeId)``
# so within a single Cycle, ``employeeId`` is unique. The generators above
# do not enforce this, which lets Hypothesis emit lists like
# ``[{"employeeId": "x", "voiceStatus": "OTHER"},
#    {"employeeId": "x", "voiceStatus": "INJURED"}]`` — physically
# impossible at the table layer. Strategy ``_unique_rows_list`` constrains
# every generated list to production semantics (unique employeeId) using
# Hypothesis' ``unique_by``.
_unique_rows_list: st.SearchStrategy[list[dict[str, Any]]] = st.lists(
    _any_row,
    min_size=0,
    max_size=20,
    unique_by=lambda row: row["employeeId"],
)

# ---------------------------------------------------------------------------
# Shared @example pins — used by every test for cheap boundary coverage.
# ---------------------------------------------------------------------------

_PIN_EMPTY: list[dict[str, Any]] = []
_PIN_SINGLE_PENDING: list[dict[str, Any]] = [
    {"employeeId": "e1", "voiceStatus": "PENDING"}
]
_PIN_SINGLE_OTHER: list[dict[str, Any]] = [
    {"employeeId": "e1", "voiceStatus": "OTHER"}
]
_PIN_SINGLE_SAFE: list[dict[str, Any]] = [
    {"employeeId": "e1", "voiceStatus": "SAFE"}
]
_PIN_SINGLE_INJURED: list[dict[str, Any]] = [
    {"employeeId": "e1", "voiceStatus": "INJURED"}
]
_PIN_SINGLE_UNAVAILABLE: list[dict[str, Any]] = [
    {"employeeId": "e1", "voiceStatus": "UNAVAILABLE"}
]
_PIN_SINGLE_UNREACHABLE: list[dict[str, Any]] = [
    {"employeeId": "e1", "voiceStatus": "UNREACHABLE"}
]
_PIN_MIXED: list[dict[str, Any]] = [
    {"employeeId": "e1", "voiceStatus": "PENDING"},
    {"employeeId": "e2", "voiceStatus": "SAFE"},
    {"employeeId": "e3", "voiceStatus": "OTHER"},
    {"employeeId": "e4", "voiceStatus": "UNREACHABLE"},
]


# ---------------------------------------------------------------------------
# Helper: oracle expression of the contract (independent re-derivation).
# ---------------------------------------------------------------------------


def _oracle(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Independent re-derivation of the apply_timeout contract.

    Used by P5 as a property-level equivalence check against the
    implementation. Written in the most literal form possible — a
    list comprehension over the spec's predicate.

    Pre-condition: every row whose ``voiceStatus`` is in REWRITE_FROM
    has a non-empty ``str`` ``employeeId`` (guaranteed by the
    Hypothesis generators in this file).
    """
    return [
        (row["employeeId"], REWRITE_TO_VOICE_STATUS)
        for row in rows
        if row.get("voiceStatus") in REWRITE_FROM_VOICE_STATUSES
    ]


# ===========================================================================
# P1: 確定済 (TERMINAL) voiceStatus 行は出力に含まれない (不変性)
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PIN_EMPTY)
@example(rows=_PIN_SINGLE_SAFE)
@example(rows=_PIN_SINGLE_INJURED)
@example(rows=_PIN_SINGLE_UNAVAILABLE)
@example(rows=_PIN_SINGLE_UNREACHABLE)
@example(rows=_PIN_MIXED)
@given(rows=_unique_rows_list)
def test_property17_p1_terminal_rows_excluded_from_output(
    rows: list[dict[str, Any]],
) -> None:
    """P1: ∀ r ∈ rows : r.voiceStatus ∈ TERMINAL ⇒ r.employeeId ∉ output[*].0.

    確定済 (SAFE / INJURED / UNAVAILABLE / UNREACHABLE) の行は
    タイムアウト処理の出力に含まれない (上書き対象外).

    Validates: Requirements 14.4 (未確定の対象者を — つまり確定済は不変)
    """
    result = apply_timeout(rows)
    output_employee_ids = {eid for eid, _ in result}
    for row in rows:
        if row.get("voiceStatus") in TERMINAL_VOICE_STATUSES:
            employee_id = row["employeeId"]
            assert employee_id not in output_employee_ids, (
                f"terminal row leaked into output: employeeId={employee_id!r}, "
                f"voiceStatus={row['voiceStatus']!r}; "
                f"rows={rows!r}; result={result!r}"
            )


# ===========================================================================
# P2: PENDING / OTHER 行は出力に (employeeId, "UNREACHABLE") で含まれる
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PIN_EMPTY)
@example(rows=_PIN_SINGLE_PENDING)
@example(rows=_PIN_SINGLE_OTHER)
@example(rows=_PIN_MIXED)
@given(rows=_unique_rows_list)
def test_property17_p2_rewrite_target_rows_included_in_output(
    rows: list[dict[str, Any]],
) -> None:
    """P2: ∀ r ∈ rows : r.voiceStatus ∈ {PENDING, OTHER}
              ⇒ (r.employeeId, "UNREACHABLE") ∈ output.

    PENDING / OTHER の行は必ず出力に ``UNREACHABLE`` への変換タプルとして
    含まれる.

    Validates: Requirements 14.4 (未確定の対象者を UNREACHABLE として確定)
    """
    result = apply_timeout(rows)
    for row in rows:
        if row.get("voiceStatus") in REWRITE_FROM_VOICE_STATUSES:
            expected_tuple = (row["employeeId"], REWRITE_TO_VOICE_STATUS)
            assert expected_tuple in result, (
                f"rewrite-target row missing from output: "
                f"expected={expected_tuple!r}; "
                f"rows={rows!r}; result={result!r}"
            )


# ===========================================================================
# P3: 全出力タプルの 2 要素目は常に "UNREACHABLE" (new status の唯一性)
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PIN_EMPTY)
@example(rows=_PIN_SINGLE_PENDING)
@example(rows=_PIN_SINGLE_OTHER)
@example(rows=_PIN_MIXED)
@given(rows=_unique_rows_list)
def test_property17_p3_all_output_new_status_is_unreachable(
    rows: list[dict[str, Any]],
) -> None:
    """P3: ∀ (eid, new) ∈ output : new == "UNREACHABLE".

    タイムアウト処理は ``UNREACHABLE`` 以外への遷移を発行しない
    (60 分タイマー経路の単一遷移先).

    Validates: Requirements 14.4 (未確定 → UNREACHABLE の単一遷移)
    """
    result = apply_timeout(rows)
    for eid, new_status in result:
        assert new_status == REWRITE_TO_VOICE_STATUS, (
            f"unexpected new status: got {new_status!r}, "
            f"expected {REWRITE_TO_VOICE_STATUS!r}; "
            f"employeeId={eid!r}; rows={rows!r}; result={result!r}"
        )


# ===========================================================================
# P4: 出力の順序は入力中の PENDING/OTHER 行の順序を保持
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PIN_EMPTY)
@example(rows=_PIN_SINGLE_PENDING)
@example(
    rows=[
        {"employeeId": "z", "voiceStatus": "PENDING"},
        {"employeeId": "a", "voiceStatus": "PENDING"},
        {"employeeId": "m", "voiceStatus": "OTHER"},
    ]
)
@example(rows=_PIN_MIXED)
@given(rows=_unique_rows_list)
def test_property17_p4_output_preserves_input_order(
    rows: list[dict[str, Any]],
) -> None:
    """P4: 出力 employeeId 列は入力中の PENDING/OTHER 行の出現順を保持.

    DynamoDB UpdateItem の冪等性とは独立に、ハンドラ側ログの可読性
    (入力順 = 出力順) を担保. 既存 example test
    ``test_apply_timeout_preserves_input_order`` を任意入力に拡張.

    Validates: Requirements 14.4 (順序保持はハンドラ運用上の不変条件)
    """
    result = apply_timeout(rows)
    expected_order = [
        row["employeeId"]
        for row in rows
        if row.get("voiceStatus") in REWRITE_FROM_VOICE_STATUSES
    ]
    actual_order = [eid for eid, _ in result]
    assert actual_order == expected_order, (
        f"output order drift: expected={expected_order!r}, "
        f"actual={actual_order!r}; rows={rows!r}; result={result!r}"
    )


# ===========================================================================
# P5 equivalence - 対称性推論 (第17原則): implementation <=> oracle
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PIN_EMPTY)
@example(rows=_PIN_SINGLE_PENDING)
@example(rows=_PIN_SINGLE_OTHER)
@example(rows=_PIN_SINGLE_SAFE)
@example(rows=_PIN_SINGLE_INJURED)
@example(rows=_PIN_SINGLE_UNAVAILABLE)
@example(rows=_PIN_SINGLE_UNREACHABLE)
@example(rows=_PIN_MIXED)
@given(rows=_unique_rows_list)
def test_property17_p5_equivalent_to_independent_oracle(
    rows: list[dict[str, Any]],
) -> None:
    """P5: apply_timeout(R)
            == [(r["employeeId"], "UNREACHABLE")
                for r in R if r.get("voiceStatus") ∈ {PENDING, OTHER}].

    実装と独立 oracle の双方向同値性を担保 (第17原則: 対称性推論).
    P1 / P2 / P3 / P4 を組み合わせれば導出可能だが、明示的に検証する
    ことで P1〜P4 の one_of(...) で網羅しなかった残差クラス
    (例: 将来追加される第3の rewrite-target 値) のドリフトを catch.

    Validates: Requirements 14.4 (タイムアウト変換の必要十分条件)
    """
    actual = apply_timeout(rows)
    expected = _oracle(rows)
    assert actual == expected, (
        f"contract drift: oracle={expected!r} impl={actual!r} "
        f"rows={rows!r}"
    )


# ===========================================================================
# P6: 出力の長さ ≤ 入力の長さ (境界)
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PIN_EMPTY)
@example(rows=_PIN_SINGLE_PENDING)
@example(rows=_PIN_SINGLE_SAFE)
@example(rows=_PIN_MIXED)
@given(rows=_unique_rows_list)
def test_property17_p6_output_length_bounded_by_input(
    rows: list[dict[str, Any]],
) -> None:
    """P6: 0 <= len(output) <= len(input).

    タイムアウト処理は入力にない行を捏造しない (TERMINAL フィルタによる
    縮小のみ).

    Validates: Requirements 14.4 (集合サイズの境界)
    """
    result = apply_timeout(rows)
    assert 0 <= len(result) <= len(rows), (
        f"output length out of bound: got len={len(result)}, "
        f"input len={len(rows)}; rows={rows!r}; result={result!r}"
    )


# ===========================================================================
# P7: 純粋性 - 同一入力で複数回呼出して同じ結果 (副作用なし)
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PIN_EMPTY)
@example(rows=_PIN_SINGLE_PENDING)
@example(rows=_PIN_MIXED)
@given(rows=_unique_rows_list)
def test_property17_p7_function_is_pure(
    rows: list[dict[str, Any]],
) -> None:
    """P7: apply_timeout(R) == apply_timeout(R) (idempotent reference).

    入力の同値性が保たれる限り、出力もリスト同値. 内部状態に依存せず、
    入力 ``rows`` を破壊的に変更しないことも併せて確認.

    Validates: Requirements 14.4 (DynamoDB UpdateItem 失敗時のリプレイ
    安全性 — 再呼出しても同じ更新計画が生成される)
    """
    snapshot_before = [dict(row) for row in rows]
    first = apply_timeout(rows)
    second = apply_timeout(rows)
    assert first == second, (
        f"non-deterministic output: first={first!r}, second={second!r}; "
        f"rows={rows!r}"
    )
    assert rows == snapshot_before, (
        f"input mutated by call: before={snapshot_before!r}, "
        f"after={rows!r}"
    )
