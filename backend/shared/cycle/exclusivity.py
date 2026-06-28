"""Pure helper for CycleApi exclusivity guard (Requirement 4.8, Property 9).

The CycleApi Lambda (``backend/lambdas/cycle_api/handler.py``) refuses to
start a new cycle while at least one Cycle row in DynamoDB has
``status == "RUNNING"``. The handler queries
``StatusStartedAtIndex`` (``status = RUNNING``) via the I/O helper
``_query_running_cycles()`` and then defers the *decision* to this
pure function so Phase 13.9 PBT can verify the exclusivity contract
under Hypothesis without spinning up a DynamoDB mock.

Contract (design.md Property 9)::

    canStartCycle(C) returns True
        iff |{c ∈ C : c.get("status") == "RUNNING"}| == 0

That is, the function filters the input set ``C`` by literal
``status == "RUNNING"`` and returns True only when the filtered count
is zero. Callers that already pre-filter (e.g. by querying
``StatusStartedAtIndex`` with ``status = RUNNING``) see identical
behavior — every element of a pre-filtered list satisfies
``status == "RUNNING"`` so the function returns True iff that list is
empty, which is the Done When stated in tasks.md 13.9.

Rows missing the ``status`` key, or with ``status`` set to ``None`` /
any non-string / any string other than ``"RUNNING"`` (e.g. lowercase
``"running"``, or future statuses ``"COMPLETED"`` / ``"TIMEOUT"`` /
``"START_FAILED"``), are NOT counted as running. Requirement 4.8 keys
exclusively off the literal ``RUNNING`` state ("実行中").

Misuse (non-list input, list element that is not a dict) raises
``TypeError`` rather than silently returning a plausible-looking
answer — this mirrors ``shared/cycle/finalize.py`` and conforms to
project principle 19(b) (no fallback hiding upstream bugs).
"""

from __future__ import annotations

from typing import Any

#: Cycle status value that must NOT coexist with a fresh start
#: (Requirement 4.8). Kept as a module-private constant so the literal
#: is grep-able and the PBT's independent oracle can use the same
#: string without circular import.
_RUNNING_STATUS = "RUNNING"


def _require_list_of_dict(
    value: object, field_name: str = "cycles"
) -> list[dict[str, Any]]:
    """Validate ``value`` is a ``list[dict]``; return it unchanged.

    Raises ``TypeError`` on structural problems so the handler surfaces
    a CloudWatch-visible exception (project principle 19(b)).
    """
    if not isinstance(value, list):
        raise TypeError(
            f"{field_name} must be a list; got {type(value).__name__}"
        )
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise TypeError(
                f"{field_name}[{idx}] must be a dict; "
                f"got {type(item).__name__}"
            )
    return value


def can_start_cycle(cycles: list[dict[str, Any]]) -> bool:
    """Return True iff no cycle in ``cycles`` is in ``RUNNING`` state.

    Property 9 (Phase 13.9 PBT candidate).

    Truth table::

        cycles == []                                       -> True
        ∀ c ∈ cycles: c.get("status") != "RUNNING"         -> True
        ∃ c ∈ cycles: c.get("status") == "RUNNING"         -> False

    Args:
        cycles: List of Cycle dicts. Typically the result of a DynamoDB
            ``Query`` against ``CycleTable`` via ``StatusStartedAtIndex``
            with ``status = RUNNING``, but the function accepts any list
            of dicts and filters internally — this is the contract from
            design.md Property 9.

    Returns:
        ``True`` when a new cycle may be started, ``False`` when at
        least one Cycle row already has ``status == "RUNNING"``.

    Raises:
        TypeError: ``cycles`` is not a list of dicts.
    """
    rows = _require_list_of_dict(cycles)
    return all(row.get("status") != _RUNNING_STATUS for row in rows)


__all__ = ("can_start_cycle",)
