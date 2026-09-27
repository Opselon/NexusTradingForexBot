"""Diagnostics / live-state / DB-manage / config / settings — REST routes.

Extracted VERBATIM from the former monolith ``server.py`` (CHG-0032 Step 3D,
behavior-preserving; Agent-5 modularization pass). Clusters:
    /api/status, /health, /api/db/hygiene, /api/diagnostics/* (incidents,
    health, lineage, forensics, trace, search, reconcile), /api/rules*,
    /api/live/state, /api/live/accounting, /api/account/{summary,trades,
    growth}, /api/engine/{toggle,mode}, /api/db/manage/*, /api/config (GET/
    POST), /api/runtime-config*, /api/settings* (+telegram), /api/telegram/test

BOUNDARY: closures over ``app.state`` + the canonical ``get_system_state``
snapshot (passed in from server.create_app — it stays the dashboard heart
shared with SSE/static). The live-path POST endpoints (engine toggle/mode)
keep their exact guards and responses; NOTHING here re-implements engine
semantics.

USED BY: server.create_app (registered at the same position as 3A/3B/3C).
DO-NOT-PUT-HERE: chart/mt5/algo/debug/experience/accounting-performance
routes (remain in server.py Step-3E slice), news/liquidity/mslie
(news_liquidity_mslie_routes.py), research surfaces.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException
from pydantic import BaseModel

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.errors import log_web_error, new_request_id

logger = get_logger("nexus_scalp.web.diagnostics_state_routes")


def _frontend_block() -> dict[str, Any]:
    """END-USER-RUNTIME-UI-INTEGRATION (contract frozen decision #7):

    Pure/deterministic — the frozen seam is consulted at call time so
    /health reflects env changes without a restart; a raise never breaks
    the health probe (defense-in-depth — ``/health`` stays callable).
    """
    try:
        from nexus_scalp.web.frontend_assets import frontend_status

        return frontend_status()
    except Exception as exc:  # pragma: no cover — probe must never die here
        return {"mode": "unknown", "error": type(exc).__name__}


def _active_provider_name() -> str:
    """The ACTIVE persistence provider for the audit domain (``sqlite``/``postgresql``).

    Single source for the routes that must route by provider instead of by
    path: ``db_path_for_audit()`` always returns the local SQLite file, so a
    route that keys on it silently reads the wrong database under PostgreSQL.
    """
    try:
        from nexus_scalp.database.config import load_database_config

        return load_database_config("audit").provider.value
    except Exception:
        return "sqlite"


def _audit_repository() -> Any:
    """The audit repository bound to the ACTIVE provider's connection.

    Constructed lazily and per-call: routes are stateless and the repository
    owns a background worker + pooled connection, so a module-level singleton
    would outlive the request and hold a pool nothing closes on re-provision.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database.config import build_postgres_url, load_database_config
    from nexus_scalp.web.server import db_path_for_audit

    cfg = load_database_config("audit")
    if cfg.is_postgresql:
        return AuditRepository(db_url=build_postgres_url(cfg))
    return AuditRepository(db_url=f"sqlite:///{db_path_for_audit()}")


#: PERF-HEALTH (2026-09-10): /health verdict cache TTL (seconds). The Docker
#: healthcheck polls /health every 15s; a full HealthEngine sweep costs
#: ~0.7-2.4s on a 1GB audit.db (PRAGMA integrity_check). A 60s TTL keeps the
#: container healthcheck cheap while verdict staleness stays bounded (the
#: engine's own state moves slower than this; deeper/uncached health remains
#: on /api/v1/system/health, `nexus doctor` and `nexus health`).
_HEALTH_TTL_SEC = 60.0


class ToggleRequest(BaseModel):
    active: bool


class EngineModeRequest(BaseModel):
    mode: str


class ToggleRuleRequest(BaseModel):
    rule_name: str
    is_enabled: bool
    parameters: dict[str, Any] | None = None


# =====================================================================
# PROVIDER TRUTH — configured vs effective (DATABASE TAB, 2026-09-23)
# ---------------------------------------------------------------------
# The tab's badge echoed ONLY `database.provider` from settings, so it
# claimed `postgresql` while every panel on the tab was fed by local
# SQLite files (hygiene sizes/plans read artifacts/*.db, schema state is
# read from the SQLite artifacts DB) and the configured PostgreSQL
# target did not answer.  These helpers MEASURE the effective provider
# instead of assuming it: probe the configured target (15s cache so the
# 30s panel poll stays cheap), then look at local file activity as
# evidence of what is actually being written.
#
# Secret-safe: probe failures are rendered through db_console._fail(),
# so exception TEXT (which may embed credentials) is never echoed —
# known conditions get code + actionable sentence, unknown ones only
# the exception type name.
# =====================================================================

_TRUTH_PROBE_TTL_S = 15.0
_TRUTH_ACTIVE_WINDOW_S = 600  # a local .db written within 10 min = live traffic
_TRUTH_DOMAINS = ("audit", "news", "candle_intel")
_PROBE_CACHE: dict[str, Any] = {"at": 0.0, "target": "", "ok": None, "error": ""}


def _local_sqlite_evidence() -> list[dict[str, Any]]:
    """Local SQLite files behind this tab's panels, with recency evidence."""
    from nexus_scalp.database.config import load_database_config

    rows: list[dict[str, Any]] = []
    now = time.time()
    for domain in _TRUTH_DOMAINS:
        try:
            cfg = load_database_config(domain)
            path = Path(cfg.sqlite_connect_path)
        except Exception:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        age = max(0, int(now - st.st_mtime))
        rows.append(
            {
                "name": domain,
                "file": path.name,
                "path": str(path),
                "bytes": int(st.st_size),
                "age_seconds": age,
                "mtime_utc": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(timespec="seconds"),
                "active": age <= _TRUTH_ACTIVE_WINDOW_S,
            }
        )
    return rows


def _probe_postgres(cfg: Any) -> tuple[bool, str, str]:
    """(reachable, target, safe_error) — measured, 15s cached per target."""
    target = f"{cfg.host}:{cfg.port or 5432}/{cfg.database}"
    now = time.monotonic()
    cached_ok = _PROBE_CACHE.get("ok")
    if (
        cached_ok is not None
        and _PROBE_CACHE.get("target") == target
        and now - float(_PROBE_CACHE.get("at") or 0.0) < _TRUTH_PROBE_TTL_S
    ):
        return bool(cached_ok), target, str(_PROBE_CACHE.get("error") or "")

    ok = False
    error = ""
    try:
        from nexus_scalp.database.drivers import get_driver

        driver = get_driver(cfg)
        try:
            ok = bool(driver.ping())
        finally:
            driver.close()
        if not ok:
            error = f"PostgreSQL at {target} did not answer the connection probe."
    except Exception as exc:  # a probe must never raise into the route
        from nexus_scalp.web.db_console import _fail

        failed = _fail(exc, "probing the configured PostgreSQL target", cfg)
        error = str(failed.get("error") or "connection probe failed")
    _PROBE_CACHE.update({"at": now, "target": target, "ok": ok, "error": error})
    return ok, target, error


def _provider_truth(ui: dict[str, Any]) -> dict[str, Any]:
    """Configured-vs-effective provider, measured (see section header).

    `mismatch=True` is the state the user reported on 2026-09-23: badge
    says postgresql, the data on the tab comes from sqlite.
    """
    configured = str(ui.get("provider") or "sqlite")
    evidence = _local_sqlite_evidence()
    active = [row for row in evidence if row.get("active")]
    truth: dict[str, Any] = {
        "configured": configured,
        "effective": configured,
        "mismatch": False,
        "pg_reachable": None,
        "pg_target": "",
        "pg_error": "",
        "evidence": evidence,
        "note": "",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if configured != "postgresql":
        truth["note"] = (
            "Configured provider is sqlite; the storage, hygiene and schema "
            "panels read the local SQLite files listed as evidence."
        )
        return truth

    from nexus_scalp.database.config import load_database_config

    try:
        cfg = load_database_config("audit")
        reachable, target, error = _probe_postgres(cfg)
    except Exception as exc:  # pragma: no cover - config edge
        reachable, target, error = False, "", type(exc).__name__
    truth["pg_reachable"] = reachable
    truth["pg_target"] = target
    truth["pg_error"] = error
    if reachable:
        truth["note"] = f"PostgreSQL answered at {target}."
        return truth

    if active:
        truth["effective"] = "sqlite"
        truth["mismatch"] = True
        mins = _TRUTH_ACTIVE_WINDOW_S // 60
        truth["note"] = (
            f"Configured provider is postgresql but {target or 'the configured target'} "
            f"did not answer; local SQLite files were written in the last {mins} minutes "
            "(see evidence) — the data on this tab comes from sqlite."
        )
    else:
        truth["effective"] = "unavailable"
        truth["mismatch"] = True
        truth["note"] = (
            f"Configured provider is postgresql but {target or 'the configured target'} "
            "did not answer and no local SQLite file was written recently — the data "
            "shown may be stale."
        )
    return truth


# PERF-DB-STATUS (2026-09-23): TTL cache for GET /api/db/hygiene — see the
# inline comment in get_db_hygiene for the measured numbers. Failure payloads
# are never stored here.
_DB_HYGIENE_TTL_SEC = 30.0
# in-place mutable cache (same pattern as _PROBE_CACHE): {"at": t, "payload": d}
_DB_HYGIENE_CACHE: dict[str, Any] = {}


def invalidate_db_hygiene_cache() -> None:
    """Drop the cached /api/db/hygiene payload (tests / mutation hooks)."""
    _DB_HYGIENE_CACHE.clear()


def _db_hygiene_cached() -> dict[str, Any] | None:
    if not _DB_HYGIENE_CACHE:
        return None
    at = float(_DB_HYGIENE_CACHE.get("at") or 0.0)
    if (time.monotonic() - at) >= _DB_HYGIENE_TTL_SEC:
        return None
    payload = _DB_HYGIENE_CACHE.get("payload")
    return payload if isinstance(payload, dict) else None


def _db_hygiene_store(payload: dict[str, Any]) -> None:
    _DB_HYGIENE_CACHE.clear()
    _DB_HYGIENE_CACHE.update({"at": time.monotonic(), "payload": payload})


# ---------------------------------------------------------------------------
# TASK-CFGUI-001 — /alt/config tab contract hardening
# ---------------------------------------------------------------------------


def mask_secret_tail(value: str) -> str:
    """BUG-072 display mask: star everything but the last 4 characters."""
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def mask_config_secrets(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Never serve plaintext credentials on GET /api/config.

    BUG-072 (telegram bot_token) extended to the MT5 credential block and
    the notification API key: every secret-shaped field this route may carry
    is replaced by a display mask before the payload reaches a browser.
    Values that already contain ``*`` (secure-store masks such as
    ``telegram.token_masked``) are left untouched — never double-masked.
    """
    for section, field in (
        ("telegram", "bot_token"),
        ("mt5", "password"),
        ("notification", "telegram_api_key"),
    ):
        block = raw_data.get(section)
        if not isinstance(block, dict):
            continue
        value = block.get(field)
        if isinstance(value, str) and value and "*" not in value:
            block[field] = mask_secret_tail(value)
    return raw_data


def restore_masked_mt5_password(
    raw_data: dict[str, Any], yaml_path: Path | None = None
) -> dict[str, Any]:
    """Round-trip guard for POST /api/config (pairs with mask_config_secrets).

    GET serves a MASK for ``mt5.password`` and the legacy config form echoes
    the whole mt5 block back verbatim (Web/app.js ``saveConfiguration`` ->
    ``mt5: selectedConfig.mt5``). Persisting that mask would overwrite the
    real credential in live.yaml — so masked/empty inbound passwords keep the
    stored value instead; a real (unmasked) value is written as-is.
    """
    mt5 = raw_data.get("mt5")
    if not isinstance(mt5, dict):
        return raw_data
    inbound = str(mt5.get("password") or "")
    if inbound and "*" not in inbound:
        return raw_data  # operator supplied a real credential — keep it
    path = yaml_path or Path("configs/live.yaml")
    stored = ""
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                current = yaml.safe_load(f) or {}
            current_mt5 = current.get("mt5") if isinstance(current, dict) else None
            if isinstance(current_mt5, dict):
                stored = str(current_mt5.get("password") or "")
    except Exception as exc:  # an unreadable copy must not block a save
        logger.warning("[CONFIG] stored mt5.password unreadable during restore", exc_info=exc)
        stored = ""
    mt5["password"] = stored
    return raw_data


def current_execution_mode(engine: Any) -> str | None:
    """Canonical current mode string ('LIVE', 'PAPER', ...) or None if unreadable."""
    try:
        mode = engine.config.execution.mode  # type: ignore[union-attr]
    except Exception:
        return None
    value = getattr(mode, "value", mode)
    text = str(value or "").strip().upper()
    return text or None


def dry_run_config_errors(engine: Any, key: str, value: Any) -> list[str]:
    """Validate one dotted key/value against the apply path's own validator.

    Runs ``build_runtime_configuration`` with NO persist step — the exact
    field validators (type/range/enum/unknown-key) and cross-field constraints
    that POST /api/runtime-config/apply enforces — so ``valid: true`` here
    means "would be accepted there". When the engine (and its live snapshot)
    is offline, bootstrap defaults form the base so validation still works.
    """
    from nexus_scalp.configuration.runtime_config import build_runtime_configuration

    base = None
    version = 0
    store = getattr(engine, "runtime_config", None) if engine else None
    if store is not None:
        try:
            base = store.get_snapshot()
            version = int(store.get_version())
        except Exception as exc:
            logger.warning("[SETTINGS_VALIDATE] snapshot unreadable", exc_info=exc)
    bootstrap = None
    if base is None:
        from nexus_scalp.configuration.config import AppConfig

        bootstrap = AppConfig()
    result = build_runtime_configuration(
        version=version + 1,
        base=base,
        bootstrap=bootstrap,
        updates={key: value},
        source="SETTINGS_VALIDATE",
    )
    return list(result.errors)


def register_diagnostics_state_routes(
    app: Any,
    _err: Any,
    _log_err: Any,
    serialize_enums: Any,
    get_system_state: Any,
) -> None:
    """Attach diagnostics/state/dbmanage/config routes (closures over app)."""
    from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY
    from nexus_scalp.domain.enums import ExecutionMode, OrderType
    from nexus_scalp.observability.telegram_notifier import (
        TelegramNotifier,
    )
    from nexus_scalp.release.runtime_snapshot import (
        build_runtime_snapshot,  # CHG-0043: canonical feature/model truth
    )

    # db-provider-pro (2026-09-23, Lane A): self-contained router for
    # POST /api/db/manage/parse-url + GET|POST /api/db/manage/options.
    # Mounted INSIDE this registration function (contract §4) so server.py
    # keeps its single mount site.
    from nexus_scalp.web.db_provider_routes import router as db_provider_router
    from nexus_scalp.web.server import (  # late import: cycle-safe
        _default_audit_config,
        _liquidity_state_section,
        db_path_for_audit,
    )

    app.include_router(db_provider_router)

    # REST APIs: System status
    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        return get_system_state()

    # =========================================================================
    # RELEASE / UPDATE STATUS (CHG-0043, TASK-RUNTIME-TRUTH)
    # -------------------------------------------------------------------------
    # Offline-safe: reports LAST-KNOWN release/update truth from local
    # records; NEVER contacts GitHub on the read path. The optional
    # ?refresh=true query is the explicit bounded network path.
    # =========================================================================
    @app.get("/api/release/status")
    def release_status(refresh: bool = False) -> dict[str, Any]:
        from nexus_scalp.release import release_status as rs

        if refresh:
            return rs.refresh_from_github()
        return rs.build_release_status()

    # =========================================================================
    # Docker/native health probe (DOCKER-REPAIR, 2026-08-20):
    # * 200 with verdict READY or DEGRADED -> healthy
    # * 200 with verdict NOT READY           -> degraded (dependencies missing,
    #   e.g. model not yet provisioned) — used by the container healthcheck
    # * 503                                   -> unhealthy (dependency check raised)
    # Verdict semantics are the HealthEngine contract: READY requires
    # SYSTEM/RUNTIME/CONFIGURATION/DATABASE/MODEL/FEATURE_SCHEMA all PASS;
    # optional subsystems (NEWS/WORKERS/TELEGRAM/...) may be WARNING.
    #
    # PERF-HEALTH (2026-09-10): the Docker healthcheck polls this endpoint
    # every 15s. A full HealthEngine sweep measured ~0.7-2.4s on a 1GB
    # audit.db (the DATABASE check runs PRAGMA integrity_check over every
    # page) — thousands of full DB scans, all on the request path. The full
    # verdict block is now cached per-app for _HEALTH_TTL_SEC (documented
    # staleness, same pattern as the api_v1 system health block); integrity
    # truth is NOT weakened — integrity_check remains the authority in
    # `nexus doctor`, `nexus health`, /api/v1/system/health and the deeper
    # diagnostic paths (explicit maintenance operations), while /health
    # serves the cached verdict.
    # =========================================================================
    @app.get("/health")
    def health_probe() -> dict[str, Any]:
        try:
            cached = getattr(app.state, "health_probe_cache", None)
            if isinstance(cached, tuple) and (time.monotonic() - cached[0]) < _HEALTH_TTL_SEC:
                verdict, checks, critical = cached[1], cached[2], cached[3]
            else:
                from nexus_scalp.release.health import HealthEngine

                verdict, entries = HealthEngine().overall()
                checks = [e.to_dict() for e in entries]
                critical = [e["category"] for e in checks if e.get("verdict") == "FAIL"]
                app.state.health_probe_cache = (
                    time.monotonic(),
                    verdict,
                    checks,
                    critical,
                )
            if verdict == "NOT READY" or critical:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "verdict": verdict,
                        "checks": checks,
                        "critical_failures": critical,
                        # ADDITIVE (contract #7): same frontend block on the
                        # degraded shape — nothing existing removed/renamed.
                        "frontend": _frontend_block(),
                    },
                )
            return {
                "status": "ok",
                "verdict": verdict,
                "checks": checks,
                # END-USER-RUNTIME-UI-INTEGRATION (contract frozen decision
                # #7): ADDITIVE frontend diagnostics — never removes/renames
                # existing fields; present and the same shape whether or not
                # the build resolved. Only this `frontend` key is new.
                "frontend": _frontend_block(),
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("health probe unhealthy", exc_info=exc)
            raise HTTPException(
                status_code=503, detail={"verdict": "UNHEALTHY", "error": type(exc).__name__}
            ) from exc

    # TASK-11: Database health / hygiene state (real backend data — never fake).
    @app.get("/api/db/hygiene")
    def get_db_hygiene() -> dict[str, Any]:
        # PERF-DB-STATUS (2026-09-23): plan_database() re-scans all three
        # SQLite files (audit 426MB + news 241MB + candle_intel) on every
        # call — measured ~9s per poll, running CONCURRENTLY with
        # /api/db/status and pushing it past the 15s frontend timeout.
        # Plans only change when a hygiene run happens (background worker /
        # CLI, never via a web POST), so a short TTL cache is safe here;
        # failures are never cached. Mirrors the PERF-HEALTH /health cache.
        fresh = _db_hygiene_cached()
        if fresh is not None:
            return fresh
        try:
            from nexus_scalp.hygiene import WorkerMode
            from nexus_scalp.hygiene.worker_runner import DatabaseHygieneWorker

            base_dir = Path.cwd()
            if engine_state := getattr(app.state, "engine", None):
                cfg = getattr(engine_state, "config", None)
                if cfg is not None and hasattr(cfg, "base_dir"):
                    base_dir = Path(cfg.base_dir)
            worker = DatabaseHygieneWorker(
                repo_root=base_dir, mode=WorkerMode.AUDIT_ONLY, apply_deletes=False
            )
            st = worker.status()
            plans = {}
            for db in ("audit", "news", "candle_intel"):
                plans[db] = worker.plan_database(db)
            # TASK-22: runtime scheduler + quarantine + cycle telemetry.
            runtime: dict[str, Any] = {"available": False}
            quarantine: dict[str, Any] = {"available": False}
            try:
                from nexus_scalp.hygiene.hygiene_runtime import RuntimeCleanupScheduler

                sched = RuntimeCleanupScheduler(repo_root=base_dir)
                runtime = {"available": True, **sched.status()}
                quarantine = sched.quarantine.stats()
                quarantine["available"] = True
                quarantine["items"] = sched.quarantine.list(limit=20)
            except Exception as _rt_err:
                _log_err(_rt_err, "db hygiene runtime status failed", endpoint="/api/db/hygiene")
            payload = {"status": st, "plans": plans, "runtime": runtime, "quarantine": quarantine}
            _db_hygiene_store(payload)
            return payload
        except Exception as exc:
            _log_err(exc, "db hygiene failed", endpoint="/api/db/hygiene")
            return {
                "status": {
                    "state": "DEGRADED",
                    "error_state": "HYGIENE_UNAVAILABLE",
                    "request_id": new_request_id(),
                },
                "plans": {},
            }

    # REST APIs: Trading Rules

    # =====================================================================
    # TASK-12: INCIDENT RESPONSE & FORENSIC DIAGNOSTICS (spec 35/36/37/44)
    # ---------------------------------------------------------------------
    # /api/diagnostics/incidents          incident list + counts
    # /api/diagnostics/incidents/{id}     one incident (full record)
    # /api/diagnostics/health             aggregated incident health
    # /api/diagnostics/lineage            value lineage / one-click trace
    # /api/diagnostics/search             deterministic bounded search
    # All read-only; no trading mutation. Reuses the existing diagnostics
    # surface (no competing diagnostic APIs).
    # ---------------------------------------------------------------------

    def _incident_store() -> Any:
        """The incident store over the ACTIVE provider, never a stale SQLite path.

        ``db_path_for_audit()`` returns the local ``artifacts/audit.db`` path
        unconditionally: under a PostgreSQL provider ``IncidentStore(db_path=...)``
        adopts it, runs its SQLite branches, and reports an EMPTY incident list
        while every live row sits in PostgreSQL — the diagnostics tab showed a
        blank board on a healthy cluster (the same "documented defaults instead
        of real data" defect the read-plane lane logged 368 warnings for).

        Only pass the SQLite path when SQLite is the active provider; under a
        pooled provider resolve the store from the audit repository so it uses
        the fabric's pooled read/write planes (the store ignores the repo's
        provider URI as a path by design).
        """
        from nexus_scalp.incidents.store import IncidentStore

        provider = _active_provider_name()
        if provider == "postgresql":
            return IncidentStore(audit_repo=_audit_repository())
        db = db_path_for_audit()
        return IncidentStore(db_path=db)

    @app.get("/api/diagnostics/incidents")
    def get_diagnostics_incidents(
        status: str = "",
        severity: str = "",
        category: str = "",
        component: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Incident list (spec 35/36). Bounded, filterable, read-only."""
        try:
            store = _incident_store()
            incidents = store.list_incidents(
                status=status or None,
                severity=severity or None,
                category=category or None,
                component=component or None,
                limit=max(1, min(int(limit), 500)),
                offset=max(0, int(offset)),
            )
            return serialize_enums(
                {
                    "available": True,
                    "counts": store.count(),
                    "incidents": [i.as_dict() for i in incidents],
                }
            )
        except Exception as exc:
            _log_err(exc, "incident list failed", endpoint="/api/diagnostics/incidents")
            return _err("INTERNAL_ERROR")

    @app.get("/api/diagnostics/incidents/{incident_id}")
    def get_diagnostics_incident(incident_id: str) -> dict[str, Any]:
        """One incident record (spec 35)."""
        try:
            store = _incident_store()
            inc = store.get(incident_id)
            if inc is None:
                return {"available": False, "error": f"incident {incident_id} not found"}
            return serialize_enums({"available": True, "incident": inc.as_dict()})
        except Exception as exc:
            _log_err(exc, "incident get failed", endpoint="/api/diagnostics/incidents/{id}")
            return _err("INTERNAL_ERROR")

    @app.get("/api/diagnostics/health")
    def get_diagnostics_health() -> dict[str, Any]:
        """Aggregated incident health (spec 35/39). Counts + worker state."""
        try:
            store = _incident_store()
            worker_health: dict[str, Any] = {"state": "DISABLED"}
            engine = app.state.engine
            w = getattr(engine, "_incident_worker", None)
            if w is not None:
                from nexus_scalp.incidents.worker import format_incident_worker_status

                worker_health = format_incident_worker_status(w)
            # Spec 39: distinguish DISABLED / RUNNING / DEGRADED / FAILED
            state = worker_health.get("state", "DISABLED")
            worker_health["display_state"] = (
                "RUNNING"
                if state in ("RUNNING", "STARTING")
                else "DEGRADED"
                if state == "DEGRADED"
                else "FAILED"
                if state == "FAILED"
                else "DISABLED"
            )
            return serialize_enums(
                {
                    "available": True,
                    "counts": store.count(),
                    "recurring": store.recurring_fingerprints(limit=10),
                    "by_component": store.stats_by_component(),
                    "worker": worker_health,
                }
            )
        except Exception as exc:
            _log_err(exc, "incident health failed", endpoint="/api/diagnostics/health")
            return _err("INTERNAL_ERROR")

    @app.get("/api/diagnostics/lineage")
    def get_diagnostics_lineage(
        field: str = "pnl",
        ticket: str = "",
    ) -> dict[str, Any]:
        """Value lineage / one-click trace (spec 8/37/38). Read-only."""
        try:
            from nexus_scalp.incidents.lineage import LineageEngine

            engine = LineageEngine()
            if field == "pnl":
                trace = engine.pnl_trace()
            elif field == "realized_r":
                trace = engine.realized_r_trace()
            elif field == "open_positions":
                trace = engine.exposure_trace()
            elif field == "model_output":
                trace = engine.model_output_trace()
            else:
                trace = engine.trace(field)
            payload: dict[str, Any] = {
                "available": True,
                "field": trace.field,
                "source": trace.source,
                "hops": trace.hops(),
                "why": {},
            }
            if ticket:
                from nexus_scalp.incidents.trace import why_closed, why_no_learning

                payload["why_closed"] = why_closed(db_path_for_audit(), ticket)
                payload["why_no_learning"] = why_no_learning(db_path_for_audit(), ticket)
            return serialize_enums(payload)
        except Exception as exc:
            _log_err(exc, "lineage failed", endpoint="/api/diagnostics/lineage")
            return _err("INTERNAL_ERROR")

    @app.get("/api/diagnostics/forensics")
    def get_diagnostics_forensics(
        kind: str = "accounting",
        ticket: str = "",
    ) -> dict[str, Any]:
        """TASK-13 read-only forensic probes (spec 14/23).

        kind=accounting -> first-divergence audit of zero-PnL ledger rows.
        kind=timebase  -> live timebase probe (host/DB/broker offsets).
        Never writes; bounded.
        """
        try:
            db = db_path_for_audit()
            if kind == "timebase":
                from nexus_scalp.incidents.timebase import TimebaseProbe

                probe = TimebaseProbe(db)
                base = probe.run()
                if ticket:
                    base["event_chain"] = probe.probe_event(ticket)
                return serialize_enums({"available": True, "kind": "timebase", **base})

            from nexus_scalp.incidents.accounting import AccountingForensicsEngine

            result = AccountingForensicsEngine(db).audit_zero_pnl_ledger(max_rows=50)
            return serialize_enums(
                {
                    "available": True,
                    "kind": "accounting",
                    "checked_records": result["checked_records"],
                    "classification_counts": result["classification_counts"],
                    "zero_outcome_classification_counts": result[
                        "zero_outcome_classification_counts"
                    ],
                    "recovery_candidate_count": result["recovery_candidate_count"],
                }
            )
        except Exception as exc:
            _log_err(exc, "diagnostics forensics failed", endpoint="/api/diagnostics/forensics")
            return _err("INTERNAL_ERROR")

    @app.get("/api/diagnostics/incidents/{incident_id}/report")
    def get_diagnostics_incident_report(incident_id: str) -> dict[str, Any]:
        """TASK-13: incident report export (JSON+MD paths, secret-masked)."""
        try:
            from nexus_scalp.incidents.reports import incident_json, incident_markdown

            store = _incident_store()
            inc = store.get(incident_id)
            if inc is None:
                return {"available": False, "error": f"incident {incident_id} not found"}
            return serialize_enums(
                {
                    "available": True,
                    "incident_id": incident_id,
                    "json": incident_json(inc),
                    "markdown": incident_markdown(inc),
                }
            )
        except Exception as exc:
            _log_err(
                exc, "incident report failed", endpoint="/api/diagnostics/incidents/{id}/report"
            )
            return _err("INTERNAL_ERROR")

    @app.get("/api/diagnostics/incidents/{incident_id}/zip")
    def get_diagnostics_incident_zip(incident_id: str) -> dict[str, Any]:
        """TASK-13: evidence ZIP export (spec 46). Secret-masked always."""
        try:
            from nexus_scalp.incidents.reports import export_zip_bundle

            store = _incident_store()
            inc = store.get(incident_id)
            if inc is None:
                return {"available": False, "error": f"incident {incident_id} not found"}
            zip_path = export_zip_bundle(inc, str(Path.cwd() / "artifacts"))
            return serialize_enums(
                {
                    "available": True,
                    "incident_id": incident_id,
                    "zip_path": str(zip_path),
                    "size_bytes": zip_path.stat().st_size if zip_path.exists() else 0,
                    "note": "all payloads secret-masked (spec 47)",
                }
            )
        except Exception as exc:
            _log_err(exc, "incident zip failed", endpoint="/api/diagnostics/incidents/{id}/zip")
            return _err("INTERNAL_ERROR")

    @app.get("/api/diagnostics/search")
    def get_diagnostics_search(query: str = "", limit: int = 50) -> dict[str, Any]:
        """Deterministic bounded incident search (spec 44)."""
        if not query.strip():
            return {"available": True, "incidents": []}
        try:
            store = _incident_store()
            incidents = store.search(query, limit=max(1, min(int(limit), 100)))
            return serialize_enums(
                {
                    "available": True,
                    "query": query,
                    "incidents": [i.as_dict() for i in incidents],
                }
            )
        except Exception as exc:
            _log_err(exc, "incident search failed", endpoint="/api/diagnostics/search")
            return _err("INTERNAL_ERROR")

    @app.post("/api/diagnostics/incidents/reconcile")
    def post_diagnostics_reconcile() -> dict[str, Any]:
        """Genuine incident audit (spec 43): re-runs every forensic
        probe against the CURRENT database, reports findings, and
        reconciles incident records (impact + evidence + lifecycle
        semantics) WITHOUT creating duplicates.
        """

        from nexus_scalp.incidents.impact import ImpactAnalyzer
        from nexus_scalp.incidents.trace import (
            broker_ledger_divergence,
            clock_skew,
            learning_pipeline_rates,
            outcome_forensics,
            split_fill_groups,
        )

        db = db_path_for_audit()
        started = datetime.now(UTC).isoformat()
        findings: dict[str, Any] = {}
        try:
            findings["accounting"] = broker_ledger_divergence(db)
        except Exception as exc:
            logger.warning("forensic accounting check failed", exc_info=exc)
            findings["accounting"] = {"error": type(exc).__name__}
        try:
            findings["timebase"] = clock_skew(db)
        except Exception as exc:
            logger.warning("forensic timebase check failed", exc_info=exc)
            findings["timebase"] = {"error": type(exc).__name__}
        try:
            findings["outcome"] = outcome_forensics(db, 500)
        except Exception as exc:
            logger.warning("forensic outcome check failed", exc_info=exc)
            findings["outcome"] = {"error": type(exc).__name__}
        try:
            findings["learning"] = learning_pipeline_rates(db)
        except Exception as exc:
            logger.warning("forensic learning check failed", exc_info=exc)
            findings["learning"] = {"error": type(exc).__name__}
        try:
            findings["split_fill"] = split_fill_groups(db)
        except Exception as exc:
            logger.warning("forensic split_fill check failed", exc_info=exc)
            findings["split_fill"] = {"error": type(exc).__name__}

        # Reconcile stored incidents (impact + evidence) in place — never
        # create new incidents here (idempotent by incident_id).
        store = _incident_store()
        analyzer = ImpactAnalyzer(db_path=db)
        reconciled = 0
        for inc in store.list_incidents(limit=200):
            inc.impact = analyzer.analyze(inc)
            store.save(inc)
            reconciled += 1
        return serialize_enums(
            {
                "available": True,
                "audit_started": started,
                "audit_scope": [
                    "accounting",
                    "timebase",
                    "outcome",
                    "learning",
                    "split_fill",
                ],
                "findings": findings,
                "incidents_discovered": store.count()["total"],
                "incidents_reconciled": reconciled,
                "note": "read-only forensic audit; incident records updated with current impact/evidence",
            }
        )

    @app.get("/api/diagnostics/trace")
    def get_diagnostics_trace(query: str = "") -> dict[str, Any]:
        """One-Click Trace (spec 24/25/26): resolve incident_id / ticket /
        execution_id / order_id / position_id / model_id / research_run_id
        into its full lineage. Never fabricates links: missing hops are
        reported with missing_link + reason + last_known_node.
        """
        if not query.strip():
            return {"available": True, "trace": {"kind": "unknown", "reason": "empty query"}}
        try:
            from nexus_scalp.incidents.trace_lineage import trace_lineage

            store = _incident_store()
            result = trace_lineage(db_path_for_audit(), query, store=store)
            return serialize_enums({"available": True, "query": query, "trace": result})
        except Exception as exc:
            _log_err(exc, "trace failed", endpoint="/api/diagnostics/trace")
            return _err("INTERNAL_ERROR")

    @app.get("/api/rules")
    def get_trading_rules() -> list[dict[str, Any]]:
        engine = app.state.engine
        if engine:
            return engine.audit.get_trading_rules()
        else:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository

            repo = AuditRepository(config=_default_audit_config())
            return repo.get_trading_rules()

    @app.post("/api/rules/toggle")
    def toggle_trading_rule(req: ToggleRuleRequest) -> dict[str, Any]:
        engine = app.state.engine
        params_json = json.dumps(req.parameters) if req.parameters is not None else None

        if engine:
            success = engine.audit.toggle_trading_rule(
                rule_name=req.rule_name, is_enabled=req.is_enabled, parameters_json=params_json
            )
            if success and hasattr(engine, "rule_matrix"):
                engine.rule_matrix.refresh_cache(force=True)
            return {"success": success}
        else:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository

            repo = AuditRepository(config=_default_audit_config())
            success = repo.toggle_trading_rule(
                rule_name=req.rule_name, is_enabled=req.is_enabled, parameters_json=params_json
            )
            return {"success": success}

    # =========================================================================
    # CANONICAL LIVE UI STATE CONTRACT (PHASE 14 FORENSIC HARDENING)
    # -------------------------------------------------------------------------
    # ONE authoritative backend state graph consumed by every UI section:
    # market / chart / features / model / strategy / risk / accounting /
    # research / intelligence. REST snapshot + SSE stream both serve this same
    # shape, so the Debug Hub and the main UI can never diverge. Every leaf
    # carries explicit source provenance; missing values are null, never fake.
    # =========================================================================
    @app.get("/api/live/state")
    def get_live_state() -> dict[str, Any]:
        state = get_system_state()
        engine = app.state.engine
        account = state.get("account", {})
        timestamps = state.get("timestamps", {}) or {}
        live = {
            "contract": "LiveUiState.2",
            "state_version": state.get("state_version"),
            "snapshot_timestamp": state.get("snapshot_timestamp"),
            "generated_at": state.get("generated_at"),
            "engine_running": state.get("engine_running"),
            "provenance": state.get("provenance"),
            "timestamps": timestamps,
            "diagnostics": state.get("diagnostics", {}),
            "market": {
                "symbol": state.get("symbol"),
                "timeframe": "M1",
                "bid": state.get("bid"),
                "ask": state.get("ask"),
                "spread": state.get("spread"),
                "atr": state.get("atr"),
                "regime": state.get("regime"),
                "execution_mode": state.get("execution_mode"),
                "source": (state.get("provenance") or {}).get("price", "UNAVAILABLE"),
            },
            "chart": {
                "bars": state.get("bars", []),
                "bars_available": bool(state.get("bars")),
                "overlays": state.get("visual_overlays", {}),
                "timeframe": "M1",
                "synchronization_timestamp": state.get("snapshot_timestamp"),
            },
            "features": {
                "schema_id": getattr(engine, "FEATURE_SCHEMA_ID", "scalp_v1")
                if engine
                else "scalp_v1",
                "dimension": len(state.get("features", [])),
                "entries": state.get("features", []),
                "source": (state.get("provenance") or {}).get("features", "UNAVAILABLE"),
                "timestamp": timestamps.get("features"),
                # CHG-0043 additive: EFFECTIVE contract of the LOADED bundle
                # (FEATURE ENABLED != FEATURE ACTIVE). The legacy fields above
                # keep their bootstrap-class semantics for old consumers.
                "effective_schema_id": (
                    getattr(engine, "effective_feature_schema_id", None) if engine else None
                ),
                "effective_dimension": (
                    getattr(engine, "effective_feature_dim", None) if engine else None
                ),
                "activation": (
                    build_runtime_snapshot(include_update=False).get("feature_activation")
                ),
            },
            "model": {
                "available": bool(state.get("model", {}).get("available")),
                "model_id": state.get("model", {}).get("model_id"),
                "model_version": state.get("model", {}).get("model_version"),
                "architecture": state.get("model", {}).get("architecture"),
                "artifact_path": state.get("model", {}).get("artifact_path"),
                "feature_schema_id": state.get("model", {}).get("feature_schema_id"),
                "feature_dimension": state.get("model", {}).get("feature_dimension"),
                "scaler_ready": state.get("model", {}).get("scaler_ready"),
                "probabilities": {
                    "no_trade": state.get("probs", {}).get("no_trade"),
                    "buy": state.get("probs", {}).get("buy"),
                    "sell": state.get("probs", {}).get("sell"),
                },
                "probabilities_available": bool(state.get("probs", {}).get("available")),
                "inference_timestamp": timestamps.get("inference"),
                "source": (state.get("provenance") or {}).get("model", "UNAVAILABLE"),
            },
            "strategy": {
                "decision": state.get("ai_decision"),
                "confidence": state.get("ai_confidence"),
                "reason": state.get("ai_reason"),
                "proposal_timestamp": timestamps.get("proposal"),
                "strategy_id": None,
                "version": None,
                "score": None,
                "state": None,
            },
            "risk": {
                "equity": account.get("equity"),
                "balance": account.get("balance"),
                "risk_pct": (engine.config.risk.risk_per_trade_pct if engine else None),
                "limits": {
                    "max_drawdown_pct": (
                        engine.config.risk.max_account_drawdown_pct if engine else None
                    ),
                    "max_concurrent_positions": (
                        engine.config.risk.max_concurrent_positions if engine else None
                    ),
                    "max_spread_points": (engine.config.risk.max_spread_points if engine else None),
                },
            },
            "accounting": {
                "available": bool(account.get("available")),
                "source": account.get("source", "UNAVAILABLE"),
                "balance": account.get("balance"),
                "equity": account.get("equity"),
                "floating_pnl": account.get("floating"),
                "drawdown_pct": account.get("drawdown"),
                "win_rate": account.get("win_rate"),
                "margin_free": account.get("margin_free"),
                "open_positions": account.get("open_positions"),
            },
            "positions": state.get("positions", []),
            "news": {
                "available": False,
                "state": None,
                "bullish_score": None,
                "bearish_score": None,
                "xauusd_relevance": None,
                "confidence": None,
                "freshness": None,
                "active_event_count": None,
                "timestamp": None,
            },
            "health": state.get("health", {}),
            "research": {
                "worker_status": state.get("research_worker_status"),
                "registry": state.get("research_registry_counts"),
            },
            "intelligence": {
                "lifecycle_events": state.get("intel_lifecycle_events"),
                "autopsies": state.get("intel_autopsies"),
                "worker_status": state.get("intel_worker_status"),
            },
            # Market Radar (BUG-138): sourced from the canonical get_system_state()
            # snapshot (same backend _last_market_radar passthrough) so REST, SSE
            # and WebSocket share one authoritative radar object.
            "radar": state.get("radar"),
            "predictions": state.get("predictions", []),
            # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: authoritative per-stage
            # freshness + UI stale flag (mirrors get_system_state() so REST,
            # SSE and WebSocket all carry the same truth).
            "live_freshness": state.get("live_freshness"),
            "is_stale": state.get("is_stale", False),
            "mt5": {
                "connection": {},
                "diagnostics": {},
                "available": False,
            },
        }

        # REAL MT5 connection + diagnostics (never derived from config).
        if engine is not None:
            try:
                conn_state = engine.adapter.connection_state()
                if hasattr(conn_state, "to_dict"):
                    live["mt5"] = {
                        "connection": conn_state.to_dict(),
                        "diagnostics": {},
                        "available": True,
                    }
                    if hasattr(engine.adapter, "diagnostics_summary"):
                        diag = engine.adapter.diagnostics_summary()
                        live["mt5"]["diagnostics"] = diag
            except Exception as e:
                log_web_error(
                    logger, "/api", None, e, context={"msg": "Live state: mt5 introspection failed"}
                )
                live["mt5"] = {"connection": {}, "diagnostics": {}, "available": False}

        # REAL news context when the subsystem is enabled (never synthetic).
        if engine is not None and getattr(engine, "news_engine", None) is not None:
            try:
                ctx = engine.news_engine.current_context()
                if ctx is not None:
                    live["news"] = {
                        "available": True,
                        "state": getattr(ctx, "state", None).value
                        if getattr(getattr(ctx, "state", None), "value", None) is not None
                        else str(getattr(ctx, "state", "NORMAL")),
                        "bullish_score": getattr(ctx, "bullish_score", None),
                        "bearish_score": getattr(ctx, "bearish_score", None),
                        "xauusd_relevance": getattr(ctx, "xauusd_relevance", None),
                        "confidence": getattr(ctx, "confidence", None),
                        "freshness": getattr(ctx, "freshness", None),
                        "active_event_count": getattr(ctx, "active_event_count", None),
                        "timestamp": getattr(ctx, "timestamp", None).isoformat()
                        if getattr(getattr(ctx, "timestamp", None), "isoformat", None)
                        else None,
                    }
            except Exception as e:
                log_web_error(
                    logger, "/api", None, e, context={"msg": "Live state: news context failed"}
                )
                live["news"] = {
                    "available": False,
                    "state": None,
                    "reason": "NEWS_CONTEXT_ERROR",
                }

        # TASK-02-70D-INTEGRATION: Liquidity section already embedded in the
        # canonical state graph (get_system_state); surface it explicitly.
        live["liquidity"] = state.get("liquidity") or _liquidity_state_section(engine)
        return serialize_enums(live)

    @app.get("/api/live/accounting")
    def get_live_accounting(
        equity: float | None = None,
        entry: float | None = None,
        stop_loss: float | None = None,
        risk_pct: float | None = None,
    ) -> dict[str, Any]:
        """Authoritative accounting/risk computation - single source of truth.

        All lot/risk math runs through the SAME RiskEngine the live engine
        uses. When no parameters are supplied it reports the live account
        state; when `equity`/`entry`/`stop_loss` are supplied it computes the
        deterministic risk plan (risk USD, lots, margin, exposure) so the UI
        never duplicates accounting math in JavaScript. Works for any account
        size ($10 .. $1M+) without hardcoded assumptions.
        """
        engine = app.state.engine
        if engine is None:
            return {"available": False, "reason": "ENGINE_OFFLINE"}

        state = get_system_state()
        account = state.get("account", {})
        live_equity = account.get("equity")

        eff_equity = equity if equity is not None else live_equity
        eff_risk_pct = (
            risk_pct
            if risk_pct is not None
            else float(getattr(engine.config.risk, "risk_per_trade_pct", 0.5))
        )

        if eff_equity is None:
            return {"available": False, "reason": "NO_LIVE_EQUITY", "plan": None}

        result: dict[str, Any] = {
            "available": True,
            "source": "RISK_ENGINE + ACCOUNTING_CORE",
            "live": {
                "balance": account.get("balance"),
                "equity": live_equity,
                "floating_pnl": account.get("floating"),
                "margin_free": account.get("margin_free"),
                "drawdown_pct": account.get("drawdown"),
                "win_rate": account.get("win_rate"),
                "open_positions": account.get("open_positions"),
            },
        }

        # Deterministic risk plan for the requested (or live) account size.
        try:
            atr = state.get("atr") or 1.5
            tick = engine._last_tick
            bid = tick.bid if tick else (state.get("bid"))
            sym_info = getattr(engine, "_symbol_info", None)
            account_info = engine.adapter.get_account_info()

            plan_entry = entry if entry is not None else (bid if bid is not None else 0.0)
            plan_sl = (
                stop_loss
                if stop_loss is not None
                else ((plan_entry - atr * 1.5) if plan_entry > 0 else 0.0)
            )
            risk_usd = eff_equity * (eff_risk_pct / 100.0)

            plan: dict[str, Any] = {
                "equity": round(eff_equity, 2),
                "risk_pct": eff_risk_pct,
                "risk_usd": round(risk_usd, 2),
                "entry": round(plan_entry, 2) if plan_entry > 0 else None,
                "stop_loss": round(plan_sl, 2) if plan_sl > 0 else None,
                "sl_distance": round(abs(plan_entry - plan_sl), 2)
                if plan_entry > 0 and plan_sl > 0
                else None,
                "lot_size": None,
                "lot_step": None,
                "min_lot": None,
                "max_lot": None,
                "margin_required": None,
                "exposure_pct": None,
                "note": None,
            }

            if sym_info is not None and account_info is not None and plan_entry > 0 and plan_sl > 0:
                volume = engine.risk_engine.calculate_volume(
                    entry=plan_entry,
                    sl=plan_sl,
                    tp=plan_entry + (plan_entry - plan_sl) * 1.5,
                    account=account_info,
                    symbol_info=sym_info,
                )
                vol_min = float(getattr(sym_info, "volume_min", 0.01))
                vol_max = float(getattr(sym_info, "volume_max", 100.0))
                vol_step = float(getattr(sym_info, "volume_step", 0.01))
                contract = float(getattr(sym_info, "trade_contract_size", 100.0))
                leverage = float(getattr(account_info, "leverage", 100.0) or 100.0)
                plan["lot_size"] = round(volume, 4)
                plan["min_lot"] = vol_min
                plan["max_lot"] = vol_max
                plan["lot_step"] = vol_step
                if volume > 0 and contract > 0:
                    plan["margin_required"] = round((contract * plan_entry * volume) / leverage, 2)
                    plan["exposure_pct"] = round(
                        ((contract * plan_entry * volume) / max(eff_equity, 1e-9)) * 100.0, 2
                    )
                # Broker-native margin verification (order_calc_margin) with
                # explicit provenance; the estimate above is kept as fallback.
                if volume > 0 and plan_entry > 0:
                    broker_check = engine.risk_engine.verify_margin_with_broker(
                        symbol=engine.config.execution.symbol,
                        order_type=OrderType.BUY,
                        volume=volume,
                        price=plan_entry,
                        adapter=engine.adapter,
                        fallback_estimate=plan["margin_required"],
                    )
                    if broker_check.get("source") == "BROKER_NATIVE":
                        plan["margin_required"] = round(float(broker_check["margin_required"]), 2)
                    plan["margin_source"] = broker_check.get("source", "UNAVAILABLE")
                if volume <= 0:
                    plan["note"] = "INSUFFICIENT_EQUITY_FOR_MIN_LOT"
            elif plan_entry <= 0 or plan_sl <= 0:
                plan["note"] = "PRICING_UNAVAILABLE"
            result["plan"] = plan
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Live accounting plan failed"})
            result["plan"] = {"note": "COMPUTE_FAILED"}

        return serialize_enums(result)

    # REST APIs: Account summary
    @app.get("/api/account/summary")
    def get_account_summary() -> dict[str, Any]:
        """
        Canonical account + performance summary.

        PHASE 08 HARDENING: every number here comes from `AccountingCore`
        (broker adapter -> live state; authoritative ledger -> performance
        totals). When the adapter cannot be read or there is no closed-trade
        history, the fields are `None` - NEVER hardcoded placeholders like
        balance=10000 or win_rate=0.0 (the previous revision served synthetic
        zeros, contradicting the no-synthetic-numbers invariant and the
        duplicate-engine rule; see agents/bugs.md BUG-020).
        """
        engine = app.state.engine
        core = getattr(engine, "accounting_core", None) if engine else None
        if core is None:
            return {
                "available": False,
                "balance": None,
                "equity": None,
                "margin": None,
                "open_positions": None,
                "win_rate": None,
                "profit_factor": None,
                "max_drawdown": None,
                "total_trades": None,
            }

        try:
            live = core.live_state()
            trades = core.load_trades(limit=1000)
            closed = [t for t in trades if t.closed_at is not None]
            decided = sum(1 for t in closed if t.outcome.value in ("WIN", "LOSS"))
            wins = sum(1 for t in closed if t.is_win)
            gross_profit = sum(t.net_pnl for t in closed if t.net_pnl > 0.0)
            gross_loss = abs(sum(t.net_pnl for t in closed if t.net_pnl < 0.0))
            dd = core.drawdown_report()
            return serialize_enums(
                {
                    "available": live.available,
                    "balance": live.balance,
                    "equity": live.equity,
                    "margin": live.margin,
                    "open_positions": live.open_positions,
                    "win_rate": round(wins / decided * 100.0, 2) if decided else None,
                    "profit_factor": (
                        round(gross_profit / gross_loss, 2) if gross_loss > 0.0 else None
                    ),
                    "max_drawdown": dd.max_drawdown_pct,
                    "total_trades": len(closed),
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Account summary read failed"})
            return _err("INTERNAL_ERROR")

    # REST APIs: Historical trade logs with pagination/filters
    @app.get("/api/account/trades")
    def get_account_trades(
        limit: int = 100, offset: int = 0, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Closed-trade history: reconstructed broker trades (authoritative),
        falling back to the engine's own ledger rows when broker history has
        not been synchronized yet. Never invents rows."""
        engine = app.state.engine
        if not engine:
            return []
        audit = getattr(engine, "audit", None)
        if audit is None:
            return []
        try:
            broker_rows = audit.get_broker_trades(limit=limit, offset=offset)
        except Exception:
            broker_rows = []
        if broker_rows:
            return broker_rows
        return audit.get_ledger_trades(limit=limit, offset=offset, status_filter=status)

    # REST APIs: Account growth data for visualizer chart
    @app.get("/api/account/growth")
    def get_account_growth() -> list[dict[str, Any]]:
        engine = app.state.engine
        if not engine:
            return []
        return engine.audit.get_equity_growth_chart_data()

    # Toggle Engine Run Loop
    # Toggle Engine Run Loop
    @app.post("/api/engine/toggle")
    async def toggle_engine(req: ToggleRequest) -> dict[str, Any]:
        # BUG-239: MUST stay ASYNC - START schedules engine.run_loop() via asyncio.create_task; sync -> anyio threadpool -> RuntimeError.
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine reference not loaded.")

        if req.active:
            if not engine._running:
                logger.info("Web Dashboard triggered system start command.")
                task = asyncio.create_task(engine.run_loop())
                if not hasattr(app.state, "background_tasks"):
                    app.state.background_tasks = set()
                app.state.background_tasks.add(task)
                task.add_done_callback(app.state.background_tasks.discard)
        else:
            logger.info("Web Dashboard triggered system stop command.")
            engine._running = False

        return {"success": True, "engine_running": engine._running}

    # POST /api/engine/mode
    # UI source-of-control: the dashboard's execution-mode selector.
    # BUG-148: routes through the ENGINE's hot set_execution_mode() so the
    # operator choice is authoritative, the adapter boundary actually swaps
    # (PAPER simulation <-> LIVE broker), and the runtime badge is re-derived
    # from real connection state immediately. Persists in the settings DB and
    # runtime-config store so the choice survives restart.
    @app.post("/api/engine/mode")
    def set_engine_mode(req: EngineModeRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine reference not loaded.")
        wanted = req.mode.strip().upper()
        # BUG-148: the UI ships SIMULATION/REPLAY labels (legacy); map them to
        # the canonical ExecutionMode values so the selector always works.
        legacy_map = {"SIMULATION": "PAPER"}
        wanted = legacy_map.get(wanted, wanted)
        allowed = {m.value for m in ExecutionMode}
        if wanted not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid execution mode '{req.mode}' (allowed: {', '.join(sorted(allowed))})",
            )
        target = ExecutionMode(wanted)
        # TASK-CFGUI-001: /api/v1/runtime/mode documents the transition
        # matrix as SERVER truth ("the SERVER remains authoritative") — but
        # the route that actually APPLIES the mode never enforced it. Validate
        # against the same matrix here; an illegal transition is refused
        # before anything is swapped or persisted.
        from nexus_scalp.web.api_v1.runtime import _validate_transition

        check = _validate_transition(current_execution_mode(engine), wanted)
        if not check["valid"]:
            raise HTTPException(status_code=422, detail="; ".join(str(e) for e in check["errors"]))
        if hasattr(engine, "set_execution_mode"):
            result = engine.set_execution_mode(target, source="WEB_UI")
            if not result.get("success"):
                raise HTTPException(status_code=500, detail=result)
        else:
            engine.config.execution.mode = target
        # RUNTIME CONFIGURATION: execution.mode is a persisted runtime value.
        # Persist exactly ONCE: the versioned runtime store is the authority
        # (it writes the same settings rows the engine boot hydrates from).
        # The former unconditional db.set below double-wrote every mode
        # switch — two audit rows + two version bumps per click (observed:
        # execution.mode drifted to v86 while sibling keys sat at v41).
        persisted = False
        if hasattr(engine, "runtime_config"):
            report = engine.runtime_config.apply(
                {"execution.mode": wanted}, source="WEB_ENGINE_MODE", actor="web"
            )
            persisted = bool(getattr(report, "persisted", False)) or bool(
                getattr(report, "runtime_applied", False)
            )
        else:
            # Store-less fallback: the settings DB is the only persistence left.
            from nexus_scalp.settings import load_settings_service

            svc = getattr(engine, "settings_service", None) or load_settings_service()
            persisted = bool(
                svc.db.set(
                    "execution.mode",
                    wanted,
                    value_type="str",
                    source="USER_SETTINGS",
                    actor="web",
                )
            )
        return {
            "success": True,
            "mode": wanted,
            "engine_running": bool(getattr(engine, "_running", False)),
            "runtime_mode": getattr(engine, "_runtime_mode", wanted),
            "persisted": persisted,
        }

    # =====================================================================
    # DATABASE MANAGEMENT (DATABASE PORTABILITY, 2026-08-20)
    # ---------------------------------------------------------------------
    # Dedicated persistence panel surface: active provider + health, the
    # PostgreSQL configuration form (password NEVER round-trips in
    # plaintext), connection testing, and the SQLite->PostgreSQL migration
    # workflow (preview -> run -> validate).  Everything is read-only or
    # operator-initiated; nothing here ever mutates trading logic.
    # =====================================================================

    def _settings_service() -> Any:
        engine = app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        if svc is None:
            from nexus_scalp.settings import load_settings_service

            svc = load_settings_service()
        return svc

    def _stored_pg_config() -> dict[str, Any]:
        """The persisted PG config row ({} when absent/unreadable).

        Lane D hardening: ``/api/db/manage/config`` merges the incoming form
        over this row so a save from the discrete form never drops keys
        another writer persisted (advanced knobs, the ``password_secret``
        reference).  Never raises — a fresh environment has no row yet.
        """
        db = None
        try:
            from nexus_scalp.settings.service import SettingsDatabase

            db = SettingsDatabase()
            row = db.get(PG_CONFIG_SETTING_KEY)
            value = row.value if row is not None else None
            if isinstance(value, str):
                import json

                value = json.loads(value)
            return dict(value) if isinstance(value, dict) else {}
        except Exception as exc:  # pragma: no cover - best-effort read
            logger.debug("[DB_MANAGE_CONFIG] stored config row unreadable: %s", exc)
            return {}
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _repair_pg_truth(ui: dict[str, Any]) -> None:
        """Honest `postgres` + `password_set` for the status payload (Lane D).

        `health.load_ui_config` runs ``json.loads()`` on the PG config row
        value even though ``SettingsDatabase.get()`` already decodes
        ``value_type="json"`` rows to a dict: the resulting TypeError is
        swallowed and BOTH fields come back false-negatives on every load
        (``postgres`` null while a valid row exists, ``password_set`` false
        while the secret sits in the OS store — the tab would claim "no
        configuration is stored" forever, and readiness checks would claim the
        password is missing while it is present).

        Re-reads the row through ``_stored_pg_config()`` and reports
        SecretStore truth, stripping any ``password`` / ``confirm_password``
        value a foreign writer may have left in the row: the secret law says
        no password material is echoed, whatever the database holds.  Never
        raises — on failure the fields keep whatever ``load_ui_config``
        produced and the detail goes to the server log only.
        """
        try:
            from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY
            from nexus_scalp.settings.secret_store import SecureSecretStore

            row = _stored_pg_config()
            if row:
                clean = {k: v for k, v in row.items() if k not in ("password", "confirm_password")}
                ui["postgres"] = clean
                secret_ref = str(clean.get("password_secret") or PG_PASSWORD_SECRET_KEY)
            else:
                secret_ref = PG_PASSWORD_SECRET_KEY
            ui["password_set"] = bool(SecureSecretStore().has_secret(secret_ref))
        except Exception as e:
            log_web_error(logger, "/api/db/manage/status", None, e)

    @app.get("/api/db/manage/status")
    def db_manage_status() -> dict[str, Any]:
        """Active provider + per-domain health (DATABASE MANAGEMENT panel).

        Carries `postgresql_driver_available` and a `hints` list so the UI can
        explain a broken state instead of showing a bare "Warning": the active
        provider can be postgresql while psycopg is absent or the connection
        configuration was never stored (both observed 2026-09-23), and those
        two faults have different fixes.
        """
        try:
            from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
            from nexus_scalp.database.health import health_snapshot, load_ui_config

            health = health_snapshot()
            ui = load_ui_config()
            _repair_pg_truth(ui)
            pg_available = PostgreSQLDriver.available()
            hints: list[str] = []
            if ui["provider"] == "postgresql":
                if not pg_available:
                    hints.append(
                        "Active provider is postgresql but psycopg is not installed: "
                        "run `pip install 'nexus[postgres]'` or switch back to sqlite."
                    )
                if not ui["postgres"]:
                    hints.append(
                        "No PostgreSQL connection configuration is stored — fill in "
                        "the form below and Save config before migrating."
                    )
                if (
                    pg_available
                    and ui["postgres"]
                    and health.get("overall")
                    not in (
                        "Healthy",
                        "OK",
                    )
                ):
                    hints.append(
                        "PostgreSQL is configured but the server did not answer "
                        "its health probe — check that it is running."
                    )
            status_payload: dict[str, Any] = {
                "success": True,
                "provider": ui["provider"],
                "supported_providers": health["supported_providers"],
                "overall": health["overall"],
                "domains": health["domains"],
                "postgres": ui["postgres"],
                "password_set": ui["password_set"],
                "postgresql_driver_available": pg_available,
                "hints": hints,
                "provider_truth": _provider_truth(ui),
            }
            # db-provider-pro (2026-09-23): advanced knob values + the known
            # domain db-name list.  Additive only — every key above keeps its
            # name and shape.  `domains` above stays the health-service's
            # per-domain snapshot; the new list is reported under a distinct
            # name so the UI never confuses the two.
            db_options = _db_options_snapshot()
            if db_options:
                status_payload["options"] = db_options.get("options", {})
                status_payload["domain_db_names"] = db_options.get("domains", [])
            return serialize_enums(status_payload)
        except Exception as e:
            log_web_error(logger, "/api/db/manage/status", None, e)
            return _err("DB_MANAGE_STATUS_FAILED")

    def _db_options_snapshot() -> dict[str, Any]:
        """Advanced knobs + known domain db names for the status payload.

        Best-effort and additive: a failure here must never break the status
        endpoint — the two new keys are omitted and the existing keys keep
        their names and shapes (contract §3.3).
        """
        try:
            from nexus_scalp.settings.provider_options import options_status_snapshot

            return options_status_snapshot("audit")
        except Exception as exc:  # pragma: no cover - defensive on a read path
            logger.debug("[DB_MANAGE_STATUS] options block unavailable: %s", exc)
            return {}

    @app.post("/api/db/manage/config")
    def db_manage_config(payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the PostgreSQL connection configuration + password.

        The password is routed to the OS SecretStore (DPAPI); the settings
        DB only ever holds a secret-key reference.  `password` and
        `confirm_password` are consumed here and NEVER stored in the config
        row or echoed back.

        Hardened (Lane D): the body is an ALLOWLIST — every key must be a real
        :class:`~nexus_scalp.database.config.DatabaseConfig` / PG config name,
        otherwise the request is refused with `DB_CONFIG_UNKNOWN_KEYS` instead
        of persisting an unknown (or typo'd) key into the settings JSON.  The
        write is ADDITIVE: the incoming keys are merged over the stored row, so
        keys another writer put there (the advanced knobs, the `password_secret`
        reference) survive a save from this form.
        """
        # Real DatabaseConfig/pg config keys this endpoint may persist, plus
        # the two password fields consumed above (never stored).
        allowed = {
            "provider",
            "domain",
            "host",
            "port",
            "database",
            "username",
            "ssl_mode",
            "command_timeout_sec",
            "connect_timeout_sec",
            "migrate_on_startup",
            "pooling_enabled",
            "sqlite_path",
            "sqlite_uri",
        }
        try:
            incoming = dict(payload)
            password = incoming.get("password") or ""
            confirm = incoming.get("confirm_password") or ""
            if password and password != confirm:
                return _err("PASSWORD_MISMATCH")
            for k in ("password", "confirm_password"):
                incoming.pop(k, None)
            unknown = sorted(k for k in incoming if k not in allowed)
            if unknown:
                return _err(
                    "DB_CONFIG_UNKNOWN_KEYS",
                    message=(
                        f"Unknown configuration key(s): {', '.join(unknown)}. "
                        f"Allowed keys: {', '.join(sorted(allowed))}."
                    ),
                )
            svc = _settings_service()
            # Additive merge: never drop keys the caller did not send.
            row: dict[str, Any] = dict(_stored_pg_config())
            row.update(incoming)
            row.setdefault("provider", "postgresql")
            row.setdefault("domain", "audit")
            if password:
                from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY

                svc.secrets.set_secret(PG_PASSWORD_SECRET_KEY, str(password))
                row["password_secret"] = PG_PASSWORD_SECRET_KEY
            # Persisted directly with actor="web" (contract §4): the settings DB
            # keeps the secret-KEY reference alongside the non-secret config.
            svc.db.set(
                PG_CONFIG_SETTING_KEY,
                row,
                value_type="json",
                source="USER_SETTINGS",
                actor="web",
            )
            return {
                "success": True,
                "password_set": bool(password) or svc.postgres_password_set(),
            }
        except Exception as e:
            log_web_error(logger, "/api/db/manage/config", None, e)
            return _err("DB_MANAGE_CONFIG_FAILED")

    @app.post("/api/db/manage/provider")
    def db_manage_provider(payload: dict[str, Any]) -> dict[str, Any]:
        """Switch the ACTIVE provider (persisted, applied next startup).

        Switching NEVER moves or destroys data: the operator is expected to
        run the migration workflow first.  This endpoint only flips the
        authoritative selection in the settings database.
        """
        try:
            from nexus_scalp.database.provider import DatabaseProvider

            provider = DatabaseProvider.parse(str(payload.get("provider") or ""))
            svc = _settings_service()
            svc.set_database_provider(provider.value)
            return {
                "success": True,
                "provider": provider.value,
                "restart_required": True,
            }
        except Exception as e:
            log_web_error(logger, "/api/db/manage/provider", None, e)
            return _err("DB_MANAGE_PROVIDER_FAILED")

    @app.post("/api/db/manage/test-connection")
    def db_manage_test_connection(payload: dict[str, Any]) -> dict[str, Any]:
        """Test the PostgreSQL connection BEFORE migration (non-destructive).

        Hardened (Lane D): the driver resolves its password from the OS
        SecretStore, so a probe CAN authenticate only when the supplied
        password is handed to the store first.  The probe therefore writes the
        supplied password — but under an EPHEMERAL key derived from the real
        one, and it deletes that key in a ``finally`` block.  The operator's
        stored secret under the canonical ``PG_PASSWORD_SECRET_KEY`` is never
        touched: a probe with a WRONG password can no longer clobber a good
        stored secret (it failed to connect, and nothing is persisted).  A
        blank password on the wire means "use the stored secret".
        """
        try:
            from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY, DatabaseConfig
            from nexus_scalp.database.drivers import get_driver
            from nexus_scalp.settings.secret_store import SecureSecretStore

            raw = dict(payload)
            password = raw.pop("password", None)
            probe_key = f"{PG_PASSWORD_SECRET_KEY}.probe"
            store = SecureSecretStore()
            if password:
                # Ephemeral: removed again in the finally block below so the
                # probe never leaves residue in the OS keystore.
                store.set_secret(probe_key, str(password))
                secret_ref = probe_key
            else:
                # Blank on the wire = keep the stored secret (contract §1).
                secret_ref = PG_PASSWORD_SECRET_KEY
            cfg = DatabaseConfig.for_postgres(
                domain="audit",
                host=str(raw.get("host") or "localhost"),
                port=int(raw.get("port") or 5432),
                database=str(raw.get("database") or "nse_audit"),
                username=str(raw.get("username") or "nse_user"),
                ssl_mode=str(raw.get("ssl_mode") or ""),
                password_secret=secret_ref,
            )
            driver = get_driver(cfg)
            try:
                ok = driver.ping()
                version = driver.database_version() if ok else ""
            finally:
                driver.close()
            return {
                "success": ok,
                "connected": ok,
                "database_version": version if ok else "",
                "latency_ms": None,
            }
        except Exception as e:
            log_web_error(logger, "/api/db/manage/test-connection", None, e)
            return _err("DB_TEST_CONNECTION_FAILED")
        finally:
            # Never leave the probe secret in the OS keystore, and never let a
            # failed probe (wrong password) disturb the stored default secret.
            # Self-contained: rebind everything locally so this block cannot
            # raise (a cleanup failure must never mutate the response).
            try:
                raw_password = dict(payload).get("password")
                if raw_password:
                    from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY
                    from nexus_scalp.settings.secret_store import SecureSecretStore

                    SecureSecretStore().delete_secret(f"{PG_PASSWORD_SECRET_KEY}.probe")
            except Exception:
                pass

    @app.post("/api/db/manage/preview")
    def db_manage_preview(payload: dict[str, Any]) -> dict[str, Any]:
        """Dry-run migration preview: tables, rows, volume, issues."""
        try:
            from nexus_scalp.database.config import DatabaseConfig
            from nexus_scalp.database.migrate_engine import (
                MigrationOptions,
                SqliteToPostgresMigrator,
            )

            src = DatabaseConfig.for_sqlite(
                "audit",
                path=str(payload.get("sqlite_path") or "") or None,
            )
            raw = dict(payload)
            dst = DatabaseConfig.for_postgres(
                domain="audit",
                host=str(raw.get("host") or "localhost"),
                port=int(raw.get("port") or 5432),
                database=str(raw.get("database") or "nse_audit"),
                username=str(raw.get("username") or "nse_user"),
                ssl_mode=str(raw.get("ssl_mode") or ""),
            )
            mig = SqliteToPostgresMigrator(src, dst, MigrationOptions(dry_run=True))
            preview = mig.preview()
            # Defense-in-depth (CodeQL py/stack-trace-exposure): the preview
            # dict is built from driver internals; keep only schema-shaped
            # fields and strip any exception-text-carrying keys before it
            # reaches the client.
            safe_preview = {
                k: v
                for k, v in preview.items()
                if k
                in {
                    "source",
                    "destination",
                    "tables",
                    "table_details",
                    "rows",
                    "estimated_volume_bytes",
                    "issues",
                    "warnings",
                }
            }
            safe_preview["issues"] = [
                i for i in safe_preview.get("issues") or [] if isinstance(i, str)
            ][:100]
            return {"success": True, "preview": safe_preview}
        except Exception as e:
            log_web_error(logger, "/api/db/manage/preview", None, e)
            return _err("DB_MIGRATION_PREVIEW_FAILED")

    @app.post("/api/db/manage/migrate")
    def db_manage_migrate(payload: dict[str, Any]) -> dict[str, Any]:
        """Run the SQLite->PostgreSQL migration (streamed batches, resumable).

        Operator-initiated; requires the destination password in the secret
        store (or supplied here as `password` once, routed to the store).
        Returns a full migration report; progress is available via
        `/api/db/manage/progress`.
        """
        try:
            import threading

            from nexus_scalp.database.config import DatabaseConfig
            from nexus_scalp.database.migrate_engine import (
                MigrationOptions,
                SqliteToPostgresMigrator,
            )
            from nexus_scalp.settings.secret_store import SecureSecretStore

            raw = dict(payload)
            password = raw.pop("password", None)
            if password:
                from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY

                SecureSecretStore().set_secret(PG_PASSWORD_SECRET_KEY, str(password))
            src = DatabaseConfig.for_sqlite(
                "audit",
                path=str(raw.get("sqlite_path") or "") or None,
            )
            dst = DatabaseConfig.for_postgres(
                domain="audit",
                host=str(raw.get("host") or "localhost"),
                port=int(raw.get("port") or 5432),
                database=str(raw.get("database") or "nse_audit"),
                username=str(raw.get("username") or "nse_user"),
                ssl_mode=str(raw.get("ssl_mode") or ""),
            )
            options = MigrationOptions(
                dry_run=bool(raw.get("dry_run")),
                confirm=bool(raw.get("confirm")),
                resume=bool(raw.get("resume", True)),
                batch_size=int(raw.get("batch_size") or 2000),
                validate_checksums=bool(raw.get("validate_checksums", True)),
            )
            mig = SqliteToPostgresMigrator(src, dst, options)

            # background thread: never block the web loop; progress/report
            # land on app.state.db_migration_state for the poll endpoints.
            def _run() -> None:
                try:
                    state: dict[str, Any] = {
                        "done": False,
                        "progress": 0.0,
                        "current_table": "",
                        "rows_copied": 0,
                        "total_rows": 0,
                        "report": None,
                    }
                    app.state.db_migration_state = state

                    def _on_progress(table: str, done_i: int, total: int, batch: int) -> None:
                        state["current_table"] = table
                        state["rows_copied"] = done_i
                        state["total_rows"] = total
                        state["progress"] = (done_i / total) if total else 0.0
                        state["batch"] = batch

                    try:
                        report = mig.run(on_progress=_on_progress)
                    except Exception:
                        state["report"] = {
                            "status": "FAILED",
                            "code": "DB_MIGRATION_FAILED",
                        }
                        state["done"] = True
                        state["progress"] = 0.0
                        return
                    state["report"] = report.to_dict()
                    state["done"] = True
                    state["progress"] = 1.0
                    # switch active provider only when validation passed
                    if report.provider_switch_ready:
                        _settings_service().set_database_provider("postgresql")
                        state["provider_switched"] = True
                    else:
                        state["provider_switched"] = False
                except Exception:
                    app.state.db_migration_state = {
                        "done": True,
                        "progress": 0.0,
                        "report": {
                            "status": "FAILED",
                            "code": "DB_MIGRATION_FAILED",
                        },
                    }

            t = threading.Thread(target=_run, daemon=True, name="nse_db_migrate")
            t.start()
            return {"success": True, "started": True, "job": "migrate"}
        except Exception as e:
            log_web_error(logger, "/api/db/manage/migrate", None, e)
            return _err("DB_MIGRATION_START_FAILED")

    @app.get("/api/db/manage/progress")
    def db_manage_progress() -> dict[str, Any]:
        """Live migration progress (polled by the panel UI)."""
        try:
            state = getattr(app.state, "db_migration_state", None)
            if state is None:
                return {"success": True, "done": False, "progress": 0, "report": None}
            return {
                "success": True,
                "done": bool(state.get("done")),
                "progress": float(state.get("progress") or 0.0),
                "current_table": state.get("current_table") or "",
                "rows_copied": int(state.get("rows_copied") or 0),
                "total_rows": int(state.get("total_rows") or 0),
                "report": state.get("report"),
            }
        except Exception as e:
            log_web_error(logger, "/api/db/manage/progress", None, e)
            return _err("DB_MIGRATION_PROGRESS_FAILED")

    @app.get("/api/db/manage/report")
    def db_manage_report() -> dict[str, Any]:
        """The last migration report (result + validation)."""
        try:
            state = getattr(app.state, "db_migration_state", None)
            if state is None:
                return {"success": True, "report": None}
            return {"success": True, "report": state.get("report")}
        except Exception as e:
            log_web_error(logger, "/api/db/manage/report", None, e)
            return _err("DB_MIGRATION_REPORT_FAILED")

    @app.post("/api/db/manage/backup")
    def db_manage_backup() -> dict[str, Any]:
        """WAL-consistent SQLite backup (streaming sqlite backup API)."""
        try:
            from nexus_scalp.database.config import DatabaseConfig
            from nexus_scalp.database.drivers import get_driver

            src = DatabaseConfig.for_sqlite("audit")
            driver = get_driver(src)
            ts = time.strftime("%Y%m%d-%H%M%S")
            backup_path = f"artifacts/backups/audit_backup_{ts}.db"
            os.makedirs("artifacts/backups", exist_ok=True)

            conn = driver.connect(timeout=30.0)
            try:
                dest = __import__("sqlite3").connect(backup_path)
                try:
                    conn.backup(dest)
                finally:
                    dest.close()
            finally:
                conn.close()
            return {"success": True, "backup_path": backup_path}
        except Exception as e:
            log_web_error(logger, "/api/db/manage/backup", None, e)
            return _err("DB_MIGRATION_BACKUP_FAILED")

    @app.get("/api/db/manage/validate")
    def db_manage_validate() -> dict[str, Any]:
        """Validate the last migration (row counts, identities, financials)."""
        try:
            from nexus_scalp.database.config import DatabaseConfig, load_database_config
            from nexus_scalp.database.migrate_engine import (
                MigrationOptions,
                SqliteToPostgresMigrator,
            )

            src = load_database_config("audit")
            if not src.is_sqlite:
                # source is the canonical SQLite artifacts DB
                src = DatabaseConfig.for_sqlite("audit")
            dst = load_database_config("audit")
            if not dst.is_postgresql:
                return {"success": True, "validation": "NOT_CONFIGURED"}
            mig = SqliteToPostgresMigrator(src, dst, MigrationOptions())
            result = mig.validate()
            return {"success": True, "validation": result}
        except Exception as e:
            log_web_error(logger, "/api/db/manage/validate", None, e)
            return _err("DB_MIGRATION_VALIDATE_FAILED")

    # GET /api/config
    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        """Configuration form — reads the AUTHORITATIVE runtime snapshot
        when the engine is up (live.yaml is only a bootstrap fallback)."""
        engine = app.state.engine
        store = getattr(engine, "runtime_config", None) if engine else None
        if store is not None:
            snap = store.get_snapshot()
            cfg = snap.to_app_config()
            raw_data = cfg.model_dump()
            raw_data["configuration_version"] = snap.version
            raw_data["runtime_applied"] = True
            # telegram section is masked status only (secrets never plaintext)
            tg = raw_data.get("telegram") or {}
            tg["bot_token"] = snap.telegram.token_masked or ""
            raw_data["telegram"] = tg
            # BUG-072 + TASK-CFGUI-001: mt5.password / notification key masked
            # too — no secret-shaped field ever reaches the browser.
            return mask_config_secrets(raw_data)
        # Engine offline: bootstrap YAML fallback (diagnostic only)
        live_config_path = Path("configs/live.yaml")
        if not live_config_path.exists():
            live_config_path = Path("configs/base.yaml")

        with open(live_config_path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

        # BUG-072: never return plaintext credentials to the browser.
        # The UI receives masked display values; real credentials live in
        # the secure store / live.yaml and are exposed only as status.
        return mask_config_secrets(raw_data)

    # POST /api/config
    @app.post("/api/config")
    def save_config(raw_config: dict[str, Any]) -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")

        try:
            # BUG-080: telegram credentials NEVER persist to live.yaml (plaintext).
            # The UI submits them through this endpoint; route them into the
            # secure secret store + rebuild the live notifier (BUG-072 path).
            # Only telegram.enabled remains in YAML (engine boot default).
            tg_payload = raw_config.get("telegram")
            if isinstance(tg_payload, dict):
                engine = app.state.engine
                svc = getattr(engine, "settings_service", None) if engine else None
                if svc is None:
                    from nexus_scalp.settings import load_settings_service

                    svc = load_settings_service()
                # BUG-080: only a REAL non-empty token/admin updates the store.
                # The UI's config form is populated from the MASKED GET value and
                # may submit '' when the operator did not type a new credential —
                # that must NOT wipe an existing secure-store secret (use the
                # dedicated /api/settings/telegram endpoint to clear creds).
                tg_token = str(tg_payload.get("bot_token") or "").strip() or None
                tg_admin = str(tg_payload.get("admin_id") or "").strip() or None
                tg_enabled = bool(tg_payload.get("enabled", True))
                svc.set_telegram(
                    enabled=tg_enabled,
                    bot_token=tg_token,
                    admin_id=tg_admin,
                    actor="web_config",
                )
                logger.info(
                    "[TELEGRAM_CONFIG] event=PERSISTED source=WEB_CONFIG token_present=%s "
                    "admin_id_present=%s",
                    bool(tg_token),
                    bool(tg_admin),
                )
                # Never write the secret into live.yaml.
                tg_payload["bot_token"] = ""
                tg_payload["admin_id"] = ""
                # Rebuild the live notifier so the change is effective NOW
                # without a restart (mirrors POST /api/settings/telegram).
                if engine is not None and getattr(engine, "notifier", None) is not None:
                    sec_token, sec_admin = svc.get_telegram_credentials()
                    enabled_row = svc.db.get("telegram.enabled")
                    enabled = bool(enabled_row.value) if enabled_row else tg_enabled
                    engine.notifier.shutdown(timeout=1.0)
                    engine.notifier = TelegramNotifier(
                        bot_token=sec_token,
                        admin_id=sec_admin,
                        enabled=enabled,
                    )
                    logger.info(
                        "[TELEGRAM_CONFIG] event=REBUILT source=WEB_CONFIG configured=%s",
                        bool(sec_token and sec_admin),
                    )

            # TASK-CFGUI-001: a MASKED mt5.password round-tripping from the
            # config form must never overwrite the stored credential in
            # live.yaml (pairs with mask_config_secrets on GET).
            restore_masked_mt5_password(raw_config, live_config_path)

            # Write to disk atomically (compatibility projection; the
            # authoritative runtime state lives in the runtime config store)
            tmp = live_config_path.with_suffix(".yaml.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw_config, f, default_flow_style=False)
            tmp.replace(live_config_path)

            # RUNTIME CONFIGURATION: apply execution/risk/model sections
            # through the authoritative versioned store (validate -> persist
            # -> version++ -> ConfigurationChanged -> atomic snapshot swap).
            engine = app.state.engine
            updates: dict[str, Any] = {}
            exec_cfg = raw_config.get("execution") or {}
            for k in ("symbol", "timeframe", "magic_number", "max_slippage_points"):
                if k in exec_cfg:
                    updates[f"execution.{k}"] = exec_cfg[k]
            risk_cfg = raw_config.get("risk") or {}
            for k in (
                "max_account_drawdown_pct",
                "risk_per_trade_pct",
                "max_concurrent_positions",
                "max_spread_points",
                "max_allowed_lots",
                "enforce_stop_loss",
            ):
                if k in risk_cfg:
                    updates[f"risk.{k}"] = risk_cfg[k]
            model_cfg = raw_config.get("model") or {}
            for k in ("confidence_threshold", "model_artifact_path"):
                if k in model_cfg:
                    updates[f"model.{k}"] = model_cfg[k]
            if engine is not None and hasattr(engine, "runtime_config") and updates:
                report = engine.apply_runtime_update(updates, source="WEB_CONFIG", actor="web")
                return {
                    "success": report.success,
                    "runtime_applied": report.runtime_applied,
                    "persisted": report.persisted,
                    "configuration_version": report.configuration_version,
                    "runtime_version": engine.runtime_config.get_version(),
                    "correlation_id": report.correlation_id,
                    "reason": report.reason,
                }

            return {"success": True, "runtime_applied": False, "reason": "ENGINE_OFFLINE"}
        except Exception as e:
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Failed to save and hot-reload configurations"},
            )
            return _err("OPERATION_FAILED")

    # ------------------------------------------------------------------
    # RUNTIME CONFIGURATION (hot reload): effective view + diagnostics.
    # The engine's authoritative config is the versioned immutable
    # snapshot in RuntimeConfigStore; live.yaml is bootstrap/export only.
    # ------------------------------------------------------------------
    @app.get("/api/runtime-config")
    def get_runtime_config_effective() -> dict[str, Any]:
        """Effective runtime configuration the engine is ACTUALLY using."""
        engine = app.state.engine
        store = getattr(engine, "runtime_config", None) if engine else None
        if store is None:
            return {"success": False, "reason": "ENGINE_OFFLINE", "runtime_applied": False}
        snap = store.get_snapshot()
        diag = store.diagnostics()
        return {
            "success": True,
            "configuration_version": snap.version,
            "runtime_applied": True,
            "source": snap.source,
            "updated_at": snap.updated_at,
            "effective": snap.to_dict(),
            "diagnostics": diag,
            "secret_masked": {
                "telegram_token": snap.telegram.token_masked or "NOT_CONFIGURED",
            },
        }

    @app.get("/api/runtime-config/diagnostics")
    def get_runtime_config_diagnostics() -> dict[str, Any]:
        """Persistent version vs runtime version vs live.yaml version."""
        from nexus_scalp.configuration import config_file_hash
        from nexus_scalp.settings import load_settings_service

        engine = app.state.engine
        store = getattr(engine, "runtime_config", None) if engine else None
        runtime_version = store.get_version() if store else None
        persistent_version = None
        last_apply = None
        last_error = ""
        try:
            svc = getattr(engine, "settings_service", None) if engine else None
            svc = svc or load_settings_service()
            persistent_version = int(svc.db.get_meta("runtime_config.version") or 0)
            last_apply = svc.db.get_meta("runtime_config.last_apply_status") or "NEVER"
            last_error = svc.db.get_meta("runtime_config.last_apply_error") or ""
        except Exception as exc:
            logger.warning("persistent store unreadable", exc_info=exc)
            last_error = "persistent store unreadable"
        yaml_path = Path("configs/live.yaml")
        return {
            "success": True,
            "persistent_version": persistent_version,
            "runtime_version": runtime_version,
            "live_yaml_version": None,  # live.yaml has no version field
            "live_yaml_hash": config_file_hash(yaml_path) if yaml_path.exists() else "",
            "live_yaml_exists": yaml_path.exists(),
            "last_apply_status": last_apply,
            "last_apply_error": last_error,
            "mismatch": (
                persistent_version is not None
                and runtime_version is not None
                and persistent_version != runtime_version
            ),
        }

    @app.post("/api/runtime-config/apply")
    def apply_runtime_config(payload: dict[str, Any]) -> dict[str, Any]:
        """Unified runtime config apply (algorithm + execution + risk + model).

        Payload: {"updates": {"algo.atr_sl_buffer_multiplier": 2.0, ...}}.
        Mirrors the verified contract: save -> validate -> persist -> version++
        -> ConfigurationChanged -> atomic snapshot swap -> confirm.
        """
        engine = app.state.engine
        if engine is None or not hasattr(engine, "runtime_config"):
            raise HTTPException(status_code=400, detail="Trading Engine offline.")
        updates = payload.get("updates") or {}
        source = str(payload.get("source") or "WEB_UI")
        if not isinstance(updates, dict) or not updates:
            raise HTTPException(status_code=422, detail="updates dict required")
        report = engine.apply_runtime_update(updates, source=source, actor="web")
        out = report.to_dict()
        out["runtime_version"] = engine.runtime_config.get_version()
        return out

    @app.post("/api/runtime-config/model-swap")
    async def model_hot_swap(payload: dict[str, Any]) -> dict[str, Any]:
        """Model artifact hot swap: load-validate-warm-atomic-swap.

        Payload: {"model_artifact_path": "..."}
        Never replaces the healthy serving model before the new artifact
        has loaded successfully and warmed up.
        """
        engine = app.state.engine
        if engine is None or not hasattr(engine, "hot_swap_model"):
            raise HTTPException(status_code=400, detail="Trading Engine offline.")
        artifact = str(payload.get("model_artifact_path") or "").strip()
        if not artifact:
            raise HTTPException(status_code=422, detail="model_artifact_path required")
        result = await engine.hot_swap_model(artifact, source="WEB_UI")
        return result

    # ------------------------------------------------------------------
    # BUG-072: /api/settings — isolated user settings + secure secrets.
    # Never returns plaintext secrets; masked token only.
    # ------------------------------------------------------------------
    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        engine = app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        if svc is None:
            from nexus_scalp.settings import load_settings_service

            svc = load_settings_service()  # standalone fallback (no engine)
        return {"success": True, **svc.safe_snapshot()}

    @app.get("/api/settings/telegram/status")
    def telegram_settings_status() -> dict[str, Any]:
        engine = app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        from nexus_scalp.settings import load_settings_service

        svc = svc or load_settings_service()
        result = svc.telegram_config_status()
        notifier = getattr(engine, "notifier", None) if engine else None
        if notifier is not None:
            result["worker"] = notifier.health_state()
        return {"success": True, **result}

    @app.post("/api/settings/telegram")
    def update_telegram_settings(payload: dict[str, Any]) -> dict[str, Any]:
        engine = app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        from nexus_scalp.settings import load_settings_service

        svc = svc or load_settings_service()
        try:
            result = svc.set_telegram(
                enabled=payload.get("enabled"),
                bot_token=str(payload.get("bot_token") or "").strip() or None,
                admin_id=str(payload.get("admin_id") or "").strip() or None,
                actor="web",
            )
        except Exception as e:
            log_web_error(logger, "/api/settings/telegram", None, e)
            return _err("SETTINGS_UPDATE_FAILED")
        # LIVE hot-rebuild of the notifier (restart-free pickup)
        if engine is not None:
            token, admin = svc.get_telegram_credentials()
            enabled_row = svc.db.get("telegram.enabled")
            enabled = bool(enabled_row.value) if enabled_row else True
            engine.notifier.shutdown(timeout=1.0)
            engine.notifier = TelegramNotifier(
                bot_token=token,
                admin_id=admin,
                enabled=enabled,
            )
            logger.info(
                "[TELEGRAM_CONFIG] event=REBUILT source=WEB_SETTINGS configured=%s",
                bool(token and admin),
            )
        return result

    # POST /api/settings/validate — server-side validation of a proposed value.
    # TASK-CFGUI-001: previously this route answered `valid: true` for every
    # key without ever seeing a value (mutability lookup only). It now dry-runs
    # the proposed value through the runtime configuration builder — the SAME
    # validator the apply path uses (type/range/enum/unknown-key/cross-field,
    # zero side effects) — whenever the caller sends one, giving the UI's
    # validate-before-apply gate real server-side verdicts to block on.
    # Key-only calls stay mutability-only (backward compatible).
    @app.post("/api/settings/validate")
    def validate_setting(payload: dict[str, Any]) -> dict[str, Any]:
        key = str(payload.get("key") or "")
        from nexus_scalp.settings.service import MUTABILITY

        mutability = MUTABILITY.get(key, "HOT_RESTRICTED")
        checked = "value" in payload
        errors: list[str] = []
        if checked and key:
            errors = dry_run_config_errors(app.state.engine, key, payload.get("value"))
        return {
            "success": True,
            "key": key,
            "mutability": mutability,
            "valid": not errors,
            "errors": errors,
            "checked": checked,
        }

    # POST /api/telegram/test — sends a connectivity test message through the
    # configured notifier. Returns the FINAL delivery state raised by the
    # worker (never a local HTTP-200-as-success illusion).
    @app.post("/api/telegram/test")
    def telegram_test() -> dict[str, Any]:
        engine = app.state.engine
        notifier = getattr(engine, "notifier", None) if engine else None
        if notifier is None:
            return _err(
                "NOTIFIER_UNAVAILABLE",
                message="Engine notifier is not available (engine not running).",
            )
        if not notifier.enabled:
            return _err(
                "NOTIFIER_DISABLED",
                message="Telegram is disabled or bot_token/admin_id are missing. "
                "Save them first, then retry.",
            )
        try:
            result = notifier.send_diagnostic("NEXUS TELEGRAM DIAGNOSTIC TEST")
            if result.get("ok"):
                return {
                    "success": True,
                    "message_id": result.get("message_id"),
                    "correlation_id": result.get("correlation_id"),
                    "notification_id": result.get("notification_id"),
                }
            return _err(
                "SEND_FAILED",
                message=result.get("safe_message") or "delivery not confirmed",
                category=result.get("category", "TELEGRAM_UNKNOWN_ERROR"),
                correlation_id=result.get("correlation_id"),
            )
        except Exception as e:
            log_web_error(logger, "/api/telegram/test", None, e, context={"msg": "telegram test"})
            return _err("SEND_FAILED", message="Telegram test raised an exception")
