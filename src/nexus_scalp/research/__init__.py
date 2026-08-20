"""
Strategy Research, Backtesting & Validation Engine
===================================================
PHASE 09B -- Evidence-Driven Strategy Discovery with Hard OOS / Robustness Gates.

This package builds a professional research layer that consumes the trustworthy
Phase 08 experience ledger (NOT a parallel trade database) and enforces a strict
research process:

    EXPERIENCE -> RESEARCH -> CANDIDATE -> BACKTEST -> WALK-FORWARD
        -> OUT-OF-SAMPLE -> ROBUSTNESS -> STATISTICAL SCORE
        -> VALIDATED STRATEGY -> SHADOW / OPERATOR-APPROVED

Model layout::

    models.py      immutable research domain contracts (samples, candidates,
                   results, runs, registry rows)
    dataset.py     ResearchDatasetBuilder: causal-safe dataset construction
                   from the immutable experience ledger
    splitting.py   TemporalSplitter + WalkForwardSplitter with purge/embargo
    leakage.py     leakage guards: fit-on-train-only transforms, embargo/purge
    backtest.py    deterministic, friction-aware backtest over recorded trades
    metrics.py     pure performance/risk statistics (expectancy, PF, DD, MAE/MFE)
    walkforward.py walk-forward validation engine (temporal folds)
    oos.py         hard out-of-sample gate
    robustness.py  spread/slippage/latency/perturbation stress engine
    scoring.py     explainable multi-dimensional Strategy Validation Score
    candidates.py  StrategyCandidate contract + deterministic versioning
    discovery.py   candidate discovery from experience (bounded grouping)
    registry.py    Strategy Registry persistence + lifecycle transitions
    lifecycle.py   research lifecycle state machine
    pipeline.py    orchestrator: dataset -> discovery -> gates -> registry
    worker.py      isolated, restart-safe background research worker
    store.py       bounded read facade over the research tables

SAFETY CONTRACT
---------------
Research NEVER places, modifies or closes an order, and NEVER bypasses
RiskEngine / OrderManager. It holds no adapter, no order manager and no risk
engine. A candidate can never become LIVE automatically; promotion is a
deliberate, operator-gated action on the production side.
"""

from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import discover_candidates
from nexus_scalp.research.evidence import (
    EvidenceArtifact,
    EvidenceKind,
    FailureClass,
    GateStatus,
    GateType,
    ResearchEvent,
    ResearchGate,
    ResearchRunSnapshot,
    RunOutcome,
    RunStatus,
    WorkerHealth,
    build_run_snapshot,
    stable_digest,
)
from nexus_scalp.research.observability import ResearchObservabilityStore
from nexus_scalp.research.lifecycle import (
    LifecycleError,
    approve_for_live,
    can_transition,
    transition,
)
from nexus_scalp.research.models import (
    BacktestResult,
    CandidateLifecycle,
    ExecutionAssumptions,
    OOSResult,
    ResearchDataset,
    ResearchRun,
    ResearchSample,
    RobustnessResult,
    StrategyRegistryEntry,
    StrategyScore,
    WalkForwardResult,
)
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.robustness import RobustnessEngine
from nexus_scalp.research.scoring import compute_strategy_score
from nexus_scalp.research.walkforward import WalkForwardEngine
from nexus_scalp.research.worker import ResearchWorker, format_research_worker_status

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "CandidateLifecycle",
    "ExecutionAssumptions",
    "LifecycleError",
    "OOSGate",
    "OOSResult",
    "ResearchDataset",
    "ResearchDatasetBuilder",
    "ResearchObservabilityStore",
    "ResearchGate",
    "ResearchEvent",
    "ResearchRunSnapshot",
    "EvidenceArtifact",
    "EvidenceKind",
    "FailureClass",
    "GateStatus",
    "GateType",
    "RunOutcome",
    "RunStatus",
    "WorkerHealth",
    "build_run_snapshot",
    "stable_digest",
    "ResearchPipeline",
    "ResearchRun",
    "ResearchSample",
    "ResearchWorker",
    "RobustnessEngine",
    "RobustnessResult",
    "StrategyCandidate",
    "StrategyRegistry",
    "StrategyRegistryEntry",
    "StrategyScore",
    "WalkForwardEngine",
    "WalkForwardResult",
    "approve_for_live",
    "can_transition",
    "compute_strategy_score",
    "discover_candidates",
    "format_research_worker_status",
    "transition",
]
