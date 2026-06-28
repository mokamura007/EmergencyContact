"""Property 20 — Logical-delete + phone NULL-out PBT (Phase 13.20).

Validates: Requirements 15.3 (削除時の電話番号無効化と対象者除外), 15.4
    (deleted=true の社員レコードを一覧 / 詳細取得対象から除外).

Target:
    - ``lambdas.employee_api.handler._delete_employee`` — logical-delete
      handler (sets ``deleted=True`` + ``phoneNumber=""``).
    - Round-trip into:
        - ``lambdas.load_targets.handler.lambda_handler`` (ALL mode) — must
          drop the deleted row via ``is_visible``.
        - ``lambdas.inbound_handler.handler._lookup_employee_by_phone`` —
          must return ``None`` for the pre-delete phone number.

Approach (DynamoDB mock via in-memory fake):
    A small ``_FakeEmployeeTable`` implements just enough of the boto3
    ``DynamoDB.Table`` surface to drive the three handlers end-to-end:
        * ``put_item(Item=...)``                                 (seeding)
        * ``get_item(Key=...)``                                  (handler reads)
        * ``update_item(Key=..., UpdateExpression=..., ...)``    (the delete
          path's SET ... REMOVE ... shape)
        * ``query(IndexName="PhoneNumberIndex", KeyConditionExpression=...,
                  ExpressionAttributeValues=...)``               (GSI lookup)
        * ``scan(ExclusiveStartKey=...)``                        (LoadTargets)
    The same fake instance is monkey-patched into all three handler
    modules so the delete write becomes visible to the readers without
    any real AWS endpoint.

    Why a hand-rolled fake instead of ``moto``? ``moto`` is not currently
    listed in ``backend/pyproject.toml`` ``[dependency-groups].dev`` and
    introducing a new dev dependency is out of scope for this task. The
    fake covers exactly the four call shapes the three handlers issue;
    deviating call shapes would surface as ``NotImplementedError`` rather
    than silently passing, so the test cannot drift undetected.

Named properties (5; aligned with task tasks.md L1058 "Done When"):
    P1 (post_delete_invisible):
        After ``_delete_employee(victim_id)``,
        ``is_visible(table.get_item(victim_id))`` is False.

    P2 (post_delete_no_phone_lookup):
        After delete, ``_lookup_employee_by_phone(orig_phone)`` is None.

    P3 (post_delete_not_in_load_targets):
        After delete, ``load_targets(ALL).targets`` does not contain
        ``victim_id``. Pre-existing other-employees remain present.

    P4 (time_budget_5s):
        Wall-clock time of ``_delete_employee(victim_id)`` is < 5 seconds.

    P5 (pre_delete_symmetric_visibility):
        BEFORE delete, the three checks all agree the victim IS visible
        / findable / present. Validates 第17原則 (対称性推論): the post-
        condition is meaningful only if the pre-condition is the opposite.

Anchored examples (``@example`` pin, exercised in addition to random draws):
    - shortest E.164 (``+1``)
    - longest E.164 (``+`` + 15 digits)
    - single-employee table (just the victim)
    - many-employee table (the victim plus several distinct phones)

Scope notes (副次的発見 / A 採用方針):
    * The handler sets ``phoneNumber=""`` (empty string), not literal
      ``None``. This is the design-intended sentinel — ``shared.employee.
      visibility.is_visible`` treats the empty string as "no phone" and
      the value also drops out of the ``PhoneNumberIndex`` GSI because
      DynamoDB indexes silently exclude items whose indexed attribute is
      an empty string (handled by the fake's ``query`` predicate).
    * ``deleted=True`` blocks ``is_visible`` even before the phone field
      is consulted, so both guards are independently checked.
    * The handler also issues ``REMOVE cognitoSub``; the fake's
      ``update_item`` honours that, but the property suite does not seed
      ``cognitoSub`` so this branch is just exercised, not asserted.
    * Existing example-level coverage for ``_delete_employee`` is not
      present in ``tests/lambdas/employee_api/`` at the time of writing;
      Property 20 is therefore the first behavioural pin for the delete
      handler. Tasks listing maintainers may add an example file later;
      this PBT is independent and complementary.
"""

