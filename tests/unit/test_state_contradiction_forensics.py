"""State-contradiction regression suite (2026-09-02, forensics task).

Each test pins ONE discovered contradiction class so it can never silently
return. Evidence: logs/info/2026/09/2026-09-02.log startup segment.

C-001  launcher "mode=PAPER" vs runtime_mode=LIVE same boot
       -> launcher must log launch_mode + configured_mode, never a bare mode.
C-002  [FEATURE_STATUS] total_features=50 vs 70D model contract
       -> scope-honest labels only.
C-005  [MODE] line repeated ~2k/day with zero state change
       -> edge-triggered emission (assert via _last_logged_runtime_mode).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.application.live_engine import LiveEngine


class _FakeAdapter:
    def __init__(self) -> None:
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected


def _make_engine(tmp_path: Path | None = None) -> LiveEngine:
    """Build a LiveEngine for contradiction probes.

    S3 ruling (NX-STP0): the engine fixture must be hermetic. The config's
    model_artifact_path is redirected under artifacts/model_generation/models/
    (tmp_path preferred when provided) so tests never touch the real serving
    artifact at artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt and never
    depend on process CWD for repo-relative config resolution.
    """
    from nexus_scalp.configuration.config import AppConfig

    yaml_path = (
        Path(__file__).resolve().parents[2] / "configs" / "base.yaml"
        if tmp_path is not None
        else Path("configs/base.yaml")
    )
    cfg = AppConfig.load_from_yaml(yaml_path)
    if tmp_path is not None:
        artifact_dir = tmp_path / "artifacts" / "model_generation" / "models"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        cfg.model.model_artifact_path = str(artifact_dir / "fixture_model.pt")
    return LiveEngine(config=cfg, adapter=_FakeAdapter(), audit_repo=MagicMock())


def test_c001_launcher_source_has_no_bare_mode_identity_claim():
    """The launcher 'Bootstrapping' log must never present a bare mode= field:
    the launch-time mode is pre-settings and routinely contradicts the
    settings-DB effective mode bound at engine construction."""
    with open("NexusTradingForexBot.py", encoding="utf-8") as _f:
        src = _f.read()
    boot = src[src.index('"Bootstrapping Engine Subsystems"') :]
    boot = boot[: boot.index("max_drawdown=") + 40] if "max_drawdown=" in boot else boot
    assert "launch_mode=" in boot, "launcher must log launch_mode separately"
    assert "configured_mode=" in boot, "launcher must log the effective configured mode"


def test_c004_mode_log_is_edge_triggered(tmp_path):
    """_update_runtime_mode must not re-log an unchanged truth every 5s."""
    engine = _make_engine(tmp_path)
    engine._account_snapshot = None
    with (
        patch.object(engine, "adapter") as ad,
        patch("nexus_scalp.application.live_engine.logger.info") as log_info,
    ):
        ad.is_connected.return_value = True
        for _ in range(5):
            engine._update_runtime_mode()  # steady state: identical truth
        calls = [str(c) for c in log_info.call_args_list]
        mode_lines = [c for c in calls if "[MODE] runtime_mode=" in c]
        assert len(mode_lines) == 1, (
            "identical [MODE] state must be logged once (edge-triggered), "
            f"got {len(mode_lines)} emissions for 5 identical evaluations"
        )
        # a real transition IS logged again: change the underlying truth
        # (mode PAPER -> LIVE with a connected adapter) - a connectivity-only
        # flip would NOT change the PAPER mode string, so it must not log.
        from nexus_scalp.domain.enums import ExecutionMode

        engine.config.execution.mode = ExecutionMode.LIVE
        engine._update_runtime_mode()
        mode_lines2 = [str(c) for c in log_info.call_args_list if "[MODE] runtime_mode=" in str(c)]
        assert len(mode_lines2) == 2, "a truth CHANGE must be logged"
