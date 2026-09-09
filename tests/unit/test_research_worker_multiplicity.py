"""EDGE ROUND-2b (2026-09-09): the research worker auto-wires multiplicity.

The discovery -> validation loop in `research.worker.ResearchWorker` must feed
the DSR/SPA controls automatically: at discovery time it captures the number
of mined candidates (n_trials) and the per-family per-trade R lists (the
Reality Check set); at validation time it passes them to
`validate_candidate`, so `OOSResult.deflated_sharpe` / `.spa` are populated
without operator intervention.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import _context_fingerprint
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.worker import ResearchWorker


@pytest.fixture
def temp_audit_repo(tmp_path) -> Generator[AuditRepository, None, None]:
    repo = AuditRepository(
        db_url=f"sqlite:///{tmp_path / 'worker_multiplicity.db'}",
        flush_interval_sec=0.02,
    )
    try:
        yield repo
    finally:
        repo.close()


def _seed(repo: AuditRepository, ledger: ExperienceLedger) -> None:
    """Two strong context families (LONDON/UP and NY/DOWN) via real records."""
    from tests.unit.test_experience_intelligence import make_outcome, make_record

    base = datetime(2026, 8, 1, tzinfo=UTC)
    recs = []
    for i in range(40):
        recs.append((f"lon_{i}", "LONDON", "UP", 0.6, base + timedelta(minutes=i)))
        recs.append((f"ny_{i}", "NEW_YORK", "DOWN", 0.5, base + timedelta(minutes=i, seconds=30)))
    from nexus_scalp.experience.models import StrategyContext

    for key, session, trend, r, ts in recs:
        ctx = StrategyContext(
            strategy_id="STRAT-W",
            session=session,
            regime=trend,
            trend_state=trend,
            volatility_regime="NORMAL",
        )
        rec = make_record(key=key, decision_ts=ts, strategy_id="STRAT-W", context=ctx)
        ledger.record_experience(rec)
        outcome = make_outcome(
            key=key,
            realized_r=r,
            outcome_ts=ts + timedelta(minutes=5),
        )
        ledger.record_outcome(outcome)
    repo.flush(timeout_sec=20)


def test_worker_captures_multiplicity_and_passes_it_to_validation(
    temp_audit_repo, monkeypatch
) -> None:
    ledger = ExperienceLedger(audit_repo=temp_audit_repo)
    _seed(temp_audit_repo, ledger)
    builder = ResearchDatasetBuilder(ledger=ledger)
    ds = builder.build()
    assert len(ds.samples) >= 60

    pipeline = ResearchPipeline(
        dataset_builder=builder, registry=StrategyRegistry(audit_repo=temp_audit_repo)
    )
    worker = ResearchWorker(
        audit_repo=temp_audit_repo,
        ledger=ledger,
        pipeline=pipeline,
        max_validations_per_cycle=5,
    )
    # prime the worker's dataset + discovery exactly like a real cycle
    worker._refresh_dataset()
    worker._refresh_discovery()

    assert worker._n_trials >= 2, "two context families were seeded"
    assert len(worker._family_r_lists) == worker._n_trials

    captured: dict = {}

    def _fake_validate(candidate, dataset, **kwargs):
        captured.update(kwargs)
        return {"lifecycle": "REJECTED", "score": {"verdict": "REJECTED"}}

    monkeypatch.setattr(pipeline, "validate_candidate", _fake_validate)
    worker._refresh_validation()
    assert captured.get("n_trials") == worker._n_trials
    assert captured.get("family_r_lists") == worker._family_r_lists


def test_worker_multiplicity_matches_discovery_fingerprints(temp_audit_repo) -> None:
    ledger = ExperienceLedger(audit_repo=temp_audit_repo)
    _seed(temp_audit_repo, ledger)
    builder = ResearchDatasetBuilder(ledger=ledger)
    ds = builder.build()
    pipeline = ResearchPipeline(
        dataset_builder=builder, registry=StrategyRegistry(audit_repo=temp_audit_repo)
    )
    worker = ResearchWorker(audit_repo=temp_audit_repo, ledger=ledger, pipeline=pipeline)
    worker._refresh_dataset()
    worker._refresh_discovery()

    candidates = pipeline.discover(ds)
    fps = {c.context_definition.get("fingerprint") for c in candidates}
    sample_fps = {_context_fingerprint(s) for s in ds.samples}
    assert fps <= sample_fps
    assert worker._n_trials == len(candidates)
