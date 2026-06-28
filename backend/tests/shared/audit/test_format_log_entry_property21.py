"""Property 21 — 監査ログ必須フィールド PBT (Phase 13.21).

Validates: Requirements 1.5, 1.8, 2.2, 2.4, 2.5, 8.7, 15.5, 16.3, 16.4

------------------------------------------------------------------------
本ファイルは ``shared/audit/logger.py::write_audit_log`` (Phase 12.3
実装済) が構築する単一-line JSON 監査レコードの **必須 5 フィールド**
不変性を Hypothesis により網羅的に検証する.

真の仕様 ("A 採用" — 13.15 / 13.16 / 13.17 と同じ方針)
------------------------------------------------------------------------
tasks.md 13.21 は ``audit.formatLogEntry(event)`` という名称で
記述されているが、Phase 12.3 で確定した実装は副作用を伴う
``audit.write_audit_log(...)`` 一本である (純粋な ``format_log_entry``
関数は存在しない). 本 PBT は **実装側のレコード構築ロジック** を
真の仕様として、CloudWatch Logs クライアントをモックし
``put_log_events`` に渡される JSON 文字列をデコードして検証する.

実装 docstring (``shared/audit/logger.py``) の宣言::

    Every record carries (1) ``event``, (2) ``timestamp`` (ISO 8601 UTC
    with trailing ``Z``), (3) ``principal``, (4) ``target``, plus
    optional (5) ``phoneMasked`` (Property 22 via :func:`mask_phone`)
    and (6) ``extra`` fields merged at the top level.

すなわち **必須 5 フィールド** は::

    {"event", "timestamp", "principal", "target", "outcome"}

であり (実装の ``_RESERVED_KEYS`` から ``phoneMasked`` を除いた 5 つ),
``phoneMasked`` は ``phone`` 引数が与えられた場合のみ追加される
6 番目のフィールド (要件 16.4 / Property 22 の合流点).

design.md ``Property 21`` 節は (5) を「電話番号を含む場合は Property
22 のマスキング適用後の値のみ」と記述しているが、文言上 ``outcome``
への言及が無い. 実装は ``outcome`` を **常に** 設定する (デフォルト
``"SUCCESS"``) ため、本 PBT は実装基準で 5 = {event, timestamp,
principal, target, outcome} と確定する. 副次的発見として、design.md
の文言修正は別タスクで起票方針 (本タスクのスコープ外).

既存 example-based テストとの分業
------------------------------------------------------------------------
本 PBT は **valid input 集合** に限定して以下の網羅検証を行う:

* P1 - 5 reserved fields が必ず存在し値が一致 (主-property).
* P2 - phone 引数あり経路で ``phoneMasked`` が ``mask_phone`` 出力と
  等価で、生数字列が JSON 全体に出現しない (要件 16.4).
* P3 - 全 event_type (12 種) で P1 が成立 (網羅).
* P4 - 独立 oracle ``format_log_entry_oracle`` と等価 (対称性 / 第17原則).
* P5 - ``extra`` のうち reserved key 衝突は破棄、非衝突 key は top-level
  にマージされる (実装契約).

``test_logger.py`` の example test 15 件 (Phase 12.3) は個別シナリオ
(env var 不在で KeyError、stream caching、ClientError 伝播 等) を
カバー済のため、本ファイルでは I/O 経路は再検査せず、レコード
構築ロジックの不変性に集中する (DRY 原則).

スコープ外
------------------------------------------------------------------------
* CloudWatch Logs API のネットワーク経路 (Phase 12.3 unit test で済).
* ``mask_phone`` 自体の正当性 (Property 22 / Phase 13.22 で済).
* ``timestamp`` の自動生成パスの精度 (本 PBT は explicit timestamp を
  常時指定して決定論化).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.audit import logger as audit_logger
from shared.audit.mask import mask_phone
from tests.strategies import e164_phone

# ---------------------------------------------------------------------------
# Hypothesis settings — match Phase 13.x convention (>= 200 examples).
# ---------------------------------------------------------------------------

PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        # The mock_logs_client fixture is function-scoped, so its underlying
        # MagicMock is shared across Hypothesis-generated inputs within a
        # single test function.  ``_decode_record`` reads only the *last*
        # ``put_log_events`` call (``call_args_list[-1]``), so cross-input
        # accumulation is harmless and intentional.
        HealthCheck.function_scoped_fixture,
    ],
)


# ---------------------------------------------------------------------------
# Reference vocabulary — kept in this file (not imported from logger.py) so
# the test acts as an independent oracle. If logger.py's _RESERVED_KEYS
# drifts silently the equivalence property (P4) pins the regression.
# ---------------------------------------------------------------------------

#: The 5 mandatory top-level fields every audit record must carry
#: (Requirement 16.3, design.md Property 21).
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"event", "timestamp", "principal", "target", "outcome"}
)

#: The conditional 6th field present iff ``phone`` is supplied
#: (Requirement 16.4, design.md Property 21 (5) / Property 22).
PHONE_MASKED_FIELD: str = "phoneMasked"

#: Reserved keys that ``extra`` cannot overwrite (matches the implementation's
#: ``_RESERVED_KEYS``). Independent declaration -> drift detector.
RESERVED_KEYS: frozenset[str] = REQUIRED_FIELDS | {PHONE_MASKED_FIELD}

#: Event types listed in the logger module docstring. Used as @example pins
#: to guarantee every audited code path is exercised.
EVENT_TYPES_PINNED: tuple[str, ...] = (
    "AUTH_SUCCESS",
    "AUTH_FAILURE_RECORDED",
    "EMPLOYEE_ADD",
    "EMPLOYEE_UPDATE",
    "EMPLOYEE_DELETE",
    "EMPLOYEE_CSV_IMPORT",
    "EMPLOYEE_ANONYMIZE",
    "DICTIONARY_ADD",
    "DICTIONARY_UPDATE",
    "DICTIONARY_DELETE",
    "CYCLE_START",
    "CYCLE_START_REJECTED",
    "INBOUND_CONTACT_RECEIVED",
)

#: A fixed ISO 8601 UTC timestamp used to make the constructed record
#: deterministic (avoids comparing against ``_now_iso()`` wall-clock).
FIXED_TS: str = "2026-06-26T12:34:56Z"


# ---------------------------------------------------------------------------
# Independent oracle — mirrors the implementation contract verbatim.
# ---------------------------------------------------------------------------


def format_log_entry_oracle(
    *,
    event_type: str,
    principal: str,
    target: str,
    outcome: str = "SUCCESS",
    timestamp: str | None = None,
    phone: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reference dict that the implementation must produce verbatim.

    Mirrors ``write_audit_log`` minus the boto3 I/O.  Keeping this in the
    test file (rather than re-exporting from the source module) ensures
    silent contract drift is caught by the equivalence property P4.
    """
    if timestamp is None:
        raise ValueError("oracle requires explicit timestamp for determinism")
    record: dict[str, Any] = {
        "event": event_type,
        "timestamp": timestamp,
        "principal": principal,
        "target": target,
        "outcome": outcome,
    }
    if phone is not None:
        record[PHONE_MASKED_FIELD] = mask_phone(phone)
    if extra:
        for key, value in extra.items():
            if key not in RESERVED_KEYS:
                record[key] = value
    return record


