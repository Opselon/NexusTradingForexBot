"""OBS-006: update lifecycle leaves log evidence with the upd- correlation id.

Before this fix the orchestrator wrote ONLY the terminal update-state.json —
zero [UPDATE] lines ever reached the severity tree, so a failed/rolled-back
update was not reconstructable from logs (grep 'upd-' over a day of logs
returned 0 hits). Pins:
  * every stage transition logs [UPDATE] event=STAGE with correlation_id
  * the compatibility-blocked verdict logs [UPDATE] event=COMPATIBILITY_BLOCKED
  * the correlation id (incl. the -<hex8> uuid suffix) survives the redactor
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nexus_scalp.observability import logging as obs_logging
from nexus_scalp.release.update_engine import orchestrator as orch_mod
from nexus_scalp.release.update_engine.orchestrator import UpdateOrchestrator


@pytest.fixture(autouse=True)
def _stdlib_logging():
    """Route structlog through the stdlib root logger so caplog captures it."""
    obs_logging.configure_logging(log_level="INFO", json_format=False, log_to_file=False)
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def orch(tmp_path: Path) -> UpdateOrchestrator:
    app = tmp_path / "app"
    user = tmp_path / "user"
    app.mkdir()
    user.mkdir()
    (app / "NexusScalpEngine.exe").write_bytes(b"MZ-fake")
    return UpdateOrchestrator(
        app_root=app,
        user_root=user,
        update_home=user / "update",
        installed_version="9.0.11",
    )


def _plan() -> dict[str, Any]:
    return {
        "status": "UPDATE_AVAILABLE",
        "target_version": "9.0.12",
        "artifact_name": "NexusScalpEngine-9.0.12.zip",
        "artifact_url": "http://127.0.0.1:1/payload.zip",
        "artifact_sha256": "0" * 64,
        "artifact_size": 10,
        "release_id": 2,
        "migration_required": False,
    }


def test_stage_transitions_log_with_correlation_id(orch: UpdateOrchestrator, caplog) -> None:
    with (
        caplog.at_level(logging.INFO, logger="nexus_scalp.release.update"),
        patch("nexus_scalp.release.update_engine.orchestrator.CompatibilityGate") as gate,
    ):
        gate.return_value.check.return_value = {
            "verdict": "BLOCKED",
            "checks": [{"name": "os", "verdict": "BLOCKED", "reason": "unsupported OS X"}],
        }
        rep = orch.run(yes=True, force=True, api_url="http://127.0.0.1:1/releases", timeout=1)
    assert rep["error_code"] in ("COMPATIBILITY_BLOCKED", "NETWORK_UNAVAILABLE", "NETWORK_ERROR")
    msgs = [r.getMessage() for r in caplog.records if "[UPDATE]" in r.getMessage()]
    stages = [m for m in msgs if "event=STAGE" in m]
    assert stages, f"no stage lines logged: {msgs}"
    assert all(orch._correlation_id in m for m in stages), stages


def test_compatibility_block_verdict_is_logged(orch: UpdateOrchestrator, caplog) -> None:
    with (
        caplog.at_level(logging.INFO, logger="nexus_scalp.release.update"),
        patch.object(type(orch), "check", lambda self, **kw: _plan()),
        patch("nexus_scalp.release.update_engine.orchestrator.CompatibilityGate") as gate,
    ):
        gate.return_value.check.return_value = {
            "verdict": "BLOCKED",
            "checks": [{"name": "os", "verdict": "BLOCKED", "reason": "unsupported OS X"}],
        }
        rep = orch.run(yes=True, force=True)
    assert rep["error_code"] == "COMPATIBILITY_BLOCKED"
    msgs = [r.getMessage() for r in caplog.records]
    assert any("event=COMPATIBILITY_BLOCKED" in m for m in msgs), msgs


def test_update_correlation_id_survives_redaction() -> None:
    from nexus_scalp.observability.logging import _redact_value

    cid = "upd-20260909T230719567352-f2b5b2ea"
    out = _redact_value(f"correlation_id={cid} state=DOWNLOADING")
    assert cid in out, out
    assert "[REDACTED_SECRET]" not in out
    # bare id also survives
    assert cid in _redact_value(f"event=STAGE {cid}")
