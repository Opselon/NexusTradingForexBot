"""Bounded anti-forgetting replay policy tests (money-path PHASE 5).

Pins the deterministic bounded-replay composition of TrainingDatasetBuilder:
recent experience at full weight, older experience replayed through
(regime, outcome-sign) strata quotas with a replay weight, identical input ->
identical subset + dataset id.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexus_scalp.model_lifecycle.dataset import _apply_bounded_replay
from nexus_scalp.model_lifecycle.models import TrainingDatasetRow


def _row(i: int, regime: str = "TREND", outcome_r: float = 1.0) -> TrainingDatasetRow:
    return TrainingDatasetRow(
        sample_id=f"s{i}",
        experience_id=f"e{i}",
        idempotency_key=f"k{i}",
        decision_timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
        feature_vector=[0.0] * 50,
        label=1,
        regime=regime,
        outcome_r=outcome_r,
    )


def _rows(n: int, regimes=("TREND", "RANGE"), outcomes=(1.0, -1.0)) -> list:
    rows = []
    for i in range(n):
        rows.append(
            _row(
                i,
                regime=regimes[i % len(regimes)],
                outcome_r=outcomes[i % len(outcomes)],
            )
        )
    return rows


def test_recent_window_kept_full_weight() -> None:
    rows = _rows(1000)
    out = _apply_bounded_replay(rows, {"enabled": True, "recent_fraction": 0.6, "min_recent": 500})
    recent = rows[400:]
    assert out[-600:] == recent
    assert all(r.sample_weight == 1.0 for r in out[-600:])


def test_older_rows_bounded_per_stratum_and_weighted() -> None:
    rows = _rows(1000)
    policy = {"enabled": True, "recent_fraction": 0.6, "stratum_quota": 50, "replay_weight": 0.5}
    out = _apply_bounded_replay(rows, policy)
    replayed = out[:-600]
    # 2 regimes x 2 outcome signs = 4 strata; quota 50 each => <= 200 older rows
    assert len(replayed) <= 4 * 50
    assert all(r.sample_weight == 0.5 for r in replayed)


def test_deterministic_and_order_preserved() -> None:
    rows = _rows(800)
    a = _apply_bounded_replay(rows, {"enabled": True})
    b = _apply_bounded_replay(rows, {"enabled": True})
    assert a == b
    ts = [r.decision_timestamp for r in a]
    assert ts == sorted(ts)


def test_all_older_tail_classes_represented() -> None:
    # big winners and big losers in the old tail must survive the quota
    rows = _rows(600)
    rows[0] = _row(0, regime="CRISIS", outcome_r=-3.0)
    rows[1] = _row(1, regime="CRISIS", outcome_r=3.0)
    out = _apply_bounded_replay(rows, {"enabled": True, "stratum_quota": 10})
    regimes = {r.regime for r in out}
    assert "CRISIS" in regimes


def test_disabled_policy_is_identity() -> None:
    rows = _rows(100)
    assert _apply_bounded_replay(rows, {"enabled": False}) == rows


def test_empty_rows_identity() -> None:
    assert _apply_bounded_replay([], {"enabled": True}) == []
