"""Property 15 - 集計関数の整合性 PBT (Phase 13.15).

Validates: Requirements 11.2, 11.3
    11.2 "THE Status_Viewer SHALL 「対象者総数」「発信完了数(少なくとも 1
         回の発信試行が完了した対象者の人数)」「応答取得数(Voice_Status
         が SAFE / INJURED / UNAVAILABLE のいずれかに確定した人数)」「未到達
         数(最大発信回数に達しても有効な Voice_Status が得られなかった対象者
         の人数)」「ステータス別内訳(SAFE / INJURED / UNAVAILABLE / OTHER /
         UNREACHABLE の各人数)」を画面上に表示する。"
    11.3 "THE Status_Viewer SHALL 個別社員ごとに、最新 Voice_Status、発信回数、
         最終応答時刻……一覧表で表示する。" (本 PBT は集計側のみを対象とする)

------------------------------------------------------------------------
本ファイルは ``shared/cycle/finalize.py::compute_summary`` (Phase 6.6
実装済) の挙動を Hypothesis により網羅的に検証する。

真の仕様 ("A 採用" - 13.16 と同じ方針)
------------------------------------------------------------------------
既存実装が定義する集計セマンティクスを真の仕様とする::

    CONFIRMED  = {SAFE, INJURED, UNAVAILABLE}

    summary = compute_summary(rows)
        summary["targetTotal"] == len(rows)
        summary["dispatched"]  == #{r : r.get("callAttempts", 0) > 0}
        summary["responded"]   == #{r : r.get("voiceStatus") ∈ CONFIRMED}
        summary["unreachable"] == #{r : r.get("voiceStatus") == "UNREACHABLE"}
        summary["byStatus"]    == histogram, voiceStatus 欠損 → "PENDING"
        Σ summary["byStatus"].values() == len(rows)

具体例:

* 空リストは ``targetTotal=0, dispatched=0, responded=0, unreachable=0,
  byStatus={}`` を返す.
* ``voiceStatus`` キー欠損行は ``byStatus["PENDING"]`` に集計される.
* 未知の文字列 (例 ``"FOO"``) は ``byStatus["FOO"]`` に集計される -
  実装は未知ステータスを silently drop しない (Phase 6.6 docstring に
  「operators see the anomaly rather than have it silently dropped」と
  明記).

これは Requirement 11.2 で列挙されている 5 つの表示値 (targetTotal /
dispatched / responded / unreachable / byStatus の SAFE INJURED
UNAVAILABLE OTHER UNREACHABLE) を集計関数として実装した形であり,
Phase 6.6 docstring の schema と整合する.

文言ズレに関する脚注
------------------------------------------------------------------------
design.md Property 15 第6項は ``byStatus[s] ≥ 0 for all s ∈ {SAFE,
INJURED, UNAVAILABLE, OTHER, UNREACHABLE, PENDING}`` と書かれているが,
これは「6 種類のみ」と読める一方で実装は未知文字列もそのまま byStatus
に登録する仕様である. ユーザー確定方針として:

* **実装を正とする (A 採用)**
* **design.md の文言修正は別タスクで起票**

本 PBT は実装側の仕様 (未知文字列も byStatus にカウント) に従い,
``Σ byStatus.values() == targetTotal`` (P7) で総数整合性を担保する.
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.cycle.finalize import compute_summary

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
# the test acts as an independent oracle. If finalize.py's CONFIRMED set
# silently drifts, this set diverges and the equivalence properties pin
# the regression.
# ---------------------------------------------------------------------------

#: voiceStatus values that count as "応答取得" per Requirement 11.2.
CONFIRMED_VOICE_STATUSES: frozenset[str] = frozenset(
    {"SAFE", "INJURED", "UNAVAILABLE"}
)

#: All documented voiceStatus values in the project vocabulary
#: (Requirement 11.2 列挙の 5 種 + PENDING).
KNOWN_VOICE_STATUSES: frozenset[str] = frozenset(
    {"SAFE", "INJURED", "UNAVAILABLE", "UNREACHABLE", "OTHER", "PENDING"}
)

# ---------------------------------------------------------------------------
# Hypothesis strategies.
# ---------------------------------------------------------------------------

_employee_id: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=8,
)

# callAttempts: bounded non-negative int. We intentionally avoid bool
# (which would trip the TypeError branch of compute_summary) — those
# negative paths are covered by the existing example-based test suite.
_call_attempts: st.SearchStrategy[int] = st.integers(min_value=0, max_value=10)

# Row variants — all four classes the implementation handles:
#   (a) Known status string                          -> normal histogram
#   (b) Missing voiceStatus key                      -> counted as PENDING
#   (c) voiceStatus is None                          -> counted under None key
#   (d) Unrecognised arbitrary string (e.g. "FOO")   -> counted under that key
_known_status_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, status, attempts: {
        "employeeId": eid,
        "voiceStatus": status,
        "callAttempts": attempts,
    },
    eid=_employee_id,
    status=st.sampled_from(sorted(KNOWN_VOICE_STATUSES)),
    attempts=_call_attempts,
)

_missing_status_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, attempts: {"employeeId": eid, "callAttempts": attempts},
    eid=_employee_id,
    attempts=_call_attempts,
)

_none_status_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, attempts: {
        "employeeId": eid,
        "voiceStatus": None,
        "callAttempts": attempts,
    },
    eid=_employee_id,
    attempts=_call_attempts,
)

_unknown_status_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, status, attempts: {
        "employeeId": eid,
        "voiceStatus": status,
        "callAttempts": attempts,
    },
    eid=_employee_id,
    status=st.text(min_size=0, max_size=10).filter(
        lambda s: s not in KNOWN_VOICE_STATUSES
    ),
    attempts=_call_attempts,
)

# Row whose callAttempts is omitted entirely — implementation reads it
# as 0 via .get("callAttempts", 0). Pinned via @example below to make
# the "missing key counts as 0 dispatched" contract explicit.
_missing_attempts_row: st.SearchStrategy[dict[str, Any]] = st.builds(
    lambda eid, status: {"employeeId": eid, "voiceStatus": status},
    eid=_employee_id,
    status=st.sampled_from(sorted(KNOWN_VOICE_STATUSES)),
)

_any_row: st.SearchStrategy[dict[str, Any]] = st.one_of(
    _known_status_row,
    _missing_status_row,
    _none_status_row,
    _unknown_status_row,
    _missing_attempts_row,
)

# ---------------------------------------------------------------------------
# Shared @example pins — used by every test for cheap boundary coverage.
# Declared as a list of (rows,) tuples so each test can spread them via
# nested @example decorators consistently.
# ---------------------------------------------------------------------------

_PINNED_EXAMPLES: list[list[dict[str, Any]]] = [
    # P-1: empty set -> all zeros
    [],
    # P-2: single SAFE, dispatched
    [{"employeeId": "e1", "voiceStatus": "SAFE", "callAttempts": 1}],
    # P-3: single UNREACHABLE, dispatched
    [{"employeeId": "e1", "voiceStatus": "UNREACHABLE", "callAttempts": 3}],
    # P-4: OTHER is neither responded nor unreachable
    [{"employeeId": "e1", "voiceStatus": "OTHER", "callAttempts": 1}],
    # P-5: PENDING + callAttempts=0 (untouched row)
    [{"employeeId": "e1", "voiceStatus": "PENDING", "callAttempts": 0}],
    # P-6: missing voiceStatus key -> PENDING bucket
    [{"employeeId": "e1", "callAttempts": 0}],
    # P-7: mixed 6-status histogram
    [
        {"employeeId": "e1", "voiceStatus": "SAFE", "callAttempts": 1},
        {"employeeId": "e2", "voiceStatus": "INJURED", "callAttempts": 2},
        {"employeeId": "e3", "voiceStatus": "UNAVAILABLE", "callAttempts": 1},
        {"employeeId": "e4", "voiceStatus": "UNREACHABLE", "callAttempts": 3},
        {"employeeId": "e5", "voiceStatus": "OTHER", "callAttempts": 1},
        {"employeeId": "e6", "voiceStatus": "PENDING", "callAttempts": 0},
    ],
]


# ===========================================================================
# P1: targetTotal == len(rows)  -- 恒等性
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PINNED_EXAMPLES[0])
@example(rows=_PINNED_EXAMPLES[1])
@example(rows=_PINNED_EXAMPLES[2])
@example(rows=_PINNED_EXAMPLES[3])
@example(rows=_PINNED_EXAMPLES[4])
@example(rows=_PINNED_EXAMPLES[5])
@example(rows=_PINNED_EXAMPLES[6])
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property15_p1_target_total_equals_input_length(
    rows: list[dict[str, Any]],
) -> None:
    """P1: summary.targetTotal == len(rows).

    Validates: Requirements 11.2 (対象者総数)
    """
    summary = compute_summary(rows)
    assert summary["targetTotal"] == len(rows), (
        f"targetTotal drift: got {summary['targetTotal']}, "
        f"expected {len(rows)}; rows={rows!r}"
    )


# ===========================================================================
# P2-P4: 0 <= dispatched / responded / unreachable <= targetTotal  -- 境界
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PINNED_EXAMPLES[0])
@example(rows=_PINNED_EXAMPLES[1])
@example(rows=_PINNED_EXAMPLES[2])
@example(rows=_PINNED_EXAMPLES[3])
@example(rows=_PINNED_EXAMPLES[4])
@example(rows=_PINNED_EXAMPLES[5])
@example(rows=_PINNED_EXAMPLES[6])
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property15_p2_p3_p4_bounded_counters(
    rows: list[dict[str, Any]],
) -> None:
    """P2/P3/P4: 0 <= dispatched, responded, unreachable <= targetTotal.

    どの個別カウンタも対象者総数を超えない / 負値にならない.

    Validates: Requirements 11.2 (発信完了数 / 応答取得数 / 未到達数)
    """
    summary = compute_summary(rows)
    target_total = summary["targetTotal"]
    for key in ("dispatched", "responded", "unreachable"):
        value = summary[key]
        assert 0 <= value <= target_total, (
            f"{key} out of bound: got {value}, target_total={target_total}; "
            f"rows={rows!r}"
        )


# ===========================================================================
# P5: responded == byStatus[SAFE] + byStatus[INJURED] + byStatus[UNAVAILABLE]
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PINNED_EXAMPLES[0])
@example(rows=_PINNED_EXAMPLES[1])  # 1 SAFE → responded=1
@example(rows=_PINNED_EXAMPLES[2])  # 1 UNREACHABLE → responded=0
@example(rows=_PINNED_EXAMPLES[3])  # 1 OTHER → responded=0
@example(rows=_PINNED_EXAMPLES[6])  # mixed: responded=3 (SAFE+INJURED+UNAVAILABLE)
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property15_p5_responded_matches_confirmed_histogram(
    rows: list[dict[str, Any]],
) -> None:
    """P5: responded == Σ byStatus[s] for s ∈ {SAFE, INJURED, UNAVAILABLE}.

    集計上の responded と histogram の確定状態合計が一致.

    Validates: Requirements 11.2 (応答取得数 ↔ ステータス別内訳の整合)
    """
    summary = compute_summary(rows)
    by_status = summary["byStatus"]
    confirmed_sum = sum(
        by_status.get(s, 0) for s in CONFIRMED_VOICE_STATUSES
    )
    assert summary["responded"] == confirmed_sum, (
        f"responded mismatch: got {summary['responded']}, "
        f"sum(byStatus[SAFE|INJURED|UNAVAILABLE])={confirmed_sum}; "
        f"byStatus={by_status!r}; rows={rows!r}"
    )


# ===========================================================================
# P6: unreachable == byStatus["UNREACHABLE"]
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PINNED_EXAMPLES[0])
@example(rows=_PINNED_EXAMPLES[2])  # 1 UNREACHABLE
@example(rows=_PINNED_EXAMPLES[6])  # mixed
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property15_p6_unreachable_matches_histogram(
    rows: list[dict[str, Any]],
) -> None:
    """P6: unreachable == byStatus.get("UNREACHABLE", 0).

    Validates: Requirements 11.2 (未到達数 ↔ ステータス別内訳の整合)
    """
    summary = compute_summary(rows)
    by_status_unreachable = summary["byStatus"].get("UNREACHABLE", 0)
    assert summary["unreachable"] == by_status_unreachable, (
        f"unreachable mismatch: got {summary['unreachable']}, "
        f"byStatus['UNREACHABLE']={by_status_unreachable}; "
        f"byStatus={summary['byStatus']!r}; rows={rows!r}"
    )


# ===========================================================================
# P7: Σ byStatus.values() == targetTotal  -- histogram 合計 = 総数
# ===========================================================================


@PBT_SETTINGS
@example(rows=_PINNED_EXAMPLES[0])
@example(rows=_PINNED_EXAMPLES[1])
@example(rows=_PINNED_EXAMPLES[4])  # PENDING explicit
@example(rows=_PINNED_EXAMPLES[5])  # missing voiceStatus → PENDING bucket
@example(rows=_PINNED_EXAMPLES[6])  # mixed
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property15_p7_histogram_sum_equals_target_total(
    rows: list[dict[str, Any]],
) -> None:
    """P7: Σ byStatus.values() == targetTotal.

    全行が histogram のどこかにちょうど 1 回ずつ計上されることを保証
    (drop も double-count もない).

    Validates: Requirements 11.2 (内訳合計と対象者総数の整合)
    """
    summary = compute_summary(rows)
    histogram_sum = sum(summary["byStatus"].values())
    assert histogram_sum == summary["targetTotal"], (
        f"histogram sum drift: Σ byStatus.values()={histogram_sum}, "
        f"targetTotal={summary['targetTotal']}; "
        f"byStatus={summary['byStatus']!r}; rows={rows!r}"
    )
    # 各 bin は非負 (第17原則: 逆方向の境界も確認)
    for status, count in summary["byStatus"].items():
        assert count >= 0, (
            f"byStatus[{status!r}]={count} negative; rows={rows!r}"
        )


# ===========================================================================
# P8: dispatched == #{r : r.get("callAttempts", 0) > 0}  -- 独立 oracle 等価
# ===========================================================================


def _dispatched_oracle(rows: list[dict[str, Any]]) -> int:
    """Independent re-derivation of the dispatched contract.

    Reads ``callAttempts`` with default 0 to mirror the implementation's
    ``.get("callAttempts", 0)`` semantics. Generators in this file never
    emit bool (which would hit the impl's TypeError branch), so the
    naive ``> 0`` check is sound here.
    """
    return sum(1 for r in rows if r.get("callAttempts", 0) > 0)


@PBT_SETTINGS
@example(rows=_PINNED_EXAMPLES[0])
@example(rows=_PINNED_EXAMPLES[1])  # callAttempts=1 → dispatched=1
@example(rows=_PINNED_EXAMPLES[4])  # callAttempts=0 → dispatched=0
@example(rows=_PINNED_EXAMPLES[5])  # missing callAttempts key → dispatched=0
@example(rows=_PINNED_EXAMPLES[6])  # mixed
@given(rows=st.lists(_any_row, min_size=0, max_size=20))
def test_property15_p8_dispatched_equivalent_to_oracle(
    rows: list[dict[str, Any]],
) -> None:
    """P8: dispatched == #{r : r.get("callAttempts", 0) > 0}.

    第17原則 (対称性推論): 独立 oracle と実装の双方向同値性を担保.

    Validates: Requirements 11.2 (発信完了数の定義との一致)
    """
    summary = compute_summary(rows)
    expected = _dispatched_oracle(rows)
    assert summary["dispatched"] == expected, (
        f"dispatched drift: impl={summary['dispatched']}, oracle={expected}; "
        f"rows={rows!r}"
    )
