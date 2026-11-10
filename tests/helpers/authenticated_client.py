"""Hermetic authenticated TestClient factory for API behavior tests.

Tests that exercise route behavior use a per-session test credential.
The auth middleware remains ENABLED; tests for missing or invalid credentials
must use a plain TestClient rather than this helper.

The credential is generated fresh at runtime (never a committed literal):
a hardcoded token in this helper is exactly what secret scanners flag,
and no fixed value is needed — the middleware only requires the env var
and the Bearer header to agree within one test session.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

# Single-entry cache so the token is generated once per test session
# without rebinding a module global (ruff PLW0603).
_TEST_TOKEN_CACHE: dict[str, str] = {}


def _test_token() -> str:
    """Return the session's test credential, generating it once."""
    token = _TEST_TOKEN_CACHE.get("token")
    if token is None:
        token = f"nse-test-{secrets.token_urlsafe(24)}"
        _TEST_TOKEN_CACHE["token"] = token
    return token


def authenticated_test_client(app: Any, **kwargs: Any):
    """Create a TestClient that reaches protected routes with a test credential."""
    from fastapi.testclient import TestClient

    token = _test_token()
    # Auth middleware resolves its token when the ASGI stack is built, which
    # can happen after create_app() returns.
    os.environ.pop("NSE_WEB_AUTH_DISABLE", None)
    os.environ["NSE_WEB_AUTH_TOKEN"] = token
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {token}")
    return TestClient(app, headers=headers, **kwargs)
