"""KeywordMatcher Lambda package (Phase 8.1).

Triggered by EventBridge for S3 ``Object Created`` events on the
TranscriptsBucket. Classifies the transcript text into a Voice_Status
using the Cycle's snapshotted dictionary version, then writes the
result to Response and TranscriptMeta.
"""
