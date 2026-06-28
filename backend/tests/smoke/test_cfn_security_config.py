"""Smoke tests for CFn security and retention configuration.

Task 14.10 (Phase 14, Requirements 17.1 / 17.3). Each test asserts a single
property of ``infrastructure/template.yaml`` so a failure narrows directly
to one operational policy. The tests are pure dict introspection ? no AWS
API is called.

Validated properties:

1. Every ``AWS::DynamoDB::Table`` resource has ``SSESpecification.SSEEnabled
   == True`` (Requirement 15.1 ? per-table SSE-KMS).
2. ``RecordingsBucket`` and ``TranscriptsBucket`` carry
   ``PublicAccessBlockConfiguration`` with all four switches set to True
   (Requirements 10.2 / 6.4 ? block public access).
3. ``RecordingsBucket`` and ``TranscriptsBucket`` carry a
   ``LifecycleConfiguration`` rule with ``ExpirationInDays`` equal to the
   project's 90-day retention parameter (Requirements 10.4 / 6.5).
4. Every ``AWS::Logs::LogGroup`` declares a ``RetentionInDays`` property
   (Requirement 16.5 ? explicit retention; ``!Ref LogRetentionDays`` ok).
5. The Cognito user pool has exactly one ``AWS::Cognito::UserPoolGroup``
   and it is named ``Administrator`` (Requirement 1.9 ? Administrator-only
   authentication; Employee group explicitly NOT created).
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resources_of_type(
    resources: dict[str, dict[str, Any]],
    type_name: str,
) -> dict[str, dict[str, Any]]:
    """Return ``{logical_id: resource_dict}`` for every resource of the given Type."""
    return {
        logical_id: resource
        for logical_id, resource in resources.items()
        if isinstance(resource, dict) and resource.get("Type") == type_name
    }


def _is_truthy_cfn_value(value: Any) -> bool:
    """Return True if a CFn property value resolves to a truthy boolean.

    Handles the canonical Python literal ``True`` plus the string forms
    ``"true"`` / ``"True"`` that CFn YAML occasionally produces when a
    boolean is double-quoted.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return False


# ---------------------------------------------------------------------------
# Property 1 ? DynamoDB SSE-KMS on every table
# ---------------------------------------------------------------------------


def test_all_dynamodb_tables_have_sse_kms_enabled(
    cfn_resources: dict[str, dict[str, Any]],
) -> None:
    """Every AWS::DynamoDB::Table must have SSESpecification.SSEEnabled == True.

    Validates: Requirement 15.1.
    """
    tables = _resources_of_type(cfn_resources, "AWS::DynamoDB::Table")
    assert tables, "Expected at least one AWS::DynamoDB::Table in the template"

    failures: list[str] = []
    for logical_id, table in tables.items():
        props = table.get("Properties", {})
        sse = props.get("SSESpecification")
        if not isinstance(sse, dict):
            failures.append(f"{logical_id}: SSESpecification missing")
            continue
        if not _is_truthy_cfn_value(sse.get("SSEEnabled")):
            failures.append(f"{logical_id}: SSESpecification.SSEEnabled is not True")
            continue
        # SSEType must be KMS when a KMSMasterKeyId is supplied (defence in
        # depth ? mainline path on this template already supplies both).
        if sse.get("SSEType") != "KMS":
            failures.append(f"{logical_id}: SSESpecification.SSEType is not 'KMS'")
            continue
        if "KMSMasterKeyId" not in sse:
            failures.append(f"{logical_id}: SSESpecification.KMSMasterKeyId missing")

    assert not failures, "DynamoDB SSE-KMS violations:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Property 2 ? S3 Public Access Block (all four flags True)
# ---------------------------------------------------------------------------


_REQUIRED_BPA_BUCKETS = ("RecordingsBucket", "TranscriptsBucket")
_BPA_FLAGS = (
    "BlockPublicAcls",
    "BlockPublicPolicy",
    "IgnorePublicAcls",
    "RestrictPublicBuckets",
)


def test_recordings_and_transcripts_buckets_have_full_bpa(
    cfn_resources: dict[str, dict[str, Any]],
) -> None:
    """Recordings and Transcripts buckets must have all 4 BPA flags True.

    Validates: Requirements 10.2 / 6.4.
    """
    failures: list[str] = []
    for bucket_id in _REQUIRED_BPA_BUCKETS:
        bucket = cfn_resources.get(bucket_id)
        assert bucket is not None, f"Resource {bucket_id} not found"
        assert bucket.get("Type") == "AWS::S3::Bucket", (
            f"{bucket_id} is not AWS::S3::Bucket (got {bucket.get('Type')!r})"
        )

        bpa = bucket.get("Properties", {}).get("PublicAccessBlockConfiguration")
        if not isinstance(bpa, dict):
            failures.append(f"{bucket_id}: PublicAccessBlockConfiguration missing")
            continue
        for flag in _BPA_FLAGS:
            if not _is_truthy_cfn_value(bpa.get(flag)):
                failures.append(
                    f"{bucket_id}: PublicAccessBlockConfiguration.{flag} is not True "
                    f"(got {bpa.get(flag)!r})"
                )

    assert not failures, "S3 BPA violations:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Property 3 ? S3 Lifecycle 90-day expiration
