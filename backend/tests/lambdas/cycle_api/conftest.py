"""Local conftest for the CycleApi Lambda unit tests.

The handler reads three environment variables at module-import time
(``CYCLE_TABLE_NAME``, ``KEYWORD_DICT_TABLE_NAME``, ``SFN_STATE_MACHINE_ARN``).
These tests don't talk to real AWS endpoints — they replace
``handler._CYCLE_TABLE`` / ``handler._DICT_TABLE`` / ``handler._SFN``
with :class:`unittest.mock.MagicMock` per test — but the names must
still be set before import succeeds, so they are seeded here.

Phase 12.3 adds ``AUDIT_LOG_GROUP_NAME`` and an autouse fixture that
swaps the ``shared.audit.logger._LOGS_CLIENT`` global for a
:class:`MagicMock` so audit emits never reach real AWS endpoints.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("CYCLE_TABLE_NAME", "Cycle-test")
os.environ.setdefault("KEYWORD_DICT_TABLE_NAME", "KeywordDictionary-test")
os.environ.setdefault(
    "SFN_STATE_MACHINE_ARN",
    "arn:aws:states:ap-northeast-1:000000000000:stateMachine:safety-confirmation-cycle-test",
)
# Phase 12.3: write_audit_log reads AUDIT_LOG_GROUP_NAME at call time.
os.environ.setdefault(
    "AUDIT_LOG_GROUP_NAME", "/aws/safety-confirmation/audit-test"
)
# Phase 15.27a: handler reads default retry parameters from env at import.
os.environ.setdefault("DEFAULT_RETRY_COUNT", "3")
os.environ.setdefault("DEFAULT_RETRY_INTERVAL_MINUTES", "5")


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
