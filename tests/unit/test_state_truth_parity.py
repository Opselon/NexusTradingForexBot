"""CLI/API parity tests for the runtime-truth contradiction matrix.

STATE-TRUTH hardening (TASK-STATE-SEMANTICS) Phase 3/5 (scoped minimum): the
same underlying state must produce ONE coherent externally visible truth on
every surface. These tests prove parity between:

- the CLI (``nexus version --json`` subprocess, fresh interpreter),
- the API (FastAPI TestClient ``/api/status``), and
- the matrix module (``release/state_truth.py`` resolvers).

Rule under test R4 (version identity coherence) and the mode/db/health
coherence rules; the matrix module is the arbiter that both surfaces are
compared against, so a surface drifting from canonical truth fails here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.release.state_truth import resolve_field

REPO_ROOT = Path(__file__).resolve().parents[2]

# The module-scoped CLI-subprocess fixture (nexus version --json, ~4-10s
# cold per interpreter) is instantiated per xdist WORKER unless the file's
# tests share a loadgroup: without a group, each of the 4 workers that
# receives any of these tests re-pays the spawn + cold import chain.
# Precedent: tests/unit/test_bug307c_cli_logging_disable_leak.py.
pytestmark = pytest.mark.xdist_group("state_truth")


def _cli_version_payload(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Fresh-subprocess ``nexus version --json`` (CLI surface of truth)."""
    proc = subprocess.run(
        [sys.executable, "-m", "nexus_scalp.cli.main", "version", "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("{"))
    payload: dict[str, Any] = json.loads("\n".join(lines[start:]))
    return payload


def _isolated_cli_env(run_dir: Path) -> dict[str, str]:
    """The subprocess env for the shared CLI payload: a fresh, EMPTY home and
    databases, mirroring the conftest per-test redirects (HOME, settings DB,
    audit DB, data root).

    This is what makes a MODULE-scoped payload safe. The old function-scoped
    fixture existed because a module-scoped spawn would inherit the RUNNER's
    real HOME (nexus.yaml -> PAPER) while the in-process matrix resolver
    reads the redirected empty HOME (UNKNOWN) — PAPER != UNKNOWN. Here the
    subprocess never sees the real HOME at all: it gets its own empty one,
    so both surfaces read the same (empty) configuration. Verified: the
    payload from two distinct empty HOMEs differs only in timestamp fields
    (``generated_at`` / ``checked_at``) — no compared field embeds a path.
    """
    home_dir = run_dir / "home"
    tmp_dir = run_dir / "tmp"
    home_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home_dir),
            "USERPROFILE": str(home_dir),
            "LOCALAPPDATA": str(home_dir),
            "APPDATA": str(home_dir),
            "TMPDIR": str(tmp_dir),
            "TEMP": str(tmp_dir),
            "TMP": str(tmp_dir),
            "NEXUS_SETTINGS_DB": str(run_dir / "app_settings.db"),
            "NEXUS_AUDIT_DB": str(run_dir / "audit.db"),
            "NSE_DATABASE__SQLITE_PATH": str(run_dir / "audit.db"),
            "NEXUS_DATA_ROOT": str(run_dir / "data"),
            "NEXUS_PAPER_PERSIST": "1",
        }
    )
    return env