# ruff: noqa: N803
#   FakeEmployeeTable mirrors the boto3 ``DynamoDB.Table`` method signatures,
#   which use PascalCase keyword arguments (``Key``, ``UpdateExpression``,
#   ``ExpressionAttributeValues`` ...). Renaming them to snake_case would
#   break call-site compatibility with the production handler code.

from __future__ import annotations

import time
from typing import Any, cast

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from lambdas.employee_api import handler as employee_handler
from lambdas.inbound_handler import handler as inbound_handler
from lambdas.load_targets import handler as load_targets_handler
from shared.employee.visibility import is_visible
from tests.strategies import E164_MAX_DIGITS, e164_phone

# Hypothesis settings: DynamoDB-mock invocations make each example more
# expensive than a pure-function call, so cap at 100 examples per
# property (task spec). ``HealthCheck.too_slow`` is suppressed because
# the time-budget property (P4) intentionally measures wall-clock.
PBT_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.function_scoped_fixture,
    ],
)

# Requirement 15.3 wall-clock budget for the delete + lookup round-trip.
_DELETE_TIME_BUDGET_SEC = 5.0


# ---------------------------------------------------------------------------
# In-memory FakeEmployeeTable — minimal subset of boto3 DynamoDB.Table.
# ---------------------------------------------------------------------------


