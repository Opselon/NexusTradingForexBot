"""AI provider ecosystem (Nexus Scalp Engine).

A production-grade, switchable evidence layer for POSITION MANAGEMENT: an
open position's hold/close/reduce/TP/SL question is put to NSE's own ML, to
System One, to OpenRouter -- or to a subset -- through ONE canonical contract.

THE ONLY SAFETY RULE (Section 1)
    An external provider ADVISES. It never executes. The deterministic policy
    layer scores the evidence, the risk engine gates it, the broker validates
    it, and execution remains the sole authority. No provider can place an
    order, size one, widen a stop beyond policy, bypass a risk limit, invent
    account values, invent market data, fabricate a position state, or claim an
    execution that MT5 did not confirm.

The pipeline:

    POSITION SNAPSHOT -> canonical request -> [providers..] -> normalized
    responses -> DETERMINISTIC POLICY -> RISK GATE -> execution eligibility

Public surface (what UI, CLI and the engine import):

    ProviderOrchestrator  -- the backend source of truth for decisions
    ADAPTER_TYPES         -- the provider catalogue
    PositionDecisionRequest / PositionDecisionResponse -- the contracts
"""

from __future__ import annotations

from nexus_scalp.ai_providers.adapters import ADAPTER_TYPES
from nexus_scalp.ai_providers.contract import (
    AIProviderAction,
    CONTRACT_VERSION,
    DecisionEvidence,
    PositionDecisionRequest,
    PositionDecisionResponse,
    SlProposal,
    TpProposal,
)
from nexus_scalp.ai_providers.errors import (
    PERMANENT_CATEGORIES,
    RETRYABLE_CATEGORIES,
    ProviderError,
    ProviderErrorCategory,
)
from nexus_scalp.ai_providers.registry import (
    ActivationState,
    DecisionMode,
    ProviderConfig,
    ProviderRegistryStore,
)
from nexus_scalp.ai_providers.templates import TEMPLATE_VERSION

__all__ = [
    "ADAPTER_TYPES",
    "AIProviderAction",
    "CONTRACT_VERSION",
    "ActivationState",
    "DecisionEvidence",
    "DecisionMode",
    "PERMANENT_CATEGORIES",
    "PositionDecisionRequest",
    "PositionDecisionResponse",
    "ProviderConfig",
    "ProviderError",
    "ProviderErrorCategory",
    "ProviderRegistryStore",
    "RETRYABLE_CATEGORIES",
    "SlProposal",
    "TEMPLATE_VERSION",
    "TpProposal",
]
