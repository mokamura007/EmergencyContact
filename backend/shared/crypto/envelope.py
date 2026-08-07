"""Envelope encryption using a cached data key (AES-256-GCM).

Performance-optimized design: Instead of generating a unique KMS data
key per field (which would require 600+ KMS calls for 300 employees),
we use a single data key per table/context that is:

1. Generated once via KMS GenerateDataKey and stored encrypted in
   SSM Parameter Store (or environment variable).
2. Decrypted once at Lambda cold start via KMS Decrypt.
3. Cached in-memory for the Lambda instance lifetime.
4. Used for all encrypt/decrypt operations (unique nonce per field).

Security properties:
- AES-256-GCM: authenticated encryption (confidentiality + integrity).
- Unique 96-bit random nonce per field value prevents ciphertext reuse.
- Data key is only in memory during Lambda execution (cleared on shutdown).
- KMS key policy controls who can decrypt the data key.
- If the encrypted data key (in SSM/env) is compromised, it cannot be
  decrypted without KMS Decrypt permission on the CMK.

Storage format per field (base64-encoded JSON):
    {
        "v": 2,
        "iv": "<base64 12-byte nonce>",
        "ct": "<base64 ciphertext + 16-byte GCM tag>"
    }

The data key itself is NOT stored alongside the ciphertext (unlike v1).
It is resolved from the environment at Lambda startup.

Project principle 19(b): No fallback. Encryption/decryption failures raise.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Any

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Environment variables:
# - FIELD_ENCRYPTION_KEY_ENCRYPTED: base64-encoded KMS-encrypted data key
# - FIELD_ENCRYPTION_KMS_KEY_ARN: KMS CMK ARN (for initial key generation only)
_ENCRYPTED_KEY_B64 = os.environ.get("FIELD_ENCRYPTION_KEY_ENCRYPTED", "")
_KMS_KEY_ARN = os.environ.get("FIELD_ENCRYPTION_KMS_KEY_ARN", "")

# Cached plaintext data key (decrypted at first use)
_CACHED_KEY: bytes | None = None

# Format version
_FORMAT_VERSION = 2
_NONCE_BYTES = 12  # 96-bit nonce for GCM


def _get_data_key() -> bytes:
    """Return the plaintext data key, decrypting from KMS on first call.

    The decrypted key is cached in module-level memory for the Lambda
    instance lifetime. This means KMS Decrypt is called exactly once
    per cold start.
    """
    global _CACHED_KEY  # noqa: PLW0603
    if _CACHED_KEY is not None:
        return _CACHED_KEY

    if not _ENCRYPTED_KEY_B64:
        raise ValueError(
            "FIELD_ENCRYPTION_KEY_ENCRYPTED environment variable is required. "
            "Generate with: aws kms generate-data-key --key-id <CMK> --key-spec AES_256"
        )

    encrypted_key = base64.b64decode(_ENCRYPTED_KEY_B64)
    kms = boto3.client("kms")
    response = kms.decrypt(CiphertextBlob=encrypted_key)
    _CACHED_KEY = response["Plaintext"]
    return _CACHED_KEY


def encrypt_field(plaintext: str) -> str:
    """Encrypt a plaintext string field using AES-256-GCM.

    Args:
        plaintext: The sensitive value to encrypt.

    Returns:
        A base64-encoded string containing the encrypted envelope.
        Returns empty string for empty input.

    Raises:
        ValueError: If encryption key is not configured.
    """
    if not plaintext:
        return ""

    key = _get_data_key()
    nonce = secrets.token_bytes(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    envelope = {
        "v": _FORMAT_VERSION,
        "iv": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ciphertext).decode("ascii"),
    }
    return base64.b64encode(
        json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def decrypt_field(encrypted_value: str) -> str:
    """Decrypt an envelope-encrypted field value.

    Args:
        encrypted_value: The base64-encoded envelope string from DynamoDB.

    Returns:
        The original plaintext string.
        Returns empty string for empty input.

    Raises:
        ValueError: On malformed envelope or decryption failure.
    """
    if not encrypted_value:
        return ""

    key = _get_data_key()

    # Decode envelope
    try:
        envelope_json = base64.b64decode(encrypted_value)
        envelope: dict[str, Any] = json.loads(envelope_json)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed encrypted envelope: {exc}") from exc

    version = envelope.get("v")
    if version != _FORMAT_VERSION:
        raise ValueError(f"Unsupported envelope version: {version}")

    nonce = base64.b64decode(envelope["iv"])
    ciphertext = base64.b64decode(envelope["ct"])

    aesgcm = AESGCM(key)
    try:
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError(f"Decryption failed: {exc}") from exc

    return plaintext_bytes.decode("utf-8")


def is_encrypted(value: str) -> bool:
    """Check if a value appears to be an encrypted envelope.

    Used during migration to handle mixed encrypted/plaintext data.
    """
    if not value:
        return False
    try:
        envelope_json = base64.b64decode(value)
        envelope = json.loads(envelope_json)
        return isinstance(envelope, dict) and envelope.get("v") == _FORMAT_VERSION
    except (ValueError, json.JSONDecodeError):
        return False
