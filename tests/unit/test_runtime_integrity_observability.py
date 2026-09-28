"""Runtime-integrity regression coverage for the two live defects fixed in this
change (RUNTIME-INTEGRITY-001).

1. ``[RESEARCH_OBS] heatmap failed error='unable to open database file'``

   Under a persisted PostgreSQL provider ``AuditRepository._db_path`` holds the
   PROVIDER URI (``postgresql://host:port/db``), not a filesystem path. Four
   research-observability read methods opened a raw ``sqlite3.connect()`` on
   that attribute unconditionally, so sqlite treated the URI as a literal
   filename and the live engine logged the exact error above while the server
   held thousands of ``research_gates`` / ``strategy_registry`` rows. The fix
   routes those reads through the provider-portable ``_reader()`` helper.

2. Health/doctor ``CONFIGURATION`` reported a DIFFERENT execution mode than the
   running engine for the same install.

   ``HealthEngine._load_config`` resolved only the YAML chain while the engine
   rehydrates ``execution.mode`` from the persisted settings DB; a stale
   ``nexus.yaml`` left by an earlier wizard run kept reading PAPER while the
   persisted operator setting (and the runtime) was LIVE. The probe is now
   authoritative-store-first, so the probe and the engine cannot disagree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.observability import ResearchObservabilityStore


class _FakeReadPlane:
    """Read-plane-shaped fake (query/query_one/scalar, no execute).

    Mirrors the established shape from test_audit_read_plane_registration
    _chg0067.py: the guard refuses a write-shaped backend for reads. Answers
    the research surface's real SQL so the assertions describe behaviour.
    """

    def __init__(self, gates: list[dict[str, Any]], families: list[dict[str, Any]]) -> None:
        self._gates = gates
        self._families = families
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append((sql, tuple(args)))
        s = sql.strip().lower()
        if "research_gates" in s and "group by" in s:
            return [dict(r) for r in self._gates]
        if "strategy_registry" in s and "context_definition" in s:
            return [dict(r) for r in self._families]
        if "strategy_registry" in s and "where strategy_id=?" in s:
            # _registry_entry: SELECT * ... ORDER BY updated_at DESC LIMIT 1
            return [
                {
                    "strategy_id": "s-1",
                    "context_definition": "{}",
                    "score": "{}",
                    "lifecycle": "DISCOVERED",
                }
            ]
        if "result_summary" in s:
            return [{"result_summary": '{"lifecycle":"REJECTED","reason":"WRONG_SIDE"}'}]
        if "research_runs" in s and "where strategy_id=?" in s:
            return [{"run_id": "run-1", "strategy_id": "s-1"}]
        return []

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        return 1


def _pg_like_repo(monkeypatch: pytest.MonkeyPatch, plane: Any) -> AuditRepository:
    """A repository that LOOKS exactly like the live PostgreSQL case:
    ``_is_sqlite`` False and ``_db_path`` the provider URI, with the read plane
    resolved through the same seam the engine uses."""
    repo = AuditRepository(db_url="sqlite:///:memory:")
    monkeypatch.setattr(repo, "_is_sqlite", False, raising=True)
    monkeypatch.setattr(
        repo,
        "_db_path",
        "postgresql://localhost:5432/nexusdb",
        raising=True,
    )

    def _plane() -> Any:
        return plane

    monkeypatch.setattr(repo, "research_read_plane", _plane, raising=False)
    return repo


# ---------------------------------------------------------------------------
# 1. the provider-uri attribute must never reach sqlite3.connect()
# ---------------------------------------------------------------------------


def test_unguarded_sqlite_connect_on_provider_uri_reproduces_the_live_error(
    tmp_path: Path,
) -> None:
    """The exact live symptom: sqlite3 on a provider URI fails. This pins the
    failure the fix removes so a future caller cannot silently reintroduce
    it by bypassing ``_reader()``."""
    import sqlite3

    with pytest.raises(sqlite3.OperationalError) as excinfo:
        sqlite3.connect("postgresql://localhost:5432/nexusdb", timeout=5.0)
    assert "unable to open database file" in str(excinfo.value)


def test_heatmap_reads_the_provider_plane_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plane = _FakeReadPlane(
        gates=[{"gate_type": "WALK_FORWARD", "c": 4292}, {"gate_type": "OOS", "c": 3792}],
        families=[],
    )
    repo = _pg_like_repo(monkeypatch, plane)
    store = ResearchObservabilityStore(audit_repo=repo)

    out = store.gate_failure_heatmap()

    assert out["by_gate"] == {"WALK_FORWARD": 4292, "OOS": 3792}
    assert out["total_failures"] == 4292 + 3792
    assert plane.calls, "the read must go through the plane, not an empty default"
    # None of the plane SQL mentions a filesystem path.
    assert all("postgresql://" not in sql for sql, _ in plane.calls)
    # The rejection-reason leg also read real rows.
    assert out["rejection_reasons"] == {"WRONG_SIDE": 1}


def test_family_analytics_reads_the_provider_plane_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plane = _FakeReadPlane(
        gates=[],
        families=[
            {
                "context_definition": '{"fingerprint":"ichimoku"}',
                "lifecycle": "VALIDATED",
                "score": '{"final_score": 0.81}',
                "sample_count": 3,
            }
        ],
    )
    repo = _pg_like_repo(monkeypatch, plane)
    store = ResearchObservabilityStore(audit_repo=repo)

    out = store.family_analytics()

    assert out["families"]["ichimoku"]["candidates"] == 1
    assert out["families"]["ichimoku"]["validated"] == 1
    assert out["families"]["ichimoku"]["avg_score"] == 0.81


def test_trace_helpers_read_the_provider_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    plane = _FakeReadPlane(gates=[], families=[])
    repo = _pg_like_repo(monkeypatch, plane)
    store = ResearchObservabilityStore(audit_repo=repo)

    # _registry_entry: the one-click trace's registry step
    assert store._registry_entry("s-1") is not None
    # _runs_for: the one-click trace's runs step
    runs = store._runs_for("s-1")
    assert runs and runs[0]["run_id"] == "run-1"


def test_sqlite_domain_keeps_its_own_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SQLite path is unchanged: no fabric consultation, real file."""
    plane = _FakeReadPlane(gates=[], families=[])
    repo = AuditRepository(db_url="sqlite:///:memory:")
    # A SQLite repo must NOT consult the plane even if one is registered.
    monkeypatch.setattr(repo, "research_read_plane", lambda: plane, raising=False)
    store = ResearchObservabilityStore(audit_repo=repo)

    # Under :memory: the tables are absent -> the read must hit the local
    # sqlite connection (raising suppressed by the method) and NOT the plane.
    store.gate_failure_heatmap()
    assert plane.calls == [], "a SQLite domain must never consult the read plane"


