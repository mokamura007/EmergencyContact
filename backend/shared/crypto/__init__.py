"""Application-layer encryption for sensitive DynamoDB fields.

Provides envelope encryption using AWS KMS data keys (AES-256-GCM)
and HMAC-SHA256 blind indexes for searchable encrypted fields.
"""

from shared.crypto.envelope import decrypt_field, encrypt_field
from shared.crypto.blind_index import compute_blind_index

__all__ = ["encrypt_field", "decrypt_field", "compute_blind_index"]
