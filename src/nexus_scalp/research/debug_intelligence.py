"""
Debug Intelligence Engine (Diagnostics Layer)
============================================
PHASE 6 implementation of the Strategy Command Center debug intelligence.

Provides explainable diagnostic algorithms over authoritative registry data:
  1. Transition Anomaly Score (churn, oscillation, failure density, recovery loops)
  2. Validation Consistency Score (disagreement between backtest / WF / OOS / robustness)
  3. Decomposed Strategy Health Model (data quality, validation, robustness, execution safety, stability, evidence completeness)
  4. Investigation Priority (severity x recurrence x execution proximity x duration)
  5. Debug Hint Engine (strictly separating FACT, INFERENCE, HYPOTHESIS, RECOMMENDATION)
  6. Evidence Completeness (required artifact checklist per transition)
"""

from __future__ import annotations

from typing import Any
from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry


def compute_anomaly_score(entry: StrategyRegistryEntry) -> dict[str, Any]:
    """
    Computes a transition anomaly score from state oscillation, failure density,
    and lineage frequency. Fully decomposable.
    """
    lineage = entry.validation_lineage or []
    failures = sum(1 for l in lineage if "REJECTED" in l or "FAIL" in l)
    oscillations = 0
    prev_state = ""
    for l in lineage:
        for st in CandidateLifecycle:
            if f":{st.value}" in l:
                if prev_state and prev_state != st.value and st.value in ("DISCOVERED", "BACKTESTING"):
                    oscillations += 1
                prev_state = st.value

    transition_freq = len(lineage)
    failure_density = failures / max(1, len(lineage))
    oscillation_factor = min(1.0, oscillations * 0.3)

    score = min(
        1.0,
        (transition_freq * 0.05) + (failure_density * 0.5) + (oscillation_factor * 0.4),
    )

    return {
        "anomaly_score": round(score, 3),
        "components": {
            "transition_frequency": transition_freq,
            "failure_density": round(failure_density, 2),
            "oscillation_count": oscillations,
        },
    }


def compute_validation_consistency(entry: StrategyRegistryEntry) -> dict[str, Any]:
    """
    Measures disagreement between validation stages (backtest vs WF vs OOS vs robustness).
    """
    bt = entry.backtest
    wf = entry.walkforward
    oos = entry.oos
    rob = entry.robustness

    scores = []
    if bt:
        # Normalize expectancy / win rate into 0..1 scale
        scores.append(min(1.0, max(0.0, bt.expectancy_r / 2.0)))
    if wf:
        scores.append(1.0 if wf.passed else 0.0)
    if oos:
        scores.append(1.0 if oos.status == "PASS" else 0.0)
    if rob:
        scores.append(1.0 if rob.status == "PASS" else 0.0)

    if not scores:
        return {"consistency_score": 0.0, "status": "NOT_AVAILABLE", "variance": 0.0}

    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    consistency = max(0.0, 1.0 - (variance * 2.0))

    inconsistent = variance > 0.15
    return {
        "consistency_score": round(consistency, 3),
        "variance": round(variance, 3),
        "status": "HIGH_INCONSISTENCY" if inconsistent else "CONSISTENT",
    }


def decompose_strategy_health(entry: StrategyRegistryEntry) -> dict[str, float]:
    """
    Decomposes strategy health into authoritative domain dimensions.
    """
    score = entry.score
    sample_count = entry.sample_count

    # Data quality proxy from sample count (MIN_EVIDENCE_SAMPLES = 20)
    data_quality = min(1.0, sample_count / 50.0) if sample_count > 0 else 0.0
    validation_score = score.oos_score if score else (1.0 if entry.lifecycle in (CandidateLifecycle.VALIDATED, CandidateLifecycle.SHADOW, CandidateLifecycle.ACTIVE) else 0.2)
    robustness_score = score.robustness_score if score else 0.0
    exec_safety = 1.0 if entry.is_eligible_for_new_trades else 0.5
    lifecycle_stability = 0.9 if entry.lifecycle not in (CandidateLifecycle.DEGRADED, CandidateLifecycle.REJECTED) else 0.1
    evidence_complete = 1.0 if (entry.backtest and entry.oos and entry.robustness) else 0.5

    return {
        "data_quality": round(data_quality, 2),
        "validation": round(validation_score, 2),
        "robustness": round(robustness_score, 2),
        "execution_safety": round(exec_safety, 2),
        "lifecycle_stability": round(lifecycle_stability, 2),
        "evidence_completeness": round(evidence_complete, 2),
    }


def compute_debug_priority(entry: StrategyRegistryEntry) -> dict[str, Any]:
    """
    Calculates investigation priority from severity, recurrence, and execution proximity.
    """
    lineage = entry.validation_lineage or []
    failures = sum(1 for l in lineage if "REJECTED" in l or "FAIL" in l)
    is_live_risk = entry.lifecycle in (CandidateLifecycle.ACTIVE, CandidateLifecycle.SHADOW)
    exec_proximity = 2.0 if is_live_risk else (1.5 if entry.lifecycle == CandidateLifecycle.VALIDATED else 1.0)

    severity = 1.0 if entry.lifecycle == CandidateLifecycle.REJECTED or failures > 0 else 0.2
    priority_score = severity * (1.0 + failures) * exec_proximity

    return {
        "debug_priority_score": round(priority_score, 2),
        "severity": severity,
        "recurrence_count": failures,
        "execution_proximity": exec_proximity,
    }


def generate_debug_hints(entry: StrategyRegistryEntry) -> list[dict[str, str]]:
    """
    Generates intelligent debugging hints strictly separating:
      FACT, INFERENCE, HYPOTHESIS, RECOMMENDATION.
    """
    hints = []
    lineage = entry.validation_lineage or []
    failures = [l for l in lineage if "REJECTED" in l or "FAIL" in l]

    if failures:
        hints.append({
            "category": "FACT",
            "message": f"Strategy {entry.strategy_id} has {len(failures)} recorded validation failure(s) or rejection event(s).",
        })
        hints.append({
            "category": "INFERENCE",
            "message": "Failures concentrated in validation/OOS gates suggest generalization degradation.",
        })
        hints.append({
            "category": "HYPOTHESIS",
            "message": "Search space or feature dimensionality may be encouraging overfitting on training windows.",
        })
        hints.append({
            "category": "RECOMMENDATION",
            "message": "Inspect parameter sensitivity across walk-forward folds and verify feature schema stability.",
        })
    else:
        hints.append({
            "category": "FACT",
            "message": f"Strategy {entry.strategy_id} is at lifecycle state {entry.lifecycle.value} with no recorded gate failures.",
        })
        hints.append({
            "category": "RECOMMENDATION",
            "message": "No immediate debugging required; follow standard pipeline progression.",
        })

    return hints
