"""Lane G (LIVE wave) - end-to-end PG verification on the canonical nexusdb.

Scope (user-directed, supersedes the isolated-cluster wave):
  1. boot on PG without RuntimeError (DB-provisioning path of engine_boot)
  2. one write+read through every previously-empty domain
  3. ms-level write/read latency per domain
  4. backend API + WebSocket + web UI read REAL rows from nexusdb
  5. SQLite fallback still functions

No NSE_DATABASE__PG_* env vars: everything resolves through
load_database_config, which is pinned to localhost:5432/nexusdb.
No src/ changes; probe only.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_pg_lib import Stopwatch, connect, stats_ms, table_counts

OUT = Path("C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/lane_g_live_smoke.json")
MARKER = "lane-g-live-"
res: dict[str, Any] = {
    "probe": "lane-g-live-smoke",
    "started_at": datetime.now(UTC).isoformat(),
    "marker_prefix": MARKER,
}


def _log(msg: str) -> None:
    print(f"[lane-g-live] {msg}", flush=True)


def _key(tag: str) -> str:
    return f"{MARKER}{tag}-{int(time.time() * 1000) % 10**9}"


def _pg_count(table: str, where: str = "", args: tuple[Any, ...] = ()) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{table}"' + (f" WHERE {where}" if where else ""), args)
        return int(cur.fetchone()[0])


# ---------------------------------------------------------------- boot on PG


def _mask(url: str) -> str:
    """Mask the password in a DSN before recording it in evidence."""
    import re

    return re.sub(r"(://[^:]+:)[^@]*@", r"\1***@", url)


def boot_on_pg() -> dict[str, Any]:
    """Exercise the DB-provisioning path of engine_boot on the live nexusdb."""
    out: dict[str, Any] = {"steps": []}

    def step(name: str, fn: Any) -> None:
        t0 = time.perf_counter()
        try:
            fn()
            out["steps"].append(
                {"step": name, "ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}
            )
        except Exception as exc:
            out["steps"].append(
                {
                    "step": name,
                    "ok": False,
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=4),
                }
            )

    def startup_gate() -> None:
        from nexus_scalp.database.gate import run_startup_migration_gate

        r = run_startup_migration_gate()
        out["migration_gate"] = {
            "state": str(r.get("state")),
            "ready": bool(r.get("ready")),
            "databases": {
                k: v.get("state") for k, v in (r.get("databases") or {}).items()
            },
        }
        if not r.get("ready"):
            raise RuntimeError(f"migration gate not ready: {r}")

    def fabric_provision() -> None:
        # Resolve the DSN the SAME way the app does (domain_dsn ->
        # load_database_config + build_postgres_url + secret store).
        # Passing a placeholder-DSN here would open pools that cannot auth and
        # would clobber the app's own good registration, so this must use the
        # real resolved DSN or be skipped entirely.
        from nexus_scalp.database.ops_provider import domain_dsn

        for dom in ("audit", "news", "candle_intel"):
            dsn = domain_dsn(dom)
            out.setdefault("resolved_dsn_masked", {})[dom] = _mask(dsn)
            from nexus_scalp.database.fabric import get_domain_backend

            backend = get_domain_backend(dom, readonly=False)
            out.setdefault("provisioned", []).append(
                {"domain": dom, "already_registered": backend is not None, "backend": type(backend).__name__}
            )

    def audit_repo() -> None:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.database.config import load_database_config

        repo = AuditRepository(config=load_database_config("audit"))
        out["audit_repo_is_sqlite"] = bool(getattr(repo, "_is_sqlite", True))
        wp = getattr(repo, "_build_pooled_write_backend", lambda: None)()
        out["audit_write_plane"] = type(wp).__name__ if wp is not None else None
        rp = getattr(repo, "_registered_audit_read_plane", lambda: None)()
        out["audit_read_plane"] = type(rp).__name__ if rp is not None else None

    step("startup_migration_gate", startup_gate)
    step("fabric_provision_domains", fabric_provision)
    step("audit_repository_construct", audit_repo)

    out["boot_on_pg"] = bool(all(s["ok"] for s in out["steps"]))
    if not out["boot_on_pg"]:
        out["runtime_error"] = next((s.get("error") for s in out["steps"] if not s["ok"]), None)
    return out


# ------------------------------------------------------------ domain smokes


def smoke_audit_signals() -> dict[str, Any]:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database.config import load_database_config
    from nexus_scalp.domain.models import TradeProposal

    key = _key("sig")
    repo = AuditRepository(config=load_database_config("audit"))
    prop = TradeProposal(
        request_id=key,
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action="NO_TRADE",
        confidence=0.012,
        proposed_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2660.0,
        risk_reward_ratio=1.0,
        reason_code="MODEL_SIGNAL",
        regime="RANGE",
    )
    sw = Stopwatch().start()
    repo.log_signal(prop)
    write_ms = sw.intervals_ms[-1] if sw.intervals_ms else 0.0
    repo.flush()
    n = _pg_count("audit_signals", "request_id = %s", (key,))
    read_ms = sw.lap() and sw.intervals_ms[-1]
    return {
        "domain": "audit",
        "table": "audit_signals",
        "key": key,
        "write": n > 0,
        "read": n > 0,
        "rows_landed": n,
        "write_ms": write_ms,
        "read_ms": read_ms,
        "latency_samples_ms": [write_ms, read_ms],
        "error": None if n > 0 else "log_signal accepted but no row in audit_signals on PG",
    }


def smoke_audit_guard_telemetry() -> dict[str, Any]:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database.config import load_database_config
    from nexus_scalp.domain.models import TradeProposal

    repo = AuditRepository(config=load_database_config("audit"))
    # a guard-telemetry code takes the aggregation path, not the signal row
    prop = TradeProposal(
        request_id=_key("gt"),
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action="NO_TRADE",
        confidence=0.01,
        proposed_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2660.0,
        risk_reward_ratio=1.0,
        reason_code="TICK_DUPLICATE_SUPPRESSED",
    )
    sw = Stopwatch().start()
    repo.log_signal(prop)
    write_ms = sw.intervals_ms[-1] if sw.intervals_ms else 0.0
    repo.flush()
    n = _pg_count("audit_guard_telemetry")
    read_ms = sw.lap() and sw.intervals_ms[-1]
    return {
        "domain": "audit",
        "table": "audit_guard_telemetry",
        "write": n > 0,
        "read": n > 0,
        "rows_landed": n,
        "write_ms": write_ms,
        "read_ms": read_ms,
        "latency_samples_ms": [write_ms, read_ms],
        "note": "aggregated guard-telemetry counter row (no per-event id)",
    }


def smoke_incidents() -> dict[str, Any]:
    from nexus_scalp.incidents.models import Incident, IncidentCategory, IncidentSeverity
    from nexus_scalp.incidents.store import IncidentStore
    from nexus_scalp.web.server import db_path_for_audit

    key = _key("inc")
    store = IncidentStore(db_path=db_path_for_audit())
    inc = Incident(
        incident_id=key,
        severity=IncidentSeverity.LOW,
        category=IncidentCategory.DATA,
        component="lane-g-live",
        operation="e2e-smoke",
        root_cause="probe write",
    )
    sw = Stopwatch().start()
    store.save(inc)
    write_ms = sw.intervals_ms[-1] if sw.intervals_ms else 0.0
    got = store.get(key)
    n = _pg_count("incidents", "incident_id = %s", (key,))
    read_ms = sw.lap() and sw.intervals_ms[-1]
    err = None
    if n == 0:
        # the row may have been dead-lettered; capture the exact reason
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT error_type, error_message FROM audit_dead_letter"
                " WHERE table_name = 'incidents' ORDER BY sequence_no DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                err = f"dead-lettered: {row[0]}: {row[1]}"
            else:
                err = "row left the store but never landed in incidents on PG (no dead-letter row)"
    return {
        "domain": "incidents",
        "table": "incidents",
        "key": key,
        "write": n > 0,
        "read": got is not None and n > 0,
        "rows_landed": n,
        "write_ms": write_ms,
        "read_ms": read_ms,
        "latency_samples_ms": [write_ms, read_ms],
        "error": err,
    }


def smoke_governance() -> dict[str, Any]:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database.config import load_database_config
    from nexus_scalp.governance.models import GovernanceEvent, GovernanceStage
    from nexus_scalp.governance.store import GovernanceStore

    key = _key("gov")
    repo = AuditRepository(config=load_database_config("audit"))
    store = GovernanceStore(audit_repo=repo)
    ev = GovernanceEvent(
        event_id=key,
        event="LANE_G_PROBE",
        stage=GovernanceStage.REGISTRY,
        actor="lane-g-live",
        reason="e2e pg smoke",
        payload={"probe": key},
    )
    sw = Stopwatch().start()
    ok = store.record_event(ev)
    write_ms = sw.intervals_ms[-1] if sw.intervals_ms else 0.0
    repo.flush()
    n = _pg_count("model_governance_events", "event_id = %s", (key,))
    read_ms = sw.lap() and sw.intervals_ms[-1]
    return {
        "domain": "governance",
        "table": "model_governance_events",
        "key": key,
        "write": bool(ok) and n > 0,
        "read": n > 0,
        "rows_landed": n,
        "write_ms": write_ms,
        "read_ms": read_ms,
        "latency_samples_ms": [write_ms, read_ms],
        "error": None if (ok and n > 0) else (f"record_event returned {ok}" if not ok else None),
    }


def smoke_shadow() -> dict[str, Any]:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database.config import load_database_config
    from nexus_scalp.shadow.models import ShadowModelRef, ShadowRun
    from nexus_scalp.shadow.store import ShadowStore

    key = _key("sh")
    repo = AuditRepository(config=load_database_config("audit"))
    store = ShadowStore(audit_repo=repo)
    run = ShadowRun(
        run_id=key,
        champion=ShadowModelRef(model_id="champ-lane-g", model_version="1.0.0", is_champion=True),
        challenger=ShadowModelRef(model_id="chall-lane-g", model_version="0.9.0"),
        status="RUNNING",
        git_revision="a3f8f490",
    )
    sw = Stopwatch().start()
    ok = store.save_run(run)
    write_ms = sw.intervals_ms[-1] if sw.intervals_ms else 0.0
    repo.flush()
    n = _pg_count("shadow_runs", "run_id = %s", (key,))
    read_ms = sw.lap() and sw.intervals_ms[-1]
    return {
        "domain": "shadow",
        "table": "shadow_runs",
        "key": key,
        "write": bool(ok) and n > 0,
        "read": n > 0,
        "rows_landed": n,
        "write_ms": write_ms,
        "read_ms": read_ms,
        "latency_samples_ms": [write_ms, read_ms],
        "error": None if (ok and n > 0) else (f"save_run returned {ok}" if not ok else None),
    }


def smoke_candle_intel() -> dict[str, Any]:
    from nexus_scalp.database.ops_provider import (
        domain_dsn,
        resolve_pooled_backend,
        resolve_read_backend,
    )

    key = _key("cnd")
    backend = resolve_pooled_backend("candle_intel", domain_dsn("candle_intel"))
    sw = Stopwatch().start()
    ok = backend.execute(
        "INSERT INTO candles (symbol, timeframe, bar_ts, open, high, low, close, volume,"
        " is_complete, ts, regime)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (key, "M1", datetime.now(UTC), 2650.0, 2651.0, 2649.0, 2650.5, 1.0, 1, datetime.now(UTC), "RANGE"),
    )
    write_ms = sw.intervals_ms[-1] if sw.intervals_ms else 0.0
    reader = resolve_read_backend("candle_intel")
    rows = reader.query("SELECT * FROM candles WHERE symbol = %s", (key,)) if reader else []
    read_ms = sw.lap() and sw.intervals_ms[-1]
    return {
        "domain": "candle_intel",
        "table": "candles",
        "key": key,
        "write_backend": type(backend).__name__,
        "read_backend": type(reader).__name__ if reader else None,
        # PgWritePlane.execute returns None on success (no rowcount contract);
        # the row read back through the separate read pool is the proof.
        "write": bool(rows),
        "read": bool(rows),
        "write_ms": write_ms,
        "read_ms": read_ms,
        "latency_samples_ms": [write_ms, read_ms],
        "error": None if (ok and rows) else f"execute={ok} rows={rows}",
    }


def smoke_news() -> dict[str, Any]:
    from nexus_scalp.database.ops_provider import (
        domain_dsn,
        resolve_pooled_backend,
        resolve_read_backend,
    )

    key = _key("src")
    backend = resolve_pooled_backend("news", domain_dsn("news"))
    sw = Stopwatch().start()
    ok = backend.execute(
        "INSERT INTO news_sources (source_id, name, kind, tier, url, feed_url, enabled,"
        " poll_interval_sec, language, priority, seed_version, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            key,
            f"Lane G live probe {key}",
            "RSS",
            2,
            "https://example.invalid/lane-g",
            "https://example.invalid/lane-g/feed",
            0,
            300,
            "en",
            5,
            "lane-g-live",
            datetime.now(UTC),
        ),
    )
    write_ms = sw.intervals_ms[-1] if sw.intervals_ms else 0.0
    reader = resolve_read_backend("news")
    rows = reader.query("SELECT source_id FROM news_sources WHERE source_id = %s", (key,)) if reader else []
    read_ms = sw.lap() and sw.intervals_ms[-1]
    return {
        "domain": "news",
        "table": "news_sources",
        "key": key,
        "write_backend": type(backend).__name__,
        "read_backend": type(reader).__name__ if reader else None,
        "write": bool(rows),
        "read": bool(rows),
        "write_ms": write_ms,
        "read_ms": read_ms,
        "latency_samples_ms": [write_ms, read_ms],
        "error": None if rows else f"execute={ok} rows={rows}",
    }


def audit_batch_insert() -> dict[str, Any]:
    """Tick hot path: 20 batches x 25 rows via one atomic transaction each."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.database.provider_store import queue_write_batch
    from nexus_scalp.database.config import load_database_config
    from nexus_scalp.domain.models import TradeProposal

    repo = AuditRepository(config=load_database_config("audit"))
    samples: list[float] = []
    expected = 0
    for b in range(20):
        batch = [
            TradeProposal(
                request_id=f"{MARKER}batch-{b}-{i}",
                symbol="XAUUSD",
                generated_at=datetime.now(UTC),
                action="NO_TRADE",
                confidence=0.01,
                proposed_entry=2650.0,
                stop_loss=2640.0,
                take_profit=2660.0,
                risk_reward_ratio=1.0,
                reason_code="MODEL_SIGNAL",
                regime="RANGE",
            )
            for i in range(25)
        ]
        expected += len(batch)
        stmts = [
            (
                "INSERT INTO audit_signals (request_id, symbol, action, confidence, proposed_entry,"
                " stop_loss, take_profit, regime, generated_at, payload, execution_mode, reason_code,"
                " signal_dedup_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [
                    (
                        p.request_id,
                        p.symbol,
                        "NO_TRADE",
                        p.confidence,
                        p.proposed_entry,
                        p.stop_loss,
                        p.take_profit,
                        "RANGE",
                        p.generated_at.isoformat(),
                        json.dumps({"batch": b}),
                        "STANDARD",
                        p.reason_code,
                        p.request_id,
                    )
                    for p in batch
                ],
            )
        ]
        t0 = time.perf_counter()
        queue_write_batch(repo, stmts, operation="lane-g-live.batch_insert")
        samples.append((time.perf_counter() - t0) * 1000.0)
    repo.flush()
    n = _pg_count("audit_signals", "request_id LIKE %s", (f"{MARKER}batch-%",))
    return {
        "rows_per_batch": 25,
        "n_batches": 20,
        "ok": n == expected,
        "rows_landed": n,
        "expected": expected,
        "stats": stats_ms(samples),
    }


