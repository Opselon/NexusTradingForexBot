"""Database Provider Lifecycle and Transition State Machine.

Enforces Section 14 & 13 of the Dual Database Architecture:
    Provider switching is not a simple toggle. The transition must proceed
    through explicit lifecycle states:
        CONFIGURED -> TESTING -> MIGRATING -> VERIFYING -> READY -> ACTIVE -> FAILED

Also enforces safe rollback from PostgreSQL -> SQLite:
    Detects data divergence if PostgreSQL has newer operational records than
    the local SQLite database, requiring explicit acknowledgment or reverse
    migration before activating SQLite.
"""

from __future__ import annotations

import enum
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.provider import DatabaseProvider
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.provider_lifecycle")


class ProviderSwitchPhase(enum.Enum):
    """Lifecycle phase of a database provider switch."""

    CONFIGURED = "CONFIGURED"
    TESTING = "TESTING"
    MIGRATING = "MIGRATING"
    VERIFYING = "VERIFYING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"


@dataclass
class DivergenceCheckResult:
    """Report comparing source and destination operational row counts."""

    diverged: bool
    source_provider: str
    target_provider: str
    table_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    unmigrated_rows_estimate: int = 0
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderTransitionState:
    """State record for database provider management."""

    active_provider: str
    target_provider: str
    phase: ProviderSwitchPhase
    updated_at: float
    last_test_passed: bool = False
    last_migration_passed: bool = False
    last_verification_passed: bool = False
    error: str = ""
    divergence: DivergenceCheckResult | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["phase"] = self.phase.value
        return d