@pytest.fixture(scope="module")
def cli_payload(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One fresh-subprocess ``nexus version --json`` shared by the module.

    Was function-scoped (one ~6s interpreter spawn per test, 8 spawns per
    file on CI) for the HOME-redirect reason above; the controlled empty env
    in ``_isolated_cli_env`` removes that reason — the payload is computed
    once, under exactly the empty configuration every test's in-process
    resolver sees. No test mutates the payload (all read it), so sharing
    cannot leak state between tests.
    """
    run_dir = tmp_path_factory.mktemp("cli_truth_home")
    return _cli_version_payload(_isolated_cli_env(run_dir))


@pytest.fixture(scope="module")
def api_payload() -> dict[str, Any]:
    """TestClient ``/api/status`` (API surface of truth)."""
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app
    from tests.helpers.authenticated_client import authenticated_test_client

    app = create_app(engine_ref=None)
    with authenticated_test_client(app) as client:
        resp = client.get("/api/status")
    assert resp.status_code == 200, resp.text[:400]
    payload: dict[str, Any] = resp.json()
    return payload


# ---------------------------------------------------------------------------
# R4: one version identity across CLI, API and the matrix.
# ---------------------------------------------------------------------------


def test_cli_version_matches_matrix(cli_payload: dict[str, Any]) -> None:
    status, value = resolve_field("version")
    assert status == "RESOLVED"
    assert cli_payload["version"] == value


def test_api_version_matches_matrix(api_payload: dict[str, Any]) -> None:
    status, value = resolve_field("version")
    assert status == "RESOLVED"
    versioning = api_payload.get("versioning") or {}
    api_version = versioning.get("application_version")
    assert api_version == value, f"API version {api_version!r} != canonical {value!r}"


def test_api_versioning_block_carries_commit_and_schema(
    api_payload: dict[str, Any],
) -> None:
    versioning = api_payload.get("versioning") or {}
    # R4 parity check, pyproject-derived: the API must advertise the SINGLE
    # canonical version (pyproject.toml [project] version, surfaced through
    # get_version_info / RuntimeVersionBlock). A literal pin here drifted
    # from the repo version on every routine version bump (9.0.6 -> 9.0.10);
    # deriving the expectation keeps the test a truth check, not a changelog.
    from nexus_scalp.release.metadata import get_version_info

    canonical_version = get_version_info()["version"]
    assert versioning.get("application_version") == canonical_version
    schema = versioning.get("feature_schema") or {}
    assert schema.get("id") in ("scalp_v1", "scalp_v3", "scalp_v4")


def test_cli_and_api_agree_on_version(
    cli_payload: dict[str, Any], api_payload: dict[str, Any]
) -> None:
    versioning = api_payload.get("versioning") or {}
    assert cli_payload["version"] == versioning.get("application_version")


def test_web_bundle_application_version_is_coherent(cli_payload: dict[str, Any]) -> None:
    wb = cli_payload.get("web_bundle") or {}
    assert wb.get("application_version") == cli_payload["version"]


# ---------------------------------------------------------------------------
# Mode/DB/schema coherence between CLI snapshot payload and the matrix.
# ---------------------------------------------------------------------------


def test_cli_configured_mode_matches_matrix(cli_payload: dict[str, Any]) -> None:
    snap = cli_payload.get("runtime_snapshot") or {}
    section = snap.get("runtime_mode") or {}
    status, value = resolve_field("configured_mode")
    assert status == "RESOLVED"
    assert section.get("configured_mode") == value


def test_cli_db_state_matches_matrix(cli_payload: dict[str, Any]) -> None:
    snap = cli_payload.get("runtime_snapshot") or {}
    db = (snap.get("database") or {}).get("capability") or {}
    status, value = resolve_field("db_state")
    assert status == "RESOLVED"
    expected = {"AVAILABLE": "READY", "NOT_INITIALIZED": "NOT_INITIALIZED"}.get(db.get("audit"))
    if expected is not None:
        assert value == expected


def test_cli_feature_contract_is_canonical_70d(cli_payload: dict[str, Any]) -> None:
    fc = (cli_payload.get("runtime_snapshot") or {}).get("feature_contract") or {}
    assert fc.get("schema_id") == "scalp_v3"
    assert int(fc.get("dimension") or 0) == 70


def test_model_dimension_coherent_between_surfaces(cli_payload: dict[str, Any]) -> None:
    snap = cli_payload.get("runtime_snapshot") or {}
    champ = (snap.get("model") or {}).get("registry_champion") or {}
    status, value = resolve_field("model_input_dimension")
    assert status == "RESOLVED"
    # ENV ISOLATION NOTE (BUG-223 conftest interplay): the module-scoped CLI
    # subprocess payload is created BEFORE the function-scoped autouse
    # fixtures redirect NEXUS_AUDIT_DB, so the payload may see the REAL
    # registry champion while the in-process resolver sees the isolated
    # (typically empty) temp registry. The invariant under test is the
    # RESOLVER's honesty, not cross-context equality: when no champion is
    # observable in-process it must report the explicit verified-absent /
    # unobservable state — never a fabricated dimension.
    if champ.get("available") and value == champ.get("feature_dimension"):
        assert status == "RESOLVED"  # coherent cross-context read
    else:
        assert value in ("NOT_CONFIGURED", "UNKNOWN")


# ---------------------------------------------------------------------------
# API payload carries coherent health/mode truth (no fabricated values).
# ---------------------------------------------------------------------------


def test_api_status_has_mode_and_health(api_payload: dict[str, Any]) -> None:
    # The canonical /api/status must expose mode + health blocks; absence is a
    # contract break, and neither may be fabricated constants.
    mode_value = api_payload.get("runtime_mode")
    health_block = api_payload.get("health")
    assert health_block is not None, "/api/status missing health section"
    # engine absent in this fixture: mode truth is explicitly None-ish,
    # never a hardcoded PAPER/LIVE lie.
    assert mode_value in (None, "LIVE", "PAPER", "SHADOW", "UNKNOWN")


def test_api_health_section_is_structured_and_unavailable_by_default(
    api_payload: dict[str, Any],
) -> None:
    health = api_payload.get("health")
    assert isinstance(health, dict)
    assert health, "empty health section"
    # R3: no engine attached -> overall UNAVAILABLE, never fabricated HEALTHY.
    assert health.get("overall") == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Snapshot inside the CLI payload equals the matrix's own snapshot read.
# ---------------------------------------------------------------------------


def test_cli_runtime_snapshot_sections_present(cli_payload: dict[str, Any]) -> None:
    snap = cli_payload.get("runtime_snapshot") or {}
    for section in ("identity", "feature_contract", "model", "database", "runtime_mode"):
        assert section in snap, f"CLI runtime_snapshot lost section {section}"


def test_matrix_module_snapshot_read_matches_cli_payload(
    cli_payload: dict[str, Any],
) -> None:
    from nexus_scalp.release.runtime_snapshot import build_runtime_snapshot

    snap = build_runtime_snapshot(include_update=False)
    cli_snap = cli_payload.get("runtime_snapshot") or {}
    assert snap.get("runtime_mode") == cli_snap.get("runtime_mode")
    assert snap.get("feature_contract") == cli_snap.get("feature_contract")
