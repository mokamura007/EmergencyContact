"""Property 13 - 再発信間隔保証 PBT (Phase 13.13).

Validates: Requirements 9.2
    9.2 "WHEN 再発信を計画する, THE Cycle_Manager SHALL prev_end_at から
         retryIntervalMinutes 分後を次回開始時刻 (next_dispatch_at) として
         算出する。"

------------------------------------------------------------------------
本ファイルは ``shared/retry/evaluator.py::compute_next_dispatch_at``
(Phase 6.5 実装済) の挙動を Hypothesis により網羅的に検証する。

真の仕様 ("A 採用" - 13.12 / 13.14 / 13.15 / 13.16 / 13.17 と同じ方針)
------------------------------------------------------------------------
既存実装が定義する契約を真の仕様とする::

    compute_next_dispatch_at(prev_end_at_iso, interval_minutes) -> str
        prev_end_at_iso  : ISO 8601 timestamp (Z または +HH:MM suffix 必須)
        interval_minutes : [1, 60] の int (Requirements 4.7 / 9.4)

        next_dispatch_at_iso = format_iso_utc_z(
            parse_iso_utc(prev_end_at_iso) + timedelta(minutes=interval_minutes)
        )

すなわち:

* 加算は **完全に決定論的**: 結果 = prev + interval * 60 秒. 任意の prev /
  interval で **等号** が成立する (tasks.md の ">=" よりも強い不変条件).
* 出力は常に **UTC ``Z`` suffix** に正規化される (入力が ``+09:00`` でも
  まず UTC へ変換してから interval を加える).
* ``interval_minutes`` が ``[1, 60]`` 外なら ``ValueError``.
* ``prev_end_at_iso`` が malformed / naive なら ``ValueError``.

これは Phase 6.5 evaluator.py のロジックと完全に一致する.

既存 example-based テストとの分業
------------------------------------------------------------------------
本 PBT は **valid input 集合** に限定して挙動を網羅検証する.
以下の負経路は既存 ``backend/tests/shared/retry/test_evaluator.py`` の
example test がカバー済のため、本ファイルでは生成しない (DRY 原則):

* ``interval_minutes == 0`` / ``61`` 等の範囲外  → ValueError
* ``interval_minutes`` が int 以外 / bool        → ValueError
* ``prev_end_at_iso`` が ``"not-a-date"`` 等        → ValueError
* ``prev_end_at_iso`` が naive (offset なし)        → ValueError
* ``Z`` / ``+00:00`` / ``+09:00`` の各 suffix 個別検証

Done When (tasks.md) と本 PBT の不変条件の関係
------------------------------------------------------------------------
tasks.md は::

    "任意の prev / interval で次回開始時刻が prev + interval*60 以上"

と "以上" (``>=``) を要求するが、実装は等号で算出するため、本 PBT は
P1 で **等号** を主張する (これは "以上" を含意する強条件). 仕様文の
"以上" は SFN の Wait 状態でクロックドリフトや実行遅延を含めた上での
緩い境界条件であり、純粋関数 compute_next_dispatch_at 単体は時計を
持たないため等号で完結する. この強化は意図的.

副次発見
------------------------------------------------------------------------
* なし (実装は Requirements 9.2 / 9.4 と整合).
* 万一 sub-second 精度 (``.ffffff``) を含む prev が来た場合も
  ``timedelta(minutes=N)`` が秒単位の演算であるため精度欠落なく保持され、
  P1 等号が成立する (P5 oracle で対称性確認).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.retry.evaluator import compute_next_dispatch_at

# ---------------------------------------------------------------------------
# Hypothesis settings — match Phase 13.x convention (>= 200 examples).
# ---------------------------------------------------------------------------

PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

# ---------------------------------------------------------------------------
# Reference constants — kept locally (not imported from evaluator) so the
# test acts as an independent oracle. If evaluator.py's bounds silently
# drift, this constant diverges and P4 equivalence pins the regression.
# ---------------------------------------------------------------------------

#: ``interval_minutes`` の許容範囲 (Requirements 4.7 / 9.4).
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 60

#: 出力フォーマットの shape: ``YYYY-MM-DDTHH:MM:SS(.ffffff)?Z``.
#: 月 / 日 / 時 / 分 / 秒は 2 桁固定 (datetime.isoformat の出力仕様).
_ISO_Z_PATTERN: re.Pattern[str] = re.compile(
    r"^-?\d{4,}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)

# ---------------------------------------------------------------------------
# Hypothesis strategies.
# ---------------------------------------------------------------------------

#: 任意の UTC aware datetime. min/max は datetime.fromisoformat が扱える
#: 範囲 (Python の datetime.MINYEAR=1, MAXYEAR=9999) のうち、interval を
#: 加えても overflow しない安全帯に絞る.
_prev_dt_utc: st.SearchStrategy[datetime] = st.datetimes(
    min_value=datetime(1, 1, 1, 0, 0, 0),
    max_value=datetime(9999, 12, 31, 22, 59, 59),
    timezones=st.just(UTC),
)

#: 任意 fixed-offset の tzinfo (UTC を含む -12h..+14h の範囲).
#: ``st.timezones()`` は IANA tz データ (Windows では別途 tzdata パッケージ要)
#: に依存するため、代わりに ``datetime.timezone(timedelta(...))`` ベースの
#: fixed-offset で生成する. Requirements 9.2 の検証範囲としては fixed offset
#: で十分 (DST 遷移は compute_next_dispatch_at の関心外).
_fixed_offset_tzinfo: st.SearchStrategy[timezone] = st.builds(
    timezone,
    st.timedeltas(
        min_value=timedelta(hours=-12),
        max_value=timedelta(hours=14),
    ),
)

#: 任意の non-UTC aware datetime (offset ±HH:MM). 出力が常に Z に正規化
#: されることを担保するため、UTC 以外の offset も生成する.
_prev_dt_any_tz: st.SearchStrategy[datetime] = st.datetimes(
    min_value=datetime(2, 1, 1, 0, 0, 0),  # 1 年目は offset 適用で underflow
    max_value=datetime(9998, 12, 31, 22, 59, 59),
    timezones=_fixed_offset_tzinfo,
)

#: ``[1, 60]`` の int.
_interval_minutes: st.SearchStrategy[int] = st.integers(
    min_value=MIN_INTERVAL_MINUTES, max_value=MAX_INTERVAL_MINUTES
)


def _format_iso_z(dt: datetime) -> str:
    """Render a UTC aware ``datetime`` as ``...Z``.

    Mirrors evaluator._format_iso_utc_z but is duplicated locally so the
    oracle is independent (no shared helper via private import).
    """
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso_z(value: str) -> datetime:
    """Parse a ``...Z`` ISO 8601 string back to a UTC aware ``datetime``.

    Mirrors evaluator._parse_iso_utc but limited to the well-formed
    output shape of compute_next_dispatch_at.
    """
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized).astimezone(UTC)


# ---------------------------------------------------------------------------
# Helper: oracle expression of the contract.
# ---------------------------------------------------------------------------


def _oracle(prev_end_at_iso: str, interval_minutes: int) -> str:
    """Independent re-derivation of the compute_next_dispatch_at contract.

    Used by P4 as a property-level equivalence check against the
    implementation. Intentionally written in the most literal form
    possible — parse input, add timedelta, render UTC Z.
    """
    normalized = (
        prev_end_at_iso[:-1] + "+00:00"
        if prev_end_at_iso.endswith("Z")
        else prev_end_at_iso
    )
    parsed = datetime.fromisoformat(normalized).astimezone(UTC)
    next_dt = parsed + timedelta(minutes=interval_minutes)
    return _format_iso_z(next_dt)


# ===========================================================================
# P1 — Done When の核: result == prev + interval * 60 秒 (等号)
# ===========================================================================


@PBT_SETTINGS
@example(
    prev_dt=datetime(2026, 6, 26, 10, 0, 0, tzinfo=UTC),
    interval_minutes=5,
)
@example(
    prev_dt=datetime(2026, 6, 26, 10, 0, 0, tzinfo=UTC),
    interval_minutes=MIN_INTERVAL_MINUTES,
)
@example(
    prev_dt=datetime(2026, 6, 26, 10, 0, 0, tzinfo=UTC),
    interval_minutes=MAX_INTERVAL_MINUTES,
)
@example(
    # Epoch 起点境界.
    prev_dt=datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC),
    interval_minutes=1,
)
@example(
    # 閏年 2/29 → 3/1 の日跨ぎ境界.
    prev_dt=datetime(2024, 2, 29, 23, 59, 0, tzinfo=UTC),
    interval_minutes=1,
)
@example(
    # サブ秒精度を含むケース (timedelta の minutes 加算で精度保持).
    prev_dt=datetime(2026, 6, 26, 10, 0, 0, 123456, tzinfo=UTC),
    interval_minutes=7,
)
@given(prev_dt=_prev_dt_utc, interval_minutes=_interval_minutes)
def test_property13_next_equals_prev_plus_interval_seconds(
    prev_dt: datetime,
    interval_minutes: int,
) -> None:
    """parse(result) - parse(prev) == interval_minutes * 60 秒 (等号).

    tasks.md の Done When は "prev + interval*60 以上" (``>=``) だが
    実装は等号で算出するため、本 PBT は等号を主張する.
    ``>=`` を強化した等価条件であり、Done When を満たすうえで
    必要十分.

    Validates: Requirements 9.2 (次回開始時刻 = prev + interval分)
    """
    prev_iso = _format_iso_z(prev_dt)
    result_iso = compute_next_dispatch_at(prev_iso, interval_minutes)
    result_dt = _parse_iso_z(result_iso)
    delta_seconds = (result_dt - prev_dt).total_seconds()
    expected_seconds = float(interval_minutes * 60)
    assert delta_seconds == expected_seconds, (
        f"next - prev must equal interval * 60 seconds; "
        f"prev={prev_iso!r}, interval_minutes={interval_minutes}, "
        f"result={result_iso!r}, "
        f"delta_seconds={delta_seconds}, expected={expected_seconds}"
    )
    # Done When (>=) も同時に成立することを明示.
    assert delta_seconds >= expected_seconds, (
        f"Done When violation (>=): delta_seconds={delta_seconds} "
        f"< expected={expected_seconds}"
    )


# ===========================================================================
# P2 — 戻り値は常に Z suffix の ISO 8601 形式
# ===========================================================================


@PBT_SETTINGS
@example(
    prev_dt=datetime(2026, 6, 26, 10, 0, 0, tzinfo=UTC),
    interval_minutes=5,
)
@example(
    prev_dt=datetime(
        2026, 6, 26, 10, 0, 0, tzinfo=timezone(timedelta(hours=9))
    ),
    interval_minutes=5,
)
@example(
    prev_dt=datetime(
        2026, 6, 26, 10, 0, 0, tzinfo=timezone(timedelta(hours=-5))
    ),
    interval_minutes=60,
)
@given(prev_dt=_prev_dt_any_tz, interval_minutes=_interval_minutes)
def test_property13_result_is_iso8601_z_suffix(
    prev_dt: datetime,
    interval_minutes: int,
) -> None:
    """戻り値は ``YYYY-MM-DDTHH:MM:SS(.ffffff)?Z`` にマッチ.

    入力 offset が ``+09:00`` や ``-05:00`` であっても、出力は UTC ``Z``
    に正規化される (evaluator._format_iso_utc_z の責務).

    Validates: Requirements 9.2 (時刻表現の形式整合)
    """
    prev_iso = prev_dt.isoformat()
    result_iso = compute_next_dispatch_at(prev_iso, interval_minutes)
    assert _ISO_Z_PATTERN.match(result_iso), (
        f"result must match ISO 8601 Z-suffix shape; "
        f"prev={prev_iso!r}, interval_minutes={interval_minutes}, "
        f"result={result_iso!r}"
    )
    # Z suffix 正規化の対称性: 末尾は必ず Z で +00:00 ではない.
    assert result_iso.endswith("Z"), (
        f"result must end with 'Z'; got {result_iso!r}"
    )
    assert "+00:00" not in result_iso, (
        f"result must be Z-normalized, not +00:00 form; got {result_iso!r}"
    )


# ===========================================================================
# P3 — 純粋性: 同一入力に対し複数回呼び出しても同一結果
# ===========================================================================


@PBT_SETTINGS
@example(
    prev_dt=datetime(2026, 6, 26, 10, 0, 0, tzinfo=UTC),
    interval_minutes=5,
)
@example(
    prev_dt=datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC),
    interval_minutes=MAX_INTERVAL_MINUTES,
)
@given(prev_dt=_prev_dt_utc, interval_minutes=_interval_minutes)
def test_property13_is_pure_idempotent_call(
    prev_dt: datetime,
    interval_minutes: int,
) -> None:
    """同じ入力で複数回呼び出した戻り値は完全に一致する (純粋関数).

    evaluator.py docstring が "Pure function. (no clocks, no I/O,
    no module-level state)" を宣言するための対称性検証.

    Validates: Requirements 9.2 (関数契約の決定論性)
    """
    prev_iso = _format_iso_z(prev_dt)
    r1 = compute_next_dispatch_at(prev_iso, interval_minutes)
    r2 = compute_next_dispatch_at(prev_iso, interval_minutes)
    r3 = compute_next_dispatch_at(prev_iso, interval_minutes)
    assert r1 == r2 == r3, (
        f"compute_next_dispatch_at must be pure; "
        f"prev={prev_iso!r}, interval_minutes={interval_minutes}, "
        f"got r1={r1!r}, r2={r2!r}, r3={r3!r}"
    )


# ===========================================================================
# P4 equivalence — 対称性推論 (第17原則): implementation <=> oracle
# ===========================================================================


@PBT_SETTINGS
@example(
    prev_dt=datetime(2026, 6, 26, 10, 0, 0, tzinfo=UTC),
    interval_minutes=5,
)
@example(
    prev_dt=datetime(
        2026, 6, 26, 10, 0, 0, tzinfo=timezone(timedelta(hours=9))
    ),
    interval_minutes=5,
)
@example(
    prev_dt=datetime(2024, 2, 29, 23, 59, 0, tzinfo=UTC),
    interval_minutes=1,
)
@example(
    prev_dt=datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC),
    interval_minutes=MAX_INTERVAL_MINUTES,
)
@example(
    prev_dt=datetime(9999, 12, 31, 22, 0, 0, tzinfo=UTC),
    interval_minutes=MAX_INTERVAL_MINUTES,
)
@given(prev_dt=_prev_dt_any_tz, interval_minutes=_interval_minutes)
def test_property13_equivalent_to_oracle(
    prev_dt: datetime,
    interval_minutes: int,
) -> None:
    """compute_next_dispatch_at(...) == _oracle(...) — 双方向不変条件.

    P1〜P3 を合わせると暗黙的にこの等価性が導かれるが、明示的に
    encode しておくことで:

    * 任意 timezone offset (Z / +HH:MM / -HH:MM / sub-second) における
      UTC 正規化ロジックの回帰検出
    * 加算順序 (parse → add → format) の入れ替えなどによる差異の検出
    * 対称性 (第17原則): 「impl が X なら oracle も X」だけでなく
      「oracle が X なら impl も X」も成り立つ

    を担保する.

    Validates: Requirements 9.2 (再発信間隔保証の必要十分条件)
    """
    prev_iso = prev_dt.isoformat()
    actual = compute_next_dispatch_at(prev_iso, interval_minutes)
    expected = _oracle(prev_iso, interval_minutes)
    assert actual == expected, (
        f"contract drift: oracle={expected!r} impl={actual!r} "
        f"prev={prev_iso!r}, interval_minutes={interval_minutes}"
    )
