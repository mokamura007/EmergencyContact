"""Conftest for integration tests.

Lambda handlers under test (ConnectDispatcher / CallEndHandler /
TranscribeStarter / RetryEvaluator / CycleFinalizer) read several
environment variables at module-import time. We seed minimal,
syntactically-valid values here so the imports succeed; the tests
then swap the boto3 singletons (``handler._RESPONSE_TABLE`` /
``handler._CONNECT`` / ``handler._SFN`` etc.) with in-memory fakes
or :class:`unittest.mock.MagicMock` instances per test.

Mirrors the env-seeding pattern already used by:
  * ``tests/lambdas/connect_dispatcher/conftest.py``
  * ``tests/lambdas/call_end_handler/conftest.py``
  * ``tests/lambdas/transcribe_starter/conftest.py``
  * ``tests/lambdas/cycle_finalizer/conftest.py``

Kept DRY: any value already set by a sibling conftest stays as-is via
``setdefault``.
"""

from __future__ import annotations

import os

# --- ConnectDispatcher ---------------------------------------------------
os.environ.setdefault("CONNECT_INSTANCE_ID", "test-instance-id")
os.environ.setdefault("OUTBOUND_CONTACT_FLOW_ID", "test-flow-id")
os.environ.setdefault("OUTBOUND_PHONE_NUMBER", "+810000000000")

# --- Shared (used by multiple Lambdas) -----------------------------------
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault("CYCLE_TABLE_NAME", "Cycle-test")
os.environ.setdefault(
    "SFN_STATE_MACHINE_ARN",
    "arn:aws:states:ap-northeast-1:111122223333:stateMachine:"
    "safety-confirmation-cycle-test",
)
os.environ.setdefault(
    "OPERATOR_TOPIC_ARN",
    "arn:aws:sns:ap-northeast-1:111122223333:safety-confirmation-operator-test",
)
os.environ.setdefault("CLOUDWATCH_NAMESPACE", "SafetyConfirmation")

# --- TranscribeStarter ---------------------------------------------------
os.environ.setdefault("RECORDINGS_BUCKET_NAME", "safety-recordings-test")
os.environ.setdefault("TRANSCRIPTS_BUCKET_NAME", "safety-transcripts-test")
os.environ.setdefault("TRANSCRIPT_META_TABLE_NAME", "TranscriptMeta-test")
os.environ.setdefault(
    "KMS_CMK_ARN",
    "arn:aws:kms:ap-northeast-1:111122223333:key/00000000-0000-0000-0000-000000000000",
)
