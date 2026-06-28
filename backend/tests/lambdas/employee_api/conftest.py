"""Local conftest for the EmployeeApi Lambda tests.

The handler reads ``EMPLOYEE_TABLE_NAME`` / ``COGNITO_USER_POOL_ID`` at
module-import time (Phase 5 convention). The Property 20 PBT does not
talk to a real DynamoDB or Cognito endpoint — it swaps
``handler._TABLE`` etc. for an in-memory fake per test — but the env
vars must still be set before the import succeeds, so they are seeded
here at conftest discovery time.

Phase 12.3 adds ``AUDIT_LOG_GROUP_NAME`` and an autouse fixture that
swaps ``shared.audit.logger._LOGS_CLIENT`` for a :class:`MagicMock` so
audit emits never reach real CloudWatch Logs endpoints.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("EMPLOYEE_TABLE_NAME", "Employee-test")
os.environ.setdefault(
    "COGNITO_USER_POOL_ID", "ap-northeast-1_TESTPOOL000"
)
os.environ.setdefault("ADMIN_GROUP_NAME", "Administrator")
# Required by load_targets handler when imported in the same test process.
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
# Required by inbound_handler handler when imported in the same test process.
os.environ.setdefault("CYCLE_TABLE_NAME", "Cycle-test")
os.environ.setdefault(
    "INBOUND_CONTACT_TABLE_NAME", "InboundContact-test"
)
# Phase 12.3: write_audit_log reads AUDIT_LOG_GROUP_NAME at call time.
os.environ.setdefault(
    "AUDIT_LOG_GROUP_NAME", "/aws/safety-confirmation/audit-test"
)
# Task 15.12: EmployeeApi anonymize handler reads this at call time so
# the empty-string fail-fast (503) can be exercised without unsetting
# the variable. Tests that need the variable cleared monkeypatch it
# locally.
os.environ.setdefault("EMPLOYEE_ANONYMIZE_SALT", "test-anonymize-salt-fixture")


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
