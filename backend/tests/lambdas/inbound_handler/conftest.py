"""Local conftest for the InboundHandler Lambda unit tests (Phase 9.2).

The Lambda handler reads its environment at module-import time. The
tests swap the boto3 ``Table`` singletons via ``monkeypatch`` per
test, so these env vars only need to be syntactically present before
the import.

Phase 12.3 adds ``AUDIT_LOG_GROUP_NAME`` and an autouse fixture that
swaps the ``shared.audit.logger._LOGS_CLIENT`` global for a
:class:`MagicMock` so audit emits never reach real AWS endpoints.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("EMPLOYEE_TABLE_NAME", "Employee-test")
os.environ.setdefault("CYCLE_TABLE_NAME", "Cycle-test")
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault("INBOUND_CONTACT_TABLE_NAME", "InboundContact-test")
# Phase 15.27b: handler reads INBOUND_RECEPTION_WINDOW_DAYS at import time
# (no silent fallback per principle 19(b)). Seed the default (30) here so
# the import succeeds; individual tests can override via monkeypatch on
# the module-level constant when exercising different window sizes.
os.environ.setdefault("INBOUND_RECEPTION_WINDOW_DAYS", "30")
# Phase 12.3: write_audit_log reads AUDIT_LOG_GROUP_NAME at call time.
os.environ.setdefault(
    "AUDIT_LOG_GROUP_NAME", "/aws/safety-confirmation/audit-test"
)


@pytest.fixture(autouse=True)
def _mock_audit_logger(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the audit logger's boto3 client with a MagicMock.

    Prevents test runs from issuing real CloudWatch Logs API calls.
    Tests that want to inspect audit emissions can request this
    fixture by name and assert on ``mock.put_log_events.call_args``.
    """
    from shared.audit import logger as audit_logger

    audit_logger._CREATED_STREAMS.clear()
    mock_client = MagicMock(name="AuditLogsClient")
    monkeypatch.setattr(audit_logger, "_LOGS_CLIENT", mock_client)
    return mock_client
