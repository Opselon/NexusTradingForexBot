"""
Candidate Discovery from Experience
====================================
PHASE 09B bounded, evidence-based discovery (spec 10 / 11).

Discovery groups closed experiences into MEANINGFUL context families using
coarse normalized ranges and a context fingerprint, so it produces pattern
families rather than one strategy per tiny numerical combination. It never
creates a candidate with fewer than a minimum support count.

TASK-4 (data-integrity forensics):
  * Family construction stays coarse and deterministic (symbol | timeframe |
    session | regime | volatility_regime | trend_state) — never strategy_id,
    never exact 50D equality.
  * `family_distribution()` reports family sizes (largest / median / smallest,
    floor rejections) so fragmentation is measurable, not assumed.
  * A candidate discovered from a family carries the family's sample ids
    (`discovery_evidence.sample_ids`): validation can then restrict every gate
    to the candidate's OWN family instead of the whole heterogeneous dataset
    (family-select validation, TASK-4).
  * One economic trade = one economic observation: the dataset builder already
    collapses duplicate idempotency keys and only executed+closed outcomes
    enter; discovery never re-counts fills.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence

from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.models import ResearchSample

#: A family must contain this many samples before we consider discovery.
MIN_FAMILY_SAMPLES: int = 20
#: Minimum discovery expectancy (R) to propose a candidate.
MIN_DISCOVERY_EXPECTANCY_R: float = 0.10
#: Absolute floor below which a family is never a candidate even at
#: SMALL_SAMPLE tier (mirrors models.SMALL_SAMPLE_FLOOR).
SMALL_SAMPLE_FLOOR: int = 8


def _context_fingerprint(s: ResearchSample) -> str:
    """Coarse, bounded context fingerprint (spec 10: normalized ranges)."""
    parts = [
        s.symbol,
        s.timeframe,
        s.session,
        s.regime,
        s.volatility_regime,
        s.trend_state,
    ]
    return "|".join(parts)


def family_distribution(
    samples: Sequence[ResearchSample],
    min_family_samples: int = MIN_FAMILY_SAMPLES,
) -> dict[str, object]:
    """
    Deterministic family census: sizes, largest/median/smallest, and how many
    families fall under the sample floor (with the exact count).

    Returns a JSON-safe dict:
        families, family_sizes (desc), largest, median, smallest,
        families_above_floor, families_below_floor,
        samples_in_below_floor_families, total_samples.
    """
    groups: dict[str, list[ResearchSample]] = defaultdict(list)
    for s in samples:
        groups[_context_fingerprint(s)].append(s)
    sizes = sorted((len(v) for v in groups.values()), reverse=True)
    above = sum(1 for v in groups.values() if len(v) >= min_family_samples)
    below = len(groups) - above
    below_samples = sum(len(v) for v in groups.values() if len(v) < min_family_samples)
    median = sizes[len(sizes) // 2] if sizes else 0
    return {
        "families": len(groups),
        "family_sizes": sizes,
        "largest": sizes[0] if sizes else 0,
        "median": median,
        "smallest": sizes[-1] if sizes else 0,
        "families_above_floor": above,
        "families_below_floor": below,
        "samples_in_below_floor_families": below_samples,
        "total_samples": len(samples),
    }


def discover_candidates(
    samples: list[ResearchSample],
    min_family_samples: int = MIN_FAMILY_SAMPLES,
    min_expectancy_r: float = MIN_DISCOVERY_EXPECTANCY_R,
    dataset_id: str = "",
    discovery_window: str = "",
) -> list[StrategyCandidate]:
    """
    Discovers candidate strategy families from causally-safe samples.

    Returns candidates (status DISCOVERED) with deterministic identity. No OOS
    result is consulted here (spec 27: discovery/validation boundary).
    """
    groups: dict[str, list[ResearchSample]] = defaultdict(list)
    for s in samples:
        groups[_context_fingerprint(s)].append(s)

    candidates: list[StrategyCandidate] = []
    for fingerprint, fam in groups.items():
        if len(fam) < SMALL_SAMPLE_FLOOR:
            continue  # below the absolute discovery floor
        expectancy = _safe_mean([s.realized_r for s in fam])
        if not expectancy or expectancy < min_expectancy_r:
            continue
        symbol = fam[0].symbol
        context = {
            "fingerprint": fingerprint,
            "symbol": symbol,
            "timeframe": fam[0].timeframe,
            "session": fam[0].session,
            "regime": fam[0].regime,
            "volatility_regime": fam[0].volatility_regime,
            "trend_state": fam[0].trend_state,
        }
        entry_logic = {
            "direction": "directional",
            "context": fingerprint,
            "regime_gate": fam[0].regime,
        }
        exit_logic = {"mode": "SL_TP", "risk_model": "fixed_stop"}
        risk_assumptions = {
            "min_expectancy_r": min_expectancy_r,
            "sample_floor": min_family_samples,
        }
        # TASK-4 two-tier discovery: families at/above the small-sample floor
        # but below the standard floor are still DISCOVERED (tier SMALL_SAMPLE);
        # validation gates independently require the evidence floor, so no
        # threshold weakening is introduced here.
        tier = "STANDARD"
        if len(fam) < min_family_samples:
            tier = "SMALL_SAMPLE"

        strategy_id = f"STRAT-{_id(fingerprint)}"
        candidate = StrategyCandidate(
            strategy_id=strategy_id,
            strategy_version="",  # set below via canonical_version
            feature_schema_id=fam[0].feature_schema_id,
            feature_dimension=fam[0].feature_dimension,
            source_dataset_id=dataset_id,
            discovery_window=discovery_window or _window_str(fam),
            context_definition=context,
            entry_logic=entry_logic,
            exit_logic=exit_logic,
            risk_assumptions=risk_assumptions,
            parent_strategy_ids=[],
            discovery_method="context_family",
            lifecycle="DISCOVERED",  # type: ignore[arg-type]
            discovery_evidence={
                "samples": len(fam),
                "expectancy_r": round(float(expectancy), 6),
                "win_rate": round(float(sum(1 for x in fam if x.realized_r > 0)) / len(fam), 4),
                "fingerprint": fingerprint,
                "tier": tier,
                # TASK-4: the exact economic observations behind this candidate.
                # Validation MUST restrict its gates to these samples.
                "sample_ids": sorted(s.idempotency_key for s in fam),
            },
        )
        # Assign the content-derived immutable version.
        candidate = candidate.model_copy(update={"strategy_version": candidate.canonical_version()})
        candidates.append(candidate)
    return candidates


def _safe_mean(values: Iterable[float]) -> float | None:
    """Mean of finite values; None when no finite values exist."""
    acc = [float(v) for v in values if isinstance(v, (int, float))]
    acc = [v for v in acc if math.isfinite(v)]
    return (sum(acc) / len(acc)) if acc else None


def _id(fingerprint: str) -> str:
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:10].upper()


def _window_str(fam: list[ResearchSample]) -> str:
    if not fam:
        return ""
    ordered = sorted(fam, key=lambda s: s.decision_timestamp)
    return f"{ordered[0].decision_timestamp.date()}..{ordered[-1].decision_timestamp.date()}"
