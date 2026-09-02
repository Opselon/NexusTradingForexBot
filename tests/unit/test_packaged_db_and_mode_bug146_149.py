"""BUG-146/147/148/149 regression tests — packaged-mode DB anchoring, mode
authority, repair provisioning, and UI mode mapping (2026-08-31 Nexus-Main).

Covers the pure logic paths; frozen-EXE behavior is verified end-to-end in
the local build smoke (release/local-build), not here.
"""

from __future__ import annotations

from pathlib import Path

from nexus_scalp.release import repair as rrepair
from nexus_scalp.release.paths import get_artifacts_dir


def test_default_sqlite_path_uses_repo_artifacts_not_cwd(tmp_path, monkeypatch) -> None:
    """BUG-149: with no workspace arg, DB paths anchor to the canonical
    artifacts dir (repo root in dev), NOT the raw process CWD."""
    from nexus_scalp.database.provider import default_sqlite_path

    monkeypatch.chdir(tmp_path)  # CWD is NOT the repo root here
    p = Path(default_sqlite_path("audit"))
    assert p == get_artifacts_dir() / "audit.db"
    assert p.is_absolute()


def test_audit_repository_relative_url_anchored_to_workspace(tmp_path, monkeypatch) -> None:
    """BUG-149: a relative sqlite URL passed to AuditRepository is anchored to
    the canonical runtime workspace, not the process CWD.

    BUG-223 coexistence: NEXUS_AUDIT_DB overrides the IMPLICIT default only.
    This test passes the legacy default EXPLICITLY (positional), so the env
    override must not hijack it and BUG-149 anchoring still applies."""
    import sqlite3

    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    monkeypatch.delenv("NEXUS_AUDIT_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    repo = AuditRepository(db_url="sqlite:///artifacts/audit.db", flush_interval_sec=3600.0)
    try:
        assert Path(repo._db_path).is_absolute()
        assert Path(repo._db_path) == get_artifacts_dir() / "audit.db"
        # And the schema really exists at that path (worker created tables).
        conn = sqlite3.connect(repo._db_path)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert tables, "audit schema must exist at the anchored path"
    finally:
        repo.close()


def test_repair_provisions_all_canonical_databases(tmp_path) -> None:
    """BUG-146: setup/repair provisions EVERY canonical DB (audit, news via
    flag, candle_intel, strategies, settings) and never deletes user data."""
    engine = rrepair.RepairEngine(workspace=tmp_path, data_root=tmp_path)
    results = engine.run(with_news=True)
    statuses = {r.action: r.status for r in results}
    assert not any(s == "FAILED" for s in statuses.values()), statuses
    for name in ("database", "news_db", "candle_intel_db", "strategies_db", "settings_db"):
        assert statuses.get(name) == "OK", statuses
    assert (tmp_path / "artifacts" / "candle_intel.db").exists()
    assert (tmp_path / "artifacts" / "strategies.db").exists()
    assert (tmp_path / "artifacts" / "news.db").exists()


def test_repair_result_covers_schema_init_without_initialize_schema(tmp_path) -> None:
    """BUG-146 root cause: AuditRepository has no initialize_schema(); repair
    must rely on constructor-side schema creation and still report OK."""
    engine = rrepair.RepairEngine(workspace=tmp_path, data_root=tmp_path)
    result = engine._ensure_database()
    assert result.status == "OK", result.detail


def test_engine_mode_override_beats_persisted_settings(tmp_path, monkeypatch) -> None:
    """BUG-148: LiveEngine(mode_override=PAPER) ignores a persisted LIVE value
    in the settings DB (explicit operator authority at boot)."""
    import os

    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.domain.enums import ExecutionMode
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    # Isolate the settings DB for this test.
    db_path = tmp_path / "databases" / "app_settings.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(db_path))

    svc = SettingsService(db=SettingsDatabase(db_path))
    svc.db.set("execution.mode", "LIVE", value_type="str", source="USER_SETTINGS", actor="test")

    cfg = AppConfig()
    cfg.execution.mode = ExecutionMode.PAPER
    LiveEngine = __import__(
        "nexus_scalp.application.live_engine", fromlist=["LiveEngine"]
    ).LiveEngine
    engine = LiveEngine(
        config=cfg,
        adapter=PaperMT5Adapter(symbol="XAUUSD"),
        mode_override=ExecutionMode.PAPER,
    )
    try:
        assert engine.config.execution.mode == ExecutionMode.PAPER
    finally:
        del os.environ["NEXUS_SETTINGS_DB"]


def test_set_execution_mode_hot_swaps_adapter_to_paper() -> None:
    """BUG-148: set_execution_mode records the override and swaps a real
    adapter to the simulation boundary in PAPER (order authority unchanged)."""
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.domain.enums import ExecutionMode

    LiveEngine = __import__(
        "nexus_scalp.application.live_engine", fromlist=["LiveEngine"]
    ).LiveEngine

    cfg = AppConfig()
    cfg.execution.mode = ExecutionMode.LIVE
    real = DirectMT5Adapter()
    engine = LiveEngine(config=cfg, adapter=real, mode_override=ExecutionMode.LIVE)
    result = engine.set_execution_mode(ExecutionMode.PAPER, source="TEST")
    assert result["success"] is True
    assert engine._mode_override == ExecutionMode.PAPER
    assert isinstance(engine.adapter, PaperMT5Adapter)
    assert engine.order_manager.adapter is engine.adapter
    assert engine._runtime_mode == "PAPER"
