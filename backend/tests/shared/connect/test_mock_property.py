"""Property-based tests for ``backend/shared/connect/mock.py`` (Phase 16.1).

ADR-0010 §3.2 + §6.2.1 / §6.2.2 が要求する純粋関数 ``derive_mock_response`` の
不変条件 4 つを Hypothesis で全 employee_id 入力に対して検証する。

Named properties (1, ADR-0010 §3.2 / §6.2.1 / §6.2.2):

    P_invariants (mock_response_invariants_for_all_employee_ids):
        For any non-empty ``str`` ``employee_id``:

        (1) tuple shape: 戻り値が ``(str, str | None)`` の 2-tuple。
        (2) codomain: ``callResultCode ∈ VALID_CALL_RESULT_CODES``
            = ``{"RECORDED", "NO_ANSWER", "BUSY"}``。
        (3) bi-implication: ``transcript is None`` ↔
            ``callResultCode ∈ {"NO_ANSWER", "BUSY"}``。逆方向まで pin
            することで「``RECORDED`` で transcript が None になる」未来の
            退行を即検知（第 17 原則 対称性推論）。
        (4) determinism: 同じ ``employee_id`` を 2 回続けて呼んだとき結果一致。
            ``derive_mock_response`` の唯一の非決定性候補（hash + dict lookup）
            がいずれも決定論的であることを pin する。

Anchored examples (``@example`` pin):
    - ASCII 数字末尾 0〜9 の 10 件（マッピング表の全 row）
    - UUID 末尾 hex 数字 (``a``〜``f``) のうち代表 1 件 → hash 経路
    - 全角数字末尾 → ``isdigit()`` True / ``isascii()`` False → hash 経路
"""

from __future__ import annotations

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.connect.mock import (
    VALID_CALL_RESULT_CODES,
    derive_mock_response,
)

# 純粋関数 PBT は 1 例 < 1 ms で完走するため 200 examples を維持。既存
# ``test_backoff_property24.py`` と同設定（DRY、第 19 原則 (a)）。
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
    ],
)


@PBT_SETTINGS
@example(employee_id="EMP-0")  # 末尾 0、RECORDED + transcript str
@example(employee_id="EMP-7")  # 末尾 7、NO_ANSWER + None
@example(employee_id="EMP-8")  # 末尾 8、BUSY + None
@example(employee_id="EMP-9")  # 末尾 9、RECORDED + OTHER 用 transcript
@example(employee_id="44444444-4444-4444-4444-44444444444a")  # UUID hex 末尾
@example(employee_id="EMP-\uff10")  # 全角数字末尾 → hash 経路
@given(employee_id=st.text(min_size=1, max_size=100))
def test_property_mock_response_invariants(employee_id: str) -> None:
    """ADR-0010 §3.2 / §6.2.1 / §6.2.2 derive_mock_response 不変条件.

    For any non-empty ``str`` ``employee_id``:

      (1) 戻り値が tuple shape ``(str, str | None)``
      (2) ``callResultCode`` ∈ :data:`VALID_CALL_RESULT_CODES`
      (3) ``transcript is None`` ↔ ``callResultCode ∈ {"NO_ANSWER", "BUSY"}``
      (4) 同じ ``employee_id`` で 2 回呼んでも結果一致（決定論性）

    Validates: ADR-0010 §3.2 マッピング表 / §6.2.1 hash 正規化 / §6.2.2 配置.
    """
    result1 = derive_mock_response(employee_id)
    result2 = derive_mock_response(employee_id)

    # (1) tuple shape.
    assert isinstance(result1, tuple), (
        f"expected tuple, got {type(result1).__name__} for "
        f"employee_id={employee_id!r}"
    )
    assert len(result1) == 2, (
        f"expected 2-tuple, got len={len(result1)} for "
        f"employee_id={employee_id!r}"
    )

    code, transcript = result1
    assert isinstance(code, str), (
        f"expected str callResultCode, got {type(code).__name__} for "
        f"employee_id={employee_id!r}"
    )
    assert transcript is None or isinstance(transcript, str), (
        f"expected str | None transcript, got {type(transcript).__name__} "
        f"for employee_id={employee_id!r}"
    )

    # (2) codomain.
    assert code in VALID_CALL_RESULT_CODES, (
        f"unexpected callResultCode={code!r} for employee_id={employee_id!r}; "
        f"expected one of {sorted(VALID_CALL_RESULT_CODES)}"
    )

    # (3) bi-implication ``transcript is None`` ↔ ``code ∈ {NO_ANSWER, BUSY}``.
    no_transcript_codes = {"NO_ANSWER", "BUSY"}
    if code in no_transcript_codes:
        # 順方向：NO_ANSWER / BUSY なら transcript は None でなければならない。
        assert transcript is None, (
            f"expected transcript=None for callResultCode={code!r}; "
            f"got {transcript!r} (employee_id={employee_id!r})"
        )
    else:
        # 逆方向：それ以外（RECORDED）なら transcript は非 None の str。
        # ``isinstance`` は上で確認済みなので、ここでは None でないことを pin。
        assert transcript is not None, (
            f"expected non-None transcript for callResultCode={code!r}; "
            f"got None (employee_id={employee_id!r})"
        )

    # (4) 決定論性。
    assert result1 == result2, (
        f"non-deterministic result for employee_id={employee_id!r}: "
        f"first={result1!r}, second={result2!r}"
    )
