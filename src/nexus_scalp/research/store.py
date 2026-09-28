"""
Research Read Store
===================
PHASE 09B bounded read facade over the research tables.

All research is DERIVED from the authoritative Phase 08 ledger; every function
here is a read-path query for observability, forensics and the self-healing
rebuild. Writes are performed by the engines through the AuditRepository
background queue; this module owns no write path.
"""

from __future__ import annotations

import contextlib
import sqlite3
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.research.store")

MAX_READ_LIMIT = 2000

#: PHASE 25 (2026-08-25): JSON column carrying context matrices
#: {session_matrix, hourly_matrix, weekday_matrix, regime_matrix} on every
#: registry entry so discovery quality can be analyzed per market condition.
CONTEXT_MATRICES_COLUMN = "context_matrices"


class _ProviderRead:
    """Provider-portable reader: SQLite keeps its own connection, a pooled
    provider reads through the registered fabric READ plane.

    PG-RESEARCH-READ-001: this module used to open
    ``sqlite3.connect(repo._db_path)`` behind an ``if not repo._is_sqlite``
    gate, so under the persisted PostgreSQL provider every research read
    returned its empty default (0 / [] / unavailable) with no exception and
    no log — the /research page reported ``Registry total: 0`` while the
    server held thousands of registry rows. The plane is the SAME registered
    read backend the audit read guard serves declared reads from (CHG-0067),
    so the research surface now reads the store the engine writes.

    A pooled provider without a resolvable plane yields ``None``; callers
    must surface that as ``available: False`` (cannot read) and never as an
    empty result (no data) — the failure mode this fix exists to remove.
    """

    def __init__(self, repo: AuditRepository) -> None:
        self._repo = repo
        self._plane: Any = None
        self._checked = False

    def _resolve(self) -> Any:
        """Resolve the plane once and cache it (idempotent across methods)."""
        if not self._checked:
            self._checked = True
            try:
                self._plane = self._repo.research_read_plane()
            except Exception:
                self._plane = None
            if self._plane is None:
                # Unreadable must stay visible in the same degradation metrics
                # the CHG-0067 audit guard already emits, so an operator can
                # tell "no data" from "cannot read" from a dashboard alone.
                # Observability must never break the read itself: a repository
                # (or a test double) without the counter hooks still reads.
                with contextlib.suppress(Exception):
                    for name, op in (
                        ("provider_read_degraded_total", None),
                        ("_bump_provider_read_operation", "research_read"),
                    ):
                        if op is None:
                            self._repo._bump_provider_read_counter(name)
                        else:
                            self._repo._bump_provider_read_operation(op)
        return self._plane

    @property
    def available(self) -> bool:
        """A readable backend is resolvable for this process.

        SQLite always reads through its own connection (``None`` plane is the
        documented, correct answer there), so readability is ``_is_sqlite`` or
        a resolved plane — never just the plane.
        """
        return self._repo._is_sqlite or self._resolve() is not None

    def rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Rows as dicts; raises so the caller's except-clause logs it."""
        if self._repo._is_sqlite:
            conn = sqlite3.connect(self._repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()
        plane = self._resolve()
        if plane is None:
            raise RuntimeError("no read plane registered for domain 'audit'")
        return [dict(r) for r in plane.query(sql, args)]

    def one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        if self._repo._is_sqlite:
            conn = sqlite3.connect(self._repo._db_path, timeout=5.0)
            try:
                return conn.execute(sql, args).fetchone()[0]
            finally:
                conn.close()
        plane = self._resolve()
        if plane is None:
            raise RuntimeError("no read plane registered for domain 'audit'")
        return plane.scalar(sql, args)

    def count(self, table: str, where: str = "", args: tuple[Any, ...] = ()) -> int:
        """Bounded ``COUNT(*)``; 0 only when the table is genuinely empty."""
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(self.scalar(sql, args) or 0)


def ensure_registry_context_columns(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE adding ``context_matrices`` to strategy_registry.

    PRAGMA pre-check (shared helper in audit_repository): an existing column
    is skipped before the ALTER is attempted, so no duplicate-column
    exception is ever raised — not even a first-chance one for an attached
    debugger. Fresh databases gain the column immediately after CREATE TABLE.
    """
    try:
        from nexus_scalp.adapters.database.audit_repository import _existing_columns
    except Exception:
        _existing_columns = None  # type: ignore[assignment]
    try:
        if _existing_columns is not None and CONTEXT_MATRICES_COLUMN in _existing_columns(
            conn, "strategy_registry"
        ):
            return
    except Exception:
        pass
    try:
        conn.execute(
            f"ALTER TABLE strategy_registry ADD COLUMN {CONTEXT_MATRICES_COLUMN} "
            "TEXT DEFAULT '{}';"
        )
    except Exception:
        pass  # column already exists (race) or table not created yet


def _json_text_safe(value: Any) -> str:
    """Normalizes a JSON-text column read from a registry row.

    Historical rows may carry the JSON literals ``"null"`` / ``null`` (BUG-075
    writer defect: ``json.dumps(None)``). Every consumer (API, UI) must treat
    those exactly like the canonical empty object ``'{}'`` — never let a
    literal ``"null"`` reach a frontend ``JSON.parse``.
    """
    if value is None:
        return "{}"
    text = str(value).strip()
    if text == "" or text.lower() == "null":
        return "{}"
    return text


def _registry_row_safe(row: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible row normalization for ``strategy_registry`` reads.

    All JSON-text columns that may contain the historical ``"null"`` literal
    are normalized to the canonical empty object so downstream decoders
    (``StrategyRegistry._from_row`` and the UI) never crash on
    ``JSON.parse("null")`` (see BUG-075).
    """
    out = dict(row)
    for col in (
        "context_definition",
        "parent_strategy_ids",
        "backtest",
        "walkforward",
        "oos",
        "robustness",
        "score",
        "validation_lineage",
        "retirement_reason",
        "context_matrices",
    ):
        if col in out:
            out[col] = _json_text_safe(out[col])
    return out


def list_registry(
    repo: AuditRepository,
    lifecycle: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Bounded listing of registry entries, newest first."""
    reader = _ProviderRead(repo)
    if not reader.available:
        return []
    bounded = max(1, min(int(limit), MAX_READ_LIMIT))
    sql = "SELECT * FROM strategy_registry"
    args: tuple[Any, ...] = ()
    if lifecycle:
        sql += " WHERE lifecycle = ?"
        args = (lifecycle,)
    sql += " ORDER BY updated_at DESC LIMIT ?;"
    out: list[dict[str, Any]] = []
    try:
        for r in reader.rows(sql, (*args, bounded)):
            out.append(_registry_row_safe(r))
    except Exception as e:
        logger.error("[STRATEGY_REGISTRY] list failed", error=str(e))
    return out


def get_registry_entry(
    repo: AuditRepository, strategy_id: str, strategy_version: str | None = None
) -> dict[str, Any] | None:
    """Single registry entry."""
    reader = _ProviderRead(repo)
    if not reader.available:
        return None
    try:
        if strategy_version:
            row = reader.one(
                "SELECT * FROM strategy_registry WHERE strategy_id=? AND strategy_version=?;",
                (strategy_id, strategy_version),
            )
        else:
            row = reader.one(
                "SELECT * FROM strategy_registry WHERE strategy_id=? "
                "ORDER BY updated_at DESC LIMIT 1;",
                (strategy_id,),
            )
        return _registry_row_safe(row) if row else None
    except Exception as e:
        logger.error("[STRATEGY_REGISTRY] entry load failed", strategy=strategy_id, error=str(e))
        return None


def list_research_runs(
    repo: AuditRepository,
    strategy_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Append-only validation run records (reproducibility lineage)."""
    reader = _ProviderRead(repo)
    if not reader.available:
        return []
    bounded = max(1, min(int(limit), 500))
    sql = "SELECT * FROM research_runs"
    args: tuple[Any, ...] = ()
    if strategy_id:
        sql += " WHERE strategy_id = ?"
        args = (strategy_id,)
    sql += " ORDER BY executed_at DESC LIMIT ?;"
    out: list[dict[str, Any]] = []
    try:
        out = list(reader.rows(sql, (*args, bounded)))
    except Exception as e:
        logger.error("[STRATEGY_RESEARCH] runs list failed", error=str(e))
    return out


def registry_summary(repo: AuditRepository) -> dict[str, Any]:
    """Candidate count / validation status / lifecycle distribution."""
    out: dict[str, Any] = {"available": False, "total": 0, "by_lifecycle": {}}
    reader = _ProviderRead(repo)
    if not reader.available:
        return out
    try:
        out["total"] = reader.count("strategy_registry")
        by_lifecycle: dict[str, int] = {}
        for r in reader.rows(
            "SELECT lifecycle, COUNT(*) AS c FROM strategy_registry GROUP BY lifecycle;"
        ):
            by_lifecycle[str(r["lifecycle"])] = int(r["c"])
        out["by_lifecycle"] = by_lifecycle
        out["available"] = True
        # The census is a derived cache: stamp it so the UI can tell a fresh
        # read from a stale one instead of guessing. The registry's own row
        # activity is the freshest signal available.
        out["census_at"] = reader.scalar("SELECT MAX(updated_at) AS c FROM strategy_registry;")
    except Exception as e:
        logger.error("[STRATEGY_RESEARCH] summary failed", error=str(e))
    return out


def outcome_quality_summary(repo: AuditRepository) -> dict[str, Any]:
    """
    BUG-046 diagnostics: R-distribution and reconstruction-source census of
    the closed outcomes feeding the research dataset. Lets the dashboard and
    API explain WHY discovery is empty (zero-R corruption vs genuinely no
    evidence) instead of showing a bare zero.
    """
    out: dict[str, Any] = {"available": False}
    reader = _ProviderRead(repo)
    if not reader.available:
        return out
    try:
        total = reader.count("audit_experience_outcomes")
        out["total_outcomes"] = total
        out["closed_outcomes"] = reader.count("audit_experience_outcomes", "is_closed = 1")
        zero_r = reader.count(
            "audit_experience_outcomes",
            "ABS(realized_r_multiple) < 1e-12 AND ABS(realized_pnl_usd) < 1e-9",
        )
        nonzero = total - zero_r
        out["zero_r_outcomes"] = zero_r
        out["nonzero_r_outcomes"] = max(0, nonzero)
        out["positive_r_outcomes"] = reader.count(
            "audit_experience_outcomes", "realized_r_multiple > 1e-12"
        )
        out["negative_r_outcomes"] = reader.count(
            "audit_experience_outcomes", "realized_r_multiple < -1e-12"
        )
        # reconstruction source census from payloads (bounded scan)
        srcs: dict[str, int] = {}
        for r in reader.rows(
            "SELECT payload FROM audit_experience_outcomes WHERE is_closed = 1 "
            "ORDER BY outcome_timestamp DESC LIMIT 2000;"
        ):
            try:
                import json

                payload = json.loads(r.get("payload") or "{}")
                bo = payload.get("broker_outcome") or {}
                src = bo.get("reconstruction_source", "") if isinstance(bo, dict) else ""
            except Exception:
                src = ""
            if not src:
                src = "NONE_OR_MISSING"
            srcs[src] = srcs.get(src, 0) + 1
        out["reconstruction_sources"] = srcs
        out["available"] = True
    except Exception as e:
        logger.error("[STRATEGY_RESEARCH] outcome quality summary failed", error=str(e))
    return out


def research_health_summary(
    repo: AuditRepository,
    dataset_builder: Any = None,
    registry: Any = None,
) -> dict[str, Any]:
    """
    TASK-4 RESEARCH DATA HEALTH diagnostics (spec 29 / 21).

    Answers "why is the registry empty?" with structured evidence instead of a
    bare total=0:
        source trades / canonical trades / eligible samples / rejected samples
        / rejection reasons / families / family distribution / candidates /
        validation attempts / OOS failures / robustness failures / registry
        count / last successful cycle / last error.

    Uses the existing architecture: the eligibility audit from the dataset
    builder, the family distribution from discovery, the worker telemetry and
    the registry summary. Never fabricates rows.
    """
    out: dict[str, Any] = {"available": False}
    reader = _ProviderRead(repo)
    if not reader.available:
        return out
    try:
        out["available"] = True
        # 1. Source / canonical trades.
        out["source_experiences"] = reader.count("audit_experiences")
        out["canonical_outcomes"] = reader.count("audit_experience_outcomes")
        out["closed_ledger_rows"] = reader.count("audit_ledger", "status = 'CLOSED'")
        out["registry_count"] = reader.count("strategy_registry")
        out["research_runs"] = reader.count("research_runs")

        # 2. Eligibility audit + family distribution (derived, read-only).
        audit: dict[str, Any] = {}
        families: dict[str, Any] = {}
        candidates_discovered = 0
        if dataset_builder is not None:
            try:
                audit = dataset_builder.audit()
                ds = dataset_builder.build()
                from nexus_scalp.research.discovery import family_distribution

                families = family_distribution(ds.samples)
                from nexus_scalp.research.discovery import discover_candidates

                candidates_discovered = len(discover_candidates(ds.samples))
            except Exception as e:
                logger.error("[STRATEGY_RESEARCH] dataset audit failed", error=str(e))
                audit = {"error": "DATASET_AUDIT_UNAVAILABLE"}
        out["eligible_samples"] = audit.get("eligible", 0)
        out["rejected_samples"] = audit.get("rejected", 0)
        out["zero_substituted"] = audit.get("zero_substituted", 0)
        out["rejection_reasons"] = audit.get("rejection_reasons", {})
        out["families"] = families.get("families", 0)
        out["family_distribution"] = {
            "family_sizes": families.get("family_sizes", []),
            "largest": families.get("largest", 0),
            "median": families.get("median", 0),
            "smallest": families.get("smallest", 0),
            "families_above_floor": families.get("families_above_floor", 0),
            "families_below_floor": families.get("families_below_floor", 0),
        }
        out["candidates_discovered"] = candidates_discovered

        # 3. Validation attempt census (research_runs).
        rows = reader.rows(
            "SELECT result_summary FROM research_runs ORDER BY executed_at DESC LIMIT 500;"
        )
        attempts = len(rows)
        oos_fail = oos_pass = rob_fail = validated = rejected = 0
        for r in rows:
            try:
                import json as _json

                s = _json.loads(r.get("result_summary") or "{}")
            except Exception:
                s = {}
            if s.get("oos_status") == "FAIL":
                oos_fail += 1
            elif s.get("oos_status") == "PASS":
                oos_pass += 1
            if s.get("robustness_status") == "FAIL":
                rob_fail += 1
            if s.get("lifecycle") == "VALIDATED":
                validated += 1
            elif s.get("lifecycle") == "REJECTED":
                rejected += 1
        out["validation_attempts"] = attempts
        out["oos_pass"] = oos_pass
        out["oos_fail"] = oos_fail
        out["robustness_fail"] = rob_fail
        out["validated_count"] = validated
        out["rejected_count"] = rejected

        # 4. Worker telemetry.
        if registry is not None and hasattr(registry, "audit_repo"):
            with contextlib.suppress(Exception):
                row = reader.one(
                    "SELECT cycle_count, last_cycle_at, last_error FROM research_worker_state "
                    "WHERE scope='research' LIMIT 1;"
                )
                if row is not None:
                    out["last_cycle_at"] = row.get("last_cycle_at", "")
                    out["last_error_worker"] = row.get("last_error", "")
        return out
    except Exception as e:
        logger.error("[STRATEGY_RESEARCH] health summary failed", error=str(e))
        # CodeQL py/stack-trace-exposure (#66): exception detail stays
        # server-side; the wire carries a stable, generic error marker.
        return {"available": False, "error": "HEALTH_SUMMARY_UNAVAILABLE"}


def self_heal_research(repo: AuditRepository, registry) -> int:
    """
    Rebuilds derived research state from the immutable ledger when corrupted.

    Never touches historical validation truth; only derived rankings/summaries
    are rebuilt. Returns the number of registry entries repaired.
    """
    reader = _ProviderRead(repo)
    if not reader.available:
        return 0
    repaired = 0
    try:
        entries = registry.list(limit=MAX_READ_LIMIT)
        for entry in entries:
            # Repair consistency between registry lifecycle and embedded results.
            needs = False
            if (
                entry.oos is not None
                and entry.oos.status != "PASS"
                and entry.lifecycle.value not in ("REJECTED", "DEGRADED", "RETIRED")
            ):
                needs = True
            if needs:
                from nexus_scalp.research.models import CandidateLifecycle

                repair = entry.model_copy(update={"lifecycle": CandidateLifecycle.REJECTED})
                # Respect the upsert result: only count REAL repairs (the
                # regression guard can refuse SHADOW/ACTIVE→REJECTED; those
                # rows need the administrative transition_lifecycle path).
                if registry.upsert(repair):
                    repaired += 1
                else:
                    logger.warning(
                        "[STRATEGY_RESEARCH] self-heal refused by regression guard",
                        strategy_id=entry.strategy_id,
                        lifecycle=entry.lifecycle.value,
                    )
    except Exception as e:
        logger.error("[STRATEGY_RESEARCH] self-heal failed", error=str(e))
    return repaired
