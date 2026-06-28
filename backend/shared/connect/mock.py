"""Connect / Transcribe mock 経路の擬似応答決定ロジック（ADR-0010 §3.2）.

ADR-0010 AWS dev 環境上の Connect/Transcribe Mock 経路（Outbound 1 巡 E2E）の
employeeId 末尾文字マッピングを純粋関数として切出。Phase 16.2 ConnectDispatcher /
Phase 16.3 TranscribeStarter から呼出される共通関数（DRY、第 19 原則 (a)）。

ADR-0010 §3.2 マッピング表：

| 末尾 | callResultCode | Voice_Status（最終）       | transcript 例                |
|------|----------------|----------------------------|------------------------------|
| 0-2  | RECORDED       | SAFE                       | 「無事です」「大丈夫です」   |
| 3-4  | RECORDED       | INJURED                    | 「怪我をしました」「痛いです」 |
| 5-6  | RECORDED       | UNAVAILABLE                | 「動けません」「出社不可です」 |
| 7    | NO_ANSWER      | UNREACHABLE                | None（録音なし）             |
| 8    | BUSY           | UNREACHABLE                | None（録音なし）             |
| 9    | RECORDED       | OTHER → UNREACHABLE        | 「あいうえお」（辞書非該当） |

末尾文字が 0〜9 以外（UUID 等）の場合は SHA-256 hash 最下位バイト mod 10 で
正規化（ADR-0010 §6.2.1「末尾文字が 0〜9 以外の場合の動作」承認済）。

第 19 原則 (b) フォールバック禁止：空文字列 / 非 str 入力は ValueError raise。

PBT 候補（Phase 16.1）：
    * (1) 戻り値 tuple shape が ``(str, str | None)``
    * (2) callResultCode ∈ :data:`VALID_CALL_RESULT_CODES`
    * (3) ``transcript is None`` ↔ ``callResultCode ∈ {"NO_ANSWER", "BUSY"}``
    * (4) 同じ employeeId に対して常に同じ戻り値（決定論性）
"""

from __future__ import annotations

import hashlib

#: 末尾数字 → ``(callResultCode, transcript)`` マッピング（ADR-0010 §3.2）。
#:
#: § 3.2 表の transcript 例 2 つから 1 つを選択している末尾は以下のとおり：
#:
#: * 末尾 1 / 4 / 6: 表の 2 例目（"大丈夫です" / "痛いです" / "出社不可です"）。
#: * それ以外（0 / 2 / 3 / 5 / 9）: 表の 1 例目。
#:
#: いずれの選択も §3.2 表記の例の範囲内であり、辞書整合性検証は ADR-0010
#: §6.7 のとおり Phase 16.5 deploy 前ドライランで担保する。
_DIGIT_TO_MOCK: dict[int, tuple[str, str | None]] = {
    0: ("RECORDED", "無事です"),
    1: ("RECORDED", "大丈夫です"),
    2: ("RECORDED", "無事です"),
    3: ("RECORDED", "怪我をしました"),
    4: ("RECORDED", "痛いです"),
    5: ("RECORDED", "動けません"),
    6: ("RECORDED", "出社不可です"),
    7: ("NO_ANSWER", None),
    8: ("BUSY", None),
    9: ("RECORDED", "あいうえお"),
}

#: 戻り値の callResultCode が取りうる値の集合（PBT 不変条件 (2) で検証）。
#:
#: ``shared.connect.call_result.VALID_CALL_RESULT_CODES`` は VOICEMAIL /
#: ERROR / TRANSCRIBE_FAILED も含む全集合だが、mock 経路の擬似応答は
#: ADR-0010 §3.2 表に列挙された 3 値のみを返すため、ここでは別途明示する。
VALID_CALL_RESULT_CODES: frozenset[str] = frozenset(
    {"RECORDED", "NO_ANSWER", "BUSY"}
)

#: 録音なし（transcript = None）となる callResultCode の集合。PBT 不変条件
#: (3) ``transcript is None`` ↔ ``code ∈ _NO_TRANSCRIPT_CODES`` で参照される。
_NO_TRANSCRIPT_CODES: frozenset[str] = frozenset({"NO_ANSWER", "BUSY"})


def derive_mock_response(employee_id: str) -> tuple[str, str | None]:
    """employeeId から決定論的に ``(callResultCode, transcript)`` を返す純粋関数。

    Phase 16.2 ConnectDispatcher / Phase 16.3 TranscribeStarter の mock
    分岐から呼ばれる共通関数。ADR-0010 §3.2 マッピング表に従い、
    employee_id 末尾文字 → 擬似応答を決定する。

    Args:
        employee_id: 社員 ID（Employee_Master の主キー）。E.164 形式の電話番号
            ではない。空文字列 / 非 ``str`` は :class:`ValueError`。

    Returns:
        ``(callResultCode, transcript_text or None)`` のタプル。

        * ``callResultCode`` ∈ :data:`VALID_CALL_RESULT_CODES`
          (``"RECORDED"`` / ``"NO_ANSWER"`` / ``"BUSY"``)
        * ``transcript_text``: ``callResultCode == "RECORDED"`` のときのみ
          非 None（``str``）。``NO_ANSWER`` / ``BUSY`` のときは ``None``
          （録音されないため）。

    Raises:
        ValueError: ``employee_id`` が空文字列または ``str`` 以外の場合
            （第 19 原則 (b) フォールバック禁止）。

    Examples:
        >>> derive_mock_response("EMP-0001")  # 末尾 "1"
        ('RECORDED', '大丈夫です')
        >>> derive_mock_response("EMP-0007")  # 末尾 "7"
        ('NO_ANSWER', None)
        >>> derive_mock_response("EMP-0008")  # 末尾 "8"
        ('BUSY', None)

    Note:
        末尾文字が 0〜9 の ASCII 数字でない場合（UUID 末尾の ``a`` 〜 ``f``、
        または任意の非数字文字）は ``SHA-256(employee_id) digest()[0] % 10``
        で 0〜9 に正規化する（ADR-0010 §6.2.1 承認済）。これにより
        ``employee_id`` が任意のフォーマットでも決定論的に同じ結果を返す。

        正規化先の数値は SHA-256 によって employee_id 全体に依存するため、
        末尾文字だけを変えても結果が大きく変わる点に注意（hash の雪崩特性）。
    """
    if not isinstance(employee_id, str):
        raise ValueError(
            f"employee_id must be str; got {type(employee_id).__name__}"
        )
    if not employee_id:
        raise ValueError("employee_id must be non-empty")

    last_char = employee_id[-1]
    if last_char.isascii() and last_char.isdigit():
        # 0〜9 の ASCII 数字。``isdigit()`` 単体だと全角数字 '０' 等も True に
        # なるため ``isascii()`` で先に絞り込む（第 17 原則 対称性推論：
        # 「ASCII 数字なら 0〜9」と「0〜9 なら ASCII 数字」を両方向に成立
        # させるための判定）。
        digit = int(last_char)
    else:
        # ADR-0010 §6.2.1: SHA-256 hash 最下位バイト mod 10。
        digest = hashlib.sha256(employee_id.encode("utf-8")).digest()
        digit = digest[0] % 10

    return _DIGIT_TO_MOCK[digit]
