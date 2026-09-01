"""Installer test-suite fixtures.

pytest ini options live in the repo-root pyproject; this conftest keeps the
installer suite sequential (real PowerShell subprocesses) and marks the
network E2E class as slow.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: network-bound E2E (env-gated)")
