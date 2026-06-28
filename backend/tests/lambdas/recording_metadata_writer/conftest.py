"""Local conftest for the RecordingMetadataWriter Lambda unit tests.

The handler reads its environment at module-import time. The tests
swap the boto3 client / table singletons via ``monkeypatch`` per test,
so these env vars only need to be syntactically present before the
import.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "RECORDINGS_BUCKET_NAME",
    "safety-confirmation-recordings-test-111122223333-ap-northeast-1",
)
os.environ.setdefault("RECORDING_META_TABLE_NAME", "RecordingMetadata-test")