class ProviderLifecycleManager:
    """Manages provider transitions, validation, and safe activation."""

    _CRITICAL_TABLES: tuple[str, ...] = (
        "audit_ledger",
        "audit_orders",
        "audit_signals",
        "audit_broker_deals",
        "audit_account_snapshots",
    )

    _STATE_KEY = "database.provider_transition_state"

    def __init__(self, workspace: str | None = None, settings_db_path: str | None = None) -> None:
        self.workspace = workspace
        self.settings_db_path = settings_db_path
        self._state: ProviderTransitionState = self._load_state()

    def _settings(self) -> Any:
        from nexus_scalp.settings.service import SettingsDatabase
        return SettingsDatabase(db_path=Path(self.settings_db_path)) if self.settings_db_path else SettingsDatabase()

    def _load_state(self) -> ProviderTransitionState:
        active = self.get_active_provider()
        try:
            db = self._settings()
            row = db.get(self._STATE_KEY)
            db.close()
            if row and isinstance(row.value, dict):
                raw = row.value
                divergence = raw.get("divergence")
                return ProviderTransitionState(
                    active_provider=str(raw.get("active_provider") or active),
                    target_provider=str(raw.get("target_provider") or active),
                    phase=ProviderSwitchPhase(str(raw.get("phase") or ProviderSwitchPhase.ACTIVE.value)),
                    updated_at=float(raw.get("updated_at") or time.time()),
                    last_test_passed=bool(raw.get("last_test_passed", False)),
                    last_migration_passed=bool(raw.get("last_migration_passed", False)),
                    last_verification_passed=bool(raw.get("last_verification_passed", False)),
                    error=str(raw.get("error") or ""),
                    divergence=DivergenceCheckResult(**divergence) if isinstance(divergence, dict) else None,
                )
        except Exception as exc:
            logger.warning("Could not load persisted provider transition state: %s", exc)
        return ProviderTransitionState(active, active, ProviderSwitchPhase.ACTIVE, time.time())

    def _persist_state(self) -> None:
        db = self._settings()
        try:
            db.set(self._STATE_KEY, self._state.to_dict(), value_type="json", source="USER_SETTINGS", actor="web")
        finally:
            db.close()

    def get_active_provider(self) -> str:
        """Resolve the authoritative provider; configuration errors propagate."""
        cfg = load_database_config("audit", settings_db_path=self.settings_db_path)
        return cfg.provider.value

    def get_state(self) -> ProviderTransitionState:
        """Get the current transition state."""
        self._state = self._load_state()
        return self._state

    def start_transition(self, target_provider: str) -> ProviderTransitionState:
        """Initiate a transition toward target_provider (sqlite or postgresql)."""
        target = DatabaseProvider.parse(target_provider).value
        active = self.get_active_provider()
        self._state = ProviderTransitionState(
            active_provider=active,
            target_provider=target,
            phase=ProviderSwitchPhase.CONFIGURED,
            updated_at=time.time(),
        )
        logger.info(
            "Provider switch transition initiated: active=%s, target=%s",
            active,
            target,
        )
        self._persist_state()
        return self._state

    def test_target_connection(self, config_overrides: dict[str, Any] | None = None) -> bool:
        """Test target provider connection and update transition state."""
        self._state = self._load_state()
        self._state.phase = ProviderSwitchPhase.TESTING
        self._state.updated_at = time.time()
        target = self._state.target_provider

        try:
            if target == DatabaseProvider.POSTGRESQL.value:
                cfg = self._build_target_pg_config(config_overrides)
            else:
                cfg = DatabaseConfig.for_sqlite("audit")

            driver = get_driver(cfg)
            try:
                ok = driver.ping()
            finally:
                driver.close()

            self._state.last_test_passed = ok
            if ok:
                self._state.phase = ProviderSwitchPhase.CONFIGURED
                self._state.error = ""
            else:
                self._state.phase = ProviderSwitchPhase.FAILED
                self._state.error = f"Connection test failed for target provider {target}"
            self._persist_state()
            return ok
        except Exception as exc:
            self._state.phase = ProviderSwitchPhase.FAILED
            self._state.error = f"Target connection test raised: {exc}"
            self._state.last_test_passed = False
            self._persist_state()
            return False

    def mark_migrating(self) -> None:
        """Set phase to MIGRATING during data transfer."""
        self._state.phase = ProviderSwitchPhase.MIGRATING
        self._state.updated_at = time.time()
        self._persist_state()

    def mark_migration(self, passed: bool, error: str = "") -> None:
        """Record migration completion without activating the provider."""
        self._state.updated_at = time.time()
        self._state.last_migration_passed = passed
        self._state.error = "" if passed else (error or "Data migration failed")
        self._state.phase = ProviderSwitchPhase.CONFIGURED if passed else ProviderSwitchPhase.FAILED
        self._persist_state()

    def mark_verification(self, passed: bool, error: str = "") -> None:
        """Record the post-migration verification result."""
        self._state.updated_at = time.time()
        self._state.last_verification_passed = passed
        if passed:
            self._state.phase = ProviderSwitchPhase.READY
            self._state.error = ""
        else:
            self._state.phase = ProviderSwitchPhase.FAILED
            self._state.error = error or "Data verification failed"
        self._persist_state()

    def _get_driver(self, cfg: DatabaseConfig) -> Any:
        return get_driver(cfg)

    def check_divergence(
        self,
        src_cfg: DatabaseConfig | None = None,
        dst_cfg: DatabaseConfig | None = None,
    ) -> DivergenceCheckResult:
        """Check for unmigrated operational rows between providers.

        Crucial when returning to SQLite from PostgreSQL: ensures the operator
        does not activate a stale or empty SQLite file without warning.
        """
        src = src_cfg or load_database_config("audit", settings_db_path=self.settings_db_path)
        dst = dst_cfg or (
            DatabaseConfig.for_sqlite("audit")
            if src.is_postgresql
            else DatabaseConfig.for_postgres("audit")
        )

        counts: dict[str, dict[str, int]] = {}
        total_unmigrated = 0
        src_driver = None
        dst_driver = None

        try:
            src_driver = self._get_driver(src)
            dst_driver = self._get_driver(dst)

            for table in self._CRITICAL_TABLES:
                src_count = 0
                dst_count = 0
                if src_driver.table_exists(table):
                    src_count = src_driver.scalar(f"SELECT COUNT(*) FROM {table}") or 0
                if dst_driver.table_exists(table):
                    dst_count = dst_driver.scalar(f"SELECT COUNT(*) FROM {table}") or 0

                counts[table] = {
                    "source": src_count,
                    "target": dst_count,
                }
                diff = src_count - dst_count
                if diff > 0:
                    total_unmigrated += diff

            diverged = total_unmigrated > 0
            warning = ""
            if diverged:
                warning = (
                    f"Warning: Source ({src.provider.value}) has approximately "
                    f"{total_unmigrated} operational rows not present in target ({dst.provider.value}). "
                    "Migrate data before activating or historical operational state will be omitted."
                )

            res = DivergenceCheckResult(
                diverged=diverged,
                source_provider=src.provider.value,
                target_provider=dst.provider.value,
                table_counts=counts,
                unmigrated_rows_estimate=total_unmigrated,
                warning=warning,
            )
            self._state.divergence = res
            self._persist_state()
            return res
        except Exception as exc:
            logger.warning("Divergence check failed: %s", exc)
            # Unknown divergence is unsafe: never report a clean comparison.
            res = DivergenceCheckResult(
                diverged=True,
                source_provider=src.provider.value,
                target_provider=dst.provider.value,
                warning=f"Divergence check could not complete: {type(exc).__name__}",
            )
            self._state.divergence = res
            return res
        finally:
            if src_driver:
                src_driver.close()
            if dst_driver:
                dst_driver.close()

    def confirm_activation(self) -> bool:
        """Activate only after target testing, migration, verification, and parity."""
        self._state = self._load_state()
        if self._state.phase != ProviderSwitchPhase.READY:
            logger.error(
                "Refusing provider activation: phase is %s (must be READY)",
                self._state.phase.value,
            )
            return False
        if not (
            self._state.last_test_passed
            and self._state.last_migration_passed
            and self._state.last_verification_passed
            and self._state.divergence is not None
            and not self._state.divergence.diverged
        ):
            logger.error("Refusing provider activation: transition evidence is incomplete")
            return False

        from pathlib import Path

        from nexus_scalp.settings.service import SettingsDatabase, SettingsService

        db_obj = (
            SettingsDatabase(db_path=Path(self.settings_db_path)) if self.settings_db_path else None
        )
        svc = SettingsService(db=db_obj)
        svc.set_database_provider(self._state.target_provider)

        import os

        os.environ["NSE_DATABASE__PROVIDER"] = self._state.target_provider
        self._state.active_provider = self._state.target_provider
        self._state.phase = ProviderSwitchPhase.ACTIVE
        self._state.updated_at = time.time()
        self._persist_state()
        logger.info(
            "Database provider successfully switched to %s (ACTIVE)",
            self._state.active_provider,
        )
        return True

    def _build_target_pg_config(self, overrides: dict[str, Any] | None = None) -> DatabaseConfig:
        raw = overrides or {}
        from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY

        return DatabaseConfig.for_postgres(
            domain="audit",
            host=str(raw.get("host") or "localhost"),
            port=int(raw.get("port") or 5432),
            database=str(raw.get("database") or "nse_audit"),
            username=str(raw.get("username") or "nse_user"),
            ssl_mode=str(raw.get("ssl_mode") or ""),
            password_secret=PG_PASSWORD_SECRET_KEY,
        )
