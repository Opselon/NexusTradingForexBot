"""Security regression tests for CORS policy configuration.

Verifies that the CORS middleware restricts allow_origins to explicit, trusted local
origins by default and respects operator environment overrides, eliminating the overly
permissive allow_origins=['*'] vulnerability.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from nexus_scalp.web.server import (
    DEFAULT_CORS_ORIGINS,
    _get_allowed_cors_origins,
    create_app,
)


def test_default_cors_origins_has_no_wildcards() -> None:
    """Default allowed origins must not contain wildcard '*'."""
    origins = _get_allowed_cors_origins()
    assert "*" not in origins
    assert "http://localhost:8000" in origins
    assert "http://127.0.0.1:8000" in origins
    assert "http://[::1]:8000" in origins


def test_cors_origins_env_override_nse(monkeypatch) -> None:
    """NSE_CORS_ORIGINS env var overrides the allowed origins list."""
    monkeypatch.setenv("NSE_CORS_ORIGINS", "http://custom-domain.com, https://app.nexus.internal")
    origins = _get_allowed_cors_origins()
    assert origins == ["http://custom-domain.com", "https://app.nexus.internal"]


def test_cors_origins_env_override_nexus(monkeypatch) -> None:
    """NEXUS_CORS_ORIGINS env var overrides allowed origins when NSE_CORS_ORIGINS is unset."""
    monkeypatch.delenv("NSE_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("NEXUS_CORS_ORIGINS", "https://dashboard.example.com")
    origins = _get_allowed_cors_origins()
    assert origins == ["https://dashboard.example.com"]


def test_cors_preflight_allowed_origin(monkeypatch) -> None:
    """Preflight request with an allowed origin returns the Access-Control-Allow-Origin header."""
    token = "test-cors-token-123"
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", token)
    app = create_app()
    client = TestClient(app)

    headers = {
        "Origin": "http://localhost:8000",
        "Access-Control-Request-Method": "GET",
    }
    response = client.options("/api/health", headers=headers)
    assert response.headers.get("access-control-allow-origin") == "http://localhost:8000"


def test_cors_preflight_disallowed_origin(monkeypatch) -> None:
    """Preflight request with an untrusted origin must NOT return Access-Control-Allow-Origin header."""
    token = "test-cors-token-123"
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", token)
    app = create_app()
    client = TestClient(app)

    headers = {
        "Origin": "http://evil-attacker-site.com",
        "Access-Control-Request-Method": "GET",
    }
    response = client.options("/api/health", headers=headers)
    assert response.headers.get("access-control-allow-origin") is None
    assert response.headers.get("access-control-allow-origin") != "*"