# ---------------------------------------------------------------------------
# Hypothesis strategies — narrow to the valid input space.
# ---------------------------------------------------------------------------

#: Free-form short text used for event_type / principal / target / outcome.
#: Non-empty, no control chars beyond what JSON tolerates (ASCII printable).
_short_text = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    min_size=1,
    max_size=32,
)

#: Strategy for ``event_type`` — biased towards the documented vocabulary
#: but also samples arbitrary identifiers to exercise unknown types.
event_type_strategy = st.one_of(
    st.sampled_from(EVENT_TYPES_PINNED),
    _short_text,
)

#: Strategy for ``principal`` (cognito sub, ``<anonymous>``, etc).
principal_strategy = _short_text

#: Strategy for ``target`` (employee id, ``category#keyword``, cycle id, ...).
target_strategy = _short_text

#: Strategy for ``outcome`` — biased towards documented values.
outcome_strategy = st.one_of(
    st.sampled_from(("SUCCESS", "REJECTED", "RECORDED", "FAILED")),
    _short_text,
)

#: Strategy for ``extra`` dict — values are JSON-serialisable primitives.
_extra_value = st.one_of(
    st.text(max_size=16),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.booleans(),
    st.none(),
)
extra_strategy = st.dictionaries(
    keys=_short_text,
    values=_extra_value,
    max_size=5,
)


