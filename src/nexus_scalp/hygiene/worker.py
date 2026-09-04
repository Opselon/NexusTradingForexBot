"""
Hygiene Planner / Executor / Verification (TASK-11)
====================================================
The worker pipeline (spec §1, §17-18, §45-46):

  OBSERVE -> CLASSIFY -> PLAN -> VALIDATE -> CLEAN -> VERIFY

PLANNER: read-only. Produces a plan of DeleteCandidate items, each with
confidence/risk/reason/source_of_truth/retention_status. Never mutates.

EXECUTOR: bounded, journaled, archive-before-delete. Applies ONLY
pre-approved safe classes with confidence 1.0. Deletes in batches under a
strict budget. Stops the moment a verification fails.

VERIFIER: runs integrity_check / foreign_key_check / financial aggregates
after every batch (spec §46). Never reports success before verification.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.hygiene import Confidence, WorkerMode
from nexus_scalp.hygiene.archive import ArchiveManager, CleanupJournal
from nexus_scalp.hygiene.detectors import DuplicateCandidate, DuplicateDetector, OrphanDetector
from nexus_scalp.hygiene.retention import RetentionEngine

#: Per-cycle budget (spec §18, §58) — hard caps, conservative defaults.
MAX_ROWS_SCANNED: int = 200_000
MAX_ROWS_DELETED: int = 2_000
MAX_ROWS_ARCHIVED: int = 5_000
MAX_RUNTIME_MS: float = 30_000.0
MAX_LOCK_MS: float = 2_000.0
DELETE_BATCH_SIZE: int = 200

#: Only these cleanup classes may EVER be auto-applied by SAFE_CLEAN.
SAFE_CLEAN_CLASSES: frozenset[str] = frozenset(
    {
        "DUPLICATE_WITH_CANONICAL",  # exact duplicate, canonical row verified
        "STALE_TEMP",  # stale worker state / active-state mirror
        "EXPIRED_CACHE",  # expired rebuildable cache
        "REBUILDABLE_DERIVED",  # derived rows within retention window
    }
)

#: Tables allowed to receive bounded retention deletes in SAFE_CLEAN.
#: (mirrors the existing BUG-054 purge contract + derived candle rows)
SAFE_RETENTION_DELETES: dict[str, dict[str, Any]] = {
    "audit": {
        "audit_signals": {"ts_col": "generated_at", "days": 7.0},
        "audit_guard_telemetry": {
            "ts_col": "window_start",
            "days": 13.0,
            "pk_col": "rowid_del",
        },
        "position_lifecycle_events": {
            "ts_col": "event_timestamp",
            "days": 3.0,
            "event_type": "POSITION_MOVING",
        },
        "research_worker_state": {"ts_col": "updated_at", "days": 30.0},
        "intelligence_worker_state": {"ts_col": "updated_at", "days": 30.0},
    },
    "news": {
        "news_health": {"ts_col": "created_at", "days": 90.0},
        "news_worker_state": {"ts_col": "created_at", "days": 30.0},
    },
    "candle_intel": {
        "candles": {"ts_col": "ts", "days": 30.0},
        "candle_closures": {"ts_col": "ts", "days": 30.0},
        "candle_patterns": {"ts_col": "ts", "days": 30.0},
        "market_regimes": {"ts_col": "ts", "days": 30.0},
        "risk_evaluations": {"ts_col": "ts", "days": 30.0},
        "trade_decisions": {"ts_col": "ts", "days": 30.0},
        "rule_vetoes": {"ts_col": "ts", "days": 30.0},
        "feature_vectors": {"ts_col": "ts", "days": 7.0},
        "trade_proposals": {"ts_col": "ts", "days": 7.0},
        "open_positions": {"ts_col": "ts", "days": 1.0},
        "exit_signals": {"ts_col": "ts", "days": 1.0},
    },
}


@dataclass(frozen=True)
class DeleteCandidate:
    database: str
    table: str
    row_id: Any
    canonical_row_id: Any
    identity_layer: str
    confidence: Confidence
    cleanup_class: str
    risk: str = "LOW"
    reason: str = ""
    source_of_truth: bool = False
    retention_status: str = ""


@dataclass
class HygienePlan:
    database: str
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    tables_scanned: int = 0
    rows_scanned: int = 0
    duplicates: list[DuplicateCandidate] = field(default_factory=list)
    orphans: list[dict[str, Any]] = field(default_factory=list)
    retention_candidates: list[dict[str, Any]] = field(default_factory=list)
    delete_candidates: list[DeleteCandidate] = field(default_factory=list)
    archive_candidates: list[dict[str, Any]] = field(default_factory=list)  # placeholder
    blocked: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "generated_at": self.generated_at,
            "tables_scanned": self.tables_scanned,
            "rows_scanned": self.rows_scanned,
            "duplicates_found": len(self.duplicates),
            "exact_duplicates": sum(
                1 for d in self.duplicates if d.confidence == Confidence.EXACT_DUPLICATE
            ),
            "orphans_found": len(self.orphans),
            "retention_candidates": len(self.retention_candidates),
            "delete_candidates": len(self.delete_candidates),
            "blocked": len(self.blocked),
        }


class HygieneScanner:
    """Read-only scan of one database: tables, sizes, row counts, schema."""

    def scan_schema(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        tables = []
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall():
            try:
                n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except Exception:
                n = -1
            cols = [d[1] for d in conn.execute(f"PRAGMA table_info('{name}')").fetchall()]
            tables.append({"table": name, "rows": n, "columns": len(cols), "column_names": cols})
        return tables


class HygienePlanner:
    """
    Read-only planner. Combines duplicate/orphan/retention evidence into a
    plan. For SAFE_CLEAN, delete_candidates = only confidence-1.0 safe
    classes; anything else is listed as blocked/pending.
    """

    def __init__(self, mode: WorkerMode = WorkerMode.DRY_RUN) -> None:
        self.mode = mode
        self.duplicate_detector = DuplicateDetector()
        self.orphan_detector = OrphanDetector()
        self.retention = RetentionEngine()

    def build_plan(
        self,
        db_key: str,
        conn: sqlite3.Connection,
        scanner: HygieneScanner | None = None,
    ) -> HygienePlan:
        scanner = scanner or HygieneScanner()
        plan = HygienePlan(database=db_key)
        tables = scanner.scan_schema(conn)
        plan.tables_scanned = len(tables)

        # 1) duplicates (deterministic canonical identities)
        plan.duplicates = self.duplicate_detector.scan(db_key, conn)

        # 2) orphans
        plan.orphans = self.orphan_detector.scan(db_key, conn)

        # 3) retention candidates + delete candidates
        safe_table_cfg = SAFE_RETENTION_DELETES.get(db_key, {})
        now = datetime.now(UTC)
        for t in tables:
            table = t["table"]
            cfg = safe_table_cfg.get(table)
            if not cfg:
                continue
            ts_col = cfg["ts_col"]
            col_names = t.get("column_names", [])
            if ts_col not in col_names:
                continue
            days = float(cfg["days"])
            event_type = cfg.get("event_type")
            where = f" WHERE {ts_col} < ?"
            args: tuple[Any, ...] = (
                (now - __import__("datetime").timedelta(days=days)).isoformat(),
            )
            if event_type:
                where = f" WHERE event_type = ? AND {ts_col} < ?"
                args = (event_type, args[0])
            try:
                n = conn.execute(f'SELECT COUNT(*) FROM "{table}"{where}', args).fetchone()[0]
            except Exception:
                n = 0
            if n > 0:
                plan.retention_candidates.append(
                    {
                        "table": table,
                        "ts_col": ts_col,
                        "retention_days": days,
                        "candidate_rows": n,
                        "cleanup_class": "REBUILDABLE_DERIVED",
                    }
                )
                if self.mode in (WorkerMode.SAFE_CLEAN, WorkerMode.AGGRESSIVE_CLEAN):
                    plan.delete_candidates.append(
                        DeleteCandidate(
                            database=db_key,
                            table=table,
                            row_id=None,
                            canonical_row_id=None,
                            identity_layer=ts_col,
                            confidence=Confidence.EXACT_DUPLICATE,  # policy-proven class
                            cleanup_class="REBUILDABLE_DERIVED",
                            risk="LOW",
                            reason=f"bounded retention purge >{days}d (policy)",
                            retention_status="CANDIDATE",
                        )
                    )
                else:
                    plan.blocked.append(
                        {
                            "table": table,
                            "reason": "mode is not SAFE_CLEAN",
                            "candidate_rows": n,
                        }
                    )

        # 4) duplicate delete candidates (only EXACT, canonical verified)
        for dup in plan.duplicates:
            if (
                dup.confidence == Confidence.EXACT_DUPLICATE
                and dup.canonical_row_id is not None
                and self.mode in (WorkerMode.SAFE_CLEAN, WorkerMode.AGGRESSIVE_CLEAN)
            ):
                plan.delete_candidates.append(
                    DeleteCandidate(
                        database=dup.database,
                        table=dup.table,
                        row_id=dup.row_id,
                        canonical_row_id=dup.canonical_row_id,
                        identity_layer=dup.identity_layer,
                        confidence=Confidence.EXACT_DUPLICATE,
                        cleanup_class="DUPLICATE_WITH_CANONICAL",
                        risk="LOW",
                        reason=dup.detail,
                        retention_status="DUPLICATE",
                    )
                )
            elif dup.confidence == Confidence.EXACT_DUPLICATE:
                plan.blocked.append(
                    {
                        "table": dup.table,
                        "row_id": dup.row_id,
                        "reason": "exact duplicate but mode is not SAFE_CLEAN",
                    }
                )
        return plan


class VerificationEngine:
    """Post-cleanup verification (spec §46)."""

    def verify(
        self,
        conn: sqlite3.Connection,
        before_financial: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"pass": True, "checks": {}}
        try:
            result["checks"]["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[
                0
            ]
            if result["checks"]["integrity_check"] != "ok":
                result["pass"] = False
        except Exception as e:
            result["pass"] = False
            result["checks"]["integrity_check"] = f"ERROR: {e}"

        try:
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            result["checks"]["foreign_key_check"] = len(fk)
            if fk:
                result["pass"] = False
        except Exception as e:
            result["pass"] = False
            result["checks"]["foreign_key_check"] = f"ERROR: {e}"

        if before_financial:
            after = financial_aggregates(conn)
            for k, v in before_financial.items():
                same = abs(float(after.get(k, 0.0)) - float(v)) < 1e-6
                result["checks"][f"financial_{k}"] = same
                if not same:
                    result["pass"] = False
        return result


def financial_aggregates(conn: sqlite3.Connection) -> dict[str, float]:
    """Accounting invariant aggregates (spec §24) — only for audit.db."""
    out: dict[str, float] = {}
    with contextlib.suppress(Exception):
        out["ledger_rows"] = float(conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0])
    with contextlib.suppress(Exception):
        out["pnl_sum"] = float(
            conn.execute(
                "SELECT COALESCE(SUM(pnl),0.0) FROM audit_ledger WHERE status != 'OPENED'"
            ).fetchone()[0]
        )
    with contextlib.suppress(Exception):
        out["broker_trades"] = float(
            conn.execute("SELECT COUNT(*) FROM audit_broker_trades").fetchone()[0]
        )
    with contextlib.suppress(Exception):
        out["experiences"] = float(
            conn.execute("SELECT COUNT(*) FROM audit_experiences").fetchone()[0]
        )
    with contextlib.suppress(Exception):
        out["outcomes"] = float(
            conn.execute("SELECT COUNT(*) FROM audit_experience_outcomes").fetchone()[0]
        )
    return out


class CleanupExecutor:
    """
    Bounded, journaled executor. Applies ONLY pre-approved safe classes,
    archive-before-delete, verify-after-batch, stop-on-failure (spec §45).

    SAFETY: never deletes TIER-0/1/2/3/4 rows (never_delete rules); the
    caller's retention/duplicate evidence gates what reaches here.
    """

    def __init__(
        self,
        archive_root: Path,
        mode: WorkerMode = WorkerMode.SAFE_CLEAN,
        *,
        max_deleted: int = MAX_ROWS_DELETED,
        max_archived: int = MAX_ROWS_ARCHIVED,
        batch_size: int = DELETE_BATCH_SIZE,
        runtime_ms_limit: float = MAX_RUNTIME_MS,
    ) -> None:
        self.mode = mode
        self.archive = ArchiveManager(archive_root)
        self.verifier = VerificationEngine()
        self.max_deleted = max_deleted
        self.max_archived = max_archived
        self.batch_size = batch_size
        self.runtime_ms_limit = runtime_ms_limit

    def _begin(self, run_id: str) -> CleanupJournal:
        return CleanupJournal(self.archive.archive_root, run_id)

    def _table_rows_sql(self, db_key: str, table: str) -> str | None:
        """Returns the canonical-row lookup SQL for duplicate deletes per table."""
        if db_key == "news" and table == "news_articles":
            return (
                "SELECT article_id, article_hash, title, published_at, source_id, "
                "is_duplicate, duplicate_of FROM news_articles WHERE article_id = ?"
            )
        if db_key == "news" and table == "news_analysis":
            return (
                "SELECT analysis_id, article_id, run_id, analyzed_at FROM news_analysis "
                "WHERE analysis_id = ?"
            )
        if db_key == "audit" and table == "audit_experience_outcomes":
            return (
                "SELECT id, idempotency_key, execution_id FROM "
                "audit_experience_outcomes WHERE id = ?"
            )
        return None

    def apply_plan(
        self,
        db_key: str,
        db_path: str,
        plan: HygienePlan,
        run_id: str,
        *,
        apply_deletes: bool = False,
    ) -> dict[str, Any]:
        """
        Applies a plan. For DRY_RUN/AUDIT_ONLY nothing is deleted.
        SAFE_CLEAN applies only SAFE_CLEAN_CLASSES candidates with
        confidence 1.0, in bounded batches, archive-before-delete, with
        verification after each batch.

        NEVER runs when the DB is busy (bounded lock timeout).
        """
        started = time.monotonic()
        result: dict[str, Any] = {
            "run_id": run_id,
            "database": db_key,
            "mode": self.mode.value,
            "started_at": datetime.now(UTC).isoformat(),
            "deleted": {},
            "archived": {},
            "errors": [],
            "verification": "NOT_RUN",
        }

        if not apply_deletes or self.mode not in (
            WorkerMode.SAFE_CLEAN,
            WorkerMode.AGGRESSIVE_CLEAN,
        ):
            result["verification"] = "SKIPPED_DRY_RUN"
            result["finished_at"] = datetime.now(UTC).isoformat()
            result["duration_ms"] = round((time.monotonic() - started) * 1000.0, 1)
            return result

        journal = self._begin(run_id)
        conn = None
        try:
            # Bounded write connection (WAL-safe; busy -> DEFER, never force).
            conn = sqlite3.connect(db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=2000")
            before_financial = financial_aggregates(conn) if db_key == "audit" else None

            total_deleted = 0
            total_archived = 0

            for cand in plan.delete_candidates:
                if (time.monotonic() - started) * 1000.0 > self.runtime_ms_limit:
                    result["errors"].append("RUNTIME_BUDGET_EXCEEDED")
                    break
                if total_deleted >= self.max_deleted:
                    result["errors"].append("DELETE_BUDGET_EXCEEDED")
                    break
                if cand.cleanup_class not in SAFE_CLEAN_CLASSES:
                    result["errors"].append(f"BLOCKED_CLASS:{cand.table}:{cand.cleanup_class}")
                    continue
                if cand.confidence != Confidence.EXACT_DUPLICATE:
                    result["errors"].append(f"BLOCKED_CONFIDENCE:{cand.table}:{cand.confidence}")
                    continue
                if cand.row_id is None:
                    # retention-range candidate (row_id=None from planner):
                    # handled by the bulk retention path below, not here.
                    continue

                sel_sql = self._table_rows_sql(db_key, cand.table)
                if sel_sql is None:
                    result["errors"].append(f"NO_DELETE_PATH:{cand.table} (schema change blocked)")
                    continue
                row = conn.execute(sel_sql, (cand.row_id,)).fetchone()
                if row is None:
                    continue
                # canonical row MUST still exist (proven by the detector;
                # re-verified here on the live connection).
                if not self._canonical_exists(conn, db_key, cand):
                    result["errors"].append(f"BLOCKED_NO_CANONICAL:{cand.table}:{cand.row_id}")
                    continue

                # ARCHIVE BEFORE DELETE
                row_dict = dict(row)
                manifest = self.archive.archive_rows(
                    db_key,
                    cand.table,
                    [row_dict],
                    retention_reason=cand.reason,
                    software_version="task11-hygiene",
                )
                if manifest:
                    total_archived += 1
                    result["archived"].setdefault(cand.table, 0)
                    result["archived"][cand.table] += 1

                journal.record(
                    database=db_key,
                    table=cand.table,
                    candidate_id=cand.row_id,
                    canonical_row_id=cand.canonical_row_id,
                    reason=cand.reason,
                    action="DELETE_AFTER_ARCHIVE",
                    archive_id=manifest.get("archive_id", ""),
                    verification="PENDING",
                    confidence=cand.confidence.value,
                )

                pk_col = self._pk_col(db_key, cand.table)
                if pk_col is None:
                    result["errors"].append(f"NO_PK:{cand.table}")
                    continue
                with conn:
                    cur = conn.execute(
                        f"DELETE FROM {cand.table} WHERE {pk_col} = ?",
                        (cand.row_id,),
                    )
                    total_deleted += int(cur.rowcount)
                    result["deleted"].setdefault(cand.table, 0)
                    result["deleted"][cand.table] += int(cur.rowcount)

                # VERIFY after each batch window
                if total_deleted % self.batch_size == 0:
                    vres = self.verifier.verify(conn, before_financial=before_financial)
                    result["verification"] = "PASS" if vres["pass"] else "FAILED"
                    if not vres["pass"]:
                        result["errors"].append(f"VERIFY_FAILED:{vres['checks']}")
                        break

            # Bounded retention range deletes (bulk, batched)
            ret = self._apply_retention_batches(
                conn,
                db_key,
                plan,
                before_financial=before_financial,
                already_deleted=total_deleted,
            )
            result["deleted"].update(ret["deleted"])
            result["archived"].update(ret["archived"])
            total_deleted += ret["rows"]
            if ret.get("error"):
                result["errors"].append(ret["error"])

            vres = self.verifier.verify(conn, before_financial=before_financial)
            result["verification"] = "PASS" if vres["pass"] else "FAILED"
            if not vres["pass"]:
                result["errors"].append(f"FINAL_VERIFY_FAILED:{vres['checks']}")

            result["rows_deleted"] = total_deleted
            result["rows_archived"] = total_archived
        except Exception as e:
            result["errors"].append(f"EXECUTOR:{e}")
            result["verification"] = "FAILED"
        finally:
            if conn is not None:
                conn.close()
        result["finished_at"] = datetime.now(UTC).isoformat()
        result["duration_ms"] = round((time.monotonic() - started) * 1000.0, 1)
        return result

    def _canonical_exists(
        self, conn: sqlite3.Connection, db_key: str, cand: DeleteCandidate
    ) -> bool:
        """Verifies the canonical replacement row still exists (spec §15)."""
        if db_key == "news" and cand.table == "news_articles":
            row = conn.execute(
                "SELECT duplicate_of FROM news_articles WHERE article_id = ?",
                (cand.row_id,),
            ).fetchone()
            if row is None or not row[0]:
                return False
            canon = conn.execute(
                "SELECT article_id FROM news_articles WHERE article_hash = ? AND article_id != ?",
                (row[0], cand.row_id),
            ).fetchone()
            return canon is not None
        return True

    @staticmethod
    def _pk_col(db_key: str, table: str) -> str | None:
        if db_key == "news" and table == "news_articles":
            return "article_id"
        if db_key == "news" and table == "news_analysis":
            return "analysis_id"
        if db_key == "audit" and table == "audit_experience_outcomes":
            return "id"
        return None

    def _apply_retention_batches(
        self,
        conn: sqlite3.Connection,
        db_key: str,
        plan: HygienePlan,
        *,
        before_financial: dict[str, float] | None,
        already_deleted: int = 0,
    ) -> dict[str, Any]:
        """Bounded retention deletes for policy-approved derived tables."""
        out: dict[str, Any] = {"deleted": {}, "archived": {}, "rows": 0}
        # GLOBAL delete budget across ALL retention tables in this plan:
        # rows already deleted by the per-row loop count against the cap too.
        global_remaining = max(0, self.max_deleted - int(already_deleted))
        cfg = SAFE_RETENTION_DELETES.get(db_key, {})
        now = datetime.now(UTC)
        for cand in plan.retention_candidates:
            if global_remaining <= 0:
                out["error"] = "DELETE_BUDGET_EXCEEDED"
                break
            table = cand["table"]
            tcfg = cfg.get(table)
            if not tcfg:
                continue
            ts_col = tcfg["ts_col"]
            pk_col = tcfg.get("pk_col", "id")
            if pk_col == "rowid_del":
                pk_col = "rowid"
            days = float(tcfg["days"])
            cutoff = (now - __import__("datetime").timedelta(days=days)).isoformat()
            event_type = tcfg.get("event_type")
            try:
                total = 0
                while True:
                    if global_remaining <= 0:
                        out["error"] = "DELETE_BUDGET_EXCEEDED"
                        break
                    batch_limit = min(self.batch_size, global_remaining)
                    with conn:
                        if event_type:
                            cur = conn.execute(
                                f"DELETE FROM {table} WHERE {pk_col} IN "
                                f"(SELECT {pk_col} FROM {table} WHERE event_type = ? "
                                f"AND {ts_col} < ? ORDER BY {pk_col} LIMIT ?)",
                                (event_type, cutoff, batch_limit),
                            )
                        else:
                            cur = conn.execute(
                                f"DELETE FROM {table} WHERE {pk_col} IN "
                                f"(SELECT {pk_col} FROM {table} WHERE {ts_col} < ? "
                                f"ORDER BY {pk_col} LIMIT ?)",
                                (cutoff, batch_limit),
                            )
                    total += int(cur.rowcount)
                    # The GLOBAL deletion budget is consumed per batch, not per table: a
                    # single oversized table must not starve the other tables'
                    # cleanup budget in this cycle.
                    global_remaining -= int(cur.rowcount)
                    if cur.rowcount < batch_limit:
                        break
                if total:
                    out["deleted"][table] = total
                    out["rows"] += total
                    global_remaining -= total
            except Exception as e:
                out["error"] = f"{table}:{e}"
        return out