SMOKES = {
    "audit_signals": smoke_audit_signals,
    "audit_guard_telemetry": smoke_audit_guard_telemetry,
    "incidents": smoke_incidents,
    "governance": smoke_governance,
    "shadow": smoke_shadow,
    "candle_intel": smoke_candle_intel,
    "news": smoke_news,
}


def main() -> None:
    _log("boot on PG (live nexusdb)")
    res["boot"] = boot_on_pg()
    _log(f"boot_on_pg={res['boot']['boot_on_pg']}")

    counts_before, _ = table_counts()
    res["empty_before"] = sum(1 for c in counts_before.values() if c == 0)
    res["total_tables"] = len(counts_before)
    _log(f"empty_before={res['empty_before']}/{res['total_tables']}")

    _log("per-domain write+read smoke")
    res["domains"] = {}
    for name, fn in SMOKES.items():
        t0 = time.perf_counter()
        try:
            r = fn()
            r["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            res["domains"][name] = r
            _log(f"  {name}: write={r['write']} read={r['read']} ({r['total_ms']} ms)")
        except Exception as exc:
            res["domains"][name] = {
                "domain": name,
                "write": False,
                "read": False,
                "total_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=6),
            }
            _log(f"  {name}: FAILED {type(exc).__name__}: {exc}")

    _log("audit batch insert hot path")
    try:
        res["audit_batch_insert"] = audit_batch_insert()
        _log(f"  batch: ok={res['audit_batch_insert']['ok']} stats={res['audit_batch_insert']['stats']}")
    except Exception as exc:
        res["audit_batch_insert"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    counts_after, _ = table_counts()
    res["counts_after"] = counts_after
    res["empty_after"] = sum(1 for c in counts_after.values() if c == 0)
    res["tables_written"] = sorted(
        t for t in counts_after if counts_after.get(t, 0) > (counts_before.get(t, 0) or 0)
    )
    _log(f"empty_after={res['empty_after']}/{len(counts_after)} written={res['tables_written']}")

    res["perf_ms"] = {
        n: stats_ms(d.get("latency_samples_ms", [])) for n, d in res["domains"].items() if isinstance(d, dict)
    }
    res["tests_passed"] = bool(
        all(isinstance(d, dict) and d.get("write") and d.get("read") for d in res["domains"].values())
    )
    res["finished_at"] = datetime.now(UTC).isoformat()
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k != "counts_after"}, indent=1, default=str))

    # REPORTING CONTRACT: a failing probe must NEVER exit 0. The caller
    # (the integration owner) keys off the exit code.
    failures = [
        n for n, d in res["domains"].items() if not (isinstance(d, dict) and d.get("write") and d.get("read"))
    ]
    if not res["boot"].get("boot_on_pg") or failures or not res.get("audit_batch_insert", {}).get("ok"):
        print(f"[lane-g-live] PROBE FAILED boot={res['boot'].get('boot_on_pg')} domains={failures}", flush=True)
        sys.exit(3)
    print("[lane-g-live] PROBE PASSED", flush=True)


if __name__ == "__main__":
    main()
