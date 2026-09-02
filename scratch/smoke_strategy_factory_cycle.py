"""smoke_strategy_factory_cycle.py — full-cycle smoke probe (scratch/).

Runs the REAL deterministic pipeline: seed experiences into the ledger,
build dataset, create a factory generation, generate a small population,
structurally validate, evaluate through ResearchPipeline.validate_candidate,
complete generation, and verify persisted rows.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
)
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.strategies.factory.models import EvolutionConfig, GenerationMode
from nexus_scalp.strategies.factory.orchestrator import StrategyFactory
from nexus_scalp.strategies.factory.store import (
    get_generation,
    list_candidates,
    list_events,
    list_failures,
    list_generations,
)


def make_record(key: str, decision_ts: datetime) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=decision_ts,
        strategy_id="strat_research",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_research",
            symbol="XAUUSD",
            session="ALL",
            regime="TRENDING" if int(key.split("_")[1]) % 2 else "RANGING",
            volatility_regime="HIGH",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
            values=[0.0] * CANONICAL_FEATURE_DIMENSION,
        ),
        action="BUY_MARKET",
        entry_reason="SMC_GOD_MODE",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )


def make_outcome(record: ExperienceRecord, realized_r: float) -> ExperienceOutcome:
    return ExperienceOutcome(
        idempotency_key=record.idempotency_key,
        execution_id=f"ticket_{record.idempotency_key}",
        outcome_timestamp=record.decision_timestamp + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="TP" if realized_r > 0 else "SL",
        realized_pnl_usd=realized_r * 100.0,
        realized_r_multiple=realized_r,
        approved_volume=0.1,
        behavior=PositionBehavior(
            mfe_r=max(0.5, realized_r) if realized_r > 0 else 0.2,
            mae_r=0.2,
            mae_points=2.0,
            mfe_points=5.0,
            expected_duration_sec=900.0,
            duration_sec=300.0,
        ),
        execution=ExecutionContext(),
        decomposition=OutcomeDecomposition(
            strategy_quality=0.5,
            entry_quality=0.4,
            position_management_quality=0.4,
            exit_quality=0.4,
            execution_quality=0.5,
            final_outcome_r=realized_r,
        ),
        behavioral_flags=[],
    )


def main() -> int:
    db_dir = tempfile.mkdtemp(prefix="factory_smoke_")
    db_path = os.path.join(db_dir, "smoke.db")
    repo = AuditRepository(db_url=f"sqlite:///{db_path}")

    # 1. Seed 90 experiences with alternating R (mostly positive).
    ledger = ExperienceLedger(audit_repo=repo)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(90):
        rec = make_record(f"smk_{i}", base + timedelta(minutes=30 * i))
        r = 0.35 if i % 5 != 4 else -0.5
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, realized_r=r))
    repo._queue.join()
    print("1. seeded 90 experiences OK")

    # 2. Research pipeline wiring.
    registry = StrategyRegistry(audit_repo=repo)
    dataset_builder = ResearchDatasetBuilder(ledger=ledger)
    pipeline = ResearchPipeline(dataset_builder=dataset_builder, registry=registry)
    dataset = dataset_builder.build()
    print(f"2. dataset samples={len(dataset.samples)}")

    # 3. Factory with a SMALL generation (20) for smoke speed.
    cfg = EvolutionConfig(
        generation_size=20,
        elite_size=4,
        max_generations=2,
        parallel_workers=1,
    )
    factory = StrategyFactory(
        audit_repo=repo,
        research_pipeline=pipeline,
        config=cfg,
    )

    # 4. create + generate + validate.
    gen = factory.create_generation(size=8, mode="MANUAL")
    print(f"3. generation {gen['generation_id']} created (target {gen['population_target']})")
    population = factory.generate_population(gen["generation_id"], size=8)
    print(f"4. population generated: {len(population)} candidates")
    assert len(population) > 0

    validation = factory.validate_population(population)
    print(f"5. structural: passed={len(validation['passed'])} failed={len(validation['failed'])}")

    # 5. Evaluate each structurally-passing candidate through the REAL pipeline.
    for c in validation["passed"]:
        res = factory.evaluate_candidate(c, dataset)
    repo._queue.join()

    # 6. Complete generation.
    completion = factory.complete_generation(gen["generation_id"])
    repo._queue.join()
    summary = completion.get("summary", {})
    print(
        f"6. generation complete: evaluated={summary.get('evaluated')} "
        f"validated={summary.get('validated')} rejected={summary.get('rejected')} "
        f"best={summary.get('best_score')}"
    )

    # 7. Verify persistence.
    gens = list_generations(repo)
    cands = list_candidates(repo, generation_id=gen["generation_id"])
    fails = list_failures(repo)
    events = list_events(repo)
    gen_row = get_generation(repo, gen["generation_id"])
    print(
        f"7. persisted: generations={len(gens)} candidates={len(cands)} "
        f"failures={len(fails)} events={len(events)} status={gen_row.get('status') if gen_row else '?'}"
    )
    assert gens and cands and gen_row
    assert gen_row["status"] == "COMPLETED"

    # 8. Loop control plane.
    assert factory.start_loop("AUTONOMOUS")
    assert factory.pause_loop()
    assert factory.resume_loop()
    assert factory.stop_loop()
    print("8. loop control plane OK (start/pause/resume/stop)")

    repo.close()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())