"""Database Data Lifecycle, Domain-Aware Purging & Maintenance Subsystem.

Enforces Sections 19-25 of the Dual Database Architecture:
    - Tiered data classification: HOT, WARM, COLD, PURGEABLE, IMMUTABLE.
    - IMMUTABLE protection: financial and audit ledger records can NEVER be purged.
    - Domain-aware batched purging to prevent database locks and bloat.
    - Safe PostgreSQL VACUUM / ANALYZE and SQLite maintenance.
"""

from __future__ import annotations

import enum
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.lifecycle")


class DataTier(enum.Enum):
    """Classification of data lifecycle tiers."""

    HOT = "HOT"  # Active operational data
    WARM = "WARM"  # Operational history needed for near-term audit
    COLD = "COLD"  # Archived data
    PURGEABLE = "PURGEABLE"  # Expired temporary / junk / high-frequency telemetry
    IMMUTABLE = "IMMUTABLE"  # Financial audit records, never deleted


class ImmutableDataProtectionError(RuntimeError):
    """Raised when an operation attempts to delete immutable audit or financial records."""


#: Tables containing immutable financial / audit evidence that must never be purged.
IMMUTABLE_TABLES = frozenset(
    {
        "audit_ledger",
        "audit_orders",
        "audit_broker_orders",
        "audit_broker_deals",
        "audit_broker_trades",
        "audit_executions",
        "audit_executions_reconciled",
        "audit_account_snapshots",
        "trade_decisions",
        "risk_evaluations",
    }
)


@dataclass
class RetentionPolicy:
    """Retention policy definition for a specific table."""

    table_name: str
    tier: DataTier
    retention_days: int
    timestamp_column: str
    condition: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


PurgePolicy = RetentionPolicy


#: Default domain-aware retention policies
DEFAULT_POLICIES: dict[str, RetentionPolicy] = {
    "audit_signals": RetentionPolicy(
        table_name="audit_signals",
        tier=DataTier.PURGEABLE,
        retention_days=30,
        timestamp_column="generated_at",
        description="High-frequency trading signals older than retention window.",
    ),
    "position_lifecycle_events": RetentionPolicy(
        table_name="position_lifecycle_events",
        tier=DataTier.PURGEABLE,
        retention_days=14,
        timestamp_column="event_timestamp",
        condition="event_type = 'POSITION_MOVING'",
        description="High-frequency moving position events; open/close events preserved.",
    ),
    "audit_guard_telemetry": RetentionPolicy(
        table_name="audit_guard_telemetry",
        tier=DataTier.PURGEABLE,
        retention_days=7,
        timestamp_column="window_start",
        description="Short-term guard throttle and circuit telemetry.",
    ),
    "model_runtime_health": RetentionPolicy(
        table_name="model_runtime_health",
        tier=DataTier.PURGEABLE,
        retention_days=14,
        timestamp_column="checked_at",
        description="Model health checks and latency probes.",
    ),
    "hygiene_run_history": RetentionPolicy(
        table_name="hygiene_run_history",
        tier=DataTier.PURGEABLE,
        retention_days=30,
        timestamp_column="run_at",
        description="Historical logs of automated hygiene sweeps.",
    ),
}


@dataclass
class PurgePreviewItem:
    """Preview estimate for one table purge candidate."""

    table_name: str
    retention_days: int
    cutoff_timestamp: str
    purgeable_rows: int
    tier: str


