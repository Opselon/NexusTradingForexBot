"""AGENT 17 (CHG-0063) — OOS hard-gate adversarial regression suite.

Covers the confirmed defect + surrounding attack surface:

  RC-1 (FIXED here): OOSGate.evaluate with a context contract that matches
  ZERO samples must FAIL (CONTEXT_CONTRACT_EMPTY_POPULATION) — never silently
  widen to the global population. Red-before repro: London-negative book +
  Asian-positive OOS tail + typo'd session contract produced PASS 0.73R on
  the wrong population.

  + temporal split determinism/disjointness, purge/embargo boundaries,
    zero-OOS FAIL, negative OOS FAIL, degradation ceiling, NaN-R exclusion,
    train<oos strict ordering, valid-scope PASS preserved.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from nexus_scalp.research.models import ResearchDataset, ResearchSample
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.splitting import split_temporal

BASE = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)


def _mk(i: int, r: float, session: str = "LONDON") -> ResearchSample:
    return ResearchSample(
        sample_id=f"s_{i}",
        experience_id=f"e_{i}",
        idempotency_key=f"k_{i}",
        strategy_id="strat_t",
        symbol="XAUUSD",
        decision_timestamp=BASE + timedelta(minutes=int(i)),
        outcome_timestamp=BASE + timedelta(minutes=int(i) + 1),
        session=session,
        regime="TRENDING",
        trend_state="UP",
        volatility_regime="EXPANSION",
        realized_r=float(r),
        realized_pnl_usd=float(r) * 10.0,
        risk_distance=1.0,
    )


def _ds(samples: list[ResearchSample], ds_id: str = "ds_t") -> ResearchDataset:
    return ResearchDataset(
        dataset_id=ds_id,
        created_at=datetime.now(UTC),
        samples=list(samples),
        source_range={"start": "2026-08-01", "end": "2026-08-01"},
        schema_ids=["scalp_v3"],
    )


# ---------------------------------------------------------------------------
# RC-1 regression (the confirmed defect)
# ---------------------------------------------------------------------------


def test_oos_empty_contract_match_fails_never_global_fallback() -> None:
    """London-negative book + Asian-positive OOS tail + typo contract.

    The declared population (LONDON) is uniformly negative -> the only honest
    verdict is FAIL. The pre-fix gate silently evaluated the GLOBAL population
    whose temporal OOS tail was the Asian block -> false PASS 0.73R.
    """
    samples = [_mk(i, -0.4) for i in range(100)] + [
        _mk(100 + i, 0.9, session="ASIAN") for i in range(20)
    ]
    ds = _ds(samples, "ds_adv")
    res = OOSGate().evaluate(ds, "strat_t", "1.0.0", context_contract={"sessions": ["LONDONN"]})
    assert res.status == "FAIL"
    assert "CONTEXT_CONTRACT_EMPTY_POPULATION" in res.reason
    diag = res.context_diagnostics or {}
    assert diag.get("matched_samples") == 0
    assert diag.get("sufficient_evidence") is False
    assert res.oos_samples == 0


def test_oos_matching_contract_still_evaluates_scoped_population() -> None:
    """A genuinely matching contract still scopes (not blocked by RC-1 fix)."""
    samples = [_mk(i, 0.5) for i in range(100)]
    ds = _ds(samples, "ds_ok")
    res = OOSGate().evaluate(ds, "strat_t", "1.0.0", context_contract={"sessions": ["LONDON"]})
    assert res.status == "PASS"
    assert (res.context_diagnostics or {}).get("matched_samples") == 100


# ---------------------------------------------------------------------------
# Temporal split semantics
# ---------------------------------------------------------------------------


def test_split_temporal_deterministic_disjoint_chronological() -> None:
    samples = [_mk(i, 0.3 if i < 60 else -0.2) for i in range(100)]
    ds = _ds(samples)
    s1 = split_temporal(ds, val_frac=0.2, oos_frac=0.2, purge_seconds=300, embargo_seconds=60)
    s2 = split_temporal(ds, val_frac=0.2, oos_frac=0.2, purge_seconds=300, embargo_seconds=60)
    assert [x.sample_id for x in s1.oos] == [x.sample_id for x in s2.oos]
    tr = {x.sample_id for x in s1.train}
    va = {x.sample_id for x in s1.validation}
    oo = {x.sample_id for x in s1.oos}
    assert not (tr & va) and not (tr & oo) and not (va & oo)
    assert all(a.decision_timestamp <= b.decision_timestamp for a, b in zip(s1.oos, s1.oos[1:]))


def test_split_temporal_purges_boundary_crossing_horizon() -> None:
    samples = [_mk(i, 0.4) for i in range(100)]
    samples[59] = ResearchSample(
        sample_id="s_59",
        experience_id="e_59",
        idempotency_key="k_59",
        strategy_id="strat_t",
        symbol="XAUUSD",
        decision_timestamp=BASE + timedelta(minutes=59),
        outcome_timestamp=BASE + timedelta(minutes=74),
        session="LONDON",
        regime="TRENDING",
        trend_state="UP",
        volatility_regime="EXPANSION",
        realized_r=0.4,
        realized_pnl_usd=4.0,
        risk_distance=1.0,
    )
    sx = split_temporal(
        _ds(samples), val_frac=0.2, oos_frac=0.2, purge_seconds=300, embargo_seconds=60
    )
    assert "s_59" not in {t.sample_id for t in sx.train}


def test_split_temporal_train_strictly_before_oos() -> None:
    sb = split_temporal(_ds([_mk(i, 0.4) for i in range(100)]), val_frac=0.2, oos_frac=0.2)
    assert max(t.decision_timestamp for t in sb.train) < min(o.decision_timestamp for o in sb.oos)


# ---------------------------------------------------------------------------
# Gate verdict semantics
# ---------------------------------------------------------------------------


def test_oos_zero_samples_fails() -> None:
    res = OOSGate().evaluate(
        _ds([_mk(i, 0.4) for i in range(4)]), "strat_t", "1.0.0", oos_frac=0.0
    )
    assert res.status == "FAIL" and res.oos_samples == 0


def test_oos_negative_expectancy_fails() -> None:
    res = OOSGate().evaluate(
        _ds([_mk(i, 0.5 if i < 80 else -0.4) for i in range(100)]), "strat_t", "1.0.0"
    )
    assert res.status == "FAIL" and res.oos_expectancy_r < 0


def test_oos_healthy_pass_and_degradation_ceiling() -> None:
    good = OOSGate().evaluate(
        _ds([_mk(i, 0.5 if i < 80 else 0.3) for i in range(100)]), "strat_t", "1.0.0"
    )
    assert good.status == "PASS"
    degraded = OOSGate().evaluate(
        _ds([_mk(i, 1.0 if i < 80 else 0.4) for i in range(100)]), "strat_t", "1.0.0"
    )
    assert degraded.status == "PASS"  # rel. degradation 0.6 <= 1.0 ceiling
    worse = OOSGate().evaluate(
        _ds([_mk(i, 1.0 if i < 80 else -0.2) for i in range(100)]), "strat_t", "1.0.0"
    )
    assert worse.status == "FAIL"


def test_oos_nan_realized_r_never_reaches_metrics() -> None:
    samples = [_mk(i, 0.5 if i < 80 else 0.3) for i in range(100)]
    samples[95] = ResearchSample(
        sample_id="s_95",
        experience_id="e_95",
        idempotency_key="k_95",
        strategy_id="strat_t",
        symbol="XAUUSD",
        decision_timestamp=BASE + timedelta(minutes=95),
        outcome_timestamp=BASE + timedelta(minutes=96),
        session="LONDON",
        regime="TRENDING",
        trend_state="UP",
        volatility_regime="EXPANSION",
        realized_r=float("nan"),
        realized_pnl_usd=0.0,
        risk_distance=1.0,
    )
    res = OOSGate().evaluate(_ds(samples), "strat_t", "1.0.0")
    assert math.isfinite(res.oos_expectancy_r)
