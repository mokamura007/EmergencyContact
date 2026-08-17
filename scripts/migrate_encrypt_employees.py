"""Migrate existing Employee table data: plaintext → encrypted fields.

Usage:
    python scripts/migrate_encrypt_employees.py

Prerequisites:
    - AWS profile 'AWS-security-check' configured
    - SSM parameters exist:
        /safety-confirmation/dev/field-encryption-key
        /safety-confirmation/dev/blind-index-hmac-key

This script:
1. Scans all Employee rows
2. For each row where name/phoneNumber are NOT already encrypted:
   - Encrypts name and phoneNumber
   - Computes phoneNumberBlindIndex
   - Updates the row in-place
3. Reports progress and results

Safe to re-run: skips already-encrypted rows (uses is_encrypted() check).
"""

from __future__ import annotations

import base64
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import boto3

# --- Configuration ---
PROFILE = "AWS-security-check"
REGION = "ap-northeast-1"
TABLE_NAME = "Employee-dev"
SSM_ENCRYPTION_KEY_PARAM = "/safety-confirmation/dev/field-encryption-key"
SSM_HMAC_KEY_PARAM = "/safety-confirmation/dev/blind-index-hmac-key"


def main() -> None:
    # Setup AWS session
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    ssm = session.client("ssm")
    ddb = session.resource("dynamodb")
    kms = session.client("kms")
    table = ddb.Table(TABLE_NAME)

    # Retrieve encryption key from SSM (SecureString → decrypt via KMS)
    print("Retrieving encryption key from SSM...")
    enc_key_resp = ssm.get_parameter(
        Name=SSM_ENCRYPTION_KEY_PARAM, WithDecryption=False
    )
    encrypted_key_b64 = enc_key_resp["Parameter"]["Value"]

    # Retrieve HMAC key from SSM
    hmac_key_resp = ssm.get_parameter(
        Name=SSM_HMAC_KEY_PARAM, WithDecryption=True
    )
    hmac_key_b64 = hmac_key_resp["Parameter"]["Value"]

    # Set environment variables for the crypto module
    os.environ["FIELD_ENCRYPTION_KEY_ENCRYPTED"] = encrypted_key_b64
    os.environ["FIELD_ENCRYPTION_KMS_KEY_ARN"] = ""  # Not needed for decrypt-only
    os.environ["BLIND_INDEX_HMAC_KEY"] = hmac_key_b64

    # Now we need to decrypt the data key via KMS
    encrypted_key_bytes = base64.b64decode(encrypted_key_b64)
    dk_response = kms.decrypt(CiphertextBlob=encrypted_key_bytes)
    plaintext_key = dk_response["Plaintext"]

    # Inject the decrypted key into the crypto module's cache
    import shared.crypto.envelope as envelope_mod
    envelope_mod._CACHED_KEY = plaintext_key

    from shared.crypto import encrypt_field, is_encrypted, compute_blind_index

    # Scan all employees
    print(f"Scanning table: {TABLE_NAME}")
    items: list[dict] = []
    last_key = None
    while True:
        kwargs = {}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    print(f"Found {len(items)} rows")

    encrypted_count = 0
    skipped_count = 0
    error_count = 0

    for item in items:
        employee_id = item.get("employeeId", "")
        name = item.get("name", "")
        phone = item.get("phoneNumber", "")

        # Skip if already encrypted
        if name and is_encrypted(name):
            skipped_count += 1
            continue

        # Skip deleted rows with empty phone (nothing to encrypt)
        if not name and not phone:
            skipped_count += 1
            continue

        try:
            update_expr_parts = []
            expr_values = {}

            if name:
                update_expr_parts.append("#n = :n")
                expr_values[":n"] = encrypt_field(name)

            if phone:
                update_expr_parts.append("phoneNumber = :p")
                expr_values[":p"] = encrypt_field(phone)
                update_expr_parts.append("phoneNumberBlindIndex = :bi")
                expr_values[":bi"] = compute_blind_index(phone)
            else:
                # Deleted employee: just encrypt name, leave phone empty
                update_expr_parts.append("phoneNumberBlindIndex = :bi")
                expr_values[":bi"] = ""

            if not update_expr_parts:
                skipped_count += 1
                continue

            expr_names = {}
            if "#n = :n" in update_expr_parts:
                expr_names["#n"] = "name"

            update_kwargs: dict = {
                "Key": {"employeeId": employee_id},
                "UpdateExpression": "SET " + ", ".join(update_expr_parts),
                "ExpressionAttributeValues": expr_values,
            }
            if expr_names:
                update_kwargs["ExpressionAttributeNames"] = expr_names

            table.update_item(**update_kwargs)
            encrypted_count += 1
            print(f"  ✓ Encrypted: {employee_id} ({name[:10]}...)")

        except Exception as exc:
            error_count += 1
            print(f"  ✗ Error: {employee_id}: {exc}")

    print(f"\n=== Migration Complete ===")
    print(f"  Encrypted: {encrypted_count}")
    print(f"  Skipped (already encrypted or empty): {skipped_count}")
    print(f"  Errors: {error_count}")
    print(f"  Total: {len(items)}")


if __name__ == "__main__":
    main()