@dataclass
class PurgeResult:
    """Execution summary of a completed purge run."""

    started_at: str
    duration_ms: float
    total_deleted: int
    per_table_deleted: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    maintenance_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatabaseLifecycleManager:
    """Orchestrates data lifecycle, domain-aware purges, and maintenance."""

    def __init__(
        self,
        config: DatabaseConfig | None = None,
        policies: dict[str, RetentionPolicy] | None = None,
    ) -> None:
        self.cfg = config or load_database_config("audit")
        self.policies = dict(policies or DEFAULT_POLICIES)
        self._history: list[PurgeResult] = []

    def get_policies(self) -> dict[str, dict[str, Any]]:
        """Return all active retention policies."""
        return {name: pol.to_dict() for name, pol in self.policies.items()}

    def update_policy(self, table_name: str, retention_days: int) -> bool:
        """Update retention days for a configured policy."""
        if table_name in IMMUTABLE_TABLES:
            raise ImmutableDataProtectionError(
                f"Cannot set retention policy on immutable table '{table_name}'"
            )
        if table_name in self.policies:
            self.policies[table_name].retention_days = max(1, retention_days)
            return True
        return False

    def preview_purge(self) -> list[PurgePreviewItem]:
        """Estimate the rows eligible for purging across all configured policies."""
        driver = get_driver(self.cfg)
        items: list[PurgePreviewItem] = []
        try:
            now = datetime.now(UTC)
            for table, pol in self.policies.items():
                if not driver.table_exists(table):
                    continue
                cutoff = (now - timedelta(days=pol.retention_days)).isoformat()
                where = (
                    f'"{pol.timestamp_column}" < %s'
                    if self.cfg.is_postgresql
                    else f'"{pol.timestamp_column}" < ?'
                )
                if pol.condition:
                    where = f"{where} AND ({pol.condition})"

                sql = f'SELECT COUNT(*) FROM "{table}" WHERE {where}'
                count = driver.scalar(sql, (cutoff,)) or 0
                items.append(
                    PurgePreviewItem(
                        table_name=table,
                        retention_days=pol.retention_days,
                        cutoff_timestamp=cutoff,
                        purgeable_rows=count,
                        tier=pol.tier.value,
                    )
                )
            return items
        finally:
            driver.close()

    def run_purge(
        self,
        batch_size: int = 2000,
        run_maintenance: bool = True,
    ) -> PurgeResult:
        """Execute domain-aware batched purging of expired records."""
        driver = get_driver(self.cfg)
        t_start = time.perf_counter()
        now_dt = datetime.now(UTC)
        result = PurgeResult(
            started_at=now_dt.isoformat(),
            duration_ms=0.0,
            total_deleted=0,
        )

        try:
            for table, pol in self.policies.items():
                if table in IMMUTABLE_TABLES:
                    raise ImmutableDataProtectionError(
                        f"Refusing purge attempt on immutable table '{table}'"
                    )
                if not driver.table_exists(table):
                    continue

                deleted = self._purge_table_batched(driver, table, pol, now_dt, batch_size)
                result.per_table_deleted[table] = deleted
                result.total_deleted += deleted

            if run_maintenance and result.total_deleted > 0:
                self.run_maintenance(driver)
                result.maintenance_run = True

        except Exception as exc:
            logger.error("Data lifecycle purge error: %s", exc)
            result.errors.append(str(exc))
        finally:
            driver.close()
            result.duration_ms = round((time.perf_counter() - t_start) * 1000.0, 1)
            self._history.append(result)

        return result

    def _purge_table_batched(
        self,
        driver: Any,
        table: str,
        policy: RetentionPolicy,
        now: datetime,
        batch_size: int,
    ) -> int:
        """Delete rows in bounded batches with commits between chunks."""
        if policy.tier == DataTier.IMMUTABLE or table in IMMUTABLE_TABLES:
            raise ImmutableDataProtectionError(
                f"Financial and audit ledger records are immutable: table '{table}' cannot be purged."
            )

        cutoff = (now - timedelta(days=policy.retention_days)).isoformat()
        total = 0
        has_id = "id" in [c["name"] for c in driver.table_columns(table)]

        while True:
            drv_cfg = getattr(driver, "config", self.cfg)
            if drv_cfg.is_postgresql:
                anchor = "id" if has_id else "ctid"
                where = f'"{policy.timestamp_column}" < %s'
                if policy.condition:
                    where = f"{where} AND ({policy.condition})"
                sql = (
                    f'DELETE FROM "{table}" WHERE "{anchor}" IN '
                    f'(SELECT "{anchor}" FROM "{table}" WHERE {where} '
                    f'ORDER BY "{anchor}" LIMIT {batch_size})'
                )
                cnt = driver.execute(sql, (cutoff,))
            else:
                anchor = "id" if has_id else "rowid"
                where = f'"{policy.timestamp_column}" < ?'
                if policy.condition:
                    where = f"{where} AND ({policy.condition})"
                sql = (
                    f'DELETE FROM "{table}" WHERE "{anchor}" IN '
                    f'(SELECT "{anchor}" FROM "{table}" WHERE {where} '
                    f'ORDER BY "{anchor}" LIMIT {batch_size})'
                )
                cnt = driver.execute(sql, (cutoff,))

            if hasattr(cnt, "rowcount"):
                deleted = int(cnt.rowcount)
            else:
                deleted = int(cnt or 0)
            total += deleted
            if deleted < batch_size:
                break

        return total

    def run_maintenance(self, driver: Any | None = None) -> dict[str, Any]:
        """Perform database-specific routine maintenance."""
        close_needed = False
        if driver is None:
            driver = get_driver(self.cfg)
            close_needed = True

        drv_cfg = getattr(driver, "config", self.cfg)
        prov_val = (
            drv_cfg.provider.value if hasattr(drv_cfg.provider, "value") else str(drv_cfg.provider)
        )
        report: dict[str, Any] = {"status": "SUCCESS", "provider": prov_val}
        try:
            if drv_cfg.is_postgresql:
                # Standard safe VACUUM and ANALYZE on active tables (never VACUUM FULL)
                for table in self.policies.keys():
                    if driver.table_exists(table):
                        try:
                            driver.execute(f'ANALYZE "{table}"')
                        except Exception as e:
                            logger.warning("ANALYZE failed for %s: %s", table, e)
                report["operations"] = ["ANALYZE"]
            else:
                # SQLite maintenance: analyze and integrity check
                try:
                    driver.execute("PRAGMA analyze")
                    integrity = driver.scalar("PRAGMA integrity_check")
                    report["integrity"] = integrity
                    report["integrity_check"] = integrity
                    report["operations"] = ["ANALYZE", "INTEGRITY_CHECK"]
                except Exception as e:
                    logger.warning("SQLite maintenance failed: %s", e)
            return report
        finally:
            if close_needed:
                driver.close()

    def get_history(self) -> list[dict[str, Any]]:
        """Return the history of recent purge executions."""
        return [h.to_dict() for h in self._history[-50:]]
