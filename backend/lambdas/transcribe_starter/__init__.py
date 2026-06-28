"""TranscribeStarter Lambda package (Phase 6.4).

Triggered by EventBridge for S3 ``Object Created`` events on the
RecordingsBucket. Starts an Amazon Transcribe job with retry on
``ThrottlingException`` / ``LimitExceededException`` and records the
job ID + transcript S3 key into TranscriptMetaTable. On final failure
for outbound recordings the Response row's ``callResultCode`` is
transitioned ``RECORDED -> TRANSCRIBE_FAILED``.
"""
