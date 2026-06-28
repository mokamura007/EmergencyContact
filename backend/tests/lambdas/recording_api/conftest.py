"""Local conftest for the RecordingApi Lambda unit tests (Phase 5.5).

The handler reads its env at module-import time. Tests use monkeypatch
to swap the boto3 ``Table`` and S3 client singletons per case, so we
only need to seed syntactically-valid values up front.
"""

from __future__ import annotations

import os

os.environ.setdefault("CYCLE_TABLE_NAME", "Cycle-test")
os.environ.setdefault("INBOUND_TABLE_NAME", "InboundContact-test")
os.environ.setdefault("RECORDINGS_BUCKET_NAME", "recordings-bucket-test")
os.environ.setdefault("TRANSCRIPTS_BUCKET_NAME", "transcripts-bucket-test")
