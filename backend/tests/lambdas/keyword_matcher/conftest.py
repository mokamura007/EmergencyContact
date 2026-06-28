"""Local conftest for the KeywordMatcher Lambda unit tests (Phase 8.1).

The Lambda handler reads its environment at module-import time. The
tests swap the boto3 client / table singletons via ``monkeypatch`` per
test, so these env vars only need to be syntactically present before
the import.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "TRANSCRIPTS_BUCKET_NAME",
    "safety-confirmation-transcripts-test-111122223333-ap-northeast-1",
)
os.environ.setdefault("TRANSCRIPT_META_TABLE_NAME", "TranscriptMetadata-test")
os.environ.setdefault("RESPONSE_TABLE_NAME", "Response-test")
os.environ.setdefault("CYCLE_TABLE_NAME", "Cycle-test")
os.environ.setdefault(
    "KEYWORD_DICT_HISTORY_TABLE_NAME", "KeywordDictionaryHistory-test"
)
