"""Shared fixtures for the CFn smoke test package.

The conftest exposes a single ``cfn_template`` fixture, scope=session, that
parses ``infrastructure/template.yaml`` once via cfn-lint's bundled YAML
decoder. The cfn-lint decoder understands CloudFormation short-form
intrinsics (``!Ref``, ``!GetAtt``, ``!Sub`` and friends) and rewrites them
into the canonical ``{"Fn::Ref": ...}`` / ``{"Fn::GetAtt": [...]}`` dict
form, so downstream tests can introspect the template as a plain Python
dict without bespoke YAML tag handlers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cfnlint.decode.decode import decode  # type: ignore[import-untyped]

# Repository layout:
#   backend/tests/smoke/conftest.py  -> ../../../infrastructure/template.yaml
_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3] / "infrastructure" / "template.yaml"
)


@pytest.fixture(scope="session")
def cfn_template_path() -> Path:
    """Absolute path to the CloudFormation template under test."""
    assert _TEMPLATE_PATH.is_file(), (
        f"infrastructure/template.yaml not found at {_TEMPLATE_PATH}"
    )
    return _TEMPLATE_PATH


@pytest.fixture(scope="session")
def cfn_template(cfn_template_path: Path) -> dict[str, Any]:
    """Parse the CloudFormation template once per pytest session.

    Raises
    ------
    AssertionError
        If cfn-lint's decoder reports YAML-level decode matches. We promote
        decode-time matches to a hard failure here so the smoke tests can
        assume the rest of the dict is well-formed.
    """
    template, matches = decode(str(cfn_template_path))
    assert not matches, (
        f"cfn-lint decode produced {len(matches)} matches while parsing "
        f"{cfn_template_path}: {matches!r}"
    )
    assert isinstance(template, dict), (
        f"Expected dict template, got {type(template).__name__}"
    )
    return template


@pytest.fixture(scope="session")
def cfn_resources(cfn_template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The ``Resources`` block of the template."""
    resources = cfn_template.get("Resources")
    assert isinstance(resources, dict), "Resources block missing or malformed"
    return resources


@pytest.fixture(scope="session")
def cfn_mappings(cfn_template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The ``Mappings`` block of the template."""
    mappings = cfn_template.get("Mappings")
    assert isinstance(mappings, dict), "Mappings block missing or malformed"
    return mappings
