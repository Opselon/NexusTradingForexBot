"""
Built-in Strategy Seeder
=========================
PHASE 15C: seeds the registered built-in strategies into the research
registry so they become first-class research candidates (DISCOVERED) that
the pipeline can backtest / walk-forward / OOS-validate like any discovered
candidate.

The seeder is idempotent: `seed_builtin_candidates()` upserts a registry
entry per (strategy_id, strategy_version) without touching existing entries'
validation results (registry immutability contract).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.models import StrategyRegistryEntry
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.strategies.base import builtin_candidates

logger = get_logger("nexus_scalp.strategies.seeder")


def seed_builtin_candidates(
    audit_repo: AuditRepository,
    registry: StrategyRegistry | None = None,
) -> list[StrategyRegistryEntry]:
    """Upserts all built-in strategy candidates into the research registry.

    Returns the registry entries created/updated. Never validates, never
    promotes, never touches the live path.
    """
    registry = registry or StrategyRegistry(audit_repo=audit_repo)
    now = datetime.now(UTC)
    entries: list[StrategyRegistryEntry] = []
    for candidate in builtin_candidates():
        # Preserve any existing validation results: load the current row and
        # only fill in the definition/discovery fields.
        existing = registry.get(candidate.strategy_id, candidate.strategy_version)
        entry = StrategyRegistryEntry(
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            feature_schema_id=candidate.feature_schema_id,
            feature_dimension=candidate.feature_dimension,
            discovery_source=f"builtin:{candidate.discovery_method}",
            discovery_window=candidate.discovery_window or "ALL",
            context_definition=candidate.context_definition,
            parent_strategy_ids=candidate.parent_strategy_ids,
            lifecycle=candidate.lifecycle,
            backtest=None,
            walkforward=None,
            oos=None,
            robustness=None,
            score=None,
            confidence=0.0,
            sample_count=0,
            validation_lineage=[],
            retirement_reason="",
            created_at=now,
            updated_at=now,
        )
        if existing is not None:
            # Preserve whatever validation truth already exists.
            entry = entry.model_copy(
                update={
                    "backtest": existing.backtest,
                    "walkforward": existing.walkforward,
                    "oos": existing.oos,
                    "robustness": existing.robustness,
                    "score": existing.score,
                    "confidence": existing.confidence,
                    "sample_count": existing.sample_count,
                    "validation_lineage": existing.validation_lineage,
                    "lifecycle": existing.lifecycle,
                    "retirement_reason": existing.retirement_reason,
                    "created_at": existing.created_at,
                }
            )
        registry.upsert(entry)
        entries.append(entry)
        logger.info(
            "[STRATEGY_SEED] event=UPSERTED strategy_id=%s version=%s",
            candidate.strategy_id,
            candidate.strategy_version,
        )
    return entries


def seed_builtin_candidates_deferred(audit_repo: AuditRepository) -> Any:
    """Thread-safe wrapper for the background research worker (isolated)."""
    try:
        return seed_builtin_candidates(audit_repo)
    except Exception as e:  # pragma: no cover - defensive isolation
        logger.error("[STRATEGY_SEED] failed (isolated)", error=str(e))
        return []


__all__ = ["seed_builtin_candidates", "seed_builtin_candidates_deferred"]
