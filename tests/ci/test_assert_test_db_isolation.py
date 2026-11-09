"""Unit tests for scripts/ci/assert_test_db_isolation.py.

The guard resolves the audit DB through the repo's own loader, so the
tests stub that loader: a fake ``nexus_scalp.database.config`` package on
PYTHONPATH returns a chosen provider/host, and the script is executed as
a subprocess exactly as CI runs it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "assert_test_db_isolation.py"


def _stub_package(tmp_path: Path, provider: str, host: str) -> Path:
    pkg = tmp_path / "nexus_scalp" / "database"
    pkg.mkdir(parents=True)
    (tmp_path / "nexus_scalp" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "config.py").write_text(
        "from types import SimpleNamespace\n"
        "\n"
        "def load_database_config(domain='audit'):\n"
        f"    return SimpleNamespace(provider={provider!r}, host={host!r}, sqlite_path='/tmp/x.db')\n"
    )
    return tmp_path


def _run(tmp_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("NSE_DATABASE__PG_HOST", "PYTHONPATH")}
    env["PYTHONPATH"] = str(tmp_path)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=60,
    )


def test_sqlite_provider_passes(tmp_path: Path):
    _stub_package(tmp_path, "sqlite", "")
    proc = _run(tmp_path, {})
    assert proc.returncode == 0, proc.stderr
    assert "isolation: OK" in proc.stdout


def test_postgresql_provider_fails(tmp_path: Path):
    _stub_package(tmp_path, "postgresql", "127.0.0.1")
    proc = _run(tmp_path, {})
    assert proc.returncode == 1
    assert "isolation FAILED" in proc.stderr


def test_pg_host_env_fails_without_touching_the_loader(tmp_path: Path):
    # A configured PG host fails even before the resolver is consulted.
    _stub_package(tmp_path, "sqlite", "")
    proc = _run(tmp_path, {"NSE_DATABASE__PG_HOST": "db.internal"})
    assert proc.returncode == 1
    assert "NSE_DATABASE__PG_HOST" in proc.stderr
