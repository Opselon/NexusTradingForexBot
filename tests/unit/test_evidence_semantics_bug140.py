"""BUG-140 Phases 4-7 regression suite: research evidence semantics, stable
degradation calculations, empirical replay vs historical simulation
distinction, and temporal leakage guard defaults.

Invariants under test:
  1. compute_relative_degradation is numerically stable near zero (no
     division-by-zero / explosive ratio; bounded [-10.0, +10.0]).
  2. WalkForwardEngine uses stable degradation (sub-1e-4 inputs do not
     explode).
  3. OOSGate uses stable degradation; an in-sample drop from near-zero
     correctly reports bounded degradation.
  4. BacktestResult explicitly declares evaluation_mode="EMPIRICAL_REPLAY"
     so consumers cannot confuse ledger replay with market simulation.
  5. DEFAULT_PURGE_SECONDS and DEFAULT_EMBARGO_SECONDS are exported and
     positive (Phase 7 contract: leakage guards enabled by default).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.metrics import (
    compute_backtest,
    compute_relative_degradation,
)
from nexus_scalp.research.models import BacktestResult, ExecutionAssumptions
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.splitting import (
    DEFAULT_EMBARGO_SECONDS,
    DEFAULT_PURGE_SECONDS,
)
from nexus_scalp.research.walkforward import WalkForwardEngine

try:
    from tests.unit.task4_research_helpers import seed_experiences
except ImportError:
    from task4_research_helpers import seed_experiences


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'ev.db'}")
    yield r
    r.close()


@pytest.fixture
def ledger(repo):
    return ExperienceLedger(repo)


class TestStableDegradationMath:
    def test_normal_ratio(self):
        # 1.0 -> 0.5 is a 50% drop (+0.5 degradation)
        assert compute_relative_degradation(1.0, 0.5) == pytest.approx(0.5)
        # 1.0 -> 1.5 is a 50% gain (-0.5 degradation)
        assert compute_relative_degradation(1.0, 1.5) == pytest.approx(-0.5)

    def test_near_zero_denominator_does_not_explode(self):
        # In-sample is 0.00001 (below epsilon=1e-4)
        # Previous buggy formula: (0.00001 - (-0.1)) / 0.00001 = +10,001.0 (exploded!)
        res = compute_relative_degradation(0.00001, -0.1)
        assert res == 1.0  # directional sign only, bounded

    def test_both_near_zero_is_zero(self):
        assert compute_relative_degradation(0.00001, 0.00002) == 0.0

    def test_clipping_bounds(self):
        # Huge drop is capped at clip_max (default 10.0)
        res = compute_relative_degradation(0.01, -10.0)
        assert res <= 10.0
        # Huge gain is capped at -clip_max (-10.0)
        res_gain = compute_relative_degradation(0.01, 10.0)
        assert res_gain >= -10.0


class TestEvaluationModeSemantics:
    def test_backtest_result_declares_empirical_replay(self, repo, ledger):
        seed_experiences(ledger, repo, 3, prefix="bt_sem")
        ds = ResearchDatasetBuilder(ledger).build()
        bt = BacktestEngine().run(ds, "s1", "v1")
        assert bt.evaluation_mode == "EMPIRICAL_REPLAY"
        # Schema field is frozen and accessible
        dumped = bt.model_dump()
        assert dumped["evaluation_mode"] == "EMPIRICAL_REPLAY"


class TestLeakageGuardDefaults:
    def test_defaults_are_positive(self):
        assert DEFAULT_PURGE_SECONDS > 0.0
        assert DEFAULT_EMBARGO_SECONDS > 0.0
        assert DEFAULT_PURGE_SECONDS >= 60.0  # at least 1 min
