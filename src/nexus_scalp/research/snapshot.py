"""
Canonical Strategy Lifecycle Snapshot (UI Read Model)
===================================================
Phase 1 implementation of the Strategy Command Center read model.

Aggregates authoritative data from `StrategyRegistryEntry`, research runs, and
the execution eligibility rules without duplicating domain state or mutating
source-of-truth tables.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry


class ExecutionEligibility(BaseModel):
    model_config = ConfigDict(frozen=True)

    can_trade: bool = Field(default=False)
    eligibility_state: str = Field(default="BLOCKED")  # YES | NO | SHADOW_ONLY | BLOCKED | UNKNOWN
    reason: str = Field(default="")
    required_gate: str = Field(default="")
    blockers: list[str] = Field(default_factory=list)


class StrategyLifecycleSnapshot(BaseModel):
    """
    The canonical read-side representation consumed by the Strategy Command Center UI.
    Backed strictly by authoritative domain models.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    current_state: str = Field(default=CandidateLifecycle.DISCOVERED.value)
    previous_state: str = Field(default="")
    lifecycle_history: list[str] = Field(default_factory=list)

    current_gate: str = Field(default="")
    next_gate: str = Field(default="")

    execution_eligibility: ExecutionEligibility = Field(default_factory=ExecutionEligibility)

    research_summary: dict[str, Any] = Field(default_factory=dict)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    risk_summary: dict[str, Any] = Field(default_factory=dict)

    ai_influence: dict[str, Any] = Field(
        default_factory=lambda: {
            "status": "PARTIALLY_MEASURABLE",
            "measured_contributions": ["ai_hypothesis_generation"],
            "unmeasured_contributions": ["parameter_selection_influence"],
            "attribution_records": [],
        }
    )
    evidence_summary: dict[str, Any] = Field(default_factory=dict)

    health_score: dict[str, float] = Field(default_factory=dict)
    confidence_score: float = Field(default=0.0)
    data_quality_score: float = Field(default=0.0)
    stability_score: float = Field(default=0.0)

    active_alerts: list[str] = Field(default_factory=list)
    debug_summary: dict[str, Any] = Field(default_factory=dict)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    transition_history: list[dict[str, Any]] = Field(default_factory=list)

    #: Honest Strategy-DNA lineage. Only fields that exist in the authoritative
    #: registry entry are populated. Descendants require a cross-entry registry
    #: scan that is NOT in scope of the per-strategy inspector read path, so the
    #: flag `descendants_recorded` stays False and the UI shows
    #: "LINEAGE PARTIALLY RECORDED" rather than inventing children.
    lineage_dna: dict[str, Any] = Field(
        default_factory=lambda: {
            "parent_strategy_ids": [],
            "generation": None,
            "mutation_note": "",
            "descendants": [],
            "descendants_recorded": False,
        }
    )


