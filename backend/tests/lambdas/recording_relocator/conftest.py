"""Local conftest for the RecordingRelocator Lambda unit tests.

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
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault("CONTACT_ID_INDEX_NAME", "ContactIdIndex")
os.environ.setdefault("CONNECT_RECORDINGS_PREFIX", "connect-raw/")
