"""
Hardening Regression Tests (P1 & P2)
=====================================
P1: Stale RUNNING generation sweeper (AutonomousLoopWorker.recover / sweep_stale_generations).
P2: StrategyRegistry.upsert lifecycle regression protection default (forbid_lifecycle_regression=True).
"""
from __future__ import annotations

import pytest
from datetime import datetime, UTC, timedelta

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.models import StrategyRegistryEntry, CandidateLifecycle
from nexus_scalp.strategies.factory.store import upsert_generation, get_generation, sweep_stale_generations


def test_p1_stale_generation_sweeper(tmp_path):
    """P1: Stale RUNNING generations are marked FAILED while recent RUNNING ones are untouched."""
    db_path = tmp_path / "hardening.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    # Create an old RUNNING generation (40 minutes old)
    old_time = (datetime.now(UTC) - timedelta(minutes=40)).isoformat()
    upsert_generation(audit, {
        "generation_id": "gen_old_running",
        "number": 1,
        "mode": "MANUAL",
        "status": "RUNNING",
        "created_at": old_time,
    })

    # Create a fresh RUNNING generation (5 minutes old)
    fresh_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    upsert_generation(audit, {
        "generation_id": "gen_fresh_running",
        "number": 2,
        "mode": "MANUAL",
        "status": "RUNNING",
        "created_at": fresh_time,
    })

    # Create a completed generation (old)
    upsert_generation(audit, {
        "generation_id": "gen_old_completed",
        "number": 3,
        "mode": "MANUAL",
        "status": "COMPLETED",
        "created_at": old_time,
    })
    audit._queue.join()

    # Run sweeper (max_age_minutes=30)
    result = sweep_stale_generations(audit, max_age_minutes=30)
    audit._queue.join()

    assert "gen_old_running" in result["swept"]
    assert "gen_fresh_running" not in result["swept"]
    assert "gen_old_completed" not in result["swept"]

    # Verify states in DB
    old_gen = get_generation(audit, "gen_old_running")
    assert old_gen["status"] == "FAILED"

    fresh_gen = get_generation(audit, "gen_fresh_running")
    assert fresh_gen["status"] == "RUNNING"

    comp_gen = get_generation(audit, "gen_old_completed")
    assert comp_gen["status"] == "COMPLETED"


def test_p2_upsert_lifecycle_regression_protection_default(tmp_path):
    """P2: StrategyRegistry.upsert defaults to forbid_lifecycle_regression=True, refusing downgrades."""
    db_path = tmp_path / "regression.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    registry = StrategyRegistry(audit_repo=audit)

    # Insert a VALIDATED entry
    entry = StrategyRegistryEntry(
        strategy_id="strat_p2",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.VALIDATED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert registry.upsert(entry) is True
    audit._queue.join()

    assert registry.get("strat_p2", "1.0.0").lifecycle == CandidateLifecycle.VALIDATED

    # Attempt to overwrite with DISCOVERED using default (forbid_lifecycle_regression=True)
    downgrade_entry = entry.model_copy(update={"lifecycle": CandidateLifecycle.DISCOVERED})
    assert registry.upsert(downgrade_entry) is False
    audit._queue.join()

    # Lifecycle must remain VALIDATED
    assert registry.get("strat_p2", "1.0.0").lifecycle == CandidateLifecycle.VALIDATED

    # Explicit administrative override (forbid_lifecycle_regression=False) must succeed
    assert registry.upsert(downgrade_entry, forbid_lifecycle_regression=False) is True
    audit._queue.join()

    assert registry.get("strat_p2", "1.0.0").lifecycle == CandidateLifecycle.DISCOVERED
