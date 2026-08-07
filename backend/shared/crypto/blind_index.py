"""Blind index computation for searchable encrypted fields.

A blind index is an HMAC-SHA256 of the plaintext value using a secret
key stored in KMS (or derived from it). This allows exact-match lookups
on encrypted fields without exposing the plaintext.

The HMAC key is derived from a KMS data key that is generated once and
stored in a DynamoDB config table (or environment variable). For
simplicity in this implementation, we use a dedicated environment
variable `BLIND_INDEX_HMAC_KEY` containing a base64-encoded 32-byte key.

In production, this key should be:
1. Generated via KMS GenerateDataKey
2. Stored encrypted in SSM Parameter Store or Secrets Manager
3. Decrypted at Lambda cold start

Design decisions:
- HMAC-SHA256 is collision-resistant and non-reversible.
- The blind index is stored as a hex string in DynamoDB.
- Only exact-match searches are possible (no prefix/range queries).
- The same plaintext always produces the same blind index (deterministic).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


# HMAC key for blind index computation. Must be a 32-byte key, base64-encoded.
_HMAC_KEY_B64 = os.environ.get("BLIND_INDEX_HMAC_KEY", "")


def _get_hmac_key() -> bytes:
    """Return the HMAC key bytes, raising if not configured."""
    if not _HMAC_KEY_B64:
        raise ValueError(
            "BLIND_INDEX_HMAC_KEY environment variable is required"
        )
    return base64.b64decode(_HMAC_KEY_B64)


def compute_blind_index(value: str) -> str:
    """Compute a blind index (HMAC-SHA256 hex) for the given value.

    Args:
        value: The plaintext value to index (e.g., phone number in E.164).

    Returns:
        A 64-character hex string (SHA-256 output).

    Raises:
        ValueError: If HMAC key is not configured or value is empty.
    """
    if not value:
        raise ValueError("Cannot compute blind index for empty value")

    key = _get_hmac_key()
    return hmac.HMAC(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
