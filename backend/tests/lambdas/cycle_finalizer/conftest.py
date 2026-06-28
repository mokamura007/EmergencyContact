"""Local conftest for CycleFinalizer Lambda unit tests.

The handler reads its env at module-import time. Tests use monkeypatch
to swap the boto3 client / table singletons per case, so we only need
to seed syntactically-valid values up front.
"""

from __future__ import annotations

import os

os.environ.setdefault("CYCLE_TABLE_NAME", "Cycle-test")
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault(
    "OPERATOR_TOPIC_ARN",
    "arn:aws:sns:ap-northeast-1:111122223333:safety-confirmation-operator-test",
)
os.environ.setdefault(
    "SFN_STATE_MACHINE_ARN",
    "arn:aws:states:ap-northeast-1:111122223333:stateMachine:"
    "safety-confirmation-cycle-test",
)
os.environ.setdefault("CLOUDWATCH_NAMESPACE", "SafetyConfirmation")
