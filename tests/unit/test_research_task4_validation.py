"""TASK-4 data-integrity regression tests (part 2): family-select validation,
OOS gate, robustness, scoring, candidate identity, registry immutability,
worker rebuild guard, health diagnostics.

Covers: TEST-RS-15, RS-16, RS-17, RS-18, RS-19, RS-20, RS-21(audit),
RS-22, RS-23, RS-24(part).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import discover_candidates
from nexus_scalp.research.models import (
    CandidateLifecycle,
    ExecutionAssumptions,
    ResearchDataset,
    ResearchSample,
    StrategyRegistryEntry,
)
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.pipeline import ResearchPipeline, _select_family
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.robustness import RobustnessEngine
from nexus_scalp.research.scoring import compute_strategy_score
from nexus_scalp.research.worker import ResearchWorker

try:
    from tests.unit.task4_research_helpers import make_outcome, make_record, seed_experiences
except ImportError:  # pragma: no cover
    from task4_research_helpers import make_outcome, make_record, seed_experiences


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 't4b.db'}")
    yield r
    r.close()


def _ds(repo) -> ResearchDataset:
    ledger = ExperienceLedger(repo)
    return ResearchDatasetBuilder(ledger).build()


def _candidate_with_family(repo) -> tuple[StrategyCandidate, ResearchDataset]:
    """A family with >=20 samples that clears discovery expectancy."""
    ledger = ExperienceLedger(repo)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    records: list = []
    for i in range(26):
        rec = make_record(f"fam{i}", ts=base + timedelta(hours=i))
        r = 0.3 if i % 2 else 0.15
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, r))
        records.append(rec)
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    cands = discover_candidates(ds.samples)
    assert len(cands) == 1
    return cands[0], ds


def test_rs15_negative_oos_always_rejects(repo):
    """TEST-RS-15: negative OOS always produces REJECTED verdict."""
    ledger = ExperienceLedger(repo)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    # 40 winning in-sample, then 10 losing OOS (same family).
    for i in range(40):
        rec = make_record(f"in{i}", ts=base + timedelta(hours=i))
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, 0.5))
    for i in range(10):
        rec = make_record(f"oos{i}", ts=base + timedelta(hours=50 + i))
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, -0.8))
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    gate = OOSGate()
    oos = gate.evaluate(ds, "s1", "v1", oos_frac=0.25)
    assert oos.in_sample_expectancy_r > 0
    assert oos.oos_expectancy_r < 0
    assert oos.status == "FAIL"
    bt = BacktestEngine().run(ds, "s1", "v1", use_split=True)
    score = compute_strategy_score(ds, backtest=bt, walkforward=None, oos=oos, robustness=None)
    assert score.verdict == "REJECTED", "negative OOS must reject regardless of in-sample"


def test_rs16_robustness_degradation_measurable(repo):
    """TEST-RS-16: robustness degradation is measurable, not just profitability."""
    ledger = ExperienceLedger(repo)
    seed_experiences(ledger, repo, 30, prefix="rs16")
    ds = ResearchDatasetBuilder(ledger).build()
    eng = RobustnessEngine()
    res = eng.evaluate(ds, "s1", "v1")
    assert res.baseline_expectancy_r != 0.0
    assert len(res.stress_expectancies) == len(eng.scenarios)
    assert res.max_degradation >= 0.0
    # Stress must never be a no-op: expectancies recorded per scenario.
    assert all(
        k in res.stress_expectancies
        for k in ("spread_plus_1", "slippage_plus_1", "latency_plus_50ms")
    )


def test_rs17_nan_inf_score_rejected():
    """TEST-RS-17: non-finite realized values cannot enter the score path."""
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    samples = [
        ResearchSample(
            sample_id="n1",
            experience_id="e1",
            idempotency_key="k1",
            decision_timestamp=t0,
            outcome_timestamp=t0 + timedelta(minutes=1),
            symbol="XAUUSD",
            strategy_id="s",
            realized_r=float("nan"),
            realized_pnl_usd=1.0,
        )
    ]
    ds = ResearchDataset(samples=samples, dataset_id="d")
    bt = BacktestEngine(assumptions=ExecutionAssumptions()).run(ds, "s", "v")
    # Non-finite realized values are excluded at the statistics boundary: the
    # backtest is EMPTY (total_trades=0), never NaN metrics and never a
    # fabricated validation from garbage input.
    assert bt.total_trades == 0
    assert bt.expectancy_r == 0.0


def test_rs18_candidate_identity_deterministic():
    """TEST-RS-18: candidate identity is deterministic for same definition."""
    a = StrategyCandidate(
        strategy_id="S",
        strategy_version="",
        context_definition={"fingerprint": "X"},
        entry_logic={"direction": "directional"},
    )
    b = StrategyCandidate(
        strategy_id="S",
        strategy_version="",
        context_definition={"fingerprint": "X"},
        entry_logic={"direction": "directional"},
    )
    a = a.model_copy(update={"strategy_version": a.canonical_version()})
    b = b.model_copy(update={"strategy_version": b.canonical_version()})
    assert a.strategy_id == b.strategy_id
    assert a.strategy_version == b.strategy_version
    assert a.content_digest() == b.content_digest()


def test_rs19_definition_change_new_version():
    """TEST-RS-19: a definition change creates a NEW identity/version."""
    c = StrategyCandidate(
        strategy_id="S",
        strategy_version="",
        context_definition={"fingerprint": "X"},
        entry_logic={"direction": "directional"},
    )
    c = c.model_copy(update={"strategy_version": c.canonical_version()})
    v1 = c.strategy_version
    c2 = c.with_definition_change(entry_logic={"direction": "counter"})
    assert c2.strategy_version != v1
    assert c.strategy_version == v1  # original immutable


def test_rs20_registry_immutable(repo):
    """TEST-RS-20: registry refuses definition mutation + lifecycle regression."""
    reg = StrategyRegistry(repo)
    e1 = StrategyRegistryEntry(
        strategy_id="S1",
        strategy_version="v1",
        lifecycle=CandidateLifecycle.VALIDATED,
        context_definition={"fingerprint": "X"},
        sample_count=30,
    )
    assert reg.upsert(e1)
    repo._queue.join()
    # Same version, different definition -> refused.
    e2 = e1.model_copy(update={"context_definition": {"fingerprint": "Y"}})
    assert reg.upsert(e2) is False
    # Same definition, lifecycle regression -> refused.
    e3 = e1.model_copy(update={"lifecycle": CandidateLifecycle.DISCOVERED})
    assert reg.upsert(e3, forbid_lifecycle_regression=True) is False
    got = reg.get("S1", "v1")
    assert got is not None and got.lifecycle == CandidateLifecycle.VALIDATED
    assert got.context_definition == {"fingerprint": "X"}


def test_rs21_audit_exposes_rejection_reasons(repo):
    """TEST-RS-21: dataset audit exposes structured rejection reasons."""
    ledger = ExperienceLedger(repo)
    rec = make_record("audit1", ts=datetime(2024, 1, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    ledger.record_outcome(make_outcome(rec, 0.0, broker_source="NONE"))  # zero-sub
    rec2 = make_record("audit2", ts=datetime(2024, 1, 2, tzinfo=UTC))
    ledger.record_experience(rec2)  # no outcome
    repo._queue.join()
    audit = ResearchDatasetBuilder(ledger).audit()
    assert audit["eligible"] == 0
    assert audit["rejected"] == 2
    reasons = audit["rejection_reasons"]
    assert reasons.get("MISSING_REALIZED_R", 0) == 1
    assert reasons.get("MISSING_OUTCOME", 0) == 1
    # every rejection carries trade_id + strategy_id + reason
    for r in audit["rejections"]:
        assert r["trade_id"] and r["strategy_id"] and r["rejection_reason"]


def test_rs22_worker_real_work_when_new_experience(repo):
    """TEST-RS-22: worker performs real work when new experience exists."""
    ledger = ExperienceLedger(repo)
    seed_experiences(ledger, repo, 26, prefix="w1")
    pipeline = ResearchPipeline(ResearchDatasetBuilder(ledger), StrategyRegistry(repo))
    worker = ResearchWorker(repo, ledger, pipeline, interval_sec=0.0)
    worker.start()
    ran = worker.tick()
    assert ran is True
    assert worker.last_work_done is True, "first build with data must be real work"
    worker.stop()


def test_rs23_worker_noop_when_dataset_unchanged(repo):
    """TEST-RS-23: worker performs no-op when the dataset is unchanged."""
    ledger = ExperienceLedger(repo)
    seed_experiences(ledger, repo, 26, prefix="w2")
    pipeline = ResearchPipeline(ResearchDatasetBuilder(ledger), StrategyRegistry(repo))
    worker = ResearchWorker(repo, ledger, pipeline, interval_sec=0.0)
    worker.start()
    worker.tick()
    # Same data -> dataset_id identical -> discovery/validation skipped.
    worker.tick()
    assert worker.last_work_done is False
    assert getattr(worker, "_dataset", None) is not None
    worker.stop()


def test_rs24_family_select_validation(repo):
    """TEST-RS-24(part): validation gates run on the candidate's OWN family."""
    cand, ds = _candidate_with_family(repo)
    family = _select_family(ds, cand)
    assert len(family.samples) == 26
    assert all(s.idempotency_key in cand.discovery_evidence["sample_ids"] for s in family.samples)
    # The registry upsert carries family-only sample count (in-sample split).
    pipeline = ResearchPipeline(
        ResearchDatasetBuilder(ExperienceLedger(repo)), StrategyRegistry(repo)
    )
    result = pipeline.validate_candidate(cand, ds)
    assert result["sample_count"] <= 26
    assert result["sample_count"] >= 20  # majority of the family, never the whole dataset


def test_no_automatic_active(repo):
    """TEST-RS-25: no candidate becomes ACTIVE automatically."""
    cand, ds = _candidate_with_family(repo)
    pipeline = ResearchPipeline(
        ResearchDatasetBuilder(ExperienceLedger(repo)), StrategyRegistry(repo)
    )
    result = pipeline.validate_candidate(cand, ds)
    assert result["lifecycle"] in (
        CandidateLifecycle.VALIDATED.value,
        CandidateLifecycle.REJECTED.value,
        CandidateLifecycle.DISCOVERED.value,
    )
    assert result["lifecycle"] != CandidateLifecycle.ACTIVE.value
