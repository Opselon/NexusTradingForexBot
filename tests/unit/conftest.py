"""Shared fixtures for the unit-test suite."""

from __future__ import annotations

import os
from typing import Any

import pytest


@pytest.fixture
def backup_dir(tmp_path: Any) -> str:
    """An empty ``artifacts/backups`` stand-in for retention tests."""
    d = str(tmp_path / "backups")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Platform link (GitHub star verification) test helpers
# ---------------------------------------------------------------------------
# The security gate inside FirstRunCoordinator.download_official is
# authoritative in production. Install-chain tests stub the gate as VERIFIED
# so they keep exercising the download->verify->install chain itself; gate
# behavior is covered by test_platform_link*.py.


class _VerifiedPlatformStub:
    """Gate stub that behaves like a platform-verified star."""

    class _Status:
        state = type("S", (), {"value": "STARRED_VERIFIED"})()

    code = "PLATFORM_VERIFICATION_REQUIRED"
    status = _Status()

    @staticmethod
    def require_download_authorization():
        return None


@pytest.fixture()
def verified_platform_gate(monkeypatch: pytest.MonkeyPatch):
    import nexus_scalp.platform_link as pl_pkg

    def _stub_service() -> _VerifiedPlatformStub:
        return _VerifiedPlatformStub()

    monkeypatch.setattr(pl_pkg, "get_platform_link_service", _stub_service)
    return _VerifiedPlatformStub
