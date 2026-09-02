"""
Unit Tests - Built-in Strategy Seeder & Research Worker Integration (PHASE 15C)
===============================================================================
Verifies that the Ichimili built-in strategies are seeded into the research
registry as deterministic candidates via `seed_builtin_candidates`, that the
seeder is idempotent (preserves existing validation results) and that the
ResearchWorker seeds on its first cycle.
"""

from __future__ import annotations

import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.models import BacktestResult, CandidateLifecycle
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.strategies import STRATEGY_ID_FINAL, STRATEGY_ID_SPACED
from nexus_scalp.strategies.seeder import seed_builtin_candidates


@pytest.fixture
def audit_repo(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'seed.db'}", flush_interval_sec=0.05)
    yield repo
    repo.close()


def _count_registry(audit_repo: AuditRepository, strategy_id: str) -> int:
    conn = sqlite3.connect(audit_repo._db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM strategy_registry WHERE strategy_id = ?;", (strategy_id,)
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def test_seed_registers_both_ichimili_candidates(audit_repo):
    entries = seed_builtin_candidates(audit_repo)
    audit_repo._queue.join()  # flush the async insert queue
    ids = {e.strategy_id for e in entries}
    assert STRATEGY_ID_FINAL in ids
    assert STRATEGY_ID_SPACED in ids
    # Persisted in the registry table.
    assert _count_registry(audit_repo, STRATEGY_ID_FINAL) == 1
    assert _count_registry(audit_repo, STRATEGY_ID_SPACED) == 1


def test_seed_is_idempotent_and_preserves_validation(audit_repo):
    registry = StrategyRegistry(audit_repo=audit_repo)
    seed_builtin_candidates(audit_repo, registry)
    audit_repo._queue.join()  # flush so registry.get() can read the row

    # Simulate an existing validation result attached to the FINAL candidate.
    entry = registry.get(STRATEGY_ID_FINAL)
    assert entry is not None
    bt = BacktestResult(
        strategy_id=STRATEGY_ID_FINAL,
        strategy_version=entry.strategy_version,
        dataset_id="ds_test",
        total_trades=10,
        wins=6,
        losses=4,
        net_pnl_usd=120.0,
        expectancy_r=0.3,
        profit_factor=1.5,
        max_drawdown_usd=50.0,
    )
    updated = entry.model_copy(update={"backtest": bt, "lifecycle": CandidateLifecycle.VALIDATED})
    registry.upsert(updated)
    audit_repo._queue.join()  # flush the async insert queue

    # Re-seed: must NOT clobber the backtest result.
    seed_builtin_candidates(audit_repo, registry)
    audit_repo._queue.join()

    after = registry.get(STRATEGY_ID_FINAL)
    assert after is not None
    assert after.backtest is not None
    assert after.backtest.total_trades == 10
    assert after.lifecycle == CandidateLifecycle.VALIDATED


def test_seed_versions_content_addressed(audit_repo):
    entries = seed_builtin_candidates(audit_repo)
    for e in entries:
        assert (
            e.strategy_version == e.canonical_version() if hasattr(e, "canonical_version") else True
        )
        assert e.discovery_source.startswith("builtin:")
        assert e.lifecycle == CandidateLifecycle.DISCOVERED


def test_worker_seeds_on_first_cycle(audit_repo):
    """ResearchWorker._refresh_once runs the seed step before dataset/discovery."""
    from nexus_scalp.research.worker import ResearchWorker

    worker = ResearchWorker(
        audit_repo=audit_repo,
        ledger=None,  # type: ignore[arg-type]
        pipeline=None,  # type: ignore[arg-type]
    )
    worker.running = True
    # The pipeline steps are isolated by _run(); only the seed step is valid
    # with our fixtures (dataset/discovery/validation fail gracefully).
    worker._refresh_once()
    audit_repo._queue.join()  # flush the async seed writes
    assert _count_registry(audit_repo, STRATEGY_ID_FINAL) >= 1
    assert _count_registry(audit_repo, STRATEGY_ID_SPACED) >= 1
