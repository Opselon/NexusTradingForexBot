"""Canonical execution-cost assumptions (money-path PHASE 2 / 2B).

ONE SOURCE OF TRUTH for spread / slippage / commission / swap / execution
timing. The canonical artifact is ``configs/execution_assumptions.json``
(tracked, versioned, calibrated from REAL broker/ledger evidence — see the
``broker_context`` block inside). Every consumer (labeling, backtesting,
paper execution, feasibility checks) must derive its cost parameters from
HERE — never from its own hardcoded defaults.

Provenance discipline (PHASE 2B): every load returns the parsed artifact
INCLUDING its ``calibration_version``; consumers that produce training or
evaluation artifacts must record that version in the artifact (the triple-
barrier labeler stamps it into ``label_config`` provenance via
``friction_provenance()``; research engines stamp it via
``to_research_assumptions()``). Models trained under different calibration
versions are not comparable — the promotion gate must compare
``calibration_version`` before comparing metrics.

Fail-closed: if the canonical file is missing or invalid, loading raises
``ExecutionCostsError`` — consumers must never silently fall back to their
own defaults (that is exactly the three-independent-cost-models defect this
module exists to end).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Repo-root canonical artifact (resolved from this file's location).
CANONICAL_PATH = Path(__file__).resolve().parents[3] / "configs" / "execution_assumptions.json"
#: Env override for tests / alternative deployments (must point at a file
#: with the SAME schema; silent fallback is forbidden by design).
ENV_OVERRIDE = "NEXUS_EXECUTION_ASSUMPTIONS"

CANONICAL_SPREAD = "0.08-0.18c paper band inside REAL measured 7-37c spread"


class ExecutionCostsError(RuntimeError):
    """The canonical execution-cost artifact is missing or invalid."""


class SpreadAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    unit: str = "usd_per_oz"
    p50: float = Field(ge=0.0)
    mean: float = Field(ge=0.0)
    p95: float = Field(ge=0.0)
    max_observed: float = Field(ge=0.0)
    paper_baseline_band: tuple[float, float]
    note: str = ""


class SlippageAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    unit: str = "usd_per_oz"
    stop_close_adverse_band: tuple[float, float]
    paper_measured_p95: float = Field(ge=0.0)
    paper_measured_worst: float = Field(ge=0.0)
    note: str = ""


class CommissionAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_lot_usd: float = Field(ge=0.0)
    evidence: str = ""


class SwapAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_lot_per_night_usd: float
    evidence: str = ""


class ExecutionTimingAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    latency_ms_typical: float = Field(ge=0.0)
    latency_ms_p95_paper: float = Field(ge=0.0)
    latency_note: str = ""
    bar_close_decision_convention: str = ""


class LabelingAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    friction_usd_per_oz: float = Field(ge=0.0)
    derivation: str = ""


class UnitsAssumptions(BaseModel):
    """Instrument units block (audit finding: paper vs replay tick_value disagree)."""

    model_config = ConfigDict(frozen=True)

    point_size: float = Field(gt=0.0)
    digits: int = Field(ge=0)
    contract_size: float = Field(gt=0.0)
    tick_value_note: str = ""
    lot_volume_min: float = Field(gt=0.0)
    lot_volume_step: float = Field(gt=0.0)


class RViewAssumptions(BaseModel):
    """The R-based friction views (labeling vs baseline_eval vs backtest cap)."""

    model_config = ConfigDict(frozen=True)

    labeling_friction_r: float = Field(ge=0.0)
    derivation: str = ""
    baseline_eval_friction_r: float = Field(ge=0.0)
    baseline_eval_note: str = ""
    backtest_friction_cap_r: float = Field(gt=0.0)
    backtest_cap_note: str = ""


class SyntheticSpreadAssumptions(BaseModel):
    """The synthetic bar-tick spread used by dataset/replay/warmup builders."""

    model_config = ConfigDict(frozen=True)

    value: float = Field(ge=0.0)
    sites: str = ""
    parity_note: str = ""


class PaperModelAssumptions(BaseModel):
    """Paper adapter cost model (spread band, slippage ranges, no comm/swap)."""

    model_config = ConfigDict(frozen=True)

    spread_band_usd: tuple[float, float]
    fx_spread: float = Field(ge=0.0)
    entry_slippage_usd: tuple[float, float]
    stop_close_slippage_usd: tuple[float, float]
    commission: float
    swap: float
    note: str = ""


class ExecutionCostAssumptions(BaseModel):
    """Typed view of the canonical artifact (frozen — treat as immutable)."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    schema_id: str = Field(alias="schema")
    version: str
    calibration_version: str
    symbol: str
    broker_context: dict[str, Any]
    spread: SpreadAssumptions
    slippage: SlippageAssumptions
    commission: CommissionAssumptions
    swap: SwapAssumptions
    execution_timing: ExecutionTimingAssumptions
    labeling: LabelingAssumptions
    consumers: dict[str, str] = Field(default_factory=dict)
    #: PHASE-2B audit enrichment (additive, optional for backward compat with
    #: older artifacts): units block, R-based friction views, the synthetic
    #: bar-spread convention, and the paper adapter's cost model.
    units: UnitsAssumptions | None = None
    r_view: RViewAssumptions | None = None
    synthetic_bar_spread_usd: SyntheticSpreadAssumptions | None = None
    paper_model: PaperModelAssumptions | None = None

    def friction_r_per_trade(self, risk_r_usd: float = 1.0) -> float:
        """Labeling friction expressed in R (the benchmark scoring convention)."""
        if risk_r_usd <= 0:
            raise ExecutionCostsError("risk_r_usd must be > 0")
        return float(self.labeling.friction_usd_per_oz / risk_r_usd)


