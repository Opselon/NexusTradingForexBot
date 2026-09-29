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
