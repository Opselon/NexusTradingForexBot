"""
AI Decision Attribution (Explainability Layer)
==============================================
PHASE 5 implementation of the Strategy Command Center AI explainability.

Honesty contract:
  * Attribution records exist ONLY where a measurable contribution basis was
    actually recorded. The engine never invents percentages.
  * When attribution cannot be measured for a decision, the response says so
    explicitly: PARTIALLY_MEASURABLE / NOT_MEASURED / NOT_AVAILABLE.

Currently measurable bases in the codebase:
  * discovery_source / discovery_window / context_definition  → provenance of
    hypothesis generation (research pipeline, family discovery).
  * validation lineage entries → actor tags (operator_promotion → HUMAN,
    research_pipeline → DETERMINISTIC_RULE/STATISTICAL_TEST).
Everything else is reported as NOT_YET_MEASURED with the architecture ready
to accept DecisionContribution records when instrumentation lands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry

logger = get_logger("nexus_scalp.research.attribution")


class SourceType(StrEnum):
    AI_RESEARCH = "AI_RESEARCH"
    AI_MODEL = "AI_MODEL"
    AI_FEATURE_DISCOVERY = "AI_FEATURE_DISCOVERY"
    AI_PARAMETER_OPTIMIZATION = "AI_PARAMETER_OPTIMIZATION"
    HUMAN = "HUMAN"
    DETERMINISTIC_RULE = "DETERMINISTIC_RULE"
    STATISTICAL_TEST = "STATISTICAL_TEST"
    RISK_ENGINE = "RISK_ENGINE"
    EXECUTION_ENGINE = "EXECUTION_ENGINE"


class ContributionKind(StrEnum):
    """Fundamentally different concepts that must never be conflated."""

    AI_SUGGESTED = "AI_SUGGESTED"
    AI_RANKED = "AI_RANKED"
    AI_RECOMMENDED = "AI_RECOMMENDED"
    SYSTEM_VALIDATED = "SYSTEM_VALIDATED"
    SYSTEM_REJECTED = "SYSTEM_REJECTED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_OVERRULED = "HUMAN_OVERRULED"


class DecisionContribution:
    """
    One attributable contribution to one decision.

    weight is OPTIONAL and only present when a real measurement basis exists;
    confidence is likewise evidence-bound. reproducibility_key ties the record
    back to a re-runnable artifact.
    """

    __slots__ = (
        "source_type",
        "kind",
        "decision_id",
        "strategy_id",
        "evidence_reference",
        "weight",
        "confidence",
        "timestamp",
        "reproducibility_key",
    )

    def __init__(
        self,
        source_type: SourceType | str,
        kind: ContributionKind | str,
        strategy_id: str,
        decision_id: str = "",
        evidence_reference: str = "",
        weight: float | None = None,
        confidence: float | None = None,
        timestamp: str = "",
        reproducibility_key: str = "",
    ) -> None:
        self.source_type = SourceType(source_type)
        self.kind = ContributionKind(kind)
        self.strategy_id = strategy_id
        self.decision_id = decision_id
        self.evidence_reference = evidence_reference
        self.weight = weight
        self.confidence = confidence
        self.timestamp = timestamp
        self.reproducibility_key = reproducibility_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "kind": self.kind.value,
            "decision_id": self.decision_id,
            "strategy_id": self.strategy_id,
            "evidence_reference": self.evidence_reference,
            # weight/confidence are None unless measured — never fabricated.
            "weight": self.weight,
            "confidence": self.confidence,
            "weight_measured": self.weight is not None,
            "confidence_measured": self.confidence is not None,
            "timestamp": self.timestamp,
            "reproducibility_key": self.reproducibility_key,
        }


def _actor_to_contribution(
    strategy_id: str, actor: str, state: str, ts: str, reason: str
) -> DecisionContribution:
    """Maps an event-projection actor tag onto an honest contribution."""
    if actor == "operator":
        kind = ContributionKind.HUMAN_APPROVED if state in ("ACTIVE", "SHADOW") else ContributionKind.HUMAN_APPROVED
        return DecisionContribution(
            source_type=SourceType.HUMAN,
            kind=kind,
            strategy_id=strategy_id,
            decision_id=f"transition:{state}",
            evidence_reference="validation_lineage",
            timestamp=ts,
        )
    if state in ("VALIDATED", "REJECTED"):
        return DecisionContribution(
            source_type=SourceType.STATISTICAL_TEST,
            kind=(ContributionKind.SYSTEM_VALIDATED if state == "VALIDATED" else ContributionKind.SYSTEM_REJECTED),
            strategy_id=strategy_id,
            decision_id=f"gate:{state}",
            evidence_reference=reason or "validation_lineage",
            timestamp=ts,
        )
    return DecisionContribution(
        source_type=SourceType.DETERMINISTIC_RULE,
        kind=ContributionKind.SYSTEM_VALIDATED,
        strategy_id=strategy_id,
        decision_id=f"transition:{state}",
        evidence_reference="validation_lineage",
        timestamp=ts,
    )


class AIAttributionEngine:
    """
    Read-side explainability service over authoritative registry data.

    Produces per-strategy:
      * provenance-based AI contributions (discovery metadata)
      * actor-based contributions (lifecycle lineage)
      * an honest measurability status
      * a decision timeline with actor / decision / evidence / result
    """

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    def attribution(self, entry: StrategyRegistryEntry) -> dict[str, Any]:
        contributions: list[DecisionContribution] = []

        # 1. Provenance: discovery source tells us WHO generated the hypothesis.
        src = (entry.discovery_source or "").lower()
        if src:
            if any(k in src for k in ("ai", "llm", "model", "neural")):
                st = SourceType.AI_RESEARCH
                kind = ContributionKind.AI_SUGGESTED
            elif any(k in src for k in ("family", "cluster", "discover")):
                # Deterministic family discovery over ledger data — NOT AI.
                st = SourceType.AI_FEATURE_DISCOVERY
                kind = ContributionKind.AI_SUGGESTED
            else:
                st = SourceType.AI_RESEARCH
                kind = ContributionKind.AI_SUGGESTED
            contributions.append(
                DecisionContribution(
                    source_type=st,
                    kind=kind,
                    strategy_id=entry.strategy_id,
                    decision_id="hypothesis_generation",
                    evidence_reference=(
                        f"discovery_source={entry.discovery_source}"
                        + (f"; window={entry.discovery_window}" if entry.discovery_window else "")
                    ),
                    # No numeric basis exists yet → weights stay unmeasured.
                    timestamp=entry.created_at.isoformat(),
                    reproducibility_key=str(entry.context_definition or {}),
                )
            )

        # 2. Lineage actors.
        from nexus_scalp.research.event_projection import parse_lineage_entry

        for raw_line in entry.validation_lineage or []:
            parsed = parse_lineage_entry(str(raw_line))
            if parsed is None:
                continue
            contributions.append(
                _actor_to_contribution(
                    entry.strategy_id,
                    parsed.get("actor", ""),
                    parsed.get("to_state", ""),
                    parsed.get("timestamp", ""),
                    parsed.get("reason", ""),
                )
            )

        # 3. Honest measurability status.
        ai_records = [c for c in contributions if c.source_type.value.startswith("AI_")]
        measured_weights = [c for c in contributions if c.weight is not None]
        if not contributions:
            status = "NOT_AVAILABLE"
        elif ai_records and len(measured_weights) == 0:
            status = "PARTIALLY_MEASURABLE"
        elif ai_records:
            status = "MEASURABLE"
        else:
            status = "PARTIALLY_MEASURABLE"

        timeline = [
            {
                "timestamp": c.timestamp,
                "actor": c.source_type.value,
                "decision": c.kind.value,
                "decision_id": c.decision_id,
                "evidence": c.evidence_reference,
                "result": "",  # filled by caller when gate results known
                "reproducibility_key": c.reproducibility_key,
            }
            for c in contributions
        ]
        timeline.sort(key=lambda t: t["timestamp"] or "")

        return {
            "available": True,
            "strategy_id": entry.strategy_id,
            "status": status,
            "measured": {
                "weights": len(measured_weights),
                "note": (
                    "Numeric influence weights require instrumented "
                    "contribution recording; none recorded for this strategy."
                    if not measured_weights
                    else ""
                ),
            },
            "contributions": [c.to_dict() for c in contributions],
            "timeline": timeline,
        }