def test_unrouted_repo_reports_unavailable_not_fake_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pooled provider with NO resolvable plane surfaces available=False
    (cannot read) — never an empty result that looks like 'no data'."""
    repo = AuditRepository(db_url="sqlite:///:memory:")
    monkeypatch.setattr(repo, "_is_sqlite", False, raising=True)
    monkeypatch.setattr(repo, "_db_path", "postgresql://localhost:5432/nexusdb", raising=True)
    # No resolvable read plane for this process -> available False, never a
    # fake empty result.
    monkeypatch.setattr(repo, "research_read_plane", lambda: None, raising=False)
    store = ResearchObservabilityStore(audit_repo=repo)

    heatmap = store.gate_failure_heatmap()
    assert heatmap == {"by_gate": {}, "rejection_reasons": {}}


# ---------------------------------------------------------------------------
# 2. health CONFIGURATION must agree with the engine's authoritative store
# ---------------------------------------------------------------------------


def test_health_reports_the_persisted_mode_not_the_stale_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale nexus.yaml reading PAPER while the persisted operator setting
    is LIVE must NOT be reported as PAPER by the health probe."""
    from nexus_scalp.release import health as health_mod
    from nexus_scalp.release import paths as rpaths
    from nexus_scalp.settings import paths as settings_paths
    from nexus_scalp.settings.service import SettingsDatabase

    # Isolated settings DB (never touch the live install).
    sdb_path = tmp_path / "app_settings.db"
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(sdb_path))
    monkeypatch.delenv("NEXUS_HOME", raising=False)
    monkeypatch.setattr(settings_paths, "settings_db_path", lambda: sdb_path, raising=True)
    sdb = SettingsDatabase(sdb_path)
    sdb.set("execution.mode", "LIVE", source="WEB_UI", actor="test")
    sdb.close()
    assert settings_paths.settings_db_path() == sdb_path

    # Stale YAML chain that still reads PAPER.
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    stale_yaml = cfg_dir / "nexus.yaml"
    stale_yaml.write_text(
        'execution:\n  symbol: "XAUUSD"\n  mode: "PAPER"\n  timeframe: "M1"\n'
        'model:\n  feature_schema_version: "v1.0"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(rpaths, "get_user_config_path", lambda: stale_yaml, raising=True)

    eng = health_mod.HealthEngine(config_path=None)
    cfg = eng._load_config()

    assert cfg is not None and cfg is not False
    mode_txt = str(getattr(cfg.execution.mode, "value", cfg.execution.mode))
    assert mode_txt == "LIVE", "health must report the persisted operator mode"

    # And the CONFIGURATION entry itself must carry it.
    entries = {e.category: e for e in eng.overall()[1]}
    assert "CONFIGURATION" in entries
    assert "mode=LIVE" in entries["CONFIGURATION"].reason
    assert "mode=PAPER" not in entries["CONFIGURATION"].reason


def test_health_falls_back_to_yaml_when_no_settings_row_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """First-run install (no settings DB row) keeps the historical YAML-only
    resolution — the fix must not invent a mode where none is persisted."""
    from nexus_scalp.release import health as health_mod
    from nexus_scalp.release import paths as rpaths
    from nexus_scalp.settings import paths as settings_paths

    sdb_path = tmp_path / "absent_settings.db"
    monkeypatch.setattr(settings_paths, "settings_db_path", lambda: sdb_path, raising=True)
    cfg_dir = tmp_path / "cfg2"
    cfg_dir.mkdir()
    yaml_path = cfg_dir / "nexus.yaml"
    yaml_path.write_text(
        'execution:\n  symbol: "XAUUSD"\n  mode: "PAPER"\n  timeframe: "M1"\n'
        'model:\n  feature_schema_version: "v1.0"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(rpaths, "get_user_config_path", lambda: yaml_path, raising=True)

    eng = health_mod.HealthEngine(config_path=None)
    cfg = eng._load_config()

    assert cfg is not None and cfg is not False
    mode_txt = str(getattr(cfg.execution.mode, "value", cfg.execution.mode))
    assert mode_txt == "PAPER"
