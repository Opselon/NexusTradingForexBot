"""Lane G — end-to-end PostgreSQL verification probe (verification lane only).

Runs against the ISOLATED PG cluster spun up by the operator harness:
    postgresql://nse_user:***@127.0.0.1:55435/nse_audit

NEVER connects to the live localhost:5432. Read-only w.r.t. source: this file
only IMPORTS nexus_scalp and drives the public write/read surfaces, so a defect
it finds is evidence for the fixing lane, not something to patch here.

Phases (each phase's result is captured to JSON for the report):
  0. connection + boot-level DB provisioning (provision_domain / AuditRepository)
  1. BEFORE empty-tables probe  (SELECT count(*) per table)
  2. write+read smoke, one row through every previously-empty domain
  3. AFTER empty-tables probe
  4. ms-level latency per domain (monotonic, min/avg/p95) + audit batch insert
     hot-path latency
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Isolated-cluster contract
# --------------------------------------------------------------------------- #

PG_PORT = "55435"
PG_HOST = "127.0.0.1"
PG_USER = "nse_user"
PG_DATABASE = "nse_audit"
PG_PASSWORD = "nse_password_dev"  # CI value for the throwaway isolated cluster

_DSN = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"

# The live cluster the operator is migrating away from. NEVER touched here.
_LIVE_PORT = "5432"


def _log(msg: str) -> None:
    print(f"[lane-g] {msg}", flush=True)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# Direct psycopg helpers (cluster-side ground truth, bypassing the app)
# --------------------------------------------------------------------------- #


def _pg_connect():
    import psycopg

    return psycopg.connect(_DSN)


def list_tables() -> list[str]:
    with _pg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        return [str(r[0]) for r in cur.fetchall()]


def table_counts() -> dict[str, int]:
    """SELECT count(*) per table — the same probe the operator used."""
    tables = list_tables()
    out: dict[str, int] = {}
    with _pg_connect() as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            out[table] = int(cur.fetchone()[0])
    return out


def run_sql(sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _pg_connect() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args))
        if cur.description is None:
            return []
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Timing helpers
# --------------------------------------------------------------------------- #


class _Latency:
    """ms-level samples (monotonic), min/avg/p95 reporting."""

    def __init__(self) -> None:
        self.samples: list[float] = []

    def time(self, fn, *args, **kwargs):
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        self.samples.append((time.perf_counter() - t0) * 1000.0)
        return result

    def stats(self) -> dict[str, float]:
        if not self.samples:
            return {"n": 0, "min_ms": 0.0, "avg_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
        ordered = sorted(self.samples)
        idx = max(0, min(len(ordered) - 1, round(0.95 * (len(ordered) - 1))))
        return {
            "n": len(ordered),
            "min_ms": round(ordered[0], 3),
            "avg_ms": round(statistics.fmean(ordered), 3),
            "p95_ms": round(ordered[idx], 3),
            "max_ms": round(ordered[-1], 3),
        }


# --------------------------------------------------------------------------- #
# Phase 0 — boot on PG
# --------------------------------------------------------------------------- #


def boot_on_pg(result: dict[str, Any]) -> None:
    from nexus_scalp.database.config import (
        build_postgres_url,
        load_database_config,
        mask_url_password,
    )
    from nexus_scalp.settings.secret_store import SecureSecretStore

    cfg = load_database_config("audit")
    result["provider_resolved"] = getattr(getattr(cfg, "provider", None), "value", "")
    result["pg_host"] = cfg.host
    result["pg_port"] = cfg.port
    result["pg_database"] = cfg.database
    result["pg_user"] = cfg.username
    result["dsn_masked"] = mask_url_password(build_postgres_url(cfg, SecureSecretStore()))
    _log(f"resolved provider={result['provider_resolved']} dsn={result['dsn_masked']}")

    # Guard: this probe must never run against the live cluster.
    assert cfg.port != int(_LIVE_PORT), "refusing to connect to the live 5432 cluster"

    boot_steps: list[dict[str, Any]] = []

    def _step(name: str, fn) -> Any:
        t0 = time.perf_counter()
        try:
            value = fn()
            fn.__result__ = value  # type: ignore[attr-defined]
            boot_steps.append(
                {"step": name, "ok": True, "ms": round((time.perf_counter() - t0) * 1000.0, 1)}
            )
        except Exception as exc:
            boot_steps.append(
                {
                    "step": name,
                    "ok": False,
                    "ms": round((time.perf_counter() - t0) * 1000.0, 1),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        return fn.__result__ if hasattr(fn, "__result__") else None

    # The engine_boot startup migration gate (SQLite-only paths, so it is a
    # no-op on PG: it reports "database not created yet"). Recorded honestly.
    def _migration_gate() -> None:
        from nexus_scalp.database.gate import run_startup_migration_gate

        gate = run_startup_migration_gate(workspace=Path.cwd(), application_version="lane-g-probe")
        result["startup_migration_gate"] = gate
        _log(f"startup migration gate: ready={gate.get('ready')} state={gate.get('state')}")

    def _fabric_provision() -> None:
        from nexus_scalp.database.fabric import provision_domain

        backend = provision_domain("audit", _DSN, min_size=1, max_size=4)
        result["audit_backend"] = type(backend).__name__
        _log(f"audit domain provisioned -> {result['audit_backend']}")

    def _audit_repo_construct() -> Any:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        repo = AuditRepository()
        result["audit_repo_is_sqlite"] = repo._is_sqlite
        result["audit_repo_write_plane"] = type(repo._write_plane).__name__
        # NOTE: deliberately NOT closed here. close() drains the fabric's
        # shared pooled backends and leaves the domain registry holding a
        # dead pool (a real finding, recorded separately); the smoke phase
        # below reuses this same repository, and the end of main() closes it.
        _log(
            f"AuditRepository() constructed on PG: is_sqlite={result['audit_repo_is_sqlite']} "
            f"plane={result['audit_repo_write_plane']}"
        )
        return repo

    _step("startup_migration_gate", _migration_gate)
    _step("fabric_provision_audit", _fabric_provision)
    repo = _step("audit_repository_construct", _audit_repo_construct)
    result["boot_steps"] = boot_steps
    result["boot_on_pg"] = all(s.get("ok") for s in boot_steps)
    return repo


def _open_smoke_repo():
    """A fresh AuditRepository bound to the isolated PG (its own worker thread)."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    return AuditRepository()


