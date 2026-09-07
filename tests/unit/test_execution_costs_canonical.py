"""Canonical execution-cost assumptions tests (money-path PHASE 2).

Pins the ONE-SOURCE-OF-TRUTH contract: configs/execution_assumptions.json is
the single calibrated artifact; the loader is fail-closed (missing/invalid ->
error, never a silent fallback to per-module defaults); the research bridge
and the provenance stamp carry the calibration version.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.configuration.execution_costs import (
    CANONICAL_PATH,
    ENV_OVERRIDE,
    ExecutionCostsError,
    friction_provenance,
    get_execution_assumptions,
    load_execution_assumptions,
    to_research_assumptions,
)


def test_canonical_artifact_exists_and_loads() -> None:
    assert CANONICAL_PATH.exists(), f"canonical artifact missing: {CANONICAL_PATH}"
    costs = get_execution_assumptions()
    assert costs.schema_id == "execution_assumptions_v1"
    assert costs.symbol == "XAUUSD"
    assert costs.calibration_version, "calibration_version must be recorded"


def test_measured_spread_values_are_pinned() -> None:
    """The spread block must match the REAL measured calibration (909 bars)."""
    costs = get_execution_assumptions()
    assert costs.spread.p50 == pytest.approx(0.14)
    assert costs.spread.mean == pytest.approx(0.147)
    assert costs.spread.p95 == pytest.approx(0.24)
    assert costs.spread.max_observed == pytest.approx(0.37)
    assert costs.spread.paper_baseline_band == (0.08, 0.18)


def test_labeling_friction_is_derived_not_independent() -> None:
    """The labeling friction is the canonical value (0.35 USD/oz)."""
    costs = get_execution_assumptions()
    assert costs.labeling.friction_usd_per_oz == pytest.approx(0.35)


def test_missing_artifact_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ExecutionCostsError):
        load_execution_assumptions(tmp_path / "does_not_exist.json")


def test_invalid_artifact_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    with pytest.raises(ExecutionCostsError):
        load_execution_assumptions(bad)


def test_env_override_respected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    costs = get_execution_assumptions()
    payload = json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))
    payload["calibration_version"] = "CAL-TEST-OVERRIDE"
    alt = tmp_path / "alt.json"
    alt.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(ENV_OVERRIDE, str(alt))
    overridden = load_execution_assumptions()
    assert overridden.calibration_version == "CAL-TEST-OVERRIDE"
    # sanity: base artifact unchanged
    assert costs.calibration_version != "CAL-TEST-OVERRIDE"


def test_research_bridge_converts_usd_to_ticks() -> None:
    costs = get_execution_assumptions()
    ra, cal_version = to_research_assumptions(costs)
    # mean spread 0.147 usd / 0.01 tick = 15 ticks (rounded)
    assert ra.spread_ticks == pytest.approx(15.0)
    # paper p95 slippage 0.05 / 0.01 = 5 ticks
    assert ra.slippage_ticks == pytest.approx(5.0)
    assert ra.pay_spread is True
    assert cal_version == costs.calibration_version


def test_provenance_stamp_carries_calibration_version() -> None:
    costs = get_execution_assumptions()
    prov = friction_provenance(costs)
    assert prov["execution_cost_calibration_version"] == costs.calibration_version
    assert prov["friction_usd_per_oz"] == str(costs.labeling.friction_usd_per_oz)
