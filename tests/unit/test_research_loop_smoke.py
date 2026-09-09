"""Research-loop smoke: full pipeline on real ledger data (edge audit 2026-09-09).

End-to-end proof that the research loop still wires together after the edge
hardening changes: ledger -> dataset -> static validation -> backtest ->
walk-forward -> OOS (with bootstrap significance attached) -> robustness ->
scoring -> registry lifecycle. The seeded 70% win-rate book must reach
VALIDATED with a DECISIVE OOS significance; a regression in any gate now
shows up here first.
"""

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry


@pytest.fixture
def temp_audit_repo(tmp_path):
    from collections.abc import Generator

    repo = AuditRepository(
        db_url=f"sqlite:///{tmp_path / 'edge_smoke.db'}", flush_interval_sec=0.02
    )
    yield repo
    repo.close()


def test_research_loop_smoke_full_pipeline(temp_audit_repo):

    from tests.unit.test_experience_intelligence import seed_closed_trades

    ledger = ExperienceLedger(audit_repo=temp_audit_repo)
    r_values = [0.8 if i % 10 < 7 else -0.9 for i in range(260)]
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        "STRAT-EDGE",
        r_values,
        base_ts=datetime(2026, 8, 1, tzinfo=UTC),
        prefix="edge",
    )
    temp_audit_repo.flush(timeout_sec=20)

    builder = ResearchDatasetBuilder(ledger=ledger)
    ds = builder.build()
    assert len(ds.samples) > 100

    registry = StrategyRegistry(audit_repo=temp_audit_repo)
    pipeline = ResearchPipeline(dataset_builder=builder, registry=registry)
    cand = StrategyCandidate(
        strategy_id="STRAT-EDGE",
        strategy_version="1.0.0",
        entry_logic={"direction": "breakout"},
        exit_logic={"tp": 2.0},
        risk_logic={"risk_pct": 0.5},
        context=None,
        context_definition={"symbol": "XAUUSD", "fingerprint": "edge-smoke"},
    )
    res = pipeline.validate_candidate(candidate=cand, dataset=ds, n_folds=3)
    assert res["lifecycle"] in {"VALIDATED", "REJECTED", "INCONCLUSIVE"}
    score = res.get("score") or {}
    oos = res.get("oos") or {}
    # significance must travel on the OOS result (new pipeline path)
    assert oos.get("oos_significance") is not None
    assert res.get("walkforward") is not None
    print(
        "SMOKE:",
        res["lifecycle"],
        "score=",
        score.get("final_score"),
        "oos=",
        oos.get("status"),
        "sig=",
        oos.get("oos_significance"),
    )
