"""Local conftest for the TranscribeStarter Lambda unit tests.

The Lambda handler reads its environment at module-import time. The tests
swap the boto3 client / table singletons via ``monkeypatch`` per test, so
these env vars only need to be syntactically present before the import.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "RECORDINGS_BUCKET_NAME",
    "safety-confirmation-recordings-test-111122223333-ap-northeast-1",
)
os.environ.setdefault(
    "TRANSCRIPTS_BUCKET_NAME",
    "safety-confirmation-transcripts-test-111122223333-ap-northeast-1",
)
os.environ.setdefault("TRANSCRIPT_META_TABLE_NAME", "TranscriptMetadata-test")
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault(
    "KMS_CMK_ARN",
    "arn:aws:kms:ap-northeast-1:111122223333:key/00000000-0000-0000-0000-000000000000",
)
os.environ.setdefault("TRANSCRIBE_LANGUAGE_CODE", "ja-JP")

# Mock-mode env (ADR-0010, Phase 16.3). Default is "false" so existing
# production-path tests are unaffected; mock-mode tests flip these via
# ``monkeypatch.setattr(handler, "_MOCK_MODE_ENABLED", True)``.
os.environ.setdefault("MOCK_MODE", "false")
os.environ.setdefault("ENVIRONMENT_NAME", "dev")
