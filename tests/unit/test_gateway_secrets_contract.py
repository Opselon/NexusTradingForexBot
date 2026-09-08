"""AUDIT-B2 contract tests: gateway credential resolution fail-closed.

The gateway must NEVER fall back to the well-known default secrets when the
engine is allowed to trade LIVE (allow_live=True), and must fail loudly
instead of silently starting with publicly-known credentials.
"""

from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def gw(monkeypatch):
    """Import the gateway module with a clean env each time."""
    monkeypatch.delenv("NSE_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("NSE_GATEWAY_SECRET", raising=False)
    monkeypatch.delenv("NSE_GATEWAY_ALLOW_DEFAULTS", raising=False)
    import nexus_scalp.gateway.server as gateway_server

    importlib.reload(gateway_server)
    return gateway_server


def test_defaults_refused_when_live_allowed(gw, monkeypatch):
    gw.set_allow_live(True)
    with pytest.raises(RuntimeError, match="GATEWAY SECRETS REQUIRED"):
        gw._expected_keys()


def test_defaults_refused_even_with_optin_when_live_allowed(gw, monkeypatch):
    monkeypatch.setenv("NSE_GATEWAY_ALLOW_DEFAULTS", "1")
    gw.set_allow_live(True)
    with pytest.raises(RuntimeError, match="GATEWAY SECRETS REQUIRED"):
        gw._expected_keys()


def test_defaults_allowed_demo_optin(gw, monkeypatch):
    monkeypatch.setenv("NSE_GATEWAY_ALLOW_DEFAULTS", "1")
    gw.set_allow_live(False)
    key, secret = gw._expected_keys()
    assert key == "default_local_key"
    assert secret == "default_local_secret"


def test_env_secrets_accepted_for_live(gw, monkeypatch):
    monkeypatch.setenv("NSE_GATEWAY_API_KEY", "real-key-123")
    monkeypatch.setenv("NSE_GATEWAY_SECRET", "real-secret-456")
    gw.set_allow_live(True)
    key, secret = gw._expected_keys()
    assert key == "real-key-123"
    assert secret == "real-secret-456"


def test_missing_secret_alone_refused(gw, monkeypatch):
    """Half-provisioned secrets must not quietly mix with defaults."""
    monkeypatch.setenv("NSE_GATEWAY_API_KEY", "real-key-123")
    gw.set_allow_live(False)
    with pytest.raises(RuntimeError, match="GATEWAY SECRETS REQUIRED"):
        gw._expected_keys()
