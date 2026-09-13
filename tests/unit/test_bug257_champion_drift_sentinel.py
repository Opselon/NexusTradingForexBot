"""BUG-257 (remainder) regression: the champion-drift SENTINEL detects an
out-of-process rewrite of the serving artifact BETWEEN boots (alert-only).

The P0-2 trust anchor fires at boot only; BUG-271 gave governed replaces an
attribution trail. The open gap (BUG-257 RISKS/OPEN item (1)): a foreign
process re-lands drifted bytes (the 09-11 04:13 bb1f0afe shape) while the
engine runs — it serves poisoned weights silently until the next refusal.

Pins:
  1. PURE verdict: MATCH / DRIFT / INERT (mirror of the boot anchor's
     comparison: sha256-prefix-16 vs the newest CHAMPION row fingerprint).
  2. I/O probe on a REAL AuditRepository: match -> MATCH; foreign rewrite of
     the artifact -> DRIFT; no champion row / empty fingerprint / absent
     artifact / non-sqlite -> INERT (never a false alarm on normal states).
  3. ALERT EQUIVALENCE: every DRIFT verdict is a state in which the boot
     anchor (_verify_champion_registry_binding) REFUSES — sentinel and anchor
     can never disagree.
  4. WIRING (the "shipped but unwired" lesson): the stage lives on
     MaintenanceCycle.run_cycle with production callers on the runtime path
     (run_loop + duplicate-tick heartbeat), off the tick pipeline.
  5. Throttle contract: None "never ran" sentinel means the FIRST check is
     always due (uptime < interval hosts — the _last=0.0 trap); the cycle is
     failure-isolated.
  6. Alert discipline: ONE alarm at 2 consecutive sightings (governed-writer
     window), re-alarm every 4th, MATCH clears (CHAMPION_DRIFT_CLEARED).
  7. Import discipline: champion_sentinel stays torch/polars/pydantic-free at
     module scope (slim-venv gating).
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.application.live.model_bundle_store import ModelBundleStore
from nexus_scalp.experience.provenance import ModelRegistry, fingerprint_artifact
from nexus_scalp.model_lifecycle import champion_sentinel as cs
from nexus_scalp.model_lifecycle.load_integrity import ArtifactIntegrityError
from nexus_scalp.model_lifecycle.models import ModelStatus
from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry

# ---------------------------------------------------------------------------
# 1. PURE verdict
# ---------------------------------------------------------------------------


def test_pure_match_drift_inert() -> None:
    row = {
        "model_id": "primary_scalp_scalp_v3_70d",
        "artifact_fingerprint": "aaaa1111bbbb2222",
        "registered_at": "2026-09-13T00:00:00+00:00",
    }
    v = cs.evaluate_champion_drift(champion_row=row, serving_fingerprint="aaaa1111bbbb2222")
    assert v["status"] == cs.STATUS_MATCH
    v = cs.evaluate_champion_drift(champion_row=row, serving_fingerprint="bb1f0afe30f746da")
    assert v["status"] == cs.STATUS_DRIFT
    assert "bb1f0afe30f746da" in v["reason"] and "aaaa1111bbbb2222" in v["reason"]
    assert (
        cs.evaluate_champion_drift(champion_row=None, serving_fingerprint="x" * 16)["status"]
        == cs.STATUS_INERT
    )
    assert (
        cs.evaluate_champion_drift(
            champion_row={**row, "artifact_fingerprint": ""}, serving_fingerprint="x" * 16
        )["status"]
        == cs.STATUS_INERT
    )
    # absent/unreadable artifact (fingerprint_artifact -> "") is INERT, never
    # a false DRIFT on a cold-start posture.
    assert (
        cs.evaluate_champion_drift(champion_row=row, serving_fingerprint="")["status"]
        == cs.STATUS_INERT
    )


# ---------------------------------------------------------------------------
# fixture: real audit DB + registered + championed artifact on disk
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path):
    db_file = tmp_path / "sentinel.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    registry = ModelRegistry(repo)
    lifecycle = ModelLifecycleRegistry(audit_repo=repo, model_registry=registry)
    art = tmp_path / "model.pt"
    art.write_bytes(b"governed-weight-bytes-v1")
    prov = registry.register_model(
        artifact_path=art,
        model_version="v1.0",
        feature_schema_id="scalp_v3",
        feature_dimension=70,
    )
    assert repo.flush(timeout_sec=10.0)
    assert lifecycle.set_status(
        model_id=prov.model_id,
        model_version=prov.model_version,
        status=ModelStatus.CHAMPION,
        reason="test champion",
    )
    assert repo.flush(timeout_sec=10.0)
    om = SimpleNamespace(audit=repo, config=SimpleNamespace(model=None), _bundle=None)
    yield SimpleNamespace(
        repo=repo,
        registry=registry,
        lifecycle=lifecycle,
        art=art,
        db_file=db_file,
        om=om,
        prov=prov,
    )
    repo.close()


def _champion_fp(db_file: Path) -> str:
    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT artifact_fingerprint FROM experience_model_registry "
            "WHERE lifecycle_status=? ORDER BY registered_at DESC LIMIT 1;",
            (ModelStatus.CHAMPION.value,),
        ).fetchone()
        return str(row[0] or "") if row else ""
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. I/O probe on the real repository
# ---------------------------------------------------------------------------


def test_probe_matches_then_detects_foreign_rewrite(env: Any) -> None:
    assert _champion_fp(env.db_file) == fingerprint_artifact(env.art)
    om = SimpleNamespace(
        audit=env.repo,
        config=SimpleNamespace(model=SimpleNamespace(model_artifact_path=str(env.art))),
        _bundle=None,
    )
    v = cs.probe_champion_drift(om)
    assert v["status"] == cs.STATUS_MATCH, v
    # OUT-OF-PROCESS rewrite: the exact BUG-257 shape (drift bytes re-landed
    # without any governed re-registration).
    env.art.write_bytes(b"foreign-drift-bytes-bb1f0afe")
    v = cs.probe_champion_drift(om)
    assert v["status"] == cs.STATUS_DRIFT, v
    assert v["serving_sha16"] == fingerprint_artifact(env.art)
    assert v["governed_sha16"] == _champion_fp(env.db_file)


def test_probe_inert_postures(env: Any) -> None:
    # no sqlite audit -> INERT
    assert (
        cs.probe_champion_drift(
            SimpleNamespace(audit=None, config=SimpleNamespace(model=None), _bundle=None)
        )["status"]
        == cs.STATUS_INERT
    )
    # artifact absent -> fingerprint "" -> INERT (never DRIFT on cold state)
    om = SimpleNamespace(
        audit=env.repo,
        config=SimpleNamespace(model=SimpleNamespace(model_artifact_path="/nonexistent/x.pt")),
        _bundle=None,
    )
    assert cs.probe_champion_drift(om)["status"] == cs.STATUS_INERT
    # no champion row -> INERT
    conn = sqlite3.connect(env.db_file)
    conn.execute("UPDATE experience_model_registry SET lifecycle_status='ARCHIVED';")
    conn.commit()
    conn.close()
    om_ok = SimpleNamespace(
        audit=env.repo,
        config=SimpleNamespace(model=SimpleNamespace(model_artifact_path=str(env.art))),
        _bundle=None,
    )
    assert cs.probe_champion_drift(om_ok)["status"] == cs.STATUS_INERT


def test_probe_prefers_loaded_bundle_path(env: Any) -> None:
    env.art.write_bytes(b"foreign-drift-bytes-bb1f0afe")
    om = SimpleNamespace(
        audit=env.repo,
        config=SimpleNamespace(model=SimpleNamespace(model_artifact_path=str(env.art))),
        _bundle=SimpleNamespace(artifact_path=env.art),
    )
    import threading

    om._bundle_lock = threading.RLock()
    assert cs.probe_champion_drift(om)["status"] == cs.STATUS_DRIFT


# ---------------------------------------------------------------------------
# 3. ALERT EQUIVALENCE: DRIFT <=> the boot anchor refuses
# ---------------------------------------------------------------------------


def test_drift_verdict_matches_boot_anchor_refusal(env: Any) -> None:
    anchor = ModelBundleStore.__new__(ModelBundleStore)
    om = SimpleNamespace(
        audit=env.repo,
        config=None,
        _bundle=SimpleNamespace(artifact_path=env.art),
        _bundle_lock=__import__("threading").RLock(),
    )
    anchor.om = om  # the helper reads the registry through the state surface
    # serving == governed: anchor passes, sentinel MATCHes
    ModelBundleStore._verify_champion_registry_binding(anchor, env.art, actual_bytes_hash=None)
    assert (
        cs.evaluate_champion_drift(
            champion_row={"artifact_fingerprint": _champion_fp(env.db_file)},
            serving_fingerprint=fingerprint_artifact(env.art),
        )["status"]
        == cs.STATUS_MATCH
    )
    # foreign rewrite: the anchor REFUSES and the sentinel says DRIFT — the
    # two can never disagree (same sha16 comparison, same CHAMPION row).
    env.art.write_bytes(b"foreign-drift-bytes-bb1f0afe")
    with pytest.raises(ArtifactIntegrityError):
        ModelBundleStore._verify_champion_registry_binding(anchor, env.art, actual_bytes_hash=None)
    verdict = cs.probe_champion_drift(om)
    assert verdict["status"] == cs.STATUS_DRIFT


# ---------------------------------------------------------------------------
# 4. WIRING: a production caller on the runtime path exists
# ---------------------------------------------------------------------------


def test_sentinel_wired_into_maintenance_runtime_path() -> None:
    from nexus_scalp.application.live import maintenance

    src = Path(maintenance.__file__).read_text(encoding="utf-8")
    assert "CHAMPION_SENTINEL" in src
    # The stage body must live INSIDE run_cycle (the only cycle the runtime
    # drives), not in a helper nobody calls.
    start = src.index("async def run_cycle(")
    nxt = src.find("\n    def ", start)
    nxt2 = src.find("\n    async def ", start)
    end = min(x for x in (nxt, nxt2, len(src)) if x > 0)
    body = src[start:end]
    assert "probe_champion_drift" in body, "sentinel is not wired into run_cycle"
    # ...and the engine drives run_cycle from the runtime path.
    engine_src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "nexus_scalp"
        / "application"
        / "live_engine.py"
    ).read_text(encoding="utf-8")
    assert "_maintenance.run_cycle" in engine_src


# ---------------------------------------------------------------------------
# 5-6. MaintenanceCycle stage behavior (throttle + alert discipline)
# ---------------------------------------------------------------------------


class _Notifier:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, text: str, severity: str = "INFO", event_type: str = "GENERIC", **kw: Any):
        self.sent.append((severity, event_type))


def _cycle(om: Any):
    from nexus_scalp.application.live.maintenance import MaintenanceCycle

    return MaintenanceCycle(om)


def _base_om(audit: Any, notifier: Any) -> Any:
    return SimpleNamespace(
        audit=audit,
        notifier=notifier,
        _last_audit_purge_time=0.0,
        _audit_purge_interval_sec=float("inf"),
        _parity_export_interval_sec=float("inf"),
        _parity_snapshot_interval_sec=float("inf"),
        _last_operational_digest_time=0.0,
        _operational_digest_interval_sec=float("inf"),
        _last_hygiene_time=float("inf"),
        _hygiene_scheduler=None,
        _last_incident_time=0.0,
        _incident_interval_sec=float("inf"),
        _storage_guard=object(),  # non-None so its stage skips construction
        _storage_cycle_interval_sec=float("inf"),
        _last_storage_cycle_time=0.0,
        _last_daily_summary_time=0.0,
        _daily_summary_interval_sec=float("inf"),
        _history_sync_started=False,
        _intelligence_worker_started=False,
        _research_worker_started=False,
        _factory_worker_started=False,
        _training_worker_started=False,
        _shadow_worker_started=False,
        _news_enabled=False,
        _news_worker_started=False,
    )


def _run(monkeypatch, cycle: Any, verdicts: list[dict[str, Any]]) -> None:
    calls = {"n": 0}

    def fake_probe(_om: Any) -> dict[str, Any]:
        v = verdicts[min(calls["n"], len(verdicts) - 1)]
        calls["n"] += 1
        return v

    monkeypatch.setattr(cs, "probe_champion_drift", fake_probe)
    # each _run simulates one DUE sentinel pass (throttle reset; the
    # throttle/first-due contract itself is pinned separately)
    cycle._last_champion_sentinel_time = None
    asyncio.run(cycle.run_cycle(now_t=time.time()))


def test_first_check_always_due_none_sentinel(monkeypatch: Any, env: Any) -> None:
    """The throttle-sentinel trap: _last=0.0 vs time.monotonic()/time.time()
    skips the FIRST pass on a freshly started host. None => always due."""
    cycle = _cycle(_base_om(env.repo, _Notifier()))
    assert cycle._last_champion_sentinel_time is None
    _run(monkeypatch, cycle, [{"status": cs.STATUS_MATCH}])
    assert cycle._last_champion_sentinel_time is not None


def test_sentinel_throttled_between_intervals(monkeypatch: Any, env: Any) -> None:
    """One probe per interval window: the stage must not re-read the artifact
    every maintenance pass (a 1.3MB sha256 per 60s heartbeat is log noise)."""
    calls = {"n": 0}

    def counting_probe(_om: Any) -> dict[str, Any]:
        calls["n"] += 1
        return {"status": cs.STATUS_MATCH}

    monkeypatch.setattr(cs, "probe_champion_drift", counting_probe)
    cycle = _cycle(_base_om(env.repo, _Notifier()))
    cycle._champion_sentinel_interval_sec = 900.0
    asyncio.run(cycle.run_cycle(now_t=0.0))  # first pass due (None sentinel)
    asyncio.run(cycle.run_cycle(now_t=0.0))  # interval not elapsed: skipped
    assert calls["n"] == 1, calls
    # rewind the stamp to simulate an elapsed interval (run_cycle re-reads
    # wall time internally)
    cycle._last_champion_sentinel_time = time.time() - 901
    asyncio.run(cycle.run_cycle(now_t=0.0))
    assert calls["n"] == 2, calls


def test_drift_alerts_once_at_two_sightings_then_clears(monkeypatch: Any, env: Any) -> None:
    notifier = _Notifier()
    cycle = _cycle(_base_om(env.repo, notifier))
    drift = {"status": cs.STATUS_DRIFT, "serving_sha16": "f" * 16, "governed_sha16": "a" * 16}
    # a single sighting (governed-writer window) must NOT alarm
    _run(monkeypatch, cycle, [drift])
    assert notifier.sent == []
    assert cycle._champion_sentinel_failures == 1
    # second consecutive sighting confirms
    _run(monkeypatch, cycle, [drift])
    assert ("CRITICAL", "CHAMPION_DRIFT_CONFIRMED") in notifier.sent
    # 3rd and 4th sightings: no spam (re-alarm only every 4th after confirm)
    _run(monkeypatch, cycle, [drift])
    _run(monkeypatch, cycle, [drift])
    assert len(notifier.sent) == 1
    assert cycle._champion_sentinel_failures == 4
    # MATCH clears the streak (and resets, so a fresh drift re-arms at 2)
    _run(monkeypatch, cycle, [{"status": cs.STATUS_MATCH}])
    assert cycle._champion_sentinel_failures == 0
    _run(monkeypatch, cycle, [drift])
    assert len(notifier.sent) == 1  # one sighting: silent again


def test_inert_never_alarms_and_isolates_failures(monkeypatch: Any, env: Any) -> None:
    notifier = _Notifier()
    cycle = _cycle(_base_om(env.repo, notifier))
    _run(monkeypatch, cycle, [{"status": cs.STATUS_INERT, "reason": "no_champion_row"}])
    assert notifier.sent == [] and cycle._champion_sentinel_failures == 0

    def boom(_om: Any) -> dict[str, Any]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cs, "probe_champion_drift", boom)
    asyncio.run(cycle.run_cycle(now_t=time.time()))  # must not raise


def test_sentinel_uses_real_repository_end_to_end(monkeypatch: Any, env: Any) -> None:
    """No monkeypatching: real WAL audit DB, real artifact bytes. Foreign
    rewrite -> two maintenance passes -> one CRITICAL notification whose text
    carries both sha16 evidence stamps."""
    notifier = _Notifier()
    om = _base_om(env.repo, notifier)
    om.config = SimpleNamespace(model=SimpleNamespace(model_artifact_path=str(env.art)))
    om._bundle = None
    om._bundle_lock = __import__("threading").RLock()
    cycle = _cycle(om)
    cycle._champion_sentinel_interval_sec = 0.0
    env.art.write_bytes(b"foreign-drift-bytes-bb1f0afe")
    asyncio.run(cycle.run_cycle(now_t=time.time()))
    assert notifier.sent == []
    asyncio.run(cycle.run_cycle(now_t=time.time()))
    assert ("CRITICAL", "CHAMPION_DRIFT_CONFIRMED") in notifier.sent
    # the pure probe leg really ran against the DB:
    v = cs.probe_champion_drift(om)
    assert v["status"] == cs.STATUS_DRIFT


# ---------------------------------------------------------------------------
# 7. import discipline (maintenance-path gating lesson)
# ---------------------------------------------------------------------------


def test_champion_sentinel_import_stays_light() -> None:
    """Maintenance-path gating lesson: nothing reachable from the cycle may
    drag the torch/polars import chain (the slim-venv gate + tick-path safety).
    Subprocess keeps the host environment (only PYTHONPATH is pinned): a
    stripped PATH broke interpreter launch on windows-latest.
    """
    import os
    import subprocess
    import sys

    code = (
        "import sys; import nexus_scalp.model_lifecycle.champion_sentinel; "
        "print(any(m in sys.modules for m in ('torch','polars')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(["src", "."])},
        cwd=str(Path(__file__).resolve().parents[2]),
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.strip().endswith("False"), out.stdout


def test_logging_severity_routes_critical(env: Any, monkeypatch: Any) -> None:
    """The confirmed-drift alarm must go out at CRITICAL severity. Captured by
    replacing the maintenance module's logger with a recorder — deterministic
    under xdist regardless of structlog configuration (the PrintLogger->stdout
    shape a capsys pin depends on is host-dependent; CI proved it by seeing
    '' where the local slim venv saw the line)."""
    from nexus_scalp.application.live import maintenance as maint_mod

    notifier = _Notifier()
    cycle = _cycle(_base_om(env.repo, notifier))
    drift = {"status": cs.STATUS_DRIFT, "serving_sha16": "f" * 16, "governed_sha16": "a" * 16}

    logged: list[tuple[str, str]] = []

    class _Rec:
        def critical(self, fmt: str, *args: Any, **kw: Any) -> None:
            logged.append(("critical", fmt % args if args else fmt))

        def warning(self, fmt: str, *args: Any, **kw: Any) -> None:
            logged.append(("warning", fmt % args if args else fmt))

        def info(self, fmt: str, *args: Any, **kw: Any) -> None:
            logged.append(("info", fmt % args if args else fmt))

        def debug(self, fmt: str, *args: Any, **kw: Any) -> None:
            logged.append(("debug", fmt % args if args else fmt))

        def error(self, fmt: str, *args: Any, **kw: Any) -> None:
            logged.append(("error", fmt % args if args else fmt))

    monkeypatch.setattr(maint_mod, "logger", _Rec())

    def fake_probe(_om: Any) -> dict[str, Any]:
        return drift

    monkeypatch.setattr(cs, "probe_champion_drift", fake_probe)
    cycle._champion_sentinel_failures = 1  # pre-arm: next sighting confirms
    asyncio.run(cycle.run_cycle(now_t=time.time()))
    assert ("CRITICAL", "CHAMPION_DRIFT_CONFIRMED") in notifier.sent
    crit = [m for lvl, m in logged if lvl == "critical"]
    assert any(
        "CHAMPION_SENTINEL" in m and "CHAMPION_DRIFT_CONFIRMED" in m and "ffff" in m for m in crit
    ), logged
