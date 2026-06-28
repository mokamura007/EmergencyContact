"""Property 12 - 再発信判定関数 PBT (Phase 13.12).

Validates: Requirements 9.1, 9.3, 9.4, 9.5
    9.1 "WHEN Voice_Status が SAFE / INJURED / UNAVAILABLE のいずれかに確定,
         THE Cycle_Manager SHALL 当該社員への以降の発信を停止する。"
    9.3 "WHEN 録音が得られた / 録音されなかった / 録音が辞書一致しなかった
         (Voice_Status が PENDING または OTHER) ……再発信を計画する。"
    9.4 "WHEN 再発信を計画する, THE Cycle_Manager SHALL 累積発信回数 a が
         Retry_Count R 未満の間、retryIntervalMinutes 経過後に発信を再実行する。"
    9.5 "WHEN 累積発信回数が Retry_Count に達した, THE Cycle_Manager SHALL
         当該社員の Voice_Status を UNREACHABLE として確定する。"

------------------------------------------------------------------------
本ファイルは ``shared/retry/evaluator.py::should_retry`` (Phase 6.5
実装済) の挙動を Hypothesis により網羅的に検証する。

真の仕様 ("A 採用" - 13.15 / 13.16 / 13.17 と同じ方針)
------------------------------------------------------------------------
既存実装が定義する truth table を真の仕様とする::

    CONFIRMED  = {SAFE, INJURED, UNAVAILABLE}
    RETRYABLE  = {PENDING, OTHER}

    should_retry(voice_status, call_result_code, attempts, retry_count)
        voice_status ∈ CONFIRMED            ⇒ False  (Req 9.1)
        voice_status == "UNREACHABLE"       ⇒ False  (Req 9.5: 既終端)
        voice_status ∈ RETRYABLE ∧ a < R    ⇒ True   (Req 9.3 / 9.4)
        voice_status ∈ RETRYABLE ∧ a ≥ R    ⇒ False  (Req 9.4 / 9.5)

すなわち:

* CONFIRMED は call_result_code / attempts / retry_count に **無関係**.
* UNREACHABLE も同様に **無関係** (既に terminal 確定).
* RETRYABLE のみ累積発信回数の予算管理が支配する.
* ``call_result_code`` は受け取るが現行 Requirements 9.x では判定に
  寄与しない (evaluator.py docstring 明記). 将来拡張のための signature
  互換シグナルである.

これは Phase 6.5 evaluator.py の truth table と完全に一致する.

既存 example-based テストとの分業
------------------------------------------------------------------------
本 PBT は **valid input 集合** に限定して挙動を網羅検証する.
以下の負経路は既存 ``backend/tests/shared/retry/test_evaluator.py`` の
example test がカバー済のため、本ファイルでは生成しない (DRY 原則):

* ``voice_status`` が VALID_VOICE_STATUS_VALUES に含まれない → ValueError
* ``attempts`` / ``retry_count`` が int 以外 / bool / 負値 → ValueError
* 上記の文言検証 (match)

文言ズレに関する脚注
------------------------------------------------------------------------
design.md Property 12 の第二条文::

    vs ∈ {OTHER, PENDING} または cc ∈ {NO_ANSWER, BUSY, VOICEMAIL,
    ERROR, TRANSCRIBE_FAILED} のいずれかであり、かつ a < R ⇒ true

は call_result_code を判定に組み込む書き方だが, 既存実装は
call_result_code を判定に **使用しない**. これは Requirements 9.x 本文
にも cc を OR 条件で使う記述が存在せず, voiceStatus が既に答えを符号化
しているための仕様であり, design.md 側の表現が冗長になっている形.
ユーザー確定方針として:

* **実装を正とする (A 採用)**
* **design.md / tasks.md の文言修正は別タスクで起票**

本 PBT は実装側の仕様 (cc 非依存) に従い, P1〜P4 で全 cc 値について
結果が voiceStatus / attempts / retry_count のみで決定することを担保
する.
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.connect.call_result import VALID_CALL_RESULT_CODES
from shared.retry.evaluator import should_retry

# ---------------------------------------------------------------------------
# Hypothesis settings — match Phase 13.x convention (>= 200 examples).
# ---------------------------------------------------------------------------

PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

# ---------------------------------------------------------------------------
# Reference vocabulary — kept locally (not imported from evaluator) so the
# test acts as an independent oracle. If evaluator.py's CONFIRMED set
# silently drifts, this set diverges and the equivalence property (P5)
# pins the regression.
# ---------------------------------------------------------------------------

#: voiceStatus values that represent a confirmed answer (Req 9.1).
CONFIRMED_VOICE_STATUSES: frozenset[str] = frozenset(
    {"SAFE", "INJURED", "UNAVAILABLE"}
)

#: voiceStatus values that may still be redialed subject to the budget
#: (Req 9.3 / 9.4).
RETRYABLE_VOICE_STATUSES: frozenset[str] = frozenset({"PENDING", "OTHER"})

#: Terminal-but-not-confirmed: already finalized by an earlier branch.
TERMINAL_NON_CONFIRMED_VOICE_STATUS: str = "UNREACHABLE"

# ---------------------------------------------------------------------------
# Hypothesis strategies.
# ---------------------------------------------------------------------------

#: call_result_code domain — every documented Connect outcome plus None
#: (the SFN payload before any call has been placed). Since the
#: implementation ignores cc for the retry decision, this strategy
#: exists solely to vary the input axis and prove the irrelevance.
_call_result_code: st.SearchStrategy[str | None] = st.one_of(
    st.none(),
    st.sampled_from(sorted(VALID_CALL_RESULT_CODES)),
)

#: attempts / retry_count domain — non-negative bounded int, no bool.
#: bool is rejected at runtime (evaluator.py guards ``isinstance(x, bool)``)
#: and the negative path is covered by the existing example test
#: ``test_should_retry_boolean_attempts_raises_value_error``.
_attempts: st.SearchStrategy[int] = st.integers(min_value=0, max_value=20)
_retry_count: st.SearchStrategy[int] = st.integers(min_value=0, max_value=20)

#: voiceStatus strategies, partitioned by truth-table region.
_confirmed_status: st.SearchStrategy[str] = st.sampled_from(
    sorted(CONFIRMED_VOICE_STATUSES)
)
_retryable_status: st.SearchStrategy[str] = st.sampled_from(
    sorted(RETRYABLE_VOICE_STATUSES)
)


@st.composite
def _budget_remaining_pair(draw: st.DrawFn) -> tuple[int, int]:
    """Generate ``(attempts, retry_count)`` with ``attempts < retry_count``.

    Returns a tuple instead of two separate draws so the pair can be
    passed as a single keyword argument to ``@given`` / ``@example``
    (Hypothesis requires example kwargs to match given kwargs exactly).
    The constraint ``retry_count >= 1`` is necessary because
    ``attempts >= 0`` plus ``attempts < retry_count`` implies
    ``retry_count > 0``.
    """
    retry_count = draw(st.integers(min_value=1, max_value=20))
    attempts = draw(st.integers(min_value=0, max_value=retry_count - 1))
    return attempts, retry_count


@st.composite
def _budget_exhausted_pair(draw: st.DrawFn) -> tuple[int, int]:
    """Generate ``(attempts, retry_count)`` with ``attempts >= retry_count``.

    Covers the zero-budget boundary (``retry_count == 0``, ``attempts ==
    0``) — the case "one dispatch then finalize, no retries" — through
    the ``attempts == retry_count`` boundary and well beyond.
    """
    retry_count = draw(st.integers(min_value=0, max_value=20))
    attempts = draw(
        st.integers(min_value=retry_count, max_value=retry_count + 20)
    )
    return attempts, retry_count


# ---------------------------------------------------------------------------
# Helper: oracle expression of the contract.
# ---------------------------------------------------------------------------


def _oracle(
    voice_status: str,
    call_result_code: str | None,
    attempts: int,
    retry_count: int,
) -> bool:
    """Independent re-derivation of the should_retry contract.

    Used by P5 as a property-level equivalence check against the
    implementation. Intentionally written in the most literal form
    possible — branching on voiceStatus class first, then on the
    attempts<retry_count budget for the RETRYABLE class.

    ``call_result_code`` is named in the signature for symmetry with
    the implementation but is intentionally unused: the contract does
    not depend on it (see module docstring "文言ズレに関する脚注").
    """
    # call_result_code is intentionally unreferenced — see docstring.
    del call_result_code
    if voice_status in CONFIRMED_VOICE_STATUSES:
        return False
    if voice_status == TERMINAL_NON_CONFIRMED_VOICE_STATUS:
        return False
    # voice_status ∈ {PENDING, OTHER}
    return attempts < retry_count


# ===========================================================================
# P1 — CONFIRMED voiceStatus は cc / attempts / retry_count に無関係に False
# ===========================================================================


@PBT_SETTINGS
@example(voice_status="SAFE", call_result_code="RECORDED", attempts=0, retry_count=3)
@example(
    voice_status="INJURED", call_result_code="NO_ANSWER", attempts=3, retry_count=3
)
@example(
    voice_status="UNAVAILABLE", call_result_code=None, attempts=0, retry_count=0
)
@example(voice_status="SAFE", call_result_code="BUSY", attempts=20, retry_count=0)
@given(
    voice_status=_confirmed_status,
    call_result_code=_call_result_code,
    attempts=_attempts,
    retry_count=_retry_count,
)
def test_property12_confirmed_voice_status_never_retries(
    voice_status: str,
    call_result_code: str | None,
    attempts: int,
    retry_count: int,
) -> None:
    """vs ∈ {SAFE, INJURED, UNAVAILABLE} ⇒ should_retry == False.

    cc / attempts / retry_count に関わらず常に False.

    Validates: Requirements 9.1 (有効応答取得済 → 以降の発信停止)
    """
    result = should_retry(voice_status, call_result_code, attempts, retry_count)
    assert result is False, (
        f"CONFIRMED voiceStatus must never retry; "
        f"voice_status={voice_status!r}, cc={call_result_code!r}, "
        f"attempts={attempts}, retry_count={retry_count}, got True"
    )


# ===========================================================================
# P2 — UNREACHABLE voiceStatus は cc / attempts / retry_count に無関係に False
# ===========================================================================


@PBT_SETTINGS
@example(
    voice_status="UNREACHABLE",
    call_result_code=None,
    attempts=0,
    retry_count=3,
)
@example(
    voice_status="UNREACHABLE",
    call_result_code="ERROR",
    attempts=10,
    retry_count=3,
)
@given(
    voice_status=st.just("UNREACHABLE"),
    call_result_code=_call_result_code,
    attempts=_attempts,
    retry_count=_retry_count,
)
def test_property12_unreachable_voice_status_never_retries(
    voice_status: str,
    call_result_code: str | None,
    attempts: int,
    retry_count: int,
) -> None:
    """vs == UNREACHABLE ⇒ should_retry == False (cc / a / R 無関係).

    UNREACHABLE は SFN が既に終端確定したマーカーであり、たとえ
    attempts < retry_count であっても再発信は起こらない (Req 9.5).

    Validates: Requirements 9.5 (UNREACHABLE は終端)
    """
    result = should_retry(voice_status, call_result_code, attempts, retry_count)
    assert result is False, (
        f"UNREACHABLE must never retry; "
        f"cc={call_result_code!r}, attempts={attempts}, "
        f"retry_count={retry_count}, got True"
    )


# ===========================================================================
# P3 — RETRYABLE かつ attempts < retry_count ⇒ True
# ===========================================================================


@PBT_SETTINGS
@example(voice_status="PENDING", call_result_code=None, budget=(0, 3))
@example(voice_status="OTHER", call_result_code="NO_ANSWER", budget=(2, 3))
@example(voice_status="PENDING", call_result_code="BUSY", budget=(4, 5))
@given(
    voice_status=_retryable_status,
    call_result_code=_call_result_code,
    budget=_budget_remaining_pair(),
)
def test_property12_retryable_with_budget_remaining_returns_true(
    voice_status: str,
    call_result_code: str | None,
    budget: tuple[int, int],
) -> None:
    """vs ∈ {PENDING, OTHER} ∧ a < R ⇒ should_retry == True.

    予算が残っている限り再発信が計画される.

    Validates: Requirements 9.3 (PENDING/OTHER で再発信計画),
               9.4 (a < R の間は再発信継続)
    """
    attempts, retry_count = budget
    # Strategy invariant — keep an explicit assertion so a future bug in
    # _budget_remaining_pair surfaces as a strategy error rather than a
    # silent property "failure".
    assert attempts < retry_count, (
        f"strategy invariant broken: attempts={attempts}, "
        f"retry_count={retry_count}"
    )

    result = should_retry(voice_status, call_result_code, attempts, retry_count)
    assert result is True, (
        f"RETRYABLE with budget remaining must retry; "
        f"voice_status={voice_status!r}, cc={call_result_code!r}, "
        f"attempts={attempts}, retry_count={retry_count}, got False"
    )


# ===========================================================================
# P4 — RETRYABLE かつ attempts >= retry_count ⇒ False (予算枯渇)
# ===========================================================================


@PBT_SETTINGS
@example(voice_status="PENDING", call_result_code="NO_ANSWER", budget=(3, 3))
@example(voice_status="OTHER", call_result_code="BUSY", budget=(0, 0))
@example(voice_status="PENDING", call_result_code="ERROR", budget=(10, 3))
@given(
    voice_status=_retryable_status,
    call_result_code=_call_result_code,
    budget=_budget_exhausted_pair(),
)
def test_property12_retryable_at_or_above_budget_returns_false(
    voice_status: str,
    call_result_code: str | None,
    budget: tuple[int, int],
) -> None:
    """vs ∈ {PENDING, OTHER} ∧ a ≥ R ⇒ should_retry == False.

    上限到達後は再発信を停止し、後続の FinalizeOne が UNREACHABLE に
    確定する (derive_final_status の責務).

    Validates: Requirements 9.4 (a ≥ R で予算枯渇),
               9.5 (UNREACHABLE 確定への遷移条件)
    """
    attempts, retry_count = budget
    assert attempts >= retry_count, (
        f"strategy invariant broken: attempts={attempts}, "
        f"retry_count={retry_count}"
    )

    result = should_retry(voice_status, call_result_code, attempts, retry_count)
    assert result is False, (
        f"RETRYABLE at or above budget must not retry; "
        f"voice_status={voice_status!r}, cc={call_result_code!r}, "
        f"attempts={attempts}, retry_count={retry_count}, got True"
    )


# ===========================================================================
# P5 equivalence - 対称性推論 (第17原則): implementation <=> oracle
# ===========================================================================


@PBT_SETTINGS
@example(
    voice_status="SAFE", call_result_code="RECORDED", attempts=0, retry_count=3
)
@example(
    voice_status="UNREACHABLE",
    call_result_code=None,
    attempts=5,
    retry_count=3,
)
@example(voice_status="PENDING", call_result_code=None, attempts=0, retry_count=0)
@example(voice_status="OTHER", call_result_code="BUSY", attempts=2, retry_count=3)
@example(
    voice_status="OTHER",
    call_result_code="TRANSCRIBE_FAILED",
    attempts=3,
    retry_count=3,
)
@given(
    voice_status=st.sampled_from(
        sorted(
            CONFIRMED_VOICE_STATUSES
            | RETRYABLE_VOICE_STATUSES
            | {TERMINAL_NON_CONFIRMED_VOICE_STATUS}
        )
    ),
    call_result_code=_call_result_code,
    attempts=_attempts,
    retry_count=_retry_count,
)
def test_property12_equivalent_to_oracle(
    voice_status: str,
    call_result_code: str | None,
    attempts: int,
    retry_count: int,
) -> None:
    """should_retry(...) == _oracle(...) — 双方向不変条件.

    P1〜P4 を合わせると暗黙的にこの等価性が導かれるが、明示的に
    encode しておくことで:

    * P1〜P4 の partition が将来未網羅クラスを生んだ場合の検出
    * 実装の if 分岐順序入れ替えなどの回帰検出
    * call_result_code が判定に影響しないこと (= 仕様の cc 非依存性)
      の対称性 (第17原則): 「True なら ...」だけでなく
      「... なら True」も成り立つ

    を担保する.

    Validates: Requirements 9.1, 9.3, 9.4, 9.5 (再発信判定の必要十分条件)
    """
    actual = should_retry(voice_status, call_result_code, attempts, retry_count)
    expected = _oracle(voice_status, call_result_code, attempts, retry_count)
    assert actual == expected, (
        f"contract drift: oracle={expected} impl={actual} "
        f"voice_status={voice_status!r}, cc={call_result_code!r}, "
        f"attempts={attempts}, retry_count={retry_count}"
    )
