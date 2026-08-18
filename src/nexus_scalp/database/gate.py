"""
Startup Migration Gate (TASK-10 §6/§7/§28)
==========================================
Runs BEFORE the engine enters READY. Applies pending SAFE_ADDITIVE
migrations automatically; surfaces BLOCKED/FAILED states truthfully.

Cheap fast path (§28): the schema_meta version lookup happens only when the
migration_engine_version / expected version differs — normal startups with a
current DB are near-zero cost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus_scalp.database.engine import DatabaseMigrationEngine, db_path_for_domain
from nexus_scalp.database.models import DatabaseDomain, MigrationState
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.gate")

#: Domains checked at startup (per-domain independence, §2).
STARTUP_DOMAINS: tuple[DatabaseDomain, ...] = (
    DatabaseDomain.AUDIT,
    DatabaseDomain.NEWS,
    DatabaseDomain.CANDLE_INTEL,
)


def run_startup_migration_gate(
    workspace: Path | None = None,
    *,
    application_version: str = "",
    git_commit: str = "",
    db_paths: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Runs the migration gate over all startup domains.

    Returns per-domain results + an overall READY/BLOCKED verdict.
    NEVER raises: failure surfaces as state=DB_MIGRATION_FAILED so the
    caller can refuse to enter READY (§7).
    """
    results: dict[str, Any] = {}
    overall = MigrationState.DB_MIGRATION_NOT_REQUIRED.value
    for domain in STARTUP_DOMAINS:
        path = db_paths.get(domain.value) if db_paths else None
        db_path = Path(path) if path else db_path_for_domain(domain.value, workspace)
        engine = DatabaseMigrationEngine(
            db_path=db_path,
            domain=domain,
            application_version=application_version,
            git_commit=git_commit,
        )
        try:
            if not db_path.exists():
                # Fresh install: bootstrap will create; mark current.
                results[domain.value] = {
                    "database": domain.value,
                    "state": "DB_MIGRATION_NOT_REQUIRED",
                    "current_version": 0,
                    "expected_version": engine.expected_version(),
                    "reason": "database not created yet (bootstrap on first use)",
                }
                continue
            result = engine.migrate()
            results[domain.value] = result
            if result["state"] in (
                MigrationState.DB_MIGRATION_FAILED.value,
                MigrationState.DB_BLOCKED.value,
                MigrationState.DB_DOWNGRADE_BLOCKED.value,
                MigrationState.DB_CORRUPTED.value,
            ):
                overall = MigrationState.DB_BLOCKED.value
            elif result["state"] == MigrationState.DB_MIGRATION_IN_PROGRESS.value:
                overall = MigrationState.DB_BLOCKED.value
            elif overall == MigrationState.DB_MIGRATION_NOT_REQUIRED.value:
                overall = MigrationState.DB_MIGRATION_SUCCEEDED.value
        except Exception as err:
            logger.error(
                "[DB_MIGRATION] event=GATE_FAILED",
                database=domain.value,
                error=str(err),
            )
            results[domain.value] = {
                "database": domain.value,
                "state": MigrationState.DB_MIGRATION_FAILED.value,
                "error": str(err),
            }
            overall = MigrationState.DB_BLOCKED.value

    return {
        "state": overall,
        "ready": overall
        not in (
            MigrationState.DB_BLOCKED.value,
            MigrationState.DB_MIGRATION_FAILED.value,
        ),
        "databases": results,
    }


def assert_ready(gate_result: dict[str, Any]) -> None:
    """Raises when the gate is not ready (§7: never report READY on failure)."""
    if not gate_result.get("ready", False):
        from nexus_scalp.database.engine import MigrationError

        bad = {
            k: v
            for k, v in gate_result.get("databases", {}).items()
            if v.get("state")
            not in (
                "DB_MIGRATION_NOT_REQUIRED",
                "DB_MIGRATION_SUCCEEDED",
            )
        }
        raise MigrationError(
            f"database migration gate blocked: {bad}",
            error_type="MIGRATION_GATE_BLOCKED",
        )