def build_snapshot(entry: StrategyRegistryEntry) -> StrategyLifecycleSnapshot:
    """
    Constructs a canonical StrategyLifecycleSnapshot from a authoritative StrategyRegistryEntry.
    """
    lc = entry.lifecycle

    # Determine eligibility
    eligible_for_trades = entry.is_eligible_for_new_trades
    elig_state = "BLOCKED"
    reason = ""
    blockers = []
    required_gate = ""

    if lc == CandidateLifecycle.ACTIVE:
        if eligible_for_trades:
            elig_state = "YES"
            reason = "Strategy is ACTIVE and trade-eligible in production."
        else:
            elig_state = "BLOCKED"
            reason = "Active strategy is in an ineligible state."
            blockers.append("state_ineligible")
    elif lc == CandidateLifecycle.SHADOW:
        elig_state = "SHADOW_ONLY"
        reason = "Strategy is in shadow/paper evaluation; live capital routing disabled."
        required_gate = "operator_active_promotion"
    elif lc == CandidateLifecycle.VALIDATED:
        elig_state = "BLOCKED"
        reason = "Validation gates passed, but execution eligibility requires shadow evaluation or operator promotion to ACTIVE."
        required_gate = "shadow_or_active_promotion"
        blockers.append("awaiting_shadow_or_promotion")
    else:
        elig_state = "BLOCKED"
        reason = f"Strategy lifecycle {lc.value} has not reached validation gates."
        required_gate = "validation_and_shadow"
        blockers.append(f"lifecycle_at_{lc.value.lower()}")

    # Evidence summary
    bt = entry.backtest
    wf = entry.walkforward
    oos = entry.oos
    rob = entry.robustness
    score = entry.score

    evidence = {
        "sample_count": entry.sample_count,
        "backtest_status": "PASS" if bt and bt.total_trades > 0 else "MISSING",
        "walkforward_status": "PASS" if wf and wf.passed else ("FAIL" if wf else "MISSING"),
        "oos_status": oos.status if oos else "MISSING",
        "robustness_status": rob.status if rob else "MISSING",
        "score_verdict": score.verdict if score else "MISSING",
    }

    # Health score decomposition
    health = {}
    if score:
        health = {
            "performance": score.performance_score,
            "risk": score.risk_score,
            "stability": score.stability_score,
            "oos": score.oos_score,
            "robustness": score.robustness_score,
            "sample_confidence": score.sample_confidence,
            "regime_coverage": score.regime_coverage,
            "recency": score.recency_score,
            "execution_resilience": score.execution_resilience,
            "degradation": score.degradation_score,
            "final": score.final_score,
        }

    # Transition history parsing
    transitions = []
    for line in entry.validation_lineage:
        # Lineage entries are "<iso-ts>:<STATE>[:<reason>]" (see
        # StrategyRegistry.transition_lifecycle). ISO timestamps contain
        # colons, so we anchor on the known lifecycle state names instead of
        # naive splitting.
        matched_state = ""
        for st in CandidateLifecycle:
            marker = f":{st.value}"
            if marker in line:
                matched_state = st.value
                break
        if matched_state:
            idx = line.find(f":{matched_state}")
            ts = line[:idx]
            rem = line[idx + len(matched_state) + 1 :]
            detail = rem[1:] if rem.startswith(":") else rem
            transitions.append(
                {
                    "timestamp": ts,
                    "state": matched_state,
                    "detail": detail,
                }
            )
        else:
            transitions.append({"timestamp": "", "state": line, "detail": ""})

    prev_state = transitions[-2]["state"] if len(transitions) >= 2 else ""

    return StrategyLifecycleSnapshot(
        strategy_id=entry.strategy_id,
        strategy_version=entry.strategy_version,
        current_state=lc.value,
        previous_state=prev_state,
        lifecycle_history=[t["state"] for t in transitions],
        current_gate=lc.value,
        next_gate="SHADOW"
        if lc == CandidateLifecycle.VALIDATED
        else ("ACTIVE" if lc == CandidateLifecycle.SHADOW else "NONE"),
        execution_eligibility=ExecutionEligibility(
            can_trade=(elig_state in ("YES", "SHADOW_ONLY")),
            eligibility_state=elig_state,
            reason=reason,
            required_gate=required_gate,
            blockers=blockers,
        ),
        research_summary={
            "discovery_source": entry.discovery_source,
            "discovery_window": entry.discovery_window,
            "context_definition": entry.context_definition,
        },
        validation_summary={
            "backtest_trades": bt.total_trades if bt else 0,
            "backtest_expectancy": bt.expectancy_r if bt else 0.0,
            "oos_expectancy": oos.oos_expectancy_r if oos else 0.0,
        },
        risk_summary={
            "max_drawdown_usd": bt.max_drawdown_usd if bt else 0.0,
        },
        evidence_summary=evidence,
        health_score=health,
        confidence_score=entry.confidence,
        data_quality_score=score.sample_confidence if score else 0.0,
        stability_score=score.stability_score if score else 0.0,
        transition_history=transitions,
        lineage_dna={
            "parent_strategy_ids": list(entry.parent_strategy_ids or []),
            "generation": entry.strategy_version,
            "mutation_note": "",
            "descendants": [],
            "descendants_recorded": False,
        },
    )
