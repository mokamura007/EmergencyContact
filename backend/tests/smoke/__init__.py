"""CloudFormation smoke tests for the Safety Confirmation System.

Task 14.10 (Phase 14 smoke tests). The tests in this package verify the
single CloudFormation template at ``infrastructure/template.yaml`` against
the security and environment-switching requirements that the project
operates under. They are pure parsing tests; no AWS API is called.

Modules
-------
test_cfn_security_config
    Verifies SSE-KMS / BPA / LCM 90-day / LogGroup retention / Cognito
    group constraints across all resources in the template.

test_cfn_env_snapshot
    Verifies the Mappings.EnvMap section produces the documented per-
    environment differential for dev / stg / prod, by comparing against
    snapshot JSON files under ``__snapshots__/``.
"""
