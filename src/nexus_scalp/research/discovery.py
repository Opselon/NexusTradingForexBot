"""
Candidate Discovery from Experience
===================================
PHASE 09B bounded, evidence-based discovery (spec 10 / 11).

Discovery groups closed experiences into MEANINGFUL context families using
coarse normalized ranges and a context fingerprint, so it produces pattern
families rather than one strategy per tiny numerical combination. It never
creates a candidate with fewer than a minimum support count.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.models import ResearchSample

#: A family must contain this many samples before we consider discovery.
MIN_FAMILY_SAMPLES: int = 20
#: Minimum discovery expectancy (R) to propose a candidate.
MIN_DISCOVERY_EXPECTANCY_R: float = 0.10


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
        if len(fam) < min_family_samples:
            continue
        expectancy = sum(x.realized_r for x in fam) / len(fam)
        if expectancy < min_expectancy_r:
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
            },
        )
        # Assign the content-derived immutable version.
        candidate = candidate.model_copy(update={"strategy_version": candidate.canonical_version()})
        candidates.append(candidate)
    return candidates


def _id(fingerprint: str) -> str:
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:10].upper()


def _window_str(fam: list[ResearchSample]) -> str:
    if not fam:
        return ""
    ordered = sorted(fam, key=lambda s: s.decision_timestamp)
    return f"{ordered[0].decision_timestamp.date()}..{ordered[-1].decision_timestamp.date()}"