# ---------------------------------------------------------------------------


_LCM_RETENTION_PARAMETER = {
    "RecordingsBucket": "RecordingsRetentionDays",
    "TranscriptsBucket": "TranscriptsRetentionDays",
}


def _extract_expiration_days(rule: dict[str, Any]) -> int | dict[str, Any] | None:
    """Return ``ExpirationInDays`` from a lifecycle rule, preserving Refs.

    The caller has already filtered to dict-typed rules before invocation,
    so no defensive ``isinstance`` is needed here.
    """
    value = rule.get("ExpirationInDays")
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return value
    return None


def test_recordings_and_transcripts_buckets_have_90day_lifecycle(
    cfn_template: dict[str, Any],
    cfn_resources: dict[str, dict[str, Any]],
) -> None:
    """Recordings/Transcripts buckets must have 90-day expiration LCM rules.

    Both buckets use ``!Ref RecordingsRetentionDays`` /
    ``!Ref TranscriptsRetentionDays`` to express the value; we resolve the
    parameter Default (which is enforced to ``[90]`` via AllowedValues) to
    confirm the wire value is 90.

    Validates: Requirements 10.4 / 6.5.
    """
    parameters = cfn_template.get("Parameters", {})
    failures: list[str] = []

    for bucket_id, expected_param in _LCM_RETENTION_PARAMETER.items():
        bucket = cfn_resources.get(bucket_id)
        assert bucket is not None, f"Resource {bucket_id} not found"

        lcm = bucket.get("Properties", {}).get("LifecycleConfiguration")
        if not isinstance(lcm, dict):
            failures.append(f"{bucket_id}: LifecycleConfiguration missing")
            continue

        rules = lcm.get("Rules")
        if not isinstance(rules, list) or not rules:
            failures.append(f"{bucket_id}: LifecycleConfiguration.Rules empty")
            continue

        # We expect at least one rule with Status=Enabled and an
        # ExpirationInDays expression that resolves to 90.
        matched = False
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if rule.get("Status") != "Enabled":
                continue
            exp_value = _extract_expiration_days(rule)

            # Literal integer path.
            if isinstance(exp_value, int) and exp_value == 90:
                matched = True
                break

            # !Ref path: {"Ref": "RecordingsRetentionDays"} (or transcripts).
            if isinstance(exp_value, dict) and exp_value.get("Ref") == expected_param:
                param_def = parameters.get(expected_param, {})
                allowed = param_def.get("AllowedValues")
                default = param_def.get("Default")
                if allowed == [90] and default == 90:
                    matched = True
                    break
                failures.append(
                    f"{bucket_id}: ExpirationInDays references {expected_param} but "
                    f"AllowedValues={allowed!r} or Default={default!r} is not [90]/90"
                )
                break

        if not matched and not failures:
            failures.append(
                f"{bucket_id}: no Status=Enabled rule with 90-day "
                f"ExpirationInDays found in LifecycleConfiguration.Rules"
            )

    assert not failures, "S3 LCM 90-day violations:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Property 4 ? CloudWatch Logs RetentionInDays on every LogGroup
# ---------------------------------------------------------------------------


def test_all_loggroups_have_retention_in_days(
    cfn_resources: dict[str, dict[str, Any]],
) -> None:
    """Every AWS::Logs::LogGroup must declare a RetentionInDays property.

    A literal integer or a ``!Ref LogRetentionDays`` reference both count.

    Validates: Requirement 16.5.
    """
    log_groups = _resources_of_type(cfn_resources, "AWS::Logs::LogGroup")
    assert log_groups, "Expected at least one AWS::Logs::LogGroup in the template"

    failures: list[str] = []
    for logical_id, log_group in log_groups.items():
        props = log_group.get("Properties", {})
        retention = props.get("RetentionInDays")
        if retention is None:
            failures.append(f"{logical_id}: RetentionInDays missing")
            continue
        # Integer or Ref dict are both acceptable.
        if isinstance(retention, int):
            if retention <= 0:
                failures.append(
                    f"{logical_id}: RetentionInDays must be positive (got {retention})"
                )
            continue
        if isinstance(retention, dict) and "Ref" in retention:
            continue
        failures.append(
            f"{logical_id}: RetentionInDays must be int or Ref, got {retention!r}"
        )

    assert not failures, "LogGroup retention violations:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Property 5 ? Only the Administrator Cognito group exists
# ---------------------------------------------------------------------------


def test_only_administrator_cognito_group_exists(
    cfn_resources: dict[str, dict[str, Any]],
) -> None:
    """The template must define exactly one Cognito UserPoolGroup, named Administrator.

    Validates: Requirement 1.9 (Administrator-only authentication).
    """
    groups = _resources_of_type(cfn_resources, "AWS::Cognito::UserPoolGroup")
    assert len(groups) == 1, (
        f"Expected exactly one AWS::Cognito::UserPoolGroup, got {len(groups)}: "
        f"{sorted(groups.keys())}"
    )

    [(logical_id, group)] = groups.items()
    group_name = group.get("Properties", {}).get("GroupName")
    assert group_name == "Administrator", (
        f"Expected GroupName='Administrator' on {logical_id}, got {group_name!r}"
    )
