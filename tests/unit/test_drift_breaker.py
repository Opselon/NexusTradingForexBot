"""Regression tests: feature-drift circuit breaker (mission P0 item 7A).

Covers:
- INSUFFICIENT_DATA before the reference distribution is set (never a
  fabricated NORMAL)
- INSUFFICIENT_DATA below the canonical sample floor (no tiny-sample trips)
- reference wiring from a real scaler-shaped .npz (dimension-exact slice)
- NORMAL / DRIFT_WARNING / DRIFT_CRITICAL aggregation over canonical alerts
- update() feeding is fail-inert on malformed vectors
- breaker never mutates execution state (data-only contract)
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from nexus_scalp.risk.drift_breaker import (
    STATE_DRIFT_CRITICAL,
    STATE_DRIFT_WARNING,
    STATE_INSUFFICIENT_DATA,
    STATE_NORMAL,
    FeatureDriftBreaker,
)
from nexus_scalp.shadow.shadow70.health import Shadow70DriftMonitor
from nexus_scalp.shadow.shadow70.models import LIQUIDITY_FEATURE_NAMES


def _seed_reference(mon: Shadow70DriftMonitor, mean: float = 0.0, std: float = 1.0) -> None:
    n = len(LIQUIDITY_FEATURE_NAMES)
    mon.set_reference([mean] * n, [std] * n)


def _vec(liq_value: float) -> list[float]:
    """A 70D vector whose liquidity slice carries the given scalar."""
    v = [0.0] * 70
    for i in range(60, 70):
        v[i] = liq_value
    return v


class TestDriftBreakerStates:
    def test_no_reference_is_insufficient(self) -> None:
        br = FeatureDriftBreaker()
        for _ in range(200):
            br.update(_vec(0.5))
        assert br.state() == STATE_INSUFFICIENT_DATA

    def test_below_sample_floor_is_insufficient(self) -> None:
        mon = Shadow70DriftMonitor(min_samples=50)
        br = FeatureDriftBreaker(mon)
        _seed_reference(mon)
        for _ in range(30):
            br.update(_vec(0.5))
        assert br.state() == STATE_INSUFFICIENT_DATA

    def test_normal_state_within_reference(self) -> None:
        # 400 samples: measured identical-distribution PSI p90 = 0.053
        # (see docs/psi_small_sample_note.md) — far below WATCH 0.10, so a
        # stable window must read NORMAL without small-n noise trips.
        mon = Shadow70DriftMonitor(min_samples=20)
        br = FeatureDriftBreaker(mon)
        _seed_reference(mon, mean=0.5, std=0.2)
        rng = random.Random(2026)
        for _ in range(400):
            br.update(_vec(rng.gauss(0.5, 0.2)))
        assert br.state() == STATE_NORMAL

    def test_critical_drift_on_huge_mean_shift(self) -> None:
        mon = Shadow70DriftMonitor(min_samples=20)
        br = FeatureDriftBreaker(mon)
        _seed_reference(mon, mean=0.0, std=0.1)
        rng = random.Random(99)
        for _ in range(400):
            br.update(_vec(rng.gauss(5.0, 0.1)))  # 50 sigma shift
        assert br.state() in (STATE_DRIFT_CRITICAL, STATE_DRIFT_WARNING)
        snap = br.snapshot()
        assert snap["reference_set"] is True
        assert snap["alerts"], "critical drift must carry alert evidence"

    def test_update_fail_inert_on_bad_vectors(self) -> None:
        mon = Shadow70DriftMonitor(min_samples=20)
        br = FeatureDriftBreaker(mon)
        _seed_reference(mon)
        assert br.update([1.0, 2.0]) is False  # wrong width
        assert br.update("not-a-list") is False  # garbage (exception path)
        assert br.update(_vec(1.0)) is True

    def test_breaker_is_data_only(self) -> None:
        """No method mutates anything beyond its own monitor buffers."""
        mon = Shadow70DriftMonitor(min_samples=20)
        br = FeatureDriftBreaker(mon)
        _seed_reference(mon)
        for _ in range(40):
            br.update(_vec(0.5))
        assert br.state() in (STATE_NORMAL, STATE_DRIFT_WARNING, STATE_DRIFT_CRITICAL)
        # mode/execution semantics are NOT this module's concern: verify the
        # breaker surface carries no execution/mode vocabulary at all.
        import inspect

        src = inspect.getsource(type(br))
        assert "set_execution_mode" not in src
        assert "kill_switch" not in src
        assert "_running" not in src


class TestScalerReferenceWiring:
    def test_reference_from_real_shaped_scaler(self, tmp_path) -> None:
        n = 70
        mean = np.full(n, 0.5)
        std = np.full(n, 0.2)
        p = tmp_path / "model.scaler.npz"
        np.savez(p, mean=mean, std=std)
        mon = Shadow70DriftMonitor(min_samples=20)
        br = FeatureDriftBreaker(mon)
        ok = br.set_reference_from_scaler(p)
        assert ok is True
        rng = random.Random(2026)
        for _ in range(400):
            br.update(_vec(rng.gauss(0.5, 0.2)))
        assert br.state() == STATE_NORMAL

    def test_missing_scaler_is_honest(self, tmp_path) -> None:
        mon = Shadow70DriftMonitor(min_samples=20)
        br = FeatureDriftBreaker(mon)
        assert br.set_reference_from_scaler(tmp_path / "nope.npz") is False
        assert br.state() == STATE_INSUFFICIENT_DATA

    def test_narrow_scaler_refused(self, tmp_path) -> None:
        p = tmp_path / "model.scaler.npz"
        np.savez(p, mean=np.full(50, 0.5), std=np.full(50, 0.2))
        mon = Shadow70DriftMonitor(min_samples=20)
        br = FeatureDriftBreaker(mon)
        assert br.set_reference_from_scaler(p) is False
