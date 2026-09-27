"""Database Event & Query Error/Warning Persistent Log Store.

Enforces Sections 16, 17, 18 of the Dual Database Architecture:
    - Structured persistence for WARNING, ERROR, and CRITICAL database events.
    - INFO and DEBUG are excluded to prevent database bloat and performance loss.
    - Sensitive credentials and tokens are strictly redacted.
    - Configurable retention and automatic pruning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.query_logging import mask_query_text
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.log_store")

LOG_TABLE = "db_operation_logs"


@dataclass
class DatabaseLogEntry:
    """Structured record of a database warning, error, or critical event."""

    level: str  # WARNING, ERROR, CRITICAL
    provider: str
    domain: str
    operation: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    repository: str = ""
    query_name: str = ""
    duration_ms: float = 0.0
    rows: int = 0
    error_code: str = ""
    error_message: str = ""
    correlation_id: str = ""
    masked_sql: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatabaseLogStore:
    """Manages persistent logging of database warnings and errors."""

    def __init__(
        self,
        config: DatabaseConfig | None = None,
        retention_days: int = 30,
    ) -> None:
        self.cfg = config or load_database_config("audit")
        self.retention_days = retention_days

    def ensure_table(self, driver: Any | None = None) -> None:
        """Create the db_operation_logs table if not present."""
        close_needed = False
        if driver is None:
            driver = get_driver(self.cfg)
            close_needed = True

        try:
            if self.cfg.is_postgresql:
                stmts = [
                    (
                        f"CREATE TABLE IF NOT EXISTS {LOG_TABLE} ("
                        "  id BIGSERIAL PRIMARY KEY,"
                        "  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                        "  level VARCHAR(16) NOT NULL,"
                        "  provider VARCHAR(32) NOT NULL,"
                        "  domain VARCHAR(64) NOT NULL,"
                        "  operation VARCHAR(128) NOT NULL,"
                        "  repository VARCHAR(128) NOT NULL DEFAULT '',"
                        "  query_name VARCHAR(128) NOT NULL DEFAULT '',"
                        "  duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,"
                        "  rows BIGINT NOT NULL DEFAULT 0,"
                        "  error_code VARCHAR(64) NOT NULL DEFAULT '',"
                        "  error_message TEXT NOT NULL DEFAULT '',"
                        "  correlation_id VARCHAR(64) NOT NULL DEFAULT '',"
                        "  masked_sql TEXT NOT NULL DEFAULT ''"
                        ")"
                    ),
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_ts ON {LOG_TABLE} (timestamp)",
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_level ON {LOG_TABLE} (level)",
                ]
            else:
                stmts = [
                    (
                        f"CREATE TABLE IF NOT EXISTS {LOG_TABLE} ("
                        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "  timestamp TEXT NOT NULL,"
                        "  level TEXT NOT NULL,"
                        "  provider TEXT NOT NULL,"
                        "  domain TEXT NOT NULL,"
                        "  operation TEXT NOT NULL,"
                        "  repository TEXT NOT NULL DEFAULT '',"
                        "  query_name TEXT NOT NULL DEFAULT '',"
                        "  duration_ms REAL NOT NULL DEFAULT 0.0,"
                        "  rows INTEGER NOT NULL DEFAULT 0,"
                        "  error_code TEXT NOT NULL DEFAULT '',"
                        "  error_message TEXT NOT NULL DEFAULT '',"
                        "  correlation_id TEXT NOT NULL DEFAULT '',"
                        "  masked_sql TEXT NOT NULL DEFAULT ''"
                        ")"
                    ),
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_ts ON {LOG_TABLE} (timestamp)",
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_level ON {LOG_TABLE} (level)",
                ]
            for stmt in stmts:
                driver.execute(stmt)
        finally:
            if close_needed:
                driver.close()

    def record(self, entry: DatabaseLogEntry, driver: Any | None = None) -> bool:
        """Persist a warning, error, or critical event."""
        # Only retain WARNING, ERROR, CRITICAL
        if entry.level.upper() not in {"WARNING", "ERROR", "CRITICAL"}:
            return False

        close_needed = False
        if driver is None:
            driver = get_driver(self.cfg)
            close_needed = True
        try:
            self.ensure_table(driver)
            safe_sql = mask_query_text(entry.masked_sql)
            params = (
                entry.timestamp or datetime.now(UTC).isoformat(),
                entry.level.upper(),
                entry.provider,
                entry.domain,
                entry.operation,
                entry.repository,
                entry.query_name,
                entry.duration_ms,
                entry.rows,
                entry.error_code,
                entry.error_message[:2000],
                entry.correlation_id,
                safe_sql,
            )

            cols = (
                "timestamp, level, provider, domain, operation, repository, "
                "query_name, duration_ms, rows, error_code, error_message, "
                "correlation_id, masked_sql"
            )
            if self.cfg.is_postgresql:
                placeholders = ", ".join("%s" for _ in range(13))
            else:
                placeholders = ", ".join("?" for _ in range(13))

            insert_sql = f"INSERT INTO {LOG_TABLE} ({cols}) VALUES ({placeholders})"
            driver.execute(insert_sql, params)
            return True
        except Exception as exc:
            logger.debug("Failed to persist database log entry: %s", exc)
            return False
        finally:
            if close_needed:
                driver.close()

    log = record

    def query_recent(
        self,
        limit: int = 50,
        level: str | None = None,
        driver: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Query recent persisted log events."""
        close_needed = False
        if driver is None:
            driver = get_driver(self.cfg)
            close_needed = True
        try:
            self.ensure_table(driver)
            where = ""
            args: tuple[Any, ...] = ()
            if level:
                where = "WHERE level = %s" if self.cfg.is_postgresql else "WHERE level = ?"
                args = (level.upper(),)

            sql = f"SELECT * FROM {LOG_TABLE} {where} ORDER BY id DESC LIMIT {int(limit)}"
            return driver.query(sql, args)
        except Exception as exc:
            logger.debug("Failed to query recent database log entries: %s", exc)
            return []
        finally:
            if close_needed:
                driver.close()

    get_recent_logs = query_recent

    def purge_expired(self) -> int:
        """Purge log records exceeding the retention period."""
        driver = get_driver(self.cfg)
        try:
            self.ensure_table(driver)
            cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
            where = "timestamp < %s" if self.cfg.is_postgresql else "timestamp < ?"
            sql = f"DELETE FROM {LOG_TABLE} WHERE {where}"
            cnt = driver.execute(sql, (cutoff,))
            return int(cnt or 0)
        except Exception as exc:
            logger.error("Failed to prune old database operation logs: %s", exc)
            return 0
        finally:
            driver.close()


DbLogEntry = DatabaseLogEntry
DbLogStore = DatabaseLogStore