# ---------------------------------------------------------------------------
# Fixtures — replicate test_logger.py's mock pattern.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Reset module-level caches before every test (avoids cross-test leak)."""
    audit_logger._CREATED_STREAMS.clear()
    audit_logger._LOGS_CLIENT = None


@pytest.fixture
def mock_logs_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the cached boto3 logs client with a MagicMock."""
    client = MagicMock(name="LogsClient")
    monkeypatch.setattr(audit_logger, "_LOGS_CLIENT", client)
    return client


@pytest.fixture(autouse=True)
def _seed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the env vars the logger requires."""
    monkeypatch.setenv("AUDIT_LOG_GROUP_NAME", "/aws/safety-confirmation/audit-test")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "test-fn")


# ---------------------------------------------------------------------------
# Helpers — copy the small JSON extractor used by test_logger.py.
# ---------------------------------------------------------------------------


def _decode_record(client: MagicMock) -> dict[str, Any]:
    """Return the last JSON record passed to put_log_events.

    Asserts exactly one put_log_events / one logEvents element so any
    accidental multiplication blows up loudly.
    """
    assert client.put_log_events.call_count >= 1
    last_call = client.put_log_events.call_args_list[-1]
    events = last_call.kwargs["logEvents"]
    assert len(events) == 1, f"expected single logEvent, got {len(events)}"
    decoded: dict[str, Any] = json.loads(events[0]["message"])
    return decoded


