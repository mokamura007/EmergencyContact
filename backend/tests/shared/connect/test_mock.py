"""Unit tests for ``backend/shared/connect/mock.py`` (Phase 16.1).

ADR-0010 §3.2 マッピング表（末尾 0〜9 の 10 件）+ SHA-256 hash 正規化
（§6.2.1）+ 異常系（第 19 原則 (b)）の例示固定。Property-based 半は
``test_mock_property.py`` で全 employeeId 入力に対する不変条件 4 つを検証。
"""

from __future__ import annotations

import hashlib

import pytest

from shared.connect.mock import (
    VALID_CALL_RESULT_CODES,
    _DIGIT_TO_MOCK,
    derive_mock_response,
)


# --- 末尾 0〜9 マッピング検証（10 件、ADR-0010 §3.2） -----------------


class TestDigitMapping:
    """末尾文字が 0〜9 の ASCII 数字なら §3.2 マッピング表通りの値を返す。"""

    def test_last_0_returns_recorded_safe(self) -> None:
        assert derive_mock_response("EMP-0") == ("RECORDED", "無事です")

    def test_last_1_returns_recorded_safe_alt(self) -> None:
        assert derive_mock_response("EMP-1") == ("RECORDED", "大丈夫です")

    def test_last_2_returns_recorded_safe(self) -> None:
        assert derive_mock_response("EMP-2") == ("RECORDED", "無事です")

    def test_last_3_returns_recorded_injured(self) -> None:
        assert derive_mock_response("EMP-3") == ("RECORDED", "怪我をしました")

    def test_last_4_returns_recorded_injured_alt(self) -> None:
        assert derive_mock_response("EMP-4") == ("RECORDED", "痛いです")

    def test_last_5_returns_recorded_unavailable(self) -> None:
        assert derive_mock_response("EMP-5") == ("RECORDED", "動けません")

    def test_last_6_returns_recorded_unavailable_alt(self) -> None:
        assert derive_mock_response("EMP-6") == ("RECORDED", "出社不可です")

    def test_last_7_returns_no_answer(self) -> None:
        assert derive_mock_response("EMP-7") == ("NO_ANSWER", None)

    def test_last_8_returns_busy(self) -> None:
        assert derive_mock_response("EMP-8") == ("BUSY", None)

    def test_last_9_returns_recorded_other(self) -> None:
        assert derive_mock_response("EMP-9") == ("RECORDED", "あいうえお")


# --- SHA-256 hash 正規化検証（ADR-0010 §6.2.1） -----------------------


class TestHashNormalization:
    """末尾文字が 0〜9 以外の場合、SHA-256 hash mod 10 で正規化されること。"""

    def test_uuid_ending_with_letter_returns_valid_tuple(self) -> None:
        """UUID 形式（末尾 ``a``）でも tuple shape が成立すること。"""
        result = derive_mock_response("44444444-4444-4444-4444-44444444444a")
        code, transcript = result
        assert code in VALID_CALL_RESULT_CODES
        # transcript shape 検証：NO_ANSWER / BUSY のときのみ None。
        if code in {"NO_ANSWER", "BUSY"}:
            assert transcript is None
        else:
            assert isinstance(transcript, str)
            assert transcript != ""

    def test_uuid_ending_with_hex_digit_letter_b(self) -> None:
        """末尾が ``b``（hex 数字だが ASCII decimal digit ではない）→ hash 正規化。"""
        emp = "deadbeef-cafe-babe-face-0123456789ab"
        result = derive_mock_response(emp)
        # ``b`` は ``isdigit()`` False（hex 数字は十進数字判定の対象外）
        # なので hash 経路に入ることを期待値計算で確認する。
        digest = hashlib.sha256(emp.encode("utf-8")).digest()
        expected_digit = digest[0] % 10
        assert result == _DIGIT_TO_MOCK[expected_digit]

    def test_hash_normalization_is_deterministic(self) -> None:
        """同じ employee_id は常に同じ結果を返す（決定論性、PBT 不変条件 (4)）。"""
        emp = "abc-xyz-end-with-z"
        first = derive_mock_response(emp)
        second = derive_mock_response(emp)
        assert first == second

    def test_hash_normalization_matches_formula(self) -> None:
        """SHA-256 digest 最下位バイト mod 10 の計算が ADR-0010 §6.2.1 と一致する。"""
        emp = "abc-xyz-end-with-z"
        digest = hashlib.sha256(emp.encode("utf-8")).digest()
        expected_digit = digest[0] % 10
        assert derive_mock_response(emp) == _DIGIT_TO_MOCK[expected_digit]

    def test_fullwidth_digit_falls_through_hash_normalization(self) -> None:
        """全角数字 ``０`` 末尾は ASCII 数字ではないので hash 正規化される。

        第 17 原則 対称性推論：「ASCII 数字なら 0〜9」と「末尾が ``０`` のような
        非 ASCII 数字なら hash 経路」を双方向に固定する anchor テスト。
        """
        emp = "EMP-\uff10"  # 末尾が全角 "０"
        digest = hashlib.sha256(emp.encode("utf-8")).digest()
        expected_digit = digest[0] % 10
        assert derive_mock_response(emp) == _DIGIT_TO_MOCK[expected_digit]


# --- 異常系（第 19 原則 (b) フォールバック禁止） ---------------------


class TestValueError:
    """空文字列 / 非 ``str`` 入力は ``ValueError`` raise。"""

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            derive_mock_response("")

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="must be str"):
            derive_mock_response(None)  # type: ignore[arg-type]

    def test_int_raises(self) -> None:
        with pytest.raises(ValueError, match="must be str"):
            derive_mock_response(123)  # type: ignore[arg-type]
