"""Hermetic authenticated TestClient factory for API behavior tests.

Tests that exercise route behavior use a fixed non-production credential.
The auth middleware remains ENABLED; tests for missing or invalid credentials
must use a plain TestClient rather than this helper.
"""
from __future__ import annotations

import os
from typing import Any

TEST_WEB_AUTH_TOKEN = "nse-ci-test-auth-token-2026"


def authenticated_test_client(app: Any, **kwargs: Any):
    """Create a TestClient that reaches protected routes with a test credential."""
    from fastapi.testclient import TestClient

    # Auth middleware resolves its token when the ASGI stack is built, which
    # can happen after create_app() returns.
    os.environ.pop("NSE_WEB_AUTH_DISABLE", None)
    os.environ["NSE_WEB_AUTH_TOKEN"] = TEST_WEB_AUTH_TOKEN
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {TEST_WEB_AUTH_TOKEN}")
    return TestClient(app, headers=headers, **kwargs)