def load_execution_assumptions(
    path: Path | str | None = None, *, refresh: bool = False
) -> ExecutionCostAssumptions:
    """Load and validate the canonical execution-cost artifact.

    Resolution order: explicit ``path`` argument > ``NEXUS_EXECUTION_ASSUMPTIONS``
    env override > repo-root ``configs/execution_assumptions.json``. Missing or
    invalid artifacts raise ``ExecutionCostsError`` (fail-closed: consumers
    must NOT fall back to private defaults).
    """
    resolved = (
        Path(path) if path is not None else Path(os.environ.get(ENV_OVERRIDE, "") or CANONICAL_PATH)
    )
    if not resolved.exists():
        raise ExecutionCostsError(
            f"canonical execution assumptions missing: {resolved} — "
            "refusing to fall back to per-module hardcoded costs"
        )
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExecutionCostsError(f"invalid JSON in {resolved}: {exc}") from exc
    try:
        return ExecutionCostAssumptions.model_validate(raw)
    except Exception as exc:
        raise ExecutionCostsError(f"invalid execution assumptions in {resolved}: {exc}") from exc


@lru_cache(maxsize=4)
def _cached(path_str: str, mtime: float) -> ExecutionCostAssumptions:
    return ExecutionCostAssumptions.model_validate(
        json.loads(Path(path_str).read_text(encoding="utf-8"))
    )


def get_execution_assumptions() -> ExecutionCostAssumptions:
    """Process-wide cached accessor (mtime-guarded; test-friendly via env)."""
    resolved = Path(os.environ.get(ENV_OVERRIDE, "") or CANONICAL_PATH)
    if not resolved.exists():
        raise ExecutionCostsError(
            f"canonical execution assumptions missing: {resolved} — "
            "refusing to fall back to per-module hardcoded costs"
        )
    return _cached(str(resolved), resolved.stat().st_mtime)


def to_research_assumptions(costs: ExecutionCostAssumptions, price_tick: float = 0.01):
    """Bridge to the research engine's ``ExecutionAssumptions`` (ticks).

    Research engines consume spread/slippage in TICKS of ``price_tick``;
    the canonical artifact is in USD/oz. Conversion: usd / price_tick.
    The calibration version rides along for provenance stamping.
    """
    from nexus_scalp.research.models import ExecutionAssumptions

    if price_tick <= 0:
        raise ExecutionCostsError("price_tick must be > 0")
    spread_ticks = round(costs.spread.mean / price_tick)
    slippage_ticks = round(costs.slippage.paper_measured_p95 / price_tick)
    return ExecutionAssumptions(
        spread_ticks=float(spread_ticks),
        slippage_ticks=float(slippage_ticks),
        price_tick=price_tick,
        pay_spread=True,
    ), costs.calibration_version


def friction_provenance(costs: ExecutionCostAssumptions) -> dict[str, str]:
    """Provenance block the labeler/trainer must stamp into artifacts."""
    return {
        "execution_cost_calibration_version": costs.calibration_version,
        "friction_usd_per_oz": str(costs.labeling.friction_usd_per_oz),
        "artifact": "configs/execution_assumptions.json",
    }


__all__ = [
    "CANONICAL_PATH",
    "ENV_OVERRIDE",
    "ExecutionCostAssumptions",
    "ExecutionCostsError",
    "friction_provenance",
    "get_execution_assumptions",
    "load_execution_assumptions",
    "to_research_assumptions",
]
