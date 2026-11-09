"""Unit tests for scripts/ci/assert_test_db_isolation.py.

The guard resolves the audit DB through the repo's own loader, so the
tests stub that loader: a fake ``nexus_scalp.database.config`` package on
PYTHONPATH returns a chosen provider/host, and the script is executed as
a subprocess exactly as CI runs it.

The subprocess env is scrubbed of every isolation variable (pins,
RUNNER_TEMP, TMPDIR) so ambient CI/dev values — including the per-test
HOME/TMPDIR redirect from the root conftest — cannot leak into the
result; each test declares exactly the env it exercises.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "assert_test_db_isolation.py"

_SCRUBBED_ENV_KEYS = (
    "NSE_DATABASE__PG_HOST",
    "NSE_DATABASE__SQLITE_PATH",
    "NEXUS_AUDIT_DB",
    "NEXUS_SETTINGS_DB",
    "NEXUS_DATA_ROOT",
    "RUNNER_TEMP",
    "TMPDIR",
    "TEMP",
    "TMP",
    "PYTHONPATH",
)


def _stub_package(
    tmp_path: Path,
    provider: str,
    host: str,
    sqlite_path: str = "/tmp/x.db",
    sqlite_uri: str | None = None,
) -> Path:
    pkg = tmp_path / "nexus_scalp" / "database"
    pkg.mkdir(parents=True, exist_ok=True)
    (tmp_path / "nexus_scalp" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    uri_field = f", sqlite_uri={sqlite_uri!r}" if sqlite_uri is not None else ""
    (pkg / "config.py").write_text(
        "from types import SimpleNamespace\n"
        "\n"
        "def load_database_config(domain='audit'):\n"
        f"    return SimpleNamespace(provider={provider!r}, host={host!r}, sqlite_path={sqlite_path!r}{uri_field})\n"
    )
    return tmp_path


def _run(tmp_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV_KEYS}
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


def _runner_temp_pins(runner_temp: Path) -> dict[str, str]:
    return {
        "RUNNER_TEMP": str(runner_temp),
        "NSE_DATABASE__SQLITE_PATH": str(runner_temp / "nse-ci-audit.sqlite"),
        "NEXUS_AUDIT_DB": str(runner_temp / "nse-ci-audit.db"),
        "NEXUS_SETTINGS_DB": str(runner_temp / "nse-ci-settings.db"),
    }


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


def test_sqlite_path_outside_temp_dir_fails(tmp_path: Path):
    # The old guard checked provider==sqlite only, so the repo-relative
    # default (artifacts/audit.db — shared across runs on a persistent
    # checkout) passed. The resolved file's location is part of the proof.
    _stub_package(tmp_path, "sqlite", "", sqlite_path=str(_REPO_ROOT / "artifacts" / "audit.db"))
    proc = _run(tmp_path, {})
    assert proc.returncode == 1
    assert "sqlite_path" in proc.stderr


def test_empty_sqlite_path_fails(tmp_path: Path):
    _stub_package(tmp_path, "sqlite", "", sqlite_path="")
    proc = _run(tmp_path, {})
    assert proc.returncode == 1
    assert "no sqlite file path" in proc.stderr


def test_sqlite_uri_fallback_is_checked(tmp_path: Path):
    # A config carrying only a sqlite_uri must still prove its file is
    # ephemeral; a persistent uri must fail.
    _stub_package(tmp_path, "sqlite", "", sqlite_path="", sqlite_uri="sqlite:////tmp/uri.db")
    proc = _run(tmp_path, {})
    assert proc.returncode == 0, proc.stderr
    _stub_package(tmp_path, "sqlite", "", sqlite_path="", sqlite_uri="sqlite:////home/x/audit.db")
    proc = _run(tmp_path, {})
    assert proc.returncode == 1
    assert "sqlite_path" in proc.stderr


def test_runner_temp_pin_shape_passes(tmp_path: Path):
    # The exact shape CI produces: RUNNER_TEMP set, all three pins under
    # it, resolved file under it.
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    _stub_package(tmp_path, "sqlite", "", sqlite_path=str(runner_temp / "nse-ci-audit.sqlite"))
    proc = _run(tmp_path, _runner_temp_pins(runner_temp))
    assert proc.returncode == 0, proc.stderr
    assert "isolation: OK" in proc.stdout


def test_missing_settings_pin_fails_when_runner_temp_set(tmp_path: Path):
    # The pin step must have exported every DB pin before the guard runs;
    # a missing NEXUS_SETTINGS_DB leaves non-pytest steps pointed at the
    # runner-persistent settings DB.
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    _stub_package(tmp_path, "sqlite", "", sqlite_path=str(runner_temp / "nse-ci-audit.sqlite"))
    pins = _runner_temp_pins(runner_temp)
    del pins["NEXUS_SETTINGS_DB"]
    proc = _run(tmp_path, pins)
    assert proc.returncode == 1
    assert "NEXUS_SETTINGS_DB" in proc.stderr


def test_pin_outside_runner_temp_fails(tmp_path: Path):
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    _stub_package(tmp_path, "sqlite", "", sqlite_path=str(runner_temp / "nse-ci-audit.sqlite"))
    pins = _runner_temp_pins(runner_temp)
    pins["NEXUS_AUDIT_DB"] = "/tmp/elsewhere.db"
    proc = _run(tmp_path, pins)
    assert proc.returncode == 1
    assert "NEXUS_AUDIT_DB" in proc.stderr


def test_resolved_path_outside_runner_temp_fails(tmp_path: Path):
    # Pins present, but the loader resolved somewhere else (e.g. the pin
    # was ignored): the resolved file is the ground truth.
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    _stub_package(tmp_path, "sqlite", "", sqlite_path="/tmp/stray-audit.db")
    proc = _run(tmp_path, _runner_temp_pins(runner_temp))
    assert proc.returncode == 1
    assert "sqlite_path" in proc.stderr
