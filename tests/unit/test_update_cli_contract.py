"""Self-Update CLI contract regression tests (2026-09-10 operator audit).

Pins the defects found by running the REAL `nexus` CLI (packaged entrypoint
``nexus_scalp.release.cli_shim``) command-by-command in a clean sandbox:

  D1  `nexus update verify` on a fully VERIFIED client exited 5 (update
      failure) instead of 0 — the exit-code mapping had no VERIFIED case.
  D2  A healthy up-to-date client whose download cache was pruned by the
      storage guard could never pass verify again ("staged artifact not
      found (pruned)" was a hard FAIL). The staged package is a transient
      cache; only present-but-wrong bytes (tamper) is a verification
      failure. Absence is now a WARNING.
  D3  `nexus update install --json` / `rollback --json` stdout was polluted
      by structlog's unconfigured PrintLogger default (writes to stdout),
      breaking the documented machine-readable contract. Update-lifecycle
      logging now uses the stdlib logger (severity-tree routing under the
      engine is unchanged).
  D4  VERIFICATION_FAILED / UPDATE_VERIFICATION_FAILED mapped to 5; the
      cli-reference contract says 4 = release verification failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.cli.update_cli import _update_exit_code
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.release.update_engine.orchestrator import UpdateOrchestrator
from nexus_scalp.release.update_engine.rollback_state import ReleaseLocalState


@pytest.fixture()
def installed_client(tmp_path: Path) -> tuple[UpdateOrchestrator, Path]:
    """A minimal honest installed-client fixture (app root + update home)."""
    app = tmp_path / "app"
    user = tmp_path / "user"
    (app / "bin").mkdir(parents=True)
    user.mkdir()
    (app / "NexusScalpEngine.exe").write_bytes(b"MZ-nexus-engine-fixture")
    (app / "build-info.json").write_text(
        json.dumps({"version": "9.0.11", "platform": "windows-x64"})
    )
    return UpdateOrchestrator(
        app_root=app,
        user_root=user,
        update_home=user / "update",
        installed_version="9.0.11",
    ), app


def _stage(orch: UpdateOrchestrator, payload: bytes) -> None:
    orch.cache_dir.mkdir(parents=True, exist_ok=True)
    artifact = orch.cache_dir / "NexusScalpEngine-9.0.11-win-x64.zip"
    artifact.write_bytes(payload)
    ReleaseLocalState(orch.update_home).write(
        {
            "target_version": "9.0.11",
            "artifact_name": artifact.name,
            "artifact_sha256": hashlib.sha256(payload).hexdigest(),
            "channel": "stable",
        },
        {"previous": ""},
    )


# ---------------------------------------------------------------------------
# D2: pruned cache must not fail verify; tampered staged bytes must
# ---------------------------------------------------------------------------


def test_verify_passes_after_cache_prune(installed_client) -> None:
    orch, _app = installed_client
    _stage(orch, b"PK\x05\x06" + b"payload" * 50)
    assert orch.verify()["status"] == "VERIFIED"
    # the storage guard prunes the download cache by design (keep=2)
    for f in orch.cache_dir.iterdir():
        f.unlink()
    rep = orch.verify()
    assert rep["status"] == "VERIFIED", rep["checks"]
    assert rep["warnings"] == ["staged_artifact_hash"]


def test_verify_fails_on_tampered_staged_artifact(installed_client) -> None:
    orch, _app = installed_client
    _stage(orch, b"PK\x05\x06" + b"payload" * 50)
    artifact = orch.cache_dir / "NexusScalpEngine-9.0.11-win-x64.zip"
    artifact.write_bytes(b"tampered-bytes")
    rep = orch.verify()
    assert rep["status"] == "VERIFICATION_FAILED"
    staged = [c for c in rep["checks"] if c["name"] == "staged_artifact_hash"]
    assert staged and staged[0]["verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# D1 + D4: exit-code contract
# ---------------------------------------------------------------------------


def test_exit_code_verified_is_success() -> None:
    assert _update_exit_code({"status": "VERIFIED", "checks": []}) == xc.EXIT_OK


def test_exit_code_verification_failed_is_release_failure() -> None:
    assert _update_exit_code({"status": "VERIFICATION_FAILED", "checks": []}) == xc.EXIT_RELEASE
    assert _update_exit_code({"status": "UPDATE_VERIFICATION_FAILED"}) == xc.EXIT_RELEASE


# ---------------------------------------------------------------------------
# D3: --json stdout stays machine-parseable (no PrintLogger pollution)
# ---------------------------------------------------------------------------


def test_update_lifecycle_logging_does_not_write_to_stdout(
    installed_client, capsys: pytest.CaptureFixture[str]
) -> None:
    orch, _app = installed_client
    # no configure_logging() in CLI context: the default structlog PrintLogger
    # used to print straight to stdout, corrupting --json output.
    orch.run(yes=True, force=True, api_url="http://127.0.0.1:1/releases", timeout=1)
    captured = capsys.readouterr()
    assert "[UPDATE]" not in captured.out, captured.out[:400]
    assert "event=STAGE" not in captured.out, captured.out[:400]


def test_update_lifecycle_logging_reaches_severity_tree(
    installed_client,
    tmp_path: Path,
) -> None:
    """Engine context: configure_logging routes the records to the severity
    tree (OBS-006 evidence must survive the stdlib switch). Note: with
    configure_logging installed, the console handler ALSO prints by design —
    the bare-CLI --json purity is pinned by the previous test (no handlers)."""
    from nexus_scalp.observability import logging as obs_logging

    orch, _app = installed_client
    log_root = tmp_path / "logs"
    obs_logging.configure_logging(
        log_level="INFO", json_format=False, log_to_file=True, log_file_path=log_root
    )
    try:
        orch.run(yes=True, force=True, api_url="http://127.0.0.1:1/releases", timeout=1)
    finally:
        for h in logging.getLogger().handlers:
            h.flush()
        logging.getLogger().handlers.clear()
        logging.getLogger().setLevel(logging.WARNING)
    content = "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in log_root.rglob("*.log")
    )
    assert "[UPDATE] event=STAGE" in content
    assert orch._correlation_id in content
