"""Local conftest for the ConnectDispatcher Lambda unit tests.

The Lambda handler reads its environment at module-import time. The tests
swap the boto3 client / table singletons via ``monkeypatch`` per test, so
these env vars only need to be syntactically present before the import.
"""

from __future__ import annotations

import os

os.environ.setdefault("CONNECT_INSTANCE_ID", "test-instance-id")
os.environ.setdefault("OUTBOUND_CONTACT_FLOW_ID", "test-flow-id")
os.environ.setdefault("OUTBOUND_PHONE_NUMBER", "+810000000000")
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault(
    "SFN_STATE_MACHINE_ARN",
    "arn:aws:states:ap-northeast-1:111122223333:stateMachine:safety-confirmation-cycle-test",
)
# Mock-mode env (ADR-0010, Phase 16.2). Default is "false" so existing
# production-path tests are unaffected; mock-mode tests flip these via
# ``monkeypatch.setattr(handler, "_MOCK_MODE_ENABLED", True)``.
os.environ.setdefault("MOCK_MODE", "false")
os.environ.setdefault("ENVIRONMENT_NAME", "dev")
os.environ.setdefault("RECORDINGS_BUCKET_NAME", "test-recordings-bucket")
