"""Per-environment Mappings.EnvMap snapshot regression test.

Task 14.10 (Phase 14, Requirement 17.2). The template's ``Mappings.EnvMap``
block carries the dev / stg / prod differential for runtime configuration
values that are NOT name-templated (LogLevel / DynamoBillingMode /
ApiThrottleRate / ApiThrottleBurst). Per the comment block in
``infrastructure/template.yaml`` (Phase 1.3), name-templated values
(Cognito pool name, S3 bucket names, DynamoDB table names, KMS alias)
intentionally use ``!Sub`` with ``${EnvironmentName}`` and are NOT in
``Mappings``, so EnvMap is the complete per-environment differential
contract.

The test serialises EnvMap for each environment to a deterministic JSON
file under ``__snapshots__/`` and compares it against the existing snap.
On first run the test self-bootstraps by writing the snapshot; on later
runs it diffs the captured value against the saved snapshot.

This is a deliberate hand-rolled snapshot implementation rather than a
syrupy dependency ? the EnvMap surface is four keys per environment and
adding a third-party library for that footprint violates principle 19(a)
DRY (the snapshot helpers also serve as the regression scaffolding for
follow-up tasks B1-B5).

Validates: Requirements 17.2 / 17.3.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

_SNAPSHOT_DIR = Path(__file__).parent / "__snapshots__"
_ENVIRONMENTS = ("dev", "stg", "prod")
_BOOTSTRAP_ENV_VAR = "CFN_ENV_SNAPSHOT_UPDATE"


def _snapshot_path(env: str) -> Path:
    return _SNAPSHOT_DIR / f"envmap_{env}.json"


def _serialise(value: Any) -> str:
    """Deterministic JSON encoding for snapshot comparison."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


@pytest.fixture(scope="session")
def envmap(cfn_mappings: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return the EnvMap mapping block."""
    envmap = cfn_mappings.get("EnvMap")
    assert isinstance(envmap, dict), "Mappings.EnvMap missing or malformed"
    for env in _ENVIRONMENTS:
        assert env in envmap, f"Mappings.EnvMap.{env} missing"
    return envmap


@pytest.mark.parametrize("env", _ENVIRONMENTS)
def test_envmap_snapshot(env: str, envmap: dict[str, dict[str, Any]]) -> None:
    """Compare the EnvMap block for one environment against its snapshot.

    Set ``CFN_ENV_SNAPSHOT_UPDATE=1`` to regenerate the snapshots on the
    next test run; the test then writes the captured value to disk and
    passes for that run. Subsequent runs without the variable must match.
    """
    captured = envmap[env]
    snapshot_path = _snapshot_path(env)

    if os.environ.get(_BOOTSTRAP_ENV_VAR) == "1" or not snapshot_path.exists():
        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            _serialise(captured) + "\n",
            encoding="utf-8",
        )
        return  # First run / explicit update: pass after writing.

    expected_text = snapshot_path.read_text(encoding="utf-8").rstrip("\n")
    captured_text = _serialise(captured)
    assert captured_text == expected_text, (
        f"Mappings.EnvMap.{env} drifted from snapshot at {snapshot_path}.\n"
        f"---- captured ----\n{captured_text}\n"
        f"---- expected ----\n{expected_text}"
    )


def test_envmap_environments_are_disjoint(
    envmap: dict[str, dict[str, Any]],
) -> None:
    """Each environment block must declare the same key set.

    Catches the cassette failure mode where a new key is added to one
    environment but forgotten on the others. The test does NOT require
    distinct values across environments (DynamoBillingMode is identical
    across dev/stg/prod in the current template).
    """
    key_sets = {env: frozenset(envmap[env].keys()) for env in _ENVIRONMENTS}
    reference = key_sets["dev"]
    failures = [
        f"{env}: extra={sorted(key_sets[env] - reference)}, "
        f"missing={sorted(reference - key_sets[env])}"
        for env in _ENVIRONMENTS
        if key_sets[env] != reference
    ]
    assert not failures, "EnvMap key drift:\n" + "\n".join(failures)
