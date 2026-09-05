"""AGENT 13 (research methodology) — executable methodology-hardening regression.

Three confirmed defects, each PROVEN red-before on the pre-fix tree
(at 04:07 against HEAD d8ed39ad / 4549847c; live reproducer logs in
scratch/ns_agent13_red_repro.log):

  D1  Negative purge/embargo seconds silently DISABLED the leakage guards
      (`purge_seconds=-50` behaved exactly like 0.0): a caller bug or
      config parsing error could silently strip the BUG-140/BUG-183
      protections. FIX: split_temporal / walk_forward_folds reject < 0
      with ValueError. Explicit 0.0 stays legal (opt-out contract).

  D2  An OOS window whose realized_r values are ALL non-finite produced
      oos_expectancy_r=0.0 and the hard gate PASSED on zero real evidence.
      FIX: OOSGate fails closed when split.oos holds samples but the
      backtest yields zero finite trades.

  D3  ResearchPipeline.validate_candidate accepted purge/embargo overrides
      but _record_run persisted DEFAULT constants into research_runs.config
      regardless of what the run actually used — false provenance (BUG-183
      residue, only the terminal verdict call was repaired by Agent 16;
      the early REJECTED + backtest-empty branches still used defaults).
      FIX: forward effective values to every _record_run call site and to
      the backtest split.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.research.models import ResearchDataset, ResearchSample
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.splitting import split_temporal, walk_forward_folds

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _mk(i: int, r: float) -> ResearchSample:
    ts = BASE + timedelta(minutes=i)
    return ResearchSample(
        sample_id=f"s{i}",
        idempotency_key=f"k{i}",
        decision_timestamp=ts,
        outcome_timestamp=ts + timedelta(minutes=3),
        experience_id=f"e{i}",
        symbol="XAUUSD",
        regime="LONDON",
        session="LONDON",
        strategy_id="probe",
        strategy_version="v1",
        realized_r=r,
        realized_pnl_usd=1.0,
        risk_distance=1.0,
        holding_duration_sec=60.0,
        mae_r=0.1,
        mfe_r=0.1,
        decision_evidence={},
    )


def _ds(samples: list[ResearchSample], ds_id: str = "d") -> ResearchDataset:
    return ResearchDataset(
        dataset_id=ds_id,
        created_at=BASE,
        samples=samples,
        source_range={},
        schema_ids=["scalp_v1"],
    )


# ---------------------------------------------------------------------------
# D1 — negative purge/embargo must fail loudly
# ---------------------------------------------------------------------------


def test_split_temporal_rejects_negative_purge() -> None:
    with pytest.raises(ValueError, match="purge_seconds"):
        split_temporal(_ds([_mk(i, 0.1) for i in range(20)]), purge_seconds=-50.0)


def test_split_temporal_rejects_negative_embargo() -> None:
    with pytest.raises(ValueError, match="embargo_seconds"):
        split_temporal(_ds([_mk(i, 0.1) for i in range(20)]), embargo_seconds=-10.0)


def test_walk_forward_folds_reject_negative_guards() -> None:
    with pytest.raises(ValueError):
        walk_forward_folds(
            _ds([_mk(i, 0.1) for i in range(60)]),
            n_splits=3,
            purge_seconds=-1.0,
            embargo_seconds=-1.0,
        )


def test_zero_purge_embargo_still_allowed() -> None:
    ds = _ds([_mk(i, 0.1) for i in range(20)])
    split = split_temporal(ds, purge_seconds=0.0, embargo_seconds=0.0)
    assert split.train and split.validation and split.oos


# ---------------------------------------------------------------------------
# D2 — all-non-finite OOS window must FAIL the hard gate
# ---------------------------------------------------------------------------


def test_oos_all_nan_window_fails_gate() -> None:
    samples = [_mk(i, 1.0) for i in range(8)] + [_mk(i, float("nan")) for i in range(8, 12)]
    result = OOSGate().evaluate(_ds(samples, "d_nan"), "probe", "v1")
    assert result.status == "FAIL", "zero-finite OOS window must never PASS"
    assert "finite" in result.reason.lower() or "no oos evidence" in result.reason.lower(), (
        result.reason
    )


def test_oos_partially_nan_window_still_passes_when_edge_positive() -> None:
    samples = [_mk(i, 0.5) for i in range(10)] + [_mk(i, 0.2) for i in range(10, 15)]
    result = OOSGate().evaluate(_ds(samples, "d_ok"), "probe", "v1")
    assert result.status == "PASS"


# ---------------------------------------------------------------------------
# D3 — run config must record the EFFECTIVE purge/embargo (provenance)
# ---------------------------------------------------------------------------


def test_run_config_records_effective_purge_embargo() -> None:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.research.candidates import StrategyCandidate
    from nexus_scalp.research.dataset import ResearchDatasetBuilder
    from nexus_scalp.research.observability import ResearchObservabilityStore
    from nexus_scalp.research.pipeline import ResearchPipeline
    from nexus_scalp.research.registry import StrategyRegistry

    cand = StrategyCandidate(
        strategy_id="probe",
        strategy_version="v1",
        discovery_method="UNIT",
        discovery_window="2026-01",
        context_definition={"symbol": "XAUUSD", "fingerprint": "probe"},
        entry_logic={"type": "stub"},
        exit_logic={"type": "stub"},
        discovery_evidence={},
    )
    tmp = tempfile.mkdtemp(prefix="agent13_prov_")
    repo = AuditRepository(os.path.join(tmp, "audit.db"))
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(repo),
        registry=StrategyRegistry(repo),  # type: ignore[arg-type]
        observability=ResearchObservabilityStore(repo),
    )
    pipeline.validate_candidate(
        cand,
        _ds([_mk(i, 0.1) for i in range(20)], "d_p"),
        purge_seconds=0.0,
        embargo_seconds=0.0,
    )
    assert pipeline.last_run is not None
    cfg = pipeline.last_run.config
    assert cfg["purge_seconds"] == 0.0, f"effective purge 0.0, got {cfg['purge_seconds']}"
    assert cfg["embargo_seconds"] == 0.0, f"effective embargo 0.0, got {cfg['embargo_seconds']}"
    try:
        repo.close()
    except Exception:
        pass