class _FakeEmployeeTable:
    """In-memory drop-in for the boto3 Employee DynamoDB Table.

    Implements only the four call shapes the EmployeeApi / LoadTargets /
    InboundHandler handlers actually issue against the Employee table.
    Any deviation surfaces as ``NotImplementedError``.

    Storage model:
        ``self._items: dict[employeeId, dict]`` keyed by ``employeeId``
        (the table's HASH key). Items are stored as plain Python dicts;
        ``boolean``, ``string``, and ``None`` are passed through
        verbatim (the real Table resource auto-marshals into AttributeValue
        shapes, which the handler code never inspects).
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    # ---- seeding / direct introspection (test-only API) -------------------

    def seed(self, items: list[dict[str, Any]]) -> None:
        """Reset the table to exactly the given items.

        Hypothesis re-uses the (function-scoped) fixture across all draws
        of a single property, so a destructive reset is required at the
        start of each example to prevent prior draws from leaking state
        into the current one. The seed semantics are therefore "clear +
        bulk insert", not "additive insert".
        """
        self._items.clear()
        for item in items:
            self._items[item["employeeId"]] = dict(item)

    def raw_get(self, employee_id: str) -> dict[str, Any] | None:
        """Direct fetch bypassing the boto3-shape wrapper."""
        item = self._items.get(employee_id)
        return dict(item) if item is not None else None

    # ---- boto3-shape surface --------------------------------------------

    def put_item(self, *, Item: dict[str, Any]) -> dict[str, Any]:
        eid = Item.get("employeeId")
        if not isinstance(eid, str) or not eid:
            raise ValueError("FakeEmployeeTable.put_item requires non-empty employeeId")
        self._items[eid] = dict(Item)
        return {}

    def get_item(self, *, Key: dict[str, Any]) -> dict[str, Any]:
        eid = Key.get("employeeId")
        if not isinstance(eid, str):
            raise ValueError("FakeEmployeeTable.get_item requires str employeeId Key")
        item = self._items.get(eid)
        if item is None:
            return {}
        return {"Item": dict(item)}

    def update_item(  # noqa: PLR0912
        self,
        *,
        Key: dict[str, Any],
        UpdateExpression: str,
        ExpressionAttributeValues: dict[str, Any] | None = None,
        ExpressionAttributeNames: dict[str, str] | None = None,
        **_unused: Any,
    ) -> dict[str, Any]:
        """Apply a minimal subset of UpdateExpression: SET ... [REMOVE ...].

        Supports the shapes used by ``_delete_employee`` and
        ``_update_employee`` / ``_create_employee``:

            SET attr1 = :v1, attr2 = :v2 [, ...] [REMOVE attrA[, attrB, ...]]

        ``ExpressionAttributeNames`` placeholders (``#name`` → ``"name"``)
        are resolved against the ``ExpressionAttributeNames`` mapping.

        Any other shape (ADD / DELETE / list_append / if_not_exists) raises
        ``NotImplementedError`` so future handler changes can't silently
        bypass the fake.
        """
        eid = Key.get("employeeId")
        if not isinstance(eid, str) or eid not in self._items:
            # The real DynamoDB would upsert; the EmployeeApi handler always
            # reads-before-update, so an unknown key here means a test bug.
            raise KeyError(f"FakeEmployeeTable.update_item: unknown employeeId={eid!r}")

        values = ExpressionAttributeValues or {}
        names = ExpressionAttributeNames or {}

        # Split SET ... [REMOVE ...] clauses. DynamoDB allows either order
        # but the handler always writes SET first, REMOVE second.
        set_clause = ""
        remove_clause = ""
        expr = UpdateExpression.strip()
        upper_expr = expr.upper()
        remove_idx = upper_expr.find("REMOVE ")
        if remove_idx == -1:
            if upper_expr.startswith("SET "):
                set_clause = expr[4:].strip()
            else:
                raise NotImplementedError(
                    f"FakeEmployeeTable.update_item: unsupported UpdateExpression: {expr!r}"
                )
        else:
            if not upper_expr.startswith("SET "):
                raise NotImplementedError(
                    f"FakeEmployeeTable.update_item: unsupported UpdateExpression: {expr!r}"
                )
            set_clause = expr[4:remove_idx].strip().rstrip(",").strip()
            remove_clause = expr[remove_idx + len("REMOVE "):].strip()

        item = self._items[eid]

        # --- SET ---
        if set_clause:
            for assignment in _split_top_level_commas(set_clause):
                stmt = assignment.strip()
                if "=" not in stmt:
                    raise NotImplementedError(
                        f"FakeEmployeeTable.update_item: bad SET fragment: {stmt!r}"
                    )
                lhs, rhs = (s.strip() for s in stmt.split("=", 1))
                attr_name = names[lhs] if lhs.startswith("#") else lhs
                if rhs.startswith(":"):
                    if rhs not in values:
                        raise KeyError(
                            f"FakeEmployeeTable.update_item: missing value for {rhs!r}"
                        )
                    item[attr_name] = values[rhs]
                else:
                    raise NotImplementedError(
                        f"FakeEmployeeTable.update_item: unsupported RHS: {rhs!r}"
                    )

        # --- REMOVE ---
        if remove_clause:
            for raw_attr in remove_clause.split(","):
                attr = raw_attr.strip()
                attr_name = names[attr] if attr.startswith("#") else attr
                item.pop(attr_name, None)

        return {}

    def query(
        self,
        *,
        IndexName: str | None = None,
        KeyConditionExpression: Any = None,
        ExpressionAttributeValues: dict[str, Any] | None = None,
        **_unused: Any,
    ) -> dict[str, Any]:
        """Support the two GSI ``PhoneNumberIndex`` query shapes used by:

        * ``employee_api._phone_already_registered`` — string-form expression
          ``"phoneNumber = :p"`` + ``ExpressionAttributeValues={":p": phone}``.
        * ``inbound_handler._lookup_employee_by_phone`` — boto3 ``Key`` /
          ``Attr`` builder form ``Key("phoneNumber").eq(caller_number)``.

        Items with ``phoneNumber == ""`` are excluded — the real DynamoDB
        ``PhoneNumberIndex`` GSI silently drops rows whose indexed attribute
        is empty, so the fake mirrors that to validate Requirement 15.3.
        """
        if IndexName != "PhoneNumberIndex":
            raise NotImplementedError(
                f"FakeEmployeeTable.query: unsupported IndexName={IndexName!r}"
            )

        target_phone = _resolve_phone_condition(
            KeyConditionExpression, ExpressionAttributeValues or {}
        )
        matches: list[dict[str, Any]] = []
        for item in self._items.values():
            phone = item.get("phoneNumber")
            # GSI rule: empty / non-str phoneNumber drops the row from the index.
            if not isinstance(phone, str) or phone == "":
                continue
            if phone == target_phone:
                matches.append(dict(item))
        return {"Items": matches}

    def scan(
        self,
        *,
        ExclusiveStartKey: dict[str, Any] | None = None,
        **_unused: Any,
    ) -> dict[str, Any]:
        """Single-page Scan response.

        LoadTargets handles ``LastEvaluatedKey`` paging, but the fake always
        returns the whole table in one page (``LastEvaluatedKey`` absent).
        """
        # ExclusiveStartKey arrives only on second pass which we never reach.
        _ = ExclusiveStartKey
        return {"Items": [dict(item) for item in self._items.values()]}


def _split_top_level_commas(s: str) -> list[str]:
    """Split a string on top-level commas (no nested expressions in handler)."""
    # The handler's UpdateExpressions never contain function calls / nested
    # commas; simple split is sufficient.
    return [part for part in s.split(",") if part.strip()]


def _resolve_phone_condition(
    condition: Any, values: dict[str, Any]
) -> str:
    """Extract the target phone number from a KeyConditionExpression.

    Handles two forms:
        * ``str``  — e.g. ``"phoneNumber = :p"`` (employee_api shape) →
          read ``values[":p"]``.
        * boto3 ``ConditionBase`` — ``Key("phoneNumber").eq(value)`` shape →
          read ``condition.get_expression()["values"][1]``.
    """
    if isinstance(condition, str):
        # employee_api shape: caller passes ExpressionAttributeValues with
        # exactly one ``":p"`` entry. Be liberal — accept any single value.
        if len(values) != 1:
            raise NotImplementedError(
                "FakeEmployeeTable.query: string KeyConditionExpression must "
                "carry exactly one ExpressionAttributeValues entry"
            )
        return str(next(iter(values.values())))

    # boto3 Key().eq() shape.
    get_expr = getattr(condition, "get_expression", None)
    if get_expr is None:
        raise NotImplementedError(
            f"FakeEmployeeTable.query: unsupported condition shape: {type(condition)!r}"
        )
    expr = get_expr()
    # Returned shape: {"operator": "=", "values": (Key, target)}
    target = expr["values"][1]
    return str(target)


# ---------------------------------------------------------------------------
# Fixtures: wire the fake into all three handler modules.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_table(monkeypatch: pytest.MonkeyPatch) -> _FakeEmployeeTable:
    """Bind a single ``_FakeEmployeeTable`` into employee_api / load_targets
    / inbound_handler so the delete round-trip is observable end-to-end."""
    table = _FakeEmployeeTable()
    monkeypatch.setattr(employee_handler, "_TABLE", table)
    monkeypatch.setattr(load_targets_handler, "_EMPLOYEE_TABLE", table)
    monkeypatch.setattr(inbound_handler, "_EMPLOYEE_TABLE", table)
    return table


# ---------------------------------------------------------------------------
# Strategies (local — strong contextual coupling to Property 20).
# ---------------------------------------------------------------------------

# Employee name: non-empty ASCII string short enough not to dominate runtime.
_employee_name = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=20,
)

#: Stable employee-id generator (uuid-like but deterministic for shrinking).
_employee_id = st.text(
    alphabet="0123456789abcdef", min_size=8, max_size=24
).map(lambda s: f"emp-{s}")


@st.composite
def _employee_set_with_victim(
    draw: st.DrawFn,
) -> tuple[list[dict[str, Any]], str, str]:
    """Draw a non-empty list of employees with one designated as the
    victim of deletion.

    Returns:
        (employees, victim_id, victim_phone)

    Invariants:
        * All ``employeeId`` values are unique.
        * All ``phoneNumber`` values are unique (the EmployeeApi
          duplicate-check enforces this on real writes; mirroring it
          keeps the fake aligned with production semantics).
        * The victim is one of the employees (by id).
    """
    n_others = draw(st.integers(min_value=0, max_value=8))
    n_total = n_others + 1

    ids = draw(
        st.lists(_employee_id, min_size=n_total, max_size=n_total, unique=True)
    )
    phones = draw(
        st.lists(e164_phone, min_size=n_total, max_size=n_total, unique=True)
    )
    names = draw(
        st.lists(_employee_name, min_size=n_total, max_size=n_total)
    )

    employees: list[dict[str, Any]] = []
    for eid, phone, name in zip(ids, phones, names, strict=True):
        employees.append(
            {
                "employeeId": eid,
                "name": name,
                "phoneNumber": phone,
                "role": "employee",
                "deleted": False,
                "createdAt": "2026-06-01T00:00:00Z",
                "updatedAt": "2026-06-01T00:00:00Z",
            }
        )

    victim_index = draw(st.integers(min_value=0, max_value=n_total - 1))
    victim_id = employees[victim_index]["employeeId"]
    victim_phone = employees[victim_index]["phoneNumber"]
    return employees, victim_id, victim_phone


# Anchored boundary cases (exercised in addition to random draws).
_ONLY_VICTIM_SHORTEST_E164: tuple[list[dict[str, Any]], str, str] = (
    [
        {
            "employeeId": "emp-only",
            "name": "Only Victim",
            "phoneNumber": "+1",
            "role": "employee",
            "deleted": False,
            "createdAt": "2026-06-01T00:00:00Z",
            "updatedAt": "2026-06-01T00:00:00Z",
        }
    ],
    "emp-only",
    "+1",
)

_LONGEST_E164_PHONE = "+" + "9" * E164_MAX_DIGITS
_VICTIM_AMONG_MANY_LONGEST_E164: tuple[list[dict[str, Any]], str, str] = (
    [
        {
            "employeeId": "emp-other-1",
            "name": "Other One",
            "phoneNumber": "+11111",
            "role": "employee",
            "deleted": False,
            "createdAt": "2026-06-01T00:00:00Z",
            "updatedAt": "2026-06-01T00:00:00Z",
        },
        {
            "employeeId": "emp-victim",
            "name": "Victim",
            "phoneNumber": _LONGEST_E164_PHONE,
            "role": "employee",
            "deleted": False,
            "createdAt": "2026-06-01T00:00:00Z",
            "updatedAt": "2026-06-01T00:00:00Z",
        },
        {
            "employeeId": "emp-other-2",
            "name": "Other Two",
            "phoneNumber": "+22222",
            "role": "employee",
            "deleted": False,
            "createdAt": "2026-06-01T00:00:00Z",
            "updatedAt": "2026-06-01T00:00:00Z",
        },
    ],
    "emp-victim",
    _LONGEST_E164_PHONE,
)


# ---------------------------------------------------------------------------
# Helpers used by every property body.
# ---------------------------------------------------------------------------


def _seed_and_delete(
    table: _FakeEmployeeTable, employees: list[dict[str, Any]], victim_id: str
) -> tuple[float, dict[str, Any]]:
    """Seed the table, run delete handler, return (elapsed_sec, response)."""
    table.seed(employees)
    start = time.perf_counter()
    response = employee_handler._delete_employee(
        {"id": victim_id}, principal="test-principal"
    )
    elapsed = time.perf_counter() - start
    return elapsed, response


def _load_targets_all(victim_cycle_id: str = "cycle-test") -> list[dict[str, Any]]:
    """Invoke LoadTargets in ALL mode against the seeded table."""
    result = load_targets_handler.lambda_handler(
        {"cycleId": victim_cycle_id, "mode": "ALL"}, None
    )
    return cast(list[dict[str, Any]], result["targets"])


# ---------------------------------------------------------------------------
# P1 — post_delete_invisible.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=_ONLY_VICTIM_SHORTEST_E164)
@example(case=_VICTIM_AMONG_MANY_LONGEST_E164)
@given(case=_employee_set_with_victim())
def test_property20_post_delete_invisible(
    fake_table: _FakeEmployeeTable,
    case: tuple[list[dict[str, Any]], str, str],
) -> None:
    """Post-delete: ``is_visible(table.get_item(victim))`` is False.

    The handler sets ``deleted=True`` and ``phoneNumber=""``. Either flip
    independently forces ``is_visible`` to False (Property 2); asserting
    on the read-back row pins the delete handler's contract end-to-end.

    Validates: Requirements 15.4 (一覧 / 詳細取得対象から除外).
    """
    employees, victim_id, _phone = case
    _seed_and_delete(fake_table, employees, victim_id)

    stored = fake_table.raw_get(victim_id)
    assert stored is not None, "deleted row must remain present (logical delete)"
    assert is_visible(stored) is False, (
        f"deleted row still visible: stored={stored!r}"
    )
    # Stronger pins on the post-state: both invariants Property 20 promises.
    assert stored.get("deleted") is True, (
        f"deleted flag not set: stored={stored!r}"
    )
    assert stored.get("phoneNumber") == "", (
        f"phoneNumber not nulled out: stored={stored!r}"
    )


# ---------------------------------------------------------------------------
# P2 — post_delete_no_phone_lookup.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=_ONLY_VICTIM_SHORTEST_E164)
@example(case=_VICTIM_AMONG_MANY_LONGEST_E164)
@given(case=_employee_set_with_victim())
def test_property20_post_delete_no_phone_lookup(
    fake_table: _FakeEmployeeTable,
    case: tuple[list[dict[str, Any]], str, str],
) -> None:
    """Post-delete: ``_lookup_employee_by_phone(orig_phone)`` is None.

    Both guards in InboundHandler must agree: the GSI no longer returns
    the row (phoneNumber="" dropped from PhoneNumberIndex), and even if
    it did, ``is_visible`` would reject ``deleted=True``.

    Validates: Requirements 15.3 (削除後の findEmployeeByPhone は当該レコードを返さない).
    """
    employees, victim_id, victim_phone = case
    _seed_and_delete(fake_table, employees, victim_id)

    result = inbound_handler._lookup_employee_by_phone(victim_phone)
    assert result is None, (
        f"deleted employee still found by phone {victim_phone!r}: {result!r}"
    )


# ---------------------------------------------------------------------------
# P3 — post_delete_not_in_load_targets.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=_VICTIM_AMONG_MANY_LONGEST_E164)
@given(case=_employee_set_with_victim())
def test_property20_post_delete_not_in_load_targets(
    fake_table: _FakeEmployeeTable,
    case: tuple[list[dict[str, Any]], str, str],
) -> None:
    """Post-delete: ``load_targets(ALL).targets`` does not contain victim.

    Other employees in the same table must remain present (the delete
    must be precisely scoped). When the victim was the only employee,
    LoadTargets raises ``NoTargetsError`` — that is also a correct
    expression of "victim not in targets".

    Validates: Requirements 15.3 (削除後の loadTargets は当該レコードを返さない).
    """
    employees, victim_id, _phone = case
    other_ids = [e["employeeId"] for e in employees if e["employeeId"] != victim_id]

    _seed_and_delete(fake_table, employees, victim_id)

    if not other_ids:
        # Only the victim existed: LoadTargets must raise NoTargetsError.
        with pytest.raises(load_targets_handler.NoTargetsError):
            _load_targets_all()
        return

    targets = _load_targets_all()
    target_ids = {t["employeeId"] for t in targets}
    assert victim_id not in target_ids, (
        f"deleted victim {victim_id!r} still in LoadTargets output: "
        f"targets={target_ids!r}"
    )
    # All non-victim employees remain present (delete must not collateral-drop).
    assert target_ids == set(other_ids), (
        f"non-victim employees missing from LoadTargets: "
        f"got={target_ids!r} expected={set(other_ids)!r}"
    )


# ---------------------------------------------------------------------------
# P4 — time_budget_5s.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=_ONLY_VICTIM_SHORTEST_E164)
@example(case=_VICTIM_AMONG_MANY_LONGEST_E164)
@given(case=_employee_set_with_victim())
def test_property20_time_budget_5s(
    fake_table: _FakeEmployeeTable,
    case: tuple[list[dict[str, Any]], str, str],
) -> None:
    """Wall-clock budget: ``_delete_employee`` finishes in < 5 seconds.

    Requirement 15.3 mandates the delete-and-propagate cycle complete
    within 5 s. The fake table is in-memory so the measurement is a
    floor on real performance — if the in-memory path blows the budget,
    the production path certainly would.

    Validates: Requirements 15.3 (5 秒以内タイムバジェット).
    """
    employees, victim_id, _phone = case
    elapsed, response = _seed_and_delete(fake_table, employees, victim_id)
    assert response["statusCode"] == 200, (
        f"unexpected non-200 delete response: {response!r}"
    )
    assert elapsed < _DELETE_TIME_BUDGET_SEC, (
        f"_delete_employee exceeded {_DELETE_TIME_BUDGET_SEC}s budget: "
        f"elapsed={elapsed:.3f}s"
    )


# ---------------------------------------------------------------------------
# P5 — pre_delete_symmetric_visibility (第17原則 対称性推論).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(case=_ONLY_VICTIM_SHORTEST_E164)
@example(case=_VICTIM_AMONG_MANY_LONGEST_E164)
@given(case=_employee_set_with_victim())
def test_property20_pre_delete_symmetric_visibility(
    fake_table: _FakeEmployeeTable,
    case: tuple[list[dict[str, Any]], str, str],
) -> None:
    """Pre-delete (對): victim is visible / findable / present.

    第17原則 (対称性推論): the post-delete invariants P1..P3 only carry
    weight if the pre-state asserts the opposite. This property pins
    that the test setup itself is sane — every "after delete" property
    has a matching "before delete" condition that the test would otherwise
    silently fail to falsify.

    Validates: Requirements 15.3, 15.4 (削除前は対象として可視).
    """
    employees, victim_id, victim_phone = case
    fake_table.seed(employees)

    # Pre-condition A: is_visible(victim) == True.
    stored = fake_table.raw_get(victim_id)
    assert stored is not None
    assert is_visible(stored) is True, (
        f"pre-delete victim already invisible: stored={stored!r}"
    )

    # Pre-condition B: phone lookup returns the victim.
    looked_up = inbound_handler._lookup_employee_by_phone(victim_phone)
    assert looked_up is not None, (
        f"pre-delete: phone lookup found no row for {victim_phone!r}"
    )
    assert looked_up.get("employeeId") == victim_id, (
        f"pre-delete: phone lookup returned wrong employee: {looked_up!r}"
    )

    # Pre-condition C: LoadTargets ALL contains the victim.
    targets = _load_targets_all()
    target_ids = {t["employeeId"] for t in targets}
    assert victim_id in target_ids, (
        f"pre-delete victim {victim_id!r} missing from LoadTargets: "
        f"targets={target_ids!r}"
    )


# ---------------------------------------------------------------------------
# Unit anchors — non-Hypothesis pins for highest-signal cases.
# ---------------------------------------------------------------------------


def test_unit_delete_then_recreate_blocked_by_phone_index_drop(
    fake_table: _FakeEmployeeTable,
) -> None:
    """After deleting a row, its phone number is free for a new employee.

    Direct consequence of Requirement 15.3: ``phoneNumber=""`` removes the
    row from ``PhoneNumberIndex``, so ``_phone_already_registered(orig)``
    returns False and a new employee can claim the phone. The handler
    code path uses the same query helper; we exercise it directly to
    pin the GSI-drop semantics.
    """
    employees, victim_id, victim_phone = _VICTIM_AMONG_MANY_LONGEST_E164
    _seed_and_delete(fake_table, list(employees), victim_id)
    assert employee_handler._phone_already_registered(victim_phone) is False, (
        f"phone {victim_phone!r} still flagged as registered after delete"
    )


def test_unit_delete_404_on_missing_employee(
    fake_table: _FakeEmployeeTable,
) -> None:
    """Delete on a non-existent employeeId returns 404 (no state mutation).

    Anchored boundary: validates the read-before-write guard in
    ``_delete_employee`` so a delete on missing key does not silently
    corrupt the table's other rows.
    """
    employees, _victim, _phone = _VICTIM_AMONG_MANY_LONGEST_E164
    fake_table.seed(list(employees))
    pre_snapshot = {
        eid: dict(fake_table.raw_get(eid) or {})
        for eid in (e["employeeId"] for e in employees)
    }
    response = employee_handler._delete_employee(
        {"id": "emp-does-not-exist"}, principal="test-principal"
    )
    assert response["statusCode"] == 404, response
    post_snapshot = {
        eid: dict(fake_table.raw_get(eid) or {})
        for eid in (e["employeeId"] for e in employees)
    }
    assert pre_snapshot == post_snapshot, (
        f"missing-key delete mutated other rows: pre={pre_snapshot!r} "
        f"post={post_snapshot!r}"
    )


def test_unit_double_delete_is_idempotent_404(
    fake_table: _FakeEmployeeTable,
) -> None:
    """Re-deleting an already-deleted row returns 404 (idempotent guard).

    Anchored case for the ``existing.get("deleted", False)`` gate: a
    second delete must not flip phoneNumber back or otherwise mutate
    the row, because that would re-introduce the row into the GSI
    if a future regression weakens the check.
    """
    employees, victim_id, _phone = _VICTIM_AMONG_MANY_LONGEST_E164
    _seed_and_delete(fake_table, list(employees), victim_id)
    after_first = dict(fake_table.raw_get(victim_id) or {})

    second_response = employee_handler._delete_employee(
        {"id": victim_id}, principal="test-principal"
    )
    assert second_response["statusCode"] == 404, second_response
    after_second = dict(fake_table.raw_get(victim_id) or {})
    assert after_first == after_second, (
        f"second delete mutated row: first={after_first!r} "
        f"second={after_second!r}"
    )
