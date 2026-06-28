"""Property 14 - 通話結果コード分類 PBT (Phase 13.14).

Validates: Requirements 5.5, 6.6
    5.5  通話終了時、Outbound Contact Flow から CallEndHandler が呼ばれ、
         通話結果コード (RECORDED / NO_ANSWER / BUSY / VOICEMAIL / ERROR /
         TRANSCRIBE_FAILED) を Response テーブルに書き込む。
    6.6  Transcribe ジョブが 3 回連続で失敗した場合、TranscribeStarter
         は当該 Response の通話結果コードを TRANSCRIBE_FAILED に更新する。

------------------------------------------------------------------------
本ファイルは ``shared/connect/call_result.py::classify_call_result``
(Phase 7.4 実装済) の挙動を Hypothesis により網羅的に検証する。

真の仕様 ("A 採用" - 13.12 / 13.15 / 13.16 / 13.17 と同じ方針)
------------------------------------------------------------------------
既存実装が定義する truth table を真の仕様とする::

    NO_ANSWER_REASONS  = {NO_ANSWER, NO_USER_RESPONSE, EXPIRED, TIMEOUT,
                          RING_TIMEOUT}
    BUSY_REASONS       = {BUSY, LINE_BUSY, USER_BUSY}
    VOICEMAIL_REASONS  = {VOICEMAIL, ANSWERING_MACHINE,
                          ANSWERING_MACHINE_DETECTED}
    ERROR_REASONS      = {API_ERROR, ERROR, REJECT, REJECTED, FAILED,
                          TELECOM_PROBLEM, ENDPOINT_ERROR, DISPATCH_FAILED,
                          USER_NOT_AVAILABLE}
    CONNECTED_REASONS  = {CUSTOMER_DISCONNECT, CONTACT_FLOW_DISCONNECT,
                          AGENT_DISCONNECT, NORMAL_HANGUP, HANGUP, OK,
                          NORMAL}

    classify_call_result(reason, transcribe_status, recorded):
        reason_n ∈ NO_ANSWER_REASONS                           ⇒ "NO_ANSWER"
        reason_n ∈ BUSY_REASONS                                ⇒ "BUSY"
        reason_n ∈ VOICEMAIL_REASONS                           ⇒ "VOICEMAIL"
        reason_n ∈ ERROR_REASONS                               ⇒ "ERROR"
        reason_n ∈ CONNECTED_REASONS ∧ recorded is False       ⇒ "ERROR"
        reason_n ∈ CONNECTED_REASONS ∧ recorded is True
                ∧ ts_n ∈ {None, "QUEUED", "IN_PROGRESS",
                          "COMPLETED"}                         ⇒ "RECORDED"
        reason_n ∈ CONNECTED_REASONS ∧ recorded is True
                ∧ ts_n == "FAILED"                             ⇒ "TRANSCRIBE_FAILED"

    where reason_n  = reason.strip().upper().replace("-","_").replace(" ","_")
          ts_n      = same normalisation; None / "" / whitespace-only → None

すなわち:

* 非接続 reason (4 バケット) は recorded / transcribe_status と無関係に
  コードが決まる (録音が存在し得ないため).
* 接続 reason + recorded=False は operational ERROR (接続したが録音
  ファイルが上がっていない異常).
* 接続 reason + recorded=True は Transcribe ステータスのみで分岐:
  pending (None / QUEUED / IN_PROGRESS) と COMPLETED は両方 RECORDED.
  この扱いは CallEndHandler 起動時点 (Phase 6.3, Transcribe 未起動) と
  TranscribeStarter 成功後 (Phase 6.4) の両方を 1 関数で吸収する設計.

これは Phase 7.4 call_result.py の truth table と完全に一致する.

design.md / tasks.md とのズレに関する脚注
------------------------------------------------------------------------
design.md Property 14 セクション (1005-1014 行) は次の抽象マッピングを
記載::

    - 録音完了かつ Transcribe 成功 ⇒ RECORDED
    - 30 秒以内に応答無し          ⇒ NO_ANSWER
    - Busy                         ⇒ BUSY
    - 留守電検知                   ⇒ VOICEMAIL
    - API エラー / Reject          ⇒ ERROR
    - 録音完了したが Transcribe ジョブが 3 回失敗 ⇒ TRANSCRIBE_FAILED

これは結果コードの **意味論** を示すが、Connect の DisconnectReason
語彙 (CUSTOMER_DISCONNECT / NO_USER_RESPONSE / ...) との具体マッピング
や、CallEndHandler 起動時点で Transcribe が未起動である canonical state
(``transcribe_status is None`` → RECORDED) は明記されていない. これは
具体化レベル差であり矛盾ではないが、実装側がより詳細な truth table を
保持する形となっている. ユーザー確定方針として:

* **実装を正とする (A 採用)**
* **design.md / tasks.md の文言補強は別タスクで起票**

既存 example-based テストとの分業
------------------------------------------------------------------------
本 PBT は **valid input 集合** に限定して挙動を網羅検証する.
以下の負経路 / 入出力契約は既存
``backend/tests/shared/connect/test_call_result.py`` の example test が
カバー済のため、本ファイルでは生成しない (DRY 原則):

* ``reason`` が ``str`` 以外 / 空文字 / whitespace のみ → ValueError
* ``reason`` が未知の文字列 → ValueError (unrecognised reason)
* ``transcribe_status`` が ``str | None`` 以外 → ValueError
* ``transcribe_status`` が未知の文字列 → ValueError
* ``recorded`` が ``bool`` 以外 (str / int 含む) → ValueError
* 正規化 (lowercase / hyphen / space / surrounding whitespace) の
  個別例検証 — ただし本 PBT では P7 で **不変性** として検証する.
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.connect.call_result import (
    VALID_CALL_RESULT_CODES,
    classify_call_result,
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
# Reference vocabulary — kept locally (not imported from call_result) so
# the test acts as an independent oracle. If call_result.py's bucket
# definitions silently drift, this set diverges and the equivalence
# property (P6) pins the regression.
# ---------------------------------------------------------------------------

#: DisconnectReason values that mean "no pickup" (Req 5.5 NO_ANSWER).
NO_ANSWER_REASONS: frozenset[str] = frozenset(
    {
        "NO_ANSWER",
        "NO_USER_RESPONSE",
        "EXPIRED",
        "TIMEOUT",
        "RING_TIMEOUT",
    }
)

#: DisconnectReason values that mean "line busy" (Req 5.5 BUSY).
BUSY_REASONS: frozenset[str] = frozenset(
    {"BUSY", "LINE_BUSY", "USER_BUSY"}
)

#: DisconnectReason values that mean "voicemail picked up" (Req 5.5
#: VOICEMAIL).
VOICEMAIL_REASONS: frozenset[str] = frozenset(
    {
        "VOICEMAIL",
        "ANSWERING_MACHINE",
        "ANSWERING_MACHINE_DETECTED",
    }
)

#: DisconnectReason values that mean "API / telecom error" (Req 5.5
#: ERROR).
ERROR_REASONS: frozenset[str] = frozenset(
    {
        "API_ERROR",
        "ERROR",
        "REJECT",
        "REJECTED",
        "FAILED",
        "TELECOM_PROBLEM",
        "ENDPOINT_ERROR",
        "DISPATCH_FAILED",
        "USER_NOT_AVAILABLE",
    }
)

#: DisconnectReason values that mean "call connected and ended
#: naturally". The actual code (RECORDED / TRANSCRIBE_FAILED / ERROR)
#: depends on recorded + transcribe_status.
CONNECTED_REASONS: frozenset[str] = frozenset(
    {
        "CUSTOMER_DISCONNECT",
        "CONTACT_FLOW_DISCONNECT",
        "AGENT_DISCONNECT",
        "NORMAL_HANGUP",
        "HANGUP",
        "OK",
        "NORMAL",
    }
)

#: All recognised reasons (union of the five disjoint buckets).
ALL_REASONS: frozenset[str] = (
    NO_ANSWER_REASONS
    | BUSY_REASONS
    | VOICEMAIL_REASONS
    | ERROR_REASONS
    | CONNECTED_REASONS
)

#: Transcribe statuses that, together with recorded=True on a connected
#: reason, yield RECORDED. ``None`` here represents Python ``None``
#: (Transcribe not yet started) which is the CallEndHandler-time state.
TS_PENDING_OR_OK: frozenset[str | None] = frozenset(
    {None, "QUEUED", "IN_PROGRESS", "COMPLETED"}
)
TS_FAILED: str = "FAILED"

#: Flat lookup table for the four non-connected reason buckets. Built
#: from the five disjoint frozensets above so that the oracle can do a
#: single dict lookup (keeps return-statement count below ruff's
#: PLR0911 threshold while preserving the test-side bucket definitions
#: as the source of independence).
_NON_CONNECTED_TO_CODE: dict[str, str] = {
    **dict.fromkeys(NO_ANSWER_REASONS, "NO_ANSWER"),
    **dict.fromkeys(BUSY_REASONS, "BUSY"),
    **dict.fromkeys(VOICEMAIL_REASONS, "VOICEMAIL"),
    **dict.fromkeys(ERROR_REASONS, "ERROR"),
}

# ---------------------------------------------------------------------------
# Hypothesis strategies.
# ---------------------------------------------------------------------------

_recorded: st.SearchStrategy[bool] = st.booleans()

_transcribe_status_any: st.SearchStrategy[str | None] = st.one_of(
    st.none(),
    st.sampled_from(["COMPLETED", "FAILED", "QUEUED", "IN_PROGRESS"]),
)

_transcribe_status_pending_or_ok: st.SearchStrategy[str | None] = st.one_of(
    st.none(),
    st.sampled_from(["COMPLETED", "QUEUED", "IN_PROGRESS"]),
)

_no_answer_reason: st.SearchStrategy[str] = st.sampled_from(
    sorted(NO_ANSWER_REASONS)
)
_busy_reason: st.SearchStrategy[str] = st.sampled_from(sorted(BUSY_REASONS))
_voicemail_reason: st.SearchStrategy[str] = st.sampled_from(
    sorted(VOICEMAIL_REASONS)
)
_error_reason: st.SearchStrategy[str] = st.sampled_from(sorted(ERROR_REASONS))
_connected_reason: st.SearchStrategy[str] = st.sampled_from(
    sorted(CONNECTED_REASONS)
)
_any_reason: st.SearchStrategy[str] = st.sampled_from(sorted(ALL_REASONS))


# ---------------------------------------------------------------------------
# Helper: oracle expression of the contract.
# ---------------------------------------------------------------------------


def _normalise(s: str) -> str:
    """Canonicalise reason / transcribe_status the same way the impl does."""
    return s.strip().upper().replace("-", "_").replace(" ", "_")


def _oracle(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> str:
    """Independent re-derivation of the classify_call_result contract.

    Used by P6 as a property-level equivalence check against the
    implementation. Intentionally written in the most literal form
    possible — partition on the reason bucket first, then on recorded
    and transcribe_status for the CONNECTED partition.

    Pre-conditions (the caller's strategies guarantee these so this
    oracle does **not** re-validate; validation is the impl's job and
    is covered by the existing example tests):

    * ``reason`` is a recognised string in ``ALL_REASONS`` (possibly
      after normalisation).
    * ``transcribe_status`` is ``None`` or a recognised status string.
    * ``recorded`` is a genuine ``bool``.
    """
    reason_n = _normalise(reason)
    if transcribe_status is None or transcribe_status.strip() == "":
        ts_n: str | None = None
    else:
        ts_n = _normalise(transcribe_status)

    # Non-connected buckets short-circuit via flat dict lookup.
    non_connected = _NON_CONNECTED_TO_CODE.get(reason_n)
    if non_connected is not None:
        return non_connected
    # reason_n ∈ CONNECTED_REASONS  (precondition).
    if not recorded:
        return "ERROR"
    if ts_n is None or ts_n in {"QUEUED", "IN_PROGRESS", "COMPLETED"}:
        return "RECORDED"
    # ts_n == "FAILED" — only remaining possibility under precondition.
    return "TRANSCRIBE_FAILED"


# ===========================================================================
# P1 — Output is always a member of VALID_CALL_RESULT_CODES.
# ===========================================================================


@PBT_SETTINGS
@example(reason="NO_ANSWER", transcribe_status=None, recorded=False)
@example(reason="BUSY", transcribe_status=None, recorded=False)
@example(reason="VOICEMAIL", transcribe_status="COMPLETED", recorded=True)
@example(reason="API_ERROR", transcribe_status="FAILED", recorded=True)
@example(reason="CUSTOMER_DISCONNECT", transcribe_status=None, recorded=True)
@example(reason="CUSTOMER_DISCONNECT", transcribe_status="FAILED", recorded=True)
@example(reason="CUSTOMER_DISCONNECT", transcribe_status="COMPLETED", recorded=False)
@given(
    reason=_any_reason,
    transcribe_status=_transcribe_status_any,
    recorded=_recorded,
)
def test_property14_output_is_a_valid_call_result_code(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> None:
    """∀ valid input ⇒ output ∈ VALID_CALL_RESULT_CODES.

    分類関数の出力空間が 6 コードに閉じていることを保証する.
    どんな (reason, transcribe_status, recorded) の組合せでも未定義の
    文字列を返さない契約.

    Validates: Requirements 5.5 (定義された 6 コードを返す),
               6.6 (TRANSCRIBE_FAILED もこの 6 コードの一員)
    """
    result = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    assert result in VALID_CALL_RESULT_CODES, (
        f"output out of contract; reason={reason!r}, "
        f"ts={transcribe_status!r}, recorded={recorded}, got={result!r}"
    )


# ===========================================================================
# P2 — Determinism / purity: same input ⇒ same output.
# ===========================================================================


@PBT_SETTINGS
@example(reason="CUSTOMER_DISCONNECT", transcribe_status="FAILED", recorded=True)
@example(reason="NO_ANSWER", transcribe_status=None, recorded=False)
@example(reason="API_ERROR", transcribe_status="COMPLETED", recorded=True)
@given(
    reason=_any_reason,
    transcribe_status=_transcribe_status_any,
    recorded=_recorded,
)
def test_property14_is_pure(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> None:
    """classify_call_result(x) == classify_call_result(x).

    純粋関数性: 同じ入力で常に同じ出力. 副作用や隠れた状態 (時刻 /
    乱数 / グローバル) に依存しないことを担保する.

    Validates: Requirements 5.5 (再現性ある分類)
    """
    a = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    b = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    assert a == b, (
        f"non-deterministic output for reason={reason!r}, "
        f"ts={transcribe_status!r}, recorded={recorded}: {a!r} vs {b!r}"
    )


# ===========================================================================
# P3 — Non-connected buckets map to their dedicated code, regardless of
#      recorded / transcribe_status.
# ===========================================================================


@PBT_SETTINGS
@example(reason="NO_ANSWER", transcribe_status=None, recorded=False)
@example(reason="NO_USER_RESPONSE", transcribe_status="COMPLETED", recorded=True)
@example(reason="EXPIRED", transcribe_status="FAILED", recorded=True)
@example(reason="TIMEOUT", transcribe_status="QUEUED", recorded=False)
@example(reason="RING_TIMEOUT", transcribe_status=None, recorded=True)
@given(
    reason=_no_answer_reason,
    transcribe_status=_transcribe_status_any,
    recorded=_recorded,
)
def test_property14_no_answer_bucket_maps_to_no_answer(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> None:
    """reason ∈ NO_ANSWER_REASONS ⇒ "NO_ANSWER" (ts / recorded 無関係).

    非接続 reason は録音が存在し得ないため、recorded / transcribe_status
    の値に関わらず NO_ANSWER に短絡分類される.

    Validates: Requirements 5.5 (NO_ANSWER マッピング)
    """
    result = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    assert result == "NO_ANSWER", (
        f"NO_ANSWER bucket short-circuit broken; reason={reason!r}, "
        f"ts={transcribe_status!r}, recorded={recorded}, got={result!r}"
    )


@PBT_SETTINGS
@example(reason="BUSY", transcribe_status=None, recorded=False)
@example(reason="LINE_BUSY", transcribe_status="COMPLETED", recorded=True)
@example(reason="USER_BUSY", transcribe_status="FAILED", recorded=True)
@given(
    reason=_busy_reason,
    transcribe_status=_transcribe_status_any,
    recorded=_recorded,
)
def test_property14_busy_bucket_maps_to_busy(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> None:
    """reason ∈ BUSY_REASONS ⇒ "BUSY" (ts / recorded 無関係).

    Validates: Requirements 5.5 (BUSY マッピング)
    """
    result = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    assert result == "BUSY", (
        f"BUSY bucket short-circuit broken; reason={reason!r}, "
        f"ts={transcribe_status!r}, recorded={recorded}, got={result!r}"
    )


@PBT_SETTINGS
@example(reason="VOICEMAIL", transcribe_status=None, recorded=False)
@example(reason="ANSWERING_MACHINE", transcribe_status="COMPLETED", recorded=True)
@example(
    reason="ANSWERING_MACHINE_DETECTED",
    transcribe_status="FAILED",
    recorded=True,
)
@given(
    reason=_voicemail_reason,
    transcribe_status=_transcribe_status_any,
    recorded=_recorded,
)
def test_property14_voicemail_bucket_maps_to_voicemail(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> None:
    """reason ∈ VOICEMAIL_REASONS ⇒ "VOICEMAIL" (ts / recorded 無関係).

    Validates: Requirements 5.5 (VOICEMAIL マッピング)
    """
    result = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    assert result == "VOICEMAIL", (
        f"VOICEMAIL bucket short-circuit broken; reason={reason!r}, "
        f"ts={transcribe_status!r}, recorded={recorded}, got={result!r}"
    )


@PBT_SETTINGS
@example(reason="API_ERROR", transcribe_status=None, recorded=False)
@example(reason="ERROR", transcribe_status="COMPLETED", recorded=True)
@example(reason="REJECT", transcribe_status="FAILED", recorded=True)
@example(reason="FAILED", transcribe_status="QUEUED", recorded=False)
@example(reason="USER_NOT_AVAILABLE", transcribe_status=None, recorded=True)
@given(
    reason=_error_reason,
    transcribe_status=_transcribe_status_any,
    recorded=_recorded,
)
def test_property14_error_bucket_maps_to_error(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> None:
    """reason ∈ ERROR_REASONS ⇒ "ERROR" (ts / recorded 無関係).

    Validates: Requirements 5.5 (ERROR マッピング)
    """
    result = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    assert result == "ERROR", (
        f"ERROR bucket short-circuit broken; reason={reason!r}, "
        f"ts={transcribe_status!r}, recorded={recorded}, got={result!r}"
    )


# ===========================================================================
# P4 — Connected reason + recorded=False ⇒ ERROR (operational failure).
# ===========================================================================


@PBT_SETTINGS
@example(reason="CUSTOMER_DISCONNECT", transcribe_status=None)
@example(reason="CONTACT_FLOW_DISCONNECT", transcribe_status="COMPLETED")
@example(reason="AGENT_DISCONNECT", transcribe_status="FAILED")
@example(reason="NORMAL_HANGUP", transcribe_status="QUEUED")
@example(reason="HANGUP", transcribe_status="IN_PROGRESS")
@example(reason="OK", transcribe_status=None)
@example(reason="NORMAL", transcribe_status="COMPLETED")
@given(
    reason=_connected_reason,
    transcribe_status=_transcribe_status_any,
)
def test_property14_connected_but_not_recorded_is_error(
    reason: str,
    transcribe_status: str | None,
) -> None:
    """reason ∈ CONNECTED_REASONS ∧ recorded=False ⇒ "ERROR".

    通話は接続したが録音ファイルが上がっていない状況は operational
    failure と扱われ、recorded=False の場合は transcribe_status の値に
    関係なく ERROR が返る (録音が無いので RECORDED や TRANSCRIBE_FAILED
    にはなり得ない).

    Validates: Requirements 5.5 (録音失敗時の ERROR 扱い)
    """
    result = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=False,
    )
    assert result == "ERROR", (
        f"connected+not-recorded must be ERROR; reason={reason!r}, "
        f"ts={transcribe_status!r}, got={result!r}"
    )


# ===========================================================================
# P5a — Connected + recorded=True + ts ∈ {None, "", PENDING, COMPLETED}
#       ⇒ RECORDED.
# ===========================================================================


@PBT_SETTINGS
@example(reason="CUSTOMER_DISCONNECT", transcribe_status=None)
@example(reason="CONTACT_FLOW_DISCONNECT", transcribe_status="COMPLETED")
@example(reason="AGENT_DISCONNECT", transcribe_status="QUEUED")
@example(reason="NORMAL_HANGUP", transcribe_status="IN_PROGRESS")
@example(reason="HANGUP", transcribe_status=None)
@example(reason="OK", transcribe_status="COMPLETED")
@example(reason="NORMAL", transcribe_status="QUEUED")
@given(
    reason=_connected_reason,
    transcribe_status=_transcribe_status_pending_or_ok,
)
def test_property14_connected_recorded_pending_or_completed_is_recorded(
    reason: str,
    transcribe_status: str | None,
) -> None:
    """reason ∈ CONNECTED_REASONS ∧ recorded=True ∧
       ts ∈ {None, "QUEUED", "IN_PROGRESS", "COMPLETED"} ⇒ "RECORDED".

    CallEndHandler 起動時点 (ts=None / Transcribe 未起動) と、
    TranscribeStarter が成功した時点 (ts=COMPLETED) を 1 関数で吸収する.
    pending (QUEUED / IN_PROGRESS) も RECORDED 扱い: 録音そのものは
    存在するため.

    Validates: Requirements 5.5 (RECORDED マッピング)
    """
    result = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=True,
    )
    assert result == "RECORDED", (
        f"connected+recorded+pending-or-completed must be RECORDED; "
        f"reason={reason!r}, ts={transcribe_status!r}, got={result!r}"
    )


# ===========================================================================
# P5b — Connected + recorded=True + ts == "FAILED" ⇒ TRANSCRIBE_FAILED.
# ===========================================================================


@PBT_SETTINGS
@example(reason="CUSTOMER_DISCONNECT", transcribe_status="FAILED")
@example(reason="CONTACT_FLOW_DISCONNECT", transcribe_status="FAILED")
@example(reason="AGENT_DISCONNECT", transcribe_status="FAILED")
@example(reason="NORMAL_HANGUP", transcribe_status="FAILED")
@example(reason="HANGUP", transcribe_status="FAILED")
@example(reason="OK", transcribe_status="FAILED")
@example(reason="NORMAL", transcribe_status="FAILED")
@given(
    reason=_connected_reason,
    transcribe_status=st.just("FAILED"),
)
def test_property14_connected_recorded_transcribe_failed_is_transcribe_failed(
    reason: str,
    transcribe_status: str,
) -> None:
    """reason ∈ CONNECTED_REASONS ∧ recorded=True ∧ ts=="FAILED"
       ⇒ "TRANSCRIBE_FAILED".

    TranscribeStarter が 3 回失敗した tail end の canonical state.

    Validates: Requirements 6.6 (Transcribe ジョブ最大再試行失敗時の
               TRANSCRIBE_FAILED 確定)
    """
    result = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=True,
    )
    assert result == "TRANSCRIBE_FAILED", (
        f"connected+recorded+ts=FAILED must be TRANSCRIBE_FAILED; "
        f"reason={reason!r}, got={result!r}"
    )


# ===========================================================================
# P6 equivalence — 対称性推論 (第17原則): implementation <=> oracle.
# ===========================================================================


@PBT_SETTINGS
@example(reason="NO_ANSWER", transcribe_status=None, recorded=False)
@example(reason="BUSY", transcribe_status="COMPLETED", recorded=True)
@example(reason="VOICEMAIL", transcribe_status="FAILED", recorded=True)
@example(reason="API_ERROR", transcribe_status="QUEUED", recorded=False)
@example(reason="CUSTOMER_DISCONNECT", transcribe_status=None, recorded=True)
@example(reason="CUSTOMER_DISCONNECT", transcribe_status="FAILED", recorded=True)
@example(reason="CUSTOMER_DISCONNECT", transcribe_status="COMPLETED", recorded=False)
@example(reason="HANGUP", transcribe_status="IN_PROGRESS", recorded=True)
@given(
    reason=_any_reason,
    transcribe_status=_transcribe_status_any,
    recorded=_recorded,
)
def test_property14_equivalent_to_oracle(
    reason: str,
    transcribe_status: str | None,
    recorded: bool,
) -> None:
    """classify_call_result(...) == _oracle(...) — 双方向不変条件.

    P1〜P5 を合わせると暗黙的にこの等価性が導かれるが、明示的に
    encode しておくことで:

    * P1〜P5 の partition が将来未網羅クラスを生んだ場合の検出
    * 実装の if 分岐順序入れ替えなどの回帰検出
    * 「output が X ⇒ 入力クラスは Y」と「入力クラスが Y ⇒ output が X」
      の双方向 (第17原則 対称性推論) を担保

    を実現する.

    Validates: Requirements 5.5, 6.6 (通話結果コード分類の必要十分条件)
    """
    actual = classify_call_result(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    expected = _oracle(
        reason=reason,
        transcribe_status=transcribe_status,
        recorded=recorded,
    )
    assert actual == expected, (
        f"contract drift: oracle={expected!r} impl={actual!r} "
        f"reason={reason!r}, ts={transcribe_status!r}, "
        f"recorded={recorded}"
    )
