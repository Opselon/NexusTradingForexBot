"""
Shadow Trading & Champion Evaluation Engine
===========================================
PHASE 11 production-safe parallel model evaluation (spec 1 / 2).

The Challenger is SHADOW-ONLY: it evaluates the SAME live market state as the
production Champion but has ZERO order authority. This package:

    * loads + verifies a validated Challenger (hash/schema/dimension/classes),
    * runs it in parallel on the Champion's feature vector,
    * records every decision as SHADOW / SIMULATED,
    * compares Champion vs Challenger across regime/strategy/session,
    * computes an explainable promotion evaluation with hard vetoes.

It holds no adapter, no order manager and no risk engine - it cannot place,
modify or close an order, and it can never replace the Champion automatically.

Modules:
    models.py       immutable shadow contracts (decisions, runs, comparisons)
    store.py        append-only shadow persistence (runs/decisions/comparisons)
    challenger.py   shadow-only model runtime (integrity + schema-safe infer)
    comparison.py   multi-dimension comparer + promotion eval + vetoes
    engine.py       bounded wiring: runtime + comparer + store
    worker.py       isolated background shadow-aggregation worker
"""

from nexus_scalp.shadow.models import (
    PromotionEvaluation,
    ShadowComparison,
    ShadowDecisionKind,
    ShadowDecisionRecord,
    ShadowEvidenceStatus,
    ShadowModelRef,
    ShadowRun,
    SharedInputRef,
)

__all__ = [
    "PromotionEvaluation",
    "ShadowComparison",
    "ShadowDecisionKind",
    "ShadowDecisionRecord",
    "ShadowEvidenceStatus",
    "ShadowModelRef",
    "ShadowRun",
    "SharedInputRef",
]
