"""Local conftest for the LoadTargets Lambda unit tests.

The Lambda handler reads ``EMPLOYEE_TABLE_NAME`` / ``RESPONSE_TABLE_NAME``
at module-import time (Phase 5 convention). These tests do not touch a
real DynamoDB endpoint — they replace ``handler._DDB`` / ``_EMPLOYEE_TABLE``
/ ``_RESPONSE_TABLE`` with :class:`unittest.mock.MagicMock` instances per
test — but the names must still be set before import succeeds, so they
are seeded here at conftest discovery time.
"""

from __future__ import annotations

import os

os.environ.setdefault("EMPLOYEE_TABLE_NAME", "Employee-test")
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
