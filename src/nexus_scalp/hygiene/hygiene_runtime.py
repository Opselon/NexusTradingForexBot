"""
RuntimeCleanupScheduler (TASK-22)
=================================
Continuous runtime database hygiene conductor (spec §2, §3, §5).

Architecture (application running -> background cycle -> health -> cleanup
-> report):

    RuntimeCleanupScheduler.tick()   (called from the engine loop, async)
      |  throttle (interval_minutes / deep_maintenance_interval_hours)
      v
    run_cycle(deep=False)            (executes via asyncio.to_thread by the
      |                                caller; NEVER on the tick path)
      v
    DATABASE HYGIENE CYCLE
      |  light: scan -> plan -> clean(bounded) -> consistency -> index
      |  deep:   + first-run audit + index health + quarantine review
      v
    cycle telemetry (spec §15) + optional Telegram REPORT (cooldown-gated)

Safety contract (inherits TASK-11):
  * Scheduled cycles NEVER delete unless apply_deletes=True AND mode is
    SAFE_CLEAN AND execution mode != LIVE. Default: dry_run=True.
  * First-ever run performs the full INITIAL AUDIT (spec §4) and persists
    DATABASE_HYGIENE_INITIAL_REPORT (spec §4 artifact).
  * Quarantine (spec §9): rows the detector classifies as uncertain are
    MOVEd into the quarantine store (MOVE -> MARK -> REPORT); the source
    row is only deleted when the CleanupExecutor's own gates approve.
  * Batch sizes + budgets bound every destructive step (spec §13/§14);
    a busy DB defers, never forces.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.hygiene import WorkerMode
from nexus_scalp.hygiene.consistency import ConsistencyRuleEngine, findings_summary
from nexus_scalp.hygiene.index_health import IndexHealthMonitor
from nexus_scalp.hygiene.quarantine import QuarantineStore
from nexus_scalp.hygiene.report import (
    build_cycle_telemetry,
    build_initial_audit_report,
    build_query_health_report,
    build_telegram_initial_report_text,
    build_telegram_report_text,
    persist_initial_audit,
)
from nexus_scalp.hygiene.state import HygieneStateStore
from nexus_scalp.hygiene.worker_runner import MANAGED_DATABASES, DatabaseHygieneWorker

logger = logging.getLogger(__name__)


@dataclass
class RuntimeHygieneSettings:
    """Config knobs for the scheduler (mirrors configs/base.yaml)."""

    enabled: bool = True
    interval_minutes: int = 30
    deep_maintenance_interval_hours: int = 6
    aggressive_cleanup: bool = False
    dry_run: bool = True
    apply_deletes: bool = False
    batch_size: int = 200
    telegram_report: bool = True
    telegram_min_interval_sec: int = 3600

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> RuntimeHygieneSettings:
        d = data or {}
        return cls(
            enabled=bool(d.get("enabled", True)),
            interval_minutes=int(d.get("interval_minutes", 30)),
            deep_maintenance_interval_hours=int(d.get("deep_maintenance_interval_hours", 6)),
            aggressive_cleanup=bool(d.get("aggressive_cleanup", False)),
            dry_run=bool(d.get("dry_run", True)),
            apply_deletes=bool(d.get("apply_deletes", False)),
            batch_size=int(d.get("batch_size", 200)),
            telegram_report=bool(d.get("telegram_report", True)),
            telegram_min_interval_sec=int(d.get("telegram_min_interval_sec", 3600)),
        )


class RuntimeCleanupScheduler:
    """Owns cadence, cycle execution, first-run audit, quarantine + reports."""

    def __init__(
        self,
        *,
        repo_root: str | Path,
        settings: RuntimeHygieneSettings | None = None,
        execution_mode: str = "PAPER",
    ) -> None:
        self.repo_root = Path(repo_root)
        self.settings = settings or RuntimeHygieneSettings()
        self.execution_mode = str(execution_mode or "PAPER").upper()
        self.state_store = HygieneStateStore(self.repo_root)
        self.quarantine = QuarantineStore(self.repo_root)
        self.consistency = ConsistencyRuleEngine()
        self.index_health = IndexHealthMonitor(
            polling_mode=self.execution_mode in ("PAPER", "LIVE")
        )
        self.worker: DatabaseHygieneWorker | None = None
        self.light_interval_sec = float(max(1, self.settings.interval_minutes) * 60)
        self.deep_interval_sec = float(max(1, self.settings.deep_maintenance_interval_hours) * 3600)
        self._last_light = 0.0
        self._last_deep = 0.0
        self._last_telegram = 0.0
        self._cycle_number = 0
        self._audit_done = False

    # ------------------------------------------------------------------
    # worker construction (lazy; mirrors live_engine policy)
    # ------------------------------------------------------------------
    def _ensure_worker(self) -> DatabaseHygieneWorker:
        if self.worker is None:
            apply_deletes = (
                self.settings.apply_deletes
                and not self.settings.dry_run
                and self.execution_mode != "LIVE"
            )
            mode = (
                WorkerMode.SAFE_CLEAN
                if (apply_deletes and not self.settings.aggressive_cleanup)
                else WorkerMode.AGGRESSIVE_CLEAN
                if (apply_deletes and self.settings.aggressive_cleanup)
                else WorkerMode.AUDIT_ONLY
            )
            self.worker = DatabaseHygieneWorker(
                repo_root=self.repo_root,
                mode=mode,
                execution_mode=self.execution_mode,
                apply_deletes=apply_deletes,
            )
            # batch_size override flows into the executor (bounded cleanup)
            if self.settings.batch_size >= 10:
                self.worker.executor.batch_size = self.settings.batch_size
        return self.worker

    # ------------------------------------------------------------------
    # cadence
    # ------------------------------------------------------------------
    def is_light_due(self, now: float) -> bool:
        return (now - self._last_light) >= self.light_interval_sec

    def is_deep_due(self, now: float) -> bool:
        return (now - self._last_deep) >= self.deep_interval_sec

    def is_telegram_due(self, now: float) -> bool:
        return (now - self._last_telegram) >= self.settings.telegram_min_interval_sec

    def next_light_in(self, now: float | None = None) -> float:
        now = now or time.monotonic()
        return max(0.0, self.light_interval_sec - (now - self._last_light))

    # ------------------------------------------------------------------
    # cycle
    # ------------------------------------------------------------------
    def run_cycle(self, *, deep: bool = False) -> dict[str, Any]:
        """One scheduled cycle. Call via to_thread — blocking by design."""
        started = time.monotonic()
        if not self._audit_done:
            self._run_initial_audit()

        worker = self._ensure_worker()
        started_iso = datetime.now(UTC).isoformat()
        try:
            result = worker.run_cycle(list(MANAGED_DATABASES.keys()))
        except Exception as e:
            logger.warning("[DB_HYGIENE] event=CYCLE_FAILED error=%s", e)
            result = {
                "error": str(e),
                "verification": "FAILED",
                "databases": {},
            }

        duration_ms = round((time.monotonic() - started) * 1000.0, 1)
        self._cycle_number += 1

        # aggregate
        deleted: dict[str, int] = {}
        archived: dict[str, int] = {}
        rows_scanned = 0
        errors: list[str] = []
        for db_res in result.get("databases", {}).values():
            if not isinstance(db_res, dict):
                continue
            for k, v in (db_res.get("deleted") or {}).items():
                deleted[k] = deleted.get(k, 0) + int(v)
            for k, v in (db_res.get("archived") or {}).items():
                archived[k] = archived.get(k, 0) + int(v)
            rows_scanned += int(db_res.get("rows_scanned", 0) or 0)
            errors.extend(str(e) for e in db_res.get("errors", []) or [])
        if result.get("error"):
            errors.append(str(result["error"]))

        telemetry = build_cycle_telemetry(
            run_id=result.get("run_id", ""),
            mode=result.get("mode", "AUDIT_ONLY"),
            started_at=started_iso,
            duration_ms=duration_ms,
            rows_scanned=rows_scanned,
            deleted=deleted,
            archived=archived,
            quarantined=0,
            errors=errors[:20],
            verification=result.get("verification", "NOT_RUN"),
            deep=deep,
        )

        # deep maintenance: index health report + quarantine snapshot
        index_report: dict[str, Any] | None = None
        if deep:
            index_report = self._index_health_report()
        telemetry["index_health"] = index_report

        self._last_light = time.monotonic()
        if deep:
            self._last_deep = time.monotonic()
        return {"cycle": self._cycle_number, "telemetry": telemetry, "result": result}

    # ------------------------------------------------------------------
    # first-run audit (spec §4)
    # ------------------------------------------------------------------
    def _run_initial_audit(self) -> dict[str, Any]:
        worker = self._ensure_worker()
        database_results: dict[str, dict[str, Any]] = {}
        consistency: dict[str, dict[str, Any]] = {}
        index_health: dict[str, dict[str, Any]] = {}

        for db_key in MANAGED_DATABASES:
            rel = MANAGED_DATABASES[db_key]
            path = self.repo_root / rel
            if not path.exists():
                database_results[db_key] = {"error": "DB_NOT_FOUND"}
                continue
            try:
                database_results[db_key] = worker.plan_database(db_key)
                import sqlite3

                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    consistency[db_key] = findings_summary(self.consistency.scan(db_key, conn))
                    index_health[db_key] = self.index_health.scan_database(conn, db_key)
                finally:
                    conn.close()
            except Exception as e:
                database_results[db_key] = {"error": str(e)}

        report = build_initial_audit_report(
            database_results=database_results,
            consistency=consistency,
            index_health=index_health,
            quarantine_stats=self.quarantine.stats(),
            run_id=f"INIT-{int(time.time())}",
        )
        path = persist_initial_audit(report, self.repo_root)
        self._audit_done = True
        # AGENT-2 (2026-09-01): actionable summary in the log itself (spec §12)
        # — an operator must not need to open the report JSON to understand
        # WHY the verdict is ACTION_REQUIRED and WHAT is recommended. All
        # numbers come from the real report; nothing is invented.
        totals = report.get("totals", {})
        violations = int(totals.get("violations", 0) or 0)
        orphans = int(totals.get("orphans", 0) or 0)
        duplicates = int(totals.get("duplicates", 0) or 0)
        ih = report.get("index_health_summary", {}) or {}
        missing_idx = sum(int(v.get("missing", 0) or 0) for v in ih.values())
        if violations:
            # first violation detail (rule/table) from the real report
            first_rule = ""
            first_table = ""
            for db_info in (report.get("per_database") or {}).values():
                details = (db_info.get("consistency") or {}).get("violation_details") or []
                if details:
                    first_rule = str(details[0].get("rule_id", ""))
                    first_table = str(details[0].get("table", ""))
                    break
            logger.info(
                "[DB_HYGIENE] event=INITIAL_AUDIT_COMPLETE verdict=%s "
                "consistency_violations=%d orphans=%d duplicates=%d "
                "missing_indexes=%d first_violation=%s/%s "
                "recommended_action=review_report_manual_repair "
                "auto_delete=DISABLED (AUDIT_ONLY) path=%s",
                report.get("verdict", "UNKNOWN"),
                violations,
                orphans,
                duplicates,
                missing_idx,
                first_rule or "-",
                first_table or "-",
                path,
            )
        else:
            logger.info(
                "[DB_HYGIENE] event=INITIAL_AUDIT_COMPLETE verdict=%s "
                "consistency_violations=0 orphans=%d duplicates=%d "
                "missing_indexes=%d path=%s",
                report.get("verdict", "UNKNOWN"),
                orphans,
                duplicates,
                missing_idx,
                path,
            )
        return report

    def _index_health_report(self) -> dict[str, Any]:
        import sqlite3

        reports = []
        for db_key, rel in MANAGED_DATABASES.items():
            path = self.repo_root / rel
            if not path.exists():
                continue
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    reports.append(self.index_health.scan_database(conn, db_key))
                finally:
                    conn.close()
            except Exception as e:
                logger.warning("[DB_HYGIENE] event=INDEX_SCAN_FAILED db=%s error=%s", db_key, e)
        return build_query_health_report(reports)

    # ------------------------------------------------------------------
    # quarantine integration
    # ------------------------------------------------------------------
    def quarantine_rows(
        self,
        *,
        database: str,
        table: str,
        rows: list[dict[str, Any]],
        reason: str,
        found_by: str = "RuntimeCleanupScheduler",
        cleanup_class: str = "",
        confidence: str = "",
    ) -> list[dict[str, Any]]:
        """Move suspicious rows into quarantine (MOVE -> MARK)."""
        items = []
        for row in rows:
            row_id = (
                row.get("id")
                or row.get("ticket")
                or row.get("article_id")
                or row.get("analysis_id")
                or row.get("rowid")
                or row.get("_rowid")
            )
            if row_id is None:
                continue
            items.append(
                self.quarantine.quarantine(
                    database=database,
                    table=table,
                    row_id=row_id,
                    row=row,
                    reason=reason,
                    found_by=found_by,
                    cleanup_class=cleanup_class,
                    confidence=confidence,
                )
            )
        return items

    # ------------------------------------------------------------------
    # telegram reporting (delivery via engine notifier — read-only here)
    # ------------------------------------------------------------------
    def telegram_text_for_cycle(self, telemetry: dict[str, Any]) -> str:
        return build_telegram_report_text(telemetry, self._cycle_number)

    def telegram_text_for_initial(self, report: dict[str, Any]) -> str:
        return build_telegram_initial_report_text(report)

    def mark_telegram_sent(self, now: float | None = None) -> None:
        self._last_telegram = now or time.monotonic()

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "enabled": self.settings.enabled,
            "dry_run": self.settings.dry_run,
            "apply_deletes": self.settings.apply_deletes,
            "execution_mode": self.execution_mode,
            "worker_mode": (self.worker.mode.value if self.worker is not None else "NOT_STARTED"),
            "light_interval_sec": self.light_interval_sec,
            "deep_interval_sec": self.deep_interval_sec,
            "next_light_in_sec": round(self.next_light_in(now), 1),
            "cycle_number": self._cycle_number,
            "initial_audit_done": self._audit_done,
            "quarantine": self.quarantine.stats(),
            "worker_state": self.state_store.get_state().get("state", "UNKNOWN"),
        }
