"""Unit tests for shared.api.cors (Phase 15.2a hotfix)."""

from __future__ import annotations

import pytest

from shared.api.cors import (
    ALLOW_HEADERS,
    ALLOW_METHODS,
    build_cors_headers,
    with_cors_headers,
)


def test_build_cors_headers_default_origin_is_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without CORS_ALLOWED_ORIGIN set, Allow-Origin defaults to '*'."""
    monkeypatch.delenv("CORS_ALLOWED_ORIGIN", raising=False)

    headers = build_cors_headers()

    assert headers == {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": ALLOW_HEADERS,
        "Access-Control-Allow-Methods": ALLOW_METHODS,
    }


def test_build_cors_headers_env_var_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CORS_ALLOWED_ORIGIN env var overrides the default '*'."""
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGIN", "https://dn8bulnup9krf.cloudfront.net"
    )

    headers = build_cors_headers()

    assert (
        headers["Access-Control-Allow-Origin"]
        == "https://dn8bulnup9krf.cloudfront.net"
    )
    assert headers["Access-Control-Allow-Headers"] == ALLOW_HEADERS
    assert headers["Access-Control-Allow-Methods"] == ALLOW_METHODS


def test_build_cors_headers_explicit_argument_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit allowed_origin argument beats the environment variable."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGIN", "https://env.example.com")

    headers = build_cors_headers(allowed_origin="https://arg.example.com")

    assert headers["Access-Control-Allow-Origin"] == "https://arg.example.com"


def test_with_cors_headers_preserves_existing_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing Content-Type entry must survive the merge unchanged."""
    monkeypatch.delenv("CORS_ALLOWED_ORIGIN", raising=False)

    merged = with_cors_headers(
        {"Content-Type": "application/json; charset=utf-8"}
    )

    assert merged["Content-Type"] == "application/json; charset=utf-8"
    assert merged["Access-Control-Allow-Origin"] == "*"
    assert merged["Access-Control-Allow-Headers"] == ALLOW_HEADERS
    assert merged["Access-Control-Allow-Methods"] == ALLOW_METHODS


def test_with_cors_headers_does_not_mutate_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller's dict must not be mutated (defensive against shared state)."""
    monkeypatch.delenv("CORS_ALLOWED_ORIGIN", raising=False)
    original: dict[str, str] = {"Content-Type": "application/json"}

    _ = with_cors_headers(original)

    assert original == {"Content-Type": "application/json"}
