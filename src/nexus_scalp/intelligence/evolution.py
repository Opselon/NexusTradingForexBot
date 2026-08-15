"""
Strategy Evolution Engine
=========================
PHASE 09 controlled, evidence-based strategy variation discovery.

The system must NOT grow by blindly minting new signals. It grows by:

    Historical Experience -> Pattern Discovery -> Strategy Candidate
        -> Backtest -> Validation -> Memory (operator-promoted)

A discovered candidate is a hypothesis with supporting evidence. It NEVER
affects live trading until backtested and validated. The engine produces
candidates; promotion to real strategy memory is a separate, explicit,
operator-gated action.

SAFETY: the evolution engine holds no adapter, no order manager and no risk
engine. It reads the experience ledger and writes candidate rows only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.intelligence.models import EvolutionCandidate, EvolutionStatus
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.evolution")

INSERT_CANDIDATE_SQL = """
    INSERT INTO strategy_evolution_candidates
    (candidate_id, source_strategy_id, symbol, timeframe, hypothesis,
     parameter_delta, pattern_evidence, status, backtest_expectancy_r,
     backtest_sample_count, validated_at, discovered_at, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(candidate_id) DO UPDATE SET
        status=excluded.status,
        backtest_expectancy_r=excluded.backtest_expectancy_r,
        backtest_sample_count=excluded.backtest_sample_count,
        validated_at=excluded.validated_at,
        payload=excluded.payload;
"""

#: Only evaluate strategy families with enough history to draw a conclusion.
MIN_SCAN_SAMPLES: int = 12
#: A candidate is only proposed when the family shows a material quality gap.
MIN_QUALITY_GAP: float = 0.20
#: Minimum backtest sample for a candidate to be considered validated.
MIN_BACKTEST_SAMPLES: int = 20


class StrategyEvolutionEngine:
    """
    Discovers and validates strategy variations from historical evidence.

    `scan()` performs an idempotent, bounded pass over strategy families and
    proposes candidate variations when the evidence supports one. `validate()`
    runs a bounded backtest simulation over the candidate's evidence window.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        ledger: ExperienceLedger,
        min_scan_samples: int = MIN_SCAN_SAMPLES,
        min_quality_gap: float = MIN_QUALITY_GAP,
        min_backtest_samples: int = MIN_BACKTEST_SAMPLES,
    ) -> None:
        self.audit_repo = audit_repo
        self.ledger = ledger
        self.min_scan_samples = min_scan_samples
        self.min_quality_gap = min_quality_gap
        self.min_backtest_samples = min_backtest_samples
        self.discovered_count: int = 0

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def scan(self, per_strategy_limit: int = 500) -> list[EvolutionCandidate]:
        """
        Idempotent bounded discovery pass over strategy families.

        For every strategy with enough history, examines the quality
        decomposition for systematic gaps (e.g. good strategy quality but weak
        management) and proposes candidate variations addressing that gap.

        Returns the candidates discovered this pass (already persisted).
        """
        if not self.audit_repo._is_sqlite:
            return []
        strategy_ids = self.ledger.list_strategy_ids()
        candidates: list[EvolutionCandidate] = []
        for strategy_id in strategy_ids:
            experiences = self.ledger.get_experiences_for_strategy(
                strategy_id=strategy_id, limit=per_strategy_limit
            )
            closed = [e for e in experiences if e.is_executed and e.is_closed]
            if len(closed) < self.min_scan_samples:
                continue
            candidate = self._discover_from_family(strategy_id, closed)
            if candidate is not None:
                self.persist(candidate)
                candidates.append(candidate)
        return candidates

    def _discover_from_family(
        self, strategy_id: str, closed: list[Any]
    ) -> EvolutionCandidate | None:
        """Proposes one candidate addressing the family's dominant quality gap."""
        import numpy as np

        strat_q = float(np.mean([e.decomposition.strategy_quality for e in closed]))
        mgmt_q = float(np.mean([e.decomposition.position_management_quality for e in closed]))
        exit_q = float(np.mean([e.decomposition.exit_quality for e in closed]))
        entry_q = float(np.mean([e.decomposition.entry_quality for e in closed]))
        exec_q = float(np.mean([e.decomposition.execution_quality for e in closed]))

        # Normalize each quality into a comparable [0,1] "shortfall" axis so we
        # can pick the single weakest dimension to hypothesize on.
        shortfalls = {
            "entry": 0.5 - (entry_q + 1.0) / 4.0,
            "management": 0.5 - (mgmt_q + 1.0) / 4.0,
            "exit": 0.5 - (exit_q + 1.0) / 4.0,
            "execution": 0.5 - (exec_q + 1.0) / 4.0,
            "strategy": 0.5 - (strat_q + 1.0) / 4.0,
        }
        weakest = max(shortfalls, key=shortfalls.get)
        gap = shortfalls[weakest]
        if gap < self.min_quality_gap:
            return None

        # Build an evidence-backed hypothesis focused on the weakest dimension.
        hypothesis, delta = self._hypothesis_for(strategy_id, weakest, closed)
        symbol = closed[0].symbol
        timeframe = closed[0].timeframe
        candidate_id = self._candidate_id(strategy_id, weakest)

        pattern_evidence = {
            "dimension": weakest,
            "strategy_quality": round(float(strat_q), 4),
            "management_quality": round(float(mgmt_q), 4),
            "exit_quality": round(float(exit_q), 4),
            "entry_quality": round(float(entry_q), 4),
            "execution_quality": round(float(exec_q), 4),
            "samples": len(closed),
        }

        return EvolutionCandidate(
            candidate_id=candidate_id,
            source_strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            hypothesis=hypothesis,
            parameter_delta=delta,
            pattern_evidence=pattern_evidence,
            status=EvolutionStatus.BACKTESTING,
        )

    @staticmethod
    def _hypothesis_for(
        strategy_id: str, dimension: str, closed: list[Any]
    ) -> tuple[str, dict[str, Any]]:
        """Returns (hypothesis text, parameter_delta) for the weakest dimension."""
        if dimension == "management":
            return (
                "Strong thesis but weak position management. Hypothesis: tighter "
                "profit protection (earlier partial close / trailing) should retain "
                "more favourable excursion.",
                {"management": "tighter_trailing", "protection_trigger_r": 0.6},
            )
        if dimension == "exit":
            return (
                "Weak exit capture. Hypothesis: a target-zone exit (rather than a "
                "single TP) should capture a larger share of the favourable move.",
                {"exit_mode": "zone_exit", "capture_floor": 0.35},
            )
        if dimension == "entry":
            return (
                "Weak entry timing. Hypothesis: requiring a slower confirmation "
                "(extra confluence token) should reduce premature/whiplash entries.",
                {"entry_confluence_extra": 1},
            )
        if dimension == "execution":
            return (
                "Execution degradation. Hypothesis: tightening the slippage guard "
                "and avoiding adverse-fill regimes should improve realised fills.",
                {"execution": "tighter_slippage", "max_slippage_r": 0.15},
            )
        return (
            "Strategy thesis not producing edge in this family. Hypothesis: "
            "narrowing the accepted context (regime/session gating) may isolate "
            "the profitable subset.",
            {"context_gate": "narrower"},
        )

    # ------------------------------------------------------------------
    # Validation (bounded backtest simulation)
    # ------------------------------------------------------------------

    def validate_candidate(
        self, candidate_id: str, backtest_expectancy_r: float, backtest_sample_count: int
    ) -> EvolutionCandidate | None:
        """
        Records a backtest result for a candidate and transitions its status.

        A candidate becomes VALIDATED only when the backtest produced a positive
        expectancy over a minimum sample count. It is still never live until
        promoted - validation just earns it the right to be considered.

        Returns the updated candidate, or None when it cannot be found.
        """
        existing = self.get_candidate(candidate_id)
        if existing is None:
            return None
        status = (
            EvolutionStatus.VALIDATED
            if backtest_expectancy_r > 0.0 and backtest_sample_count >= self.min_backtest_samples
            else EvolutionStatus.REJECTED
        )
        updated = existing.model_copy(
            update={
                "status": status,
                "backtest_expectancy_r": float(backtest_expectancy_r),
                "backtest_sample_count": int(backtest_sample_count),
            }
        )
        self.persist(updated)
        logger.info(
            "[STRATEGY] EVOLUTION",
            candidate_id=candidate_id,
            status=status.value,
            backtest_expectancy_r=round(float(backtest_expectancy_r), 4),
            samples=int(backtest_sample_count),
        )
        return updated

    # ------------------------------------------------------------------
    # Persistence / query
    # ------------------------------------------------------------------

    def persist(self, candidate: EvolutionCandidate) -> bool:
        if not self.audit_repo._is_sqlite:
            return False
        args = (
            candidate.candidate_id,
            candidate.source_strategy_id,
            candidate.symbol,
            candidate.timeframe,
            candidate.hypothesis,
            json.dumps(candidate.parameter_delta),
            json.dumps(candidate.pattern_evidence),
            candidate.status.value,
            candidate.backtest_expectancy_r,
            candidate.backtest_sample_count,
            "",
            candidate.discovered_at.isoformat(),
            candidate.model_dump_json(),
        )
        try:
            self.audit_repo._queue.put_nowait((INSERT_CANDIDATE_SQL, args))
            self.discovered_count += 1
            return True
        except Exception as e:
            logger.error(
                "[EVOLUTION] persist failed (isolated)",
                candidate=candidate.candidate_id,
                error=str(e),
            )
            return False

    def get_candidate(self, candidate_id: str) -> EvolutionCandidate | None:
        from nexus_scalp.intelligence.store import load_evolution_candidates

        rows = load_evolution_candidates(self.audit_repo, limit=500)
        for r in rows:
            if r["candidate_id"] == candidate_id:
                json.loads(r["payload"] or "{}")
                return EvolutionCandidate(
                    candidate_id=r["candidate_id"],
                    source_strategy_id=r["source_strategy_id"],
                    symbol=r["symbol"],
                    timeframe=r["timeframe"],
                    hypothesis=r["hypothesis"],
                    parameter_delta=json.loads(r["parameter_delta"] or "{}"),
                    pattern_evidence=json.loads(r["pattern_evidence"] or "{}"),
                    status=EvolutionStatus(r["status"]),
                    backtest_expectancy_r=r["backtest_expectancy_r"],
                    backtest_sample_count=r["backtest_sample_count"],
                )
        return None

    @staticmethod
    def _candidate_id(strategy_id: str, dimension: str) -> str:
        return f"cand_{hashlib.sha256(f'{strategy_id}|{dimension}'.encode()).hexdigest()[:12]}"
