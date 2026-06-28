"""Local conftest for the CallEndHandler Lambda unit tests.

The Lambda handler reads its environment at module-import time. The
tests swap the boto3 client / table singletons via ``monkeypatch`` per
test, so these env vars only need to be syntactically present before
the import.
"""

from __future__ import annotations

import os

os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault(
    "SFN_STATE_MACHINE_ARN",
    "arn:aws:states:ap-northeast-1:111122223333:stateMachine:safety-confirmation-cycle-test",
)