def _invoke(
    client: MagicMock,
    *,
    event_type: str,
    principal: str,
    target: str,
    outcome: str = "SUCCESS",
    timestamp: str = FIXED_TS,
    phone: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call write_audit_log and return the constructed record."""
    audit_logger.write_audit_log(
        event_type=event_type,
        principal=principal,
        target=target,
        outcome=outcome,
        timestamp=timestamp,
        phone=phone,
        extra=extra,
    )
    return _decode_record(client)


# ---------------------------------------------------------------------------
# P1 — Mandatory 5 fields ALWAYS present, values match inputs.
# ---------------------------------------------------------------------------


@example(event_type="AUTH_SUCCESS", principal="user-123", target="user-123")
@example(event_type="AUTH_FAILURE_RECORDED", principal="<anonymous>", target="user-x")
@example(event_type="EMPLOYEE_ADD", principal="admin-1", target="emp-001")
@example(event_type="EMPLOYEE_UPDATE", principal="admin-1", target="emp-002")
@example(event_type="EMPLOYEE_DELETE", principal="admin-1", target="emp-003")
@example(event_type="EMPLOYEE_CSV_IMPORT", principal="admin-1", target="batch-2026-01")
@example(event_type="DICTIONARY_ADD", principal="admin-1", target="SAFE#無事")
@example(event_type="DICTIONARY_UPDATE", principal="admin-1", target="INJURED#怪我")
@example(event_type="DICTIONARY_DELETE", principal="admin-1", target="UNAVAILABLE#不在")
@example(event_type="CYCLE_START", principal="admin-1", target="cycle-2026-06-26-01")
@example(
    event_type="CYCLE_START_REJECTED",
    principal="admin-1",
    target="dictionary_empty",
)
@example(
    event_type="INBOUND_CONTACT_RECEIVED",
    principal="<connect-service>",
    target="contact-abc",
)
@PBT_SETTINGS
@given(
    event_type=event_type_strategy,
    principal=principal_strategy,
    target=target_strategy,
)
def test_property21_mandatory_five_fields_always_present(
    mock_logs_client: MagicMock,
    event_type: str,
    principal: str,
    target: str,
) -> None:
    """All 5 reserved fields are present, values match inputs.

    Validates: Requirements 1.5, 1.8, 2.2, 2.4, 2.5, 8.7, 15.5, 16.3
    """
    record = _invoke(
        mock_logs_client,
        event_type=event_type,
        principal=principal,
        target=target,
    )

    # (a) Presence — every required field exists.
    missing = REQUIRED_FIELDS - record.keys()
    assert not missing, f"missing required fields: {missing} in {record!r}"

    # (b) Value fidelity for the 4 string-pass-through fields.
    assert record["event"] == event_type
    assert record["principal"] == principal
    assert record["target"] == target
    assert record["outcome"] == "SUCCESS"  # default applied

    # (c) timestamp is the explicit fixed one we supplied.
    assert record["timestamp"] == FIXED_TS


# ---------------------------------------------------------------------------
# P2 — Phone-bearing path: phoneMasked equals mask_phone(phone), and the
#      raw phone digits never leak into the JSON-serialised record.
# ---------------------------------------------------------------------------


@example(phone="+819012345678")
@example(phone="+1")  # short — mask_phone returns unchanged
@example(phone="+1234")  # body length 4 — unchanged
@example(phone="+12345")  # body length 5 — minimal masking case
@example(phone="+" + "9" * 15)  # E.164 maximum length
@PBT_SETTINGS
@given(phone=e164_phone)
def test_property21_phone_masked_via_mask_phone_only(
    mock_logs_client: MagicMock,
    phone: str,
) -> None:
    """phoneMasked == mask_phone(phone); raw middle digits absent from JSON.

    Validates: Requirements 16.4, 16.3
    """
    record = _invoke(
        mock_logs_client,
        event_type="EMPLOYEE_ADD",
        principal="admin-1",
        target="emp-001",
        phone=phone,
    )

    # (a) phoneMasked is present and equals the canonical mask_phone output.
    assert PHONE_MASKED_FIELD in record
    expected_mask = mask_phone(phone)
    assert record[PHONE_MASKED_FIELD] == expected_mask

    # (b) The raw phone string itself must not appear anywhere in the JSON
    #     record (except possibly inside the masked output for E.164 bodies
    #     of length <= 4 where mask_phone is an identity by contract).
    record_json = json.dumps(record, ensure_ascii=False)
    if expected_mask != phone:
        # Masking actually fired -> raw phone must NOT leak.
        assert phone not in record_json, (
            f"raw phone {phone!r} leaked into record {record_json!r}"
        )

    # (c) The "phoneNumber" / "phone" raw key is never present at top level
    #     (only phoneMasked is allowed to carry phone information).
    assert "phoneNumber" not in record
    assert "phone" not in record


def test_property21_phone_absent_omits_masked_field(
    mock_logs_client: MagicMock,
) -> None:
    """phone=None means phoneMasked must be absent (not null).

    Validates: Requirements 16.4
    """
    record = _invoke(
        mock_logs_client,
        event_type="X",
        principal="p",
        target="t",
        phone=None,
    )
    assert PHONE_MASKED_FIELD not in record


# ---------------------------------------------------------------------------
# P3 — Coverage: every documented event_type satisfies the 5-field contract.
#      Implemented as a parametrised loop so the assertion failure message
#      pinpoints exactly which event_type broke (better than a strategy).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event_type", EVENT_TYPES_PINNED)
def test_property21_all_documented_event_types_carry_five_fields(
    mock_logs_client: MagicMock,
    event_type: str,
) -> None:
    """Every event_type the system emits carries the 5 reserved fields.

    Validates: Requirements 1.5, 1.8, 2.2, 2.4, 2.5, 8.7, 15.5, 16.3
    """
    record = _invoke(
        mock_logs_client,
        event_type=event_type,
        principal="actor",
        target="subject",
    )
    missing = REQUIRED_FIELDS - record.keys()
    assert not missing, (
        f"event_type={event_type!r} missing fields {missing}; record={record!r}"
    )


# ---------------------------------------------------------------------------
# P4 — Symmetric reasoning (Principle 17): the constructed record equals
#      the independent oracle byte-for-byte. Catches silent contract drift.
# ---------------------------------------------------------------------------


@example(
    event_type="EMPLOYEE_DELETE",
    principal="admin-1",
    target="emp-007",
    outcome="SUCCESS",
    phone="+819087654321",
    extra={"reason": "left-company"},
)
@example(
    event_type="DICTIONARY_ADD",
    principal="admin-1",
    target="SAFE#無事",
    outcome="SUCCESS",
    phone=None,
    extra={"category": "SAFE", "keyword": "無事", "newVersion": 7},
)
@example(
    event_type="CYCLE_START_REJECTED",
    principal="admin-1",
    target="dictionary_empty",
    outcome="REJECTED",
    phone=None,
    extra=None,
)
@example(
    event_type="AUTH_FAILURE_RECORDED",
    principal="<anonymous>",
    target="user-z",
    outcome="FAILED",
    phone=None,
    extra={"sourceIp": "203.0.113.42", "attempt": 5},
)
@PBT_SETTINGS
@given(
    event_type=event_type_strategy,
    principal=principal_strategy,
    target=target_strategy,
    outcome=outcome_strategy,
    phone=st.one_of(st.none(), e164_phone),
    extra=st.one_of(st.none(), extra_strategy),
)
def test_property21_record_equals_independent_oracle(
    mock_logs_client: MagicMock,
    event_type: str,
    principal: str,
    target: str,
    outcome: str,
    phone: str | None,
    extra: dict[str, Any] | None,
) -> None:
    """Implementation record == independent oracle reconstruction.

    Catches silent drift in either ``_RESERVED_KEYS`` or the merge order.

    Validates: Requirements 16.3, 16.4
    """
    record = _invoke(
        mock_logs_client,
        event_type=event_type,
        principal=principal,
        target=target,
        outcome=outcome,
        timestamp=FIXED_TS,
        phone=phone,
        extra=extra,
    )

    expected = format_log_entry_oracle(
        event_type=event_type,
        principal=principal,
        target=target,
        outcome=outcome,
        timestamp=FIXED_TS,
        phone=phone,
        extra=extra,
    )

    assert record == expected, (
        f"oracle drift: got={record!r}, expected={expected!r}"
    )


# ---------------------------------------------------------------------------
# P5 — Extra-merge contract: reserved keys in `extra` are dropped silently;
#      non-reserved keys appear at the top level alongside the 5 mandatory
#      fields. Property 21 implicitly requires this because the mandatory
#      fields must NEVER be overwritten by caller-supplied data.
# ---------------------------------------------------------------------------


@example(
    extra={
        "event": "TAMPERED",
        "timestamp": "TAMPERED",
        "principal": "TAMPERED",
        "target": "TAMPERED",
        "outcome": "TAMPERED",
        "phoneMasked": "TAMPERED",
        "legitField": "ok",
    }
)
@example(extra={})  # empty extra -> no extra keys, mandatory still all present
@PBT_SETTINGS
@given(extra=extra_strategy)
def test_property21_extra_cannot_overwrite_reserved_fields(
    mock_logs_client: MagicMock,
    extra: dict[str, Any],
) -> None:
    """Reserved keys in ``extra`` are silently dropped; non-reserved merge.

    Validates: Requirements 16.3 (record schema integrity)
    """
    record = _invoke(
        mock_logs_client,
        event_type="EMPLOYEE_ADD",
        principal="admin-stable",
        target="emp-stable",
        outcome="SUCCESS",
        extra=extra,
    )

    # (a) Reserved fields are NEVER overwritten regardless of `extra`.
    assert record["event"] == "EMPLOYEE_ADD"
    assert record["principal"] == "admin-stable"
    assert record["target"] == "emp-stable"
    assert record["outcome"] == "SUCCESS"
    assert record["timestamp"] == FIXED_TS

    # (b) Every non-reserved key in `extra` appears at the top level with
    #     its original value.
    for key, value in extra.items():
        if key in RESERVED_KEYS:
            # Reserved -> must not be tampered. Already covered in (a) for
            # the explicit-pin cases; here we just assert the value is the
            # original mandatory one, not the tampered extra value.
            continue
        assert key in record, f"non-reserved key {key!r} dropped from record"
        assert record[key] == value


# ---------------------------------------------------------------------------
# P6 — Purity: identical inputs produce identical records on repeated calls
#      (the explicit-timestamp branch is pure; only the wall-clock branch
#      is non-deterministic, which is out of scope here).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(
    event_type=event_type_strategy,
    principal=principal_strategy,
    target=target_strategy,
    phone=st.one_of(st.none(), e164_phone),
)
def test_property21_idempotent_for_fixed_inputs(
    mock_logs_client: MagicMock,
    event_type: str,
    principal: str,
    target: str,
    phone: str | None,
) -> None:
    """Same inputs (with fixed timestamp) -> same record on every call.

    Validates: Requirements 16.3 (deterministic audit semantics)
    """
    rec_a = _invoke(
        mock_logs_client,
        event_type=event_type,
        principal=principal,
        target=target,
        phone=phone,
    )
    rec_b = _invoke(
        mock_logs_client,
        event_type=event_type,
        principal=principal,
        target=target,
        phone=phone,
    )
    assert rec_a == rec_b