def _close_smoke_repo(repo) -> None:
    with _Suppress():
        repo.close()


# --------------------------------------------------------------------------- #
# Domain smoke drivers
# --------------------------------------------------------------------------- #


def _make_proposal():
    from datetime import UTC, datetime

    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.domain.models import TradeProposal

    return TradeProposal(
        request_id=f"lane-g-{os.getpid()}-{int(time.time() * 1000)}",
        symbol="EURUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY,
        confidence=0.72,
        proposed_entry=1.0850,
        stop_loss=1.0820,
        take_profit=1.0910,
        risk_reward_ratio=2.0,
        reason_code="MODEL_SIGNAL",
    )


def _drain_audit_plane(repo, timeout: float = 15.0) -> None:
    """Wait for the audit write plane to flush queued rows to the provider."""
    deadline = time.time() + timeout
    plane = getattr(repo, "_write_plane", None)
    while time.time() < deadline:
        try:
            if plane is not None and callable(getattr(plane, "flush", None)):
                if bool(plane.flush(timeout_sec=2.0)):
                    return
        except Exception:
            pass
        try:
            q = plane._effective_queue() if plane is not None else None
            if q is not None and q.qsize() == 0:
                return
        except Exception:
            return
        time.sleep(0.05)


def _smoke_audit_signals(repo, lat: _Latency) -> dict[str, Any]:
    """audit_signals via the audit repository's log path (tick hot path)."""
    out: dict[str, Any] = {"domain": "audit", "table": "audit_signals"}
    proposal = _make_proposal()
    try:
        lat.time(repo.log_signal, proposal)
        # The write plane is an async queue under SQLite and an eager pool
        # under PG; either way, wait for the flush before reading back.
        _drain_audit_plane(repo)
        rows = lat.time(
            run_sql, "SELECT * FROM audit_signals WHERE request_id = %s", (proposal.request_id,)
        )
        out["write"] = bool(rows)
        out["read"] = bool(rows)
        out["key"] = proposal.request_id
    except Exception as exc:
        out["write"] = False
        out["read"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _smoke_guard_telemetry(repo, lat: _Latency) -> dict[str, Any]:
    """audit_guard_telemetry upsert — the proven live-failing statement."""
    from datetime import UTC, datetime

    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.domain.models import TradeProposal

    out: dict[str, Any] = {"domain": "audit", "table": "audit_guard_telemetry"}
    proposal = TradeProposal(
        request_id="lane-g-guard-telemetry",
        symbol="EURUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.NO_TRADE,
        confidence=0.1,
        proposed_entry=1.0,
        stop_loss=0.9,
        take_profit=1.1,
        risk_reward_ratio=1.0,
        reason_code="TICK_DUPLICATE_SUPPRESSED",
    )
    window = proposal.generated_at.replace(second=0, microsecond=0).isoformat()
    try:
        lat.time(repo._log_guard_telemetry, proposal, "TICK_DUPLICATE_SUPPRESSED")
        _drain_audit_plane(repo)
        rows = lat.time(
            run_sql,
            "SELECT count FROM audit_guard_telemetry WHERE window_start = %s AND symbol = %s",
            (window, "EURUSD"),
        )
        out["write"] = bool(rows)
        out["read"] = bool(rows)
        out["count"] = int(rows[0]["count"]) if rows else 0
        out["key"] = window
    except Exception as exc:
        out["write"] = False
        out["read"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _smoke_governance(repo, lat: _Latency) -> dict[str, Any]:
    from nexus_scalp.governance.models import GovernanceEvent, GovernanceStage
    from nexus_scalp.governance.store import GovernanceStore

    out: dict[str, Any] = {"domain": "ops_shadow", "table": "model_governance_events"}
    store = GovernanceStore(audit_repo=repo)
    event = GovernanceEvent(
        event_id=f"evt-lane-g-{int(time.time() * 1000)}",
        event="LANE_G_SMOKE",
        stage=GovernanceStage.PROMOTION,
        model_id="lane-g-model",
        model_version="0.0.1",
        timestamp=datetime.now(UTC),
    )
    try:
        ok = bool(lat.time(store.record_event, event))
        out["write"] = ok
        rows = lat.time(
            run_sql,
            "SELECT event_id FROM model_governance_events WHERE event_id = %s",
            (event.event_id,),
        )
        out["read"] = bool(rows)
        out["key"] = event.event_id
        if not ok:
            out["error"] = "record_event returned False (see [DB-FABRIC] log lines above)"
    except Exception as exc:
        out["write"] = False
        out["read"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _smoke_shadow(repo, lat: _Latency) -> dict[str, Any]:
    from nexus_scalp.shadow.models import (
        ShadowComparison,
        ShadowDecisionRecord,
        ShadowModelRef,
        ShadowRun,
    )
    from nexus_scalp.shadow.store import ShadowStore

    out: dict[str, Any] = {"domain": "ops_shadow", "table": "shadow_runs"}
    store = ShadowStore(audit_repo=repo)
    champ = ShadowModelRef(model_id="lane-g-champ", model_version="1.0.0", is_champion=True)
    chall = ShadowModelRef(model_id="lane-g-chall", model_version="0.9.0")
    run_id = f"run-lane-g-{int(time.time() * 1000)}"
    run = ShadowRun(
        run_id=run_id,
        champion=champ,
        challenger=chall,
        status="RUNNING",
        started_at=datetime.now(UTC),
        decision_count=1,
    )
    try:
        ok = bool(lat.time(store.save_run, run))
        out["write"] = ok
        rows = lat.time(run_sql, "SELECT run_id FROM shadow_runs WHERE run_id = %s", (run_id,))
        out["read"] = bool(rows)
        out["key"] = run_id
        if not ok:
            out["error"] = "save_run returned False (see [DB-FABRIC] log lines above)"
    except Exception as exc:
        out["write"] = False
        out["read"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _smoke_incidents(repo, lat: _Latency) -> dict[str, Any]:
    from nexus_scalp.incidents.models import (
        Incident,
        IncidentCategory,
        IncidentSeverity,
        IncidentStatus,
    )
    from nexus_scalp.incidents.store import IncidentStore

    out: dict[str, Any] = {"domain": "audit", "table": "incidents"}
    store = IncidentStore(audit_repo=repo)
    incident = Incident(
        incident_id=f"inc-lane-g-{int(time.time() * 1000)}",
        severity=IncidentSeverity.MEDIUM,
        category=IncidentCategory.DATA,
        status=IncidentStatus.OPEN,
        component="database",
        operation="lane_g_smoke",
    )
    try:
        lat.time(store.save, incident)
        # IncidentStore.save() puts onto the audit repository's background
        # queue (store.py:543) rather than hitting the write backend, so wait
        # for the audit plane to flush before reading back.
        _drain_audit_plane(repo)
        out["write"] = True
        rows = lat.time(
            run_sql,
            "SELECT incident_id FROM incidents WHERE incident_id = %s",
            (incident.incident_id,),
        )
        out["read"] = bool(rows)
        out["key"] = incident.incident_id
    except Exception as exc:
        out["write"] = False
        out["read"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    # Capture the exact driver error string when the queue's batch flush
    # rejected the row — that string IS the fixing lane's spec.
    err = getattr(repo, "audit_batch_error", None) or getattr(repo, "audit_last_error", None)
    if not err:
        dl = repo.dead_letter_store
        try:
            recent = dl.list_recent(limit=3) if hasattr(dl, "list_recent") else []
        except Exception:
            recent = []
        for row in recent or []:
            note = str(
                getattr(row, "error", "") or (row.get("error") if isinstance(row, dict) else "")
            )
            if "placeholder" in note or "incident" in note:
                err = note
                break
    if err:
        out["dead_letter_error"] = str(err)
    if out.get("write") and not out.get("read") and not out.get("error"):
        out["error"] = (
            "row left the audit queue but never landed in incidents — likely the "
            "named-placeholder (:name) SQL translated with 0 placeholders "
            "(see the dead-letter audit_batch_error string)"
        )
    return out


def _smoke_hygiene(repo, lat: _Latency) -> dict[str, Any]:
    """hygiene_run_history — the SQLite-only hygiene state store.

    HygieneStateStore hardcodes ``sqlite3.connect`` (hygiene/state.py:77), so
    on a PostgreSQL provider this domain cannot write at all. Driven here to
    PROVE that with the exact error string, which is the fixing lane's spec.
    """
    from nexus_scalp.hygiene import WorkerState
    from nexus_scalp.hygiene.state import HygieneStateStore

    out: dict[str, Any] = {"domain": "ops_hygiene", "table": "hygiene_run_history"}
    workspace = Path(os.environ.get("NSE_HYGIENE_WS") or (Path.cwd() / "artifacts"))
    try:
        store = HygieneStateStore(root=workspace)
        store.set_state(WorkerState.IDLE)
        store.record_run(
            {
                "run_id": f"hyg-lane-g-{int(time.time() * 1000)}",
                "database": "audit",
                "started_at": _now_iso(),
                "finished_at": _now_iso(),
                "duration_ms": 12.0,
                "mode": "AUDIT_ONLY",
            }
        )
        runs = store.list_runs(limit=5)
        out["write"] = any(r.get("run_id", "").startswith("hyg-lane-g-") for r in runs)
        out["read"] = bool(runs)
        out["note"] = (
            "HygieneStateStore opened its own sqlite3 file (state.py:77 hardcodes "
            "sqlite3.connect) — the row landed in SQLite, NOT on PostgreSQL"
        )
        out["sqlite_path"] = str(store._db_path)
    except Exception as exc:
        out["write"] = False
        out["read"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _smoke_candle_intel(repo, lat: _Latency) -> dict[str, Any]:
    """candle_intel — a declared DatabaseDomain with a registered backend."""
    out: dict[str, Any] = {"domain": "candle_intel", "table": "candles"}
    try:
        from nexus_scalp.database.ops_provider import domain_dsn, resolve_pooled_backend

        backend = resolve_pooled_backend("candle_intel", domain_dsn("candle_intel"))
        out["backend"] = type(backend).__name__ if backend else None
        if backend is None:
            out["write"] = False
            out["read"] = False
            out["error"] = "no pooled write backend for candle_intel"
            return out
        from datetime import UTC, datetime

        ts = datetime.now(UTC).isoformat()
        lat.time(
            backend.execute,
            "INSERT INTO candles (symbol, timeframe, ts, bar_ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            ("EURUSD", "M1", ts, ts, 1.08, 1.09, 1.07, 1.085, 100.0),
        )
        rows = lat.time(run_sql, "SELECT ts FROM candles WHERE ts = %s", (ts,))
        out["write"] = bool(rows)
        out["read"] = bool(rows)
        out["key"] = ts
    except Exception as exc:
        out["write"] = False
        out["read"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _smoke_news(repo, lat: _Latency) -> dict[str, Any]:
    from datetime import UTC, datetime

    out: dict[str, Any] = {"domain": "news", "table": "news_sources"}
    try:
        from nexus_scalp.database.ops_provider import domain_dsn, resolve_pooled_backend

        backend = resolve_pooled_backend("news", domain_dsn("news"))
        out["backend"] = type(backend).__name__ if backend else None
        if backend is None:
            out["write"] = False
            out["read"] = False
            out["error"] = "no pooled write backend for news"
            return out
        lat.time(
            backend.execute,
            "INSERT INTO news_sources (source_id, name, kind, tier, enabled, poll_interval_sec, "
            "priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            ("lane-g-src", "lane-g", "rss", "PRIMARY", 1, 300, 1.0, datetime.now(UTC).isoformat()),
        )
        rows = lat.time(
            run_sql, "SELECT source_id FROM news_sources WHERE source_id = %s", ("lane-g-src",)
        )
        out["write"] = bool(rows)
        out["read"] = bool(rows)
        out["key"] = "lane-g-src"
    except Exception as exc:
        out["write"] = False
        out["read"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# --------------------------------------------------------------------------- #
# Audit batch insert hot path
# --------------------------------------------------------------------------- #


def audit_batch_latency(
    repo, lat: _Latency, n_batches: int = 20, rows_per_batch: int = 25
) -> dict[str, Any]:
    """The tick hot path: batched audit_signals inserts through the write plane."""
    out: dict[str, Any] = {"rows_per_batch": rows_per_batch, "n_batches": n_batches}
    try:
        from nexus_scalp.database.fabric import get_domain_backend

        backend = get_domain_backend("audit", readonly=False)
        if backend is None:
            out["error"] = "no pooled write backend for audit"
            return out
        base = time.time()
        for b in range(n_batches):
            batch = []
            for r in range(rows_per_batch):
                rid = f"batch-{base}-{b}-{r}"
                batch.append(
                    (
                        rid,
                        "EURUSD",
                        "BUY",
                        0.5,
                        1.08,
                        1.07,
                        1.09,
                        "UNKNOWN",
                        datetime.now(UTC).isoformat(),
                        "{}",
                    )
                )
            # signal_dedup_key is NULL for these synthetic rows -> ON CONFLICT
            # (signal_dedup_key) cannot fire, so use the plain batch API.
            plain = (
                "INSERT INTO audit_signals (request_id, symbol, action, confidence, proposed_entry, "
                "stop_loss, take_profit, regime, generated_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING"
            )
            lat.time(backend.executemany, plain, batch)
        out["ok"] = True
        out["stats"] = lat.stats()
    except Exception as exc:
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class _Suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


def main() -> dict[str, Any]:
    result: dict[str, Any] = {
        "probe": "lane-g-pg-e2e",
        "started_at": _now_iso(),
        "isolated_dsn": f"postgresql://{PG_USER}:***@{PG_HOST}:{PG_PORT}/{PG_DATABASE}",
    }

    # ---- phase 0: boot ----------------------------------------------------
    repo = None
    try:
        repo = boot_on_pg(result)
    except Exception as exc:
        result["boot_on_pg"] = False
        result["boot_error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        _finish(result)
        return result

    # ---- phase 1: BEFORE counts -------------------------------------------
    before = table_counts()
    result["empty_before"] = sum(1 for c in before.values() if c == 0)
    result["total_tables_before"] = len(before)
    _log(f"BEFORE: {len(before)} tables, {result['empty_before']} empty")

    # ---- phase 2 + 4: write/read smoke with latency ------------------------
    if repo is None:
        repo = _open_smoke_repo()
    smoke: list[dict[str, Any]] = []
    per_domain_lat: dict[str, _Latency] = {
        "audit_signals": _Latency(),
        "audit_guard_telemetry": _Latency(),
        "governance": _Latency(),
        "shadow": _Latency(),
        "incidents": _Latency(),
        "candle_intel": _Latency(),
        "news": _Latency(),
    }
    drivers = [
        ("audit_signals", lambda: _smoke_audit_signals(repo, per_domain_lat["audit_signals"])),
        (
            "audit_guard_telemetry",
            lambda: _smoke_guard_telemetry(repo, per_domain_lat["audit_guard_telemetry"]),
        ),
        ("governance", lambda: _smoke_governance(repo, per_domain_lat["governance"])),
        ("shadow", lambda: _smoke_shadow(repo, per_domain_lat["shadow"])),
        ("incidents", lambda: _smoke_incidents(repo, per_domain_lat["incidents"])),
        ("hygiene", lambda: _smoke_hygiene(repo, _Latency())),
        ("candle_intel", lambda: _smoke_candle_intel(repo, per_domain_lat["candle_intel"])),
        ("news", lambda: _smoke_news(repo, per_domain_lat["news"])),
    ]
    for name, driver in drivers:
        t0 = time.perf_counter()
        try:
            entry = driver()
        except Exception as exc:
            entry = {
                "domain": name,
                "write": False,
                "read": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        entry["domain_key"] = name
        entry["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        smoke.append(entry)
        _log(
            f"smoke {name}: write={entry.get('write')} read={entry.get('read')} "
            f"({entry['total_ms']}ms) err={entry.get('error') or entry.get('note') or '-'}"
        )
    result["domains_write_read"] = {
        (e.get("table") or e["domain_key"]): {
            "write": bool(e.get("write")),
            "read": bool(e.get("read")),
        }
        for e in smoke
    }
    result["smoke_details"] = smoke

    # audit batch hot path
    batch_lat = _Latency()
    result["audit_batch_insert"] = audit_batch_latency(repo, batch_lat)

    with _Suppress():
        _close_smoke_repo(repo)

    # ---- phase 3: AFTER counts --------------------------------------------
    after = table_counts()
    result["empty_after"] = sum(1 for c in after.values() if c == 0)
    result["total_tables_after"] = len(after)
    _log(f"AFTER: {len(after)} tables, {result['empty_after']} empty")

    result["tables_written"] = sorted([t for t in after if after[t] > 0 and before.get(t, 0) == 0])
    result["still_empty"] = sorted([t for t in after if after[t] == 0])
    result["table_counts_after"] = after

    # ---- phase 4: latency roll-up -----------------------------------------
    result["perf_ms"] = {
        "audit_signals": per_domain_lat["audit_signals"].stats(),
        "audit_guard_telemetry": per_domain_lat["audit_guard_telemetry"].stats(),
        "governance": per_domain_lat["governance"].stats(),
        "shadow": per_domain_lat["shadow"].stats(),
        "incidents": per_domain_lat["incidents"].stats(),
        "candle_intel": per_domain_lat["candle_intel"].stats(),
        "news": per_domain_lat["news"].stats(),
        "audit_batch_insert": batch_lat.stats(),
    }
    result["tests_passed"] = bool(
        result.get("boot_on_pg")
        and all(e.get("write") and e.get("read") for e in smoke if e["domain_key"] != "hygiene")
    )
    result["finished_at"] = _now_iso()
    _finish(result)
    return result


def _finish(result: dict[str, Any]) -> None:
    out_path = Path(os.environ.get("LANE_G_RESULT") or "lane_g_result.json")
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    _log(f"result written to {out_path}")


if __name__ == "__main__":
    sys.exit(0 if main().get("tests_passed") else 1)
