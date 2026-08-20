"""
Strategy Factory Orchestrator
=============================
STRATEGY FACTORY (2026-08-20).

Coordinates the full strategy lifecycle: generate -> validate -> backtest
(via the authoritative research pipeline) -> walk-forward -> OOS -> robustness
-> score -> rank -> elite selection -> failure analysis -> evolution -> next
generation (spec 109 phases B-H).

RESPONSIBILITY BOUNDARY (spec 14 / 62 / 63 / 105):
  * The factory NEVER computes backtest performance itself. All measured
    results come from `ResearchPipeline.validate_candidate` — the existing
    deterministic engine is authoritative and unmodified.
  * The factory NEVER touches the live path: no adapter, no risk engine,
    no order authority. Promotion beyond VALIDATED remains operator-gated.
  * Global risk governance always wins; generated strategies cannot override
    it (they only declare risk ASSUMPTIONS, which are validated by the
    pipeline against the research dataset).

CRASH RECOVERY (spec 41 / 74 / 75):
  * Every candidate is persisted at each stage (factory_candidates +
    factory_failures + factory_events); the generation status is persisted.
  * `resume_generation()` reloads the persisted population and continues
    from the first candidate without a recorded evaluation.
"""

from __future__ import annotations

import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.strategies.factory.dsl import (
    GENERATOR_VERSION,
    RANDOM_SEED,
    candidate_id_from_hash,
    dsl_hash,
)
from nexus_scalp.strategies.factory.evolution import (
    adapt_probabilities,
    crossover,
    explore,
    mutate,
)
from nexus_scalp.strategies.factory.models import (
    CandidateSource,
    EvolutionConfig,
    EvolutionOperator,
    FactoryCandidate,
    FactoryStage,
    FailureReason,
    GenerationMode,
    LoopState,
    StrategyFamily,
)
from nexus_scalp.strategies.factory.provider import LLMGenerationProvider
from nexus_scalp.strategies.factory.ranking import (
    rank_strategies,
)
from nexus_scalp.strategies.factory.store import (
    emit_event,
    get_generation,
    list_candidates,
    list_generations,
    record_failure,
    set_loop_state,
    upsert_candidate,
    upsert_generation,
)
from nexus_scalp.strategies.factory.summarizer import (
    build_summary,
    memory_summary,
)
from nexus_scalp.strategies.factory.validators import validate_candidate

logger = get_logger("nexus_scalp.strategies.factory.orchestrator")


def _now() -> datetime:
    return datetime.now(UTC)


def _event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


class StrategyFactory:
    """The strategy generation + evolution orchestrator.

    Wired into LiveEngine as `strategy_factory`. Runs OFF the tick path via
    `asyncio.to_thread()`; all persistence goes through the AuditRepository
    background queue so it can never block trading.
    """

    def __init__(
        self,
        audit_repo: Any,
        research_pipeline: Any,
        config: EvolutionConfig | None = None,
        provider: LLMGenerationProvider | None = None,
        symbols: list[str] | None = None,
        notifier: Any | None = None,
    ) -> None:
        self.audit_repo = audit_repo
        self.research_pipeline = research_pipeline
        self.config = config or EvolutionConfig()
        self.provider = provider
        self.symbols = symbols or ["XAUUSD"]
        self.notifier = notifier
        self.loop_state: str = LoopState.STOPPED.value
        self.current_generation_id: str = ""
        self._operator_stats: dict[str, dict[str, int]] = {}
        self._last_run_summary: dict[str, Any] = {}
        self._kill_requested = False

    # ------------------------------------------------------------------
    # Control plane (spec 73)
    # ------------------------------------------------------------------

    def start_loop(self, mode: str = "AUTONOMOUS") -> bool:
        """Starts the autonomous loop control state (persisted)."""
        if self.loop_state in (LoopState.RUNNING.value, LoopState.STARTING.value):
            return False
        self.loop_state = LoopState.RUNNING.value
        self._kill_requested = False
        set_loop_state(
            self.audit_repo,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        return True

    def pause_loop(self) -> bool:
        """Pauses the loop between candidates (persisted)."""
        if self.loop_state != LoopState.RUNNING.value:
            return False
        self.loop_state = LoopState.PAUSED.value
        set_loop_state(
            self.audit_repo,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        emit_event(
            self.audit_repo,
            {"event_id": _event_id(), "event_type": "LOOP_PAUSED", "message": "Autonomous loop paused"},
        )
        return True

    def resume_loop(self) -> bool:
        if self.loop_state != LoopState.PAUSED.value:
            return False
        self.loop_state = LoopState.RUNNING.value
        set_loop_state(
            self.audit_repo,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        emit_event(
            self.audit_repo,
            {"event_id": _event_id(), "event_type": "LOOP_RESUMED", "message": "Autonomous loop resumed"},
        )
        return True

    def stop_loop(self) -> bool:
        """Kill switch (spec 106): no new generation, no new LLM requests."""
        if self.loop_state in (LoopState.STOPPED.value, LoopState.STOPPING.value):
            return False
        self.loop_state = LoopState.STOPPING.value
        self._kill_requested = True
        set_loop_state(
            self.audit_repo,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        emit_event(
            self.audit_repo,
            {"event_id": _event_id(), "event_type": "LOOP_STOPPED", "message": "Kill switch engaged"},
        )
        self.loop_state = LoopState.STOPPED.value
        # Persist the FINAL state (not the transient STOPPING) so a crash
        # after stop never resumes into a half-stopped loop (spec 73).
        set_loop_state(
            self.audit_repo,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        return True

    def loop_status(self) -> dict[str, Any]:
        return {
            "state": self.loop_state,
            "current_generation": self.current_generation_id,
            "operator_stats": self._operator_stats,
            "kill_requested": self._kill_requested,
        }

    # ------------------------------------------------------------------
    # Generation lifecycle
    # ------------------------------------------------------------------

    def create_generation(
        self,
        size: int | None = None,
        mode: str = "MANUAL",
        parent_generation: str = "",
    ) -> dict[str, Any]:
        """Creates and persists a new generation (population shell)."""
        cfg = self.config
        population = max(1, min(int(size or cfg.generation_size), cfg.max_candidates_per_generation))
        number = self._next_generation_number()
        generation_id = f"G{number}"
        gen = {
            "generation_id": generation_id,
            "number": number,
            "mode": mode,
            "parent_generation": parent_generation,
            "population_target": population,
            "created_at": _now().isoformat(),
            "completed_at": None,
            "status": "PENDING",
            "config": {
                "generator_version": GENERATOR_VERSION,
                "schema_version": "1.0",
                "mutation_rate": cfg.mutation_rate,
                "crossover_rate": cfg.crossover_rate,
                "exploration_rate": cfg.exploration_rate,
                "elite_size": cfg.elite_size,
            },
        }
        upsert_generation(self.audit_repo, gen)
        emit_event(
            self.audit_repo,
            {
                "event_id": _event_id(),
                "generation_id": generation_id,
                "event_type": "GENERATION_CREATED",
                "message": f"Generation {generation_id} created (population {population})",
                "payload": {"population": population, "mode": mode},
            },
        )
        self._send_telegram("GENERATION_STARTED", gen)
        self.current_generation_id = generation_id
        return gen

    def _send_telegram(self, event_type: str, payload: dict[str, Any]) -> None:
        """Routes a lifecycle event to Telegram through the engine notifier.

        Bounded event types only (no per-candidate spam); never raises.
        """
        if self.notifier is None or not getattr(self.notifier, "enabled", False):
            return
        try:
            from nexus_scalp.strategies.factory.telegram import send_factory_event

            send_factory_event(self.notifier, event_type, payload)
        except Exception as e:
            logger.warning("[STRATEGY_FACTORY] telegram event failed (isolated)", error=str(e))

    def _next_generation_number(self) -> int:
        gens = list_generations(self.audit_repo, limit=MAX_GENERATIONS_READ)
        numbers = [int(g.get("number", 0)) for g in gens]
        return (max(numbers) + 1) if numbers else 1

    def _next_generation_number_safe(self) -> int:
        """Bounded variant used purely for RNG seeding (never blocks)."""
        try:
            return self._next_generation_number()
        except Exception:
            return 1

    def generate_population(
        self,
        generation_id: str,
        size: int | None = None,
        memory: dict[str, Any] | None = None,
    ) -> list[FactoryCandidate]:
        """Generates the candidate population for a generation.

        Composition: elite preservation + mutation + crossover + exploration
        (subsequent generations, spec 7) or the Generation-0 mixture
        (initial, spec 6). LLM provider fills the assisted slice when
        configured; deterministic generators are ALWAYS the correctness base.
        """
        cfg = self.config
        population = size or cfg.generation_size
        gen = get_generation(self.audit_repo, generation_id) or {}
        number = int(gen.get("number", 0))

        upsert_generation(
            self.audit_repo,
            {**gen, "status": "RUNNING"},
        )

        if number <= 1:
            base = self._generation_zero_population(generation_id, population)
        else:
            base = self._evolved_population(generation_id, population, memory)

        return self._dedupe_population(base)

    def _generation_zero_population(
        self, generation_id: str, population: int
    ) -> list[FactoryCandidate]:
        """Generation 0 mixture: templates / diversity / regime / random +
        optional LLM slice (spec 6)."""
        from nexus_scalp.strategies.factory.dsl import generate_generation_zero

        candidates = generate_generation_zero(population, seed=RANDOM_SEED)
        # (The generation_id stamping loop was dead code: it discarded its
        #  result; _ensure_family_coverage below re-stamps nothing. Generation
        #  ids are assigned at registration. Removed for PLW2901.)
        candidates = _ensure_family_coverage(candidates, generation_id)
        return candidates

    def _evolved_population(
        self, generation_id: str, population: int, memory: dict[str, Any] | None
    ) -> list[FactoryCandidate]:
        """Subsequent generations: elite preservation + mutation + crossover +
        exploration with adaptive probabilities (spec 7 / 99)."""
        cfg = self.config
        elite_rows = self._load_elite(generation_id)
        elite_candidates = [self._candidate_from_registry(e) for e in elite_rows]
        elite_candidates = [c for c in elite_candidates if c is not None]

        out: list[FactoryCandidate] = []
        # Elite preservation (never destroy best validated strategies).
        preserved = int(population * cfg.elite_preservation_rate)
        for c in elite_candidates[:preserved]:
            out.append(c)
        # Substitutes for missing elites: fresh templates.
        while len(out) < preserved:
            dsl_tpl = self._fresh_template()
            out.append(
                FactoryCandidate(
                    candidate_id=candidate_id_from_hash(dsl_hash(dsl_tpl)),
                    definition_hash=dsl_hash(dsl_tpl),
                    generation_id=generation_id,
                    source=CandidateSource.TEMPLATE,
                    operator=EvolutionOperator.NONE,
                    dsl=dsl_tpl,
                    family=dsl_tpl.family,
                    population_index=len(out),
                )
            )

        rng = random.Random(RANDOM_SEED + int(self._next_generation_number_safe()))
        probs = self._adaptive_probabilities(memory)
        elite_pool = out[:preserved]

        while len(out) < population:
            roll = rng.random()
            child: FactoryCandidate | None = None
            op = "EXPLORATION"
            if roll < probs["mutation_rate"] and elite_pool:
                base = rng.choice(elite_pool)
                child = mutate(base, rng=rng, budgets=self._budgets())
                op = "MUTATION"
            elif roll < probs["mutation_rate"] + probs["crossover_rate"] and len(elite_pool) >= 2:
                a, b = rng.sample(elite_pool, 2)
                child = crossover(a, b, rng=rng, budgets=self._budgets())
                op = "CROSSOVER"
            else:
                base = rng.choice(elite_pool) if elite_pool else None
                if base is not None:
                    child = explore(base, rng=rng, budgets=self._budgets())
                op = "EXPLORATION"
            if child is None:
                # Operator failed the validity gates; fall back to a fresh
                # template (bounded exploration, never an invalid strategy).
                child = self._fresh_candidate(generation_id, len(out))
                op = "TEMPLATE"
            self._tally_operator(op, len(out))
            out.append(
                child.model_copy(
                    update={
                        "generation_id": generation_id,
                        "population_index": len(out) - 1,
                    }
                )
            )
        return out

    def _adaptive_probabilities(self, memory: dict[str, Any] | None) -> dict[str, float]:
        cfg = self.config
        base = {
            "mutation_rate": cfg.mutation_rate,
            "crossover_rate": cfg.crossover_rate,
            "exploration_rate": cfg.exploration_rate,
        }
        if not memory or memory.get("generation_count", 0) < 1:
            return base
        op_success = memory.get("operator_success") or {}
        diversity = float(memory.get("diversity", 0.0) or 0.0)
        return adapt_probabilities(base, op_success, diversity, cfg.stagnation_diversity_floor)

    def _load_elite(self, generation_id: str) -> list[dict[str, Any]]:
        """Loads the current VALIDATED/SHADOW registry entries as elite pool."""
        from nexus_scalp.research.store import list_registry

        entries = list_registry(self.audit_repo, limit=200)
        elites = [
            e
            for e in entries
            if (e.get("score") or {}).get("verdict") == "VALIDATED"
            and float((e.get("score") or {}).get("final_score", 0.0) or 0.0) >= 0.6
        ]
        elites.sort(
            key=lambda e: float((e.get("score") or {}).get("final_score", 0.0) or 0.0),
            reverse=True,
        )
        return elites[: self.config.elite_size]

    def _candidate_from_registry(self, entry: dict[str, Any]) -> FactoryCandidate | None:
        """Rehydrates a registry row into a FactoryCandidate (idempotent)."""
        try:
            from nexus_scalp.strategies.factory.dsl import canonicalize_dsl

            ctx = entry.get("context_definition") or {}
            dsl = canonicalize_dsl(ctx.get("dsl") or {})
            digest = dsl_hash(dsl)
            return FactoryCandidate(
                candidate_id=candidate_id_from_hash(digest),
                definition_hash=digest,
                generation_id=self.current_generation_id or "G0",
                source=CandidateSource.TEMPLATE,
                operator=EvolutionOperator.NONE,
                parent_ids=[],
                dsl=dsl,
                family=dsl.family,
                population_index=0,
            )
        except Exception as e:
            logger.warning("[STRATEGY_FACTORY] elite rehydrate failed", error=str(e))
            return None

    def _fresh_template(self) -> Any:
        import random as _r

        from nexus_scalp.strategies.factory.dsl import _template_dsl

        return _template_dsl(
            _r.choice(
                [
                    StrategyFamily.TREND_FOLLOWING,
                    StrategyFamily.MEAN_REVERSION,
                    StrategyFamily.BREAKOUT,
                    StrategyFamily.MOMENTUM,
                ]
            ),
            _r.Random(RANDOM_SEED),
        )

    def _fresh_candidate(self, generation_id: str, idx: int) -> FactoryCandidate:
        dsl = self._fresh_template()
        digest = dsl_hash(dsl)
        return FactoryCandidate(
            candidate_id=candidate_id_from_hash(digest),
            definition_hash=digest,
            generation_id=generation_id,
            source=CandidateSource.TEMPLATE,
            operator=EvolutionOperator.NONE,
            dsl=dsl,
            family=dsl.family,
            population_index=idx,
        )

    def _budgets(self) -> dict[str, int]:
        cfg = self.config
        return {
            "max_conditions": cfg.max_conditions,
            "max_features": cfg.max_features,
            "max_timeframes": cfg.max_timeframes,
            "max_entry_clauses": cfg.max_entry_clauses,
            "max_exit_clauses": cfg.max_exit_clauses,
        }

    def _dedupe_population(self, candidates: list[FactoryCandidate]) -> list[FactoryCandidate]:
        """Canonical dedup within the population (spec 13)."""
        seen: set[str] = set()
        out: list[FactoryCandidate] = []
        for c in candidates:
            if c.definition_hash in seen:
                continue
            seen.add(c.definition_hash)
            out.append(c)
        return out

    def _tally_operator(self, op: str, index: int) -> None:
        stats = self._operator_stats.setdefault(op, {"generated": 0, "survived": 0, "elite": 0})
        stats["generated"] = stats.get("generated", 0) + 1

    # ------------------------------------------------------------------
    # Structural validation + evaluation
    # ------------------------------------------------------------------

    def validate_population(self, population: list[FactoryCandidate]) -> dict[str, Any]:
        """Runs the structural gate chain over the whole population.

        Returns counts per outcome; every candidate is persisted with its
        verdict (spec 34 / 90).
        """
        passed = failed = duplicated = 0
        existing_hashes: set[str] = set()
        results: dict[str, Any] = {"passed": [], "failed": []}
        for candidate in population:
            verdict = validate_candidate(
                candidate,
                budgets=self._budgets(),
                existing_hashes=existing_hashes,
                symbols=self.symbols,
            )
            if verdict.passed:
                existing_hashes.add(candidate.definition_hash)
                passed += 1
                results["passed"].append(candidate)
                self._persist_candidate(candidate, verdict=verdict)
            else:
                failed += 1
                if verdict.failure_reason == FailureReason.DUPLICATE:
                    duplicated += 1
                results["failed"].append(candidate)
                self._persist_candidate(candidate, verdict=verdict, lifecycle="REJECTED")
                self._persist_failure(candidate, verdict)
        emit_event(
            self.audit_repo,
            {
                "event_id": _event_id(),
                "generation_id": self.current_generation_id,
                "event_type": "STRUCTURAL_VALIDATION",
                "message": f"Structural validation: {passed} passed, {failed} failed",
                "payload": {"passed": passed, "failed": failed, "duplicated": duplicated},
            },
        )
        return results

    def _persist_candidate(
        self,
        candidate: FactoryCandidate,
        verdict: Any = None,
        lifecycle: str = "GENERATED",
        preserve_structural: bool = False,
    ) -> None:
        if preserve_structural:
            # Keep the existing structural verdict from the first upsert
            # (immutability: evaluation must not erase the validator result).
            from nexus_scalp.strategies.factory.store import get_candidate_structural

            existing = get_candidate_structural(self.audit_repo, candidate.candidate_id)
            structural = existing if existing is not None else (verdict.model_dump() if verdict else None)
        else:
            structural = verdict.model_dump() if verdict else None
        upsert_candidate(
            self.audit_repo,
            {
                "candidate_id": candidate.candidate_id,
                "definition_hash": candidate.definition_hash,
                "generation_id": candidate.generation_id,
                "source": candidate.source.value,
                "operator": candidate.operator.value,
                "parent_ids": candidate.parent_ids,
                "family": candidate.family.value,
                "population_index": candidate.population_index,
                "dsl": candidate.dsl.model_dump(),
                "structural": structural,
                "lifecycle": lifecycle,
                "failure_reasons": [verdict.failure_reason.value] if verdict and verdict.failure_reason else [],
                "llm_response_id": candidate.llm_response_id,
                "created_at": candidate.created_at.isoformat(),
            }
        )

    def _persist_failure(self, candidate: FactoryCandidate, verdict: Any) -> None:
        record_failure(
            self.audit_repo,
            {
                "failure_id": f"fail_{uuid.uuid4().hex[:16]}",
                "candidate_id": candidate.candidate_id,
                "strategy_id": candidate.candidate_id,
                "generation_id": candidate.generation_id,
                "stage": verdict.stage.value,
                "reason": verdict.failure_reason.value if verdict.failure_reason else "INVALID_SCHEMA",
                "detail": {"message": verdict.reasons, **verdict.details},
                "created_at": _now().isoformat(),
            }
        )

    def evaluate_candidate(
        self,
        candidate: FactoryCandidate,
        dataset: Any,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Schedules one candidate through the authoritative research pipeline.

        The pipeline runs backtest -> walk-forward -> OOS -> robustness ->
        score and persists the strategy_registry row. The factory records the
        outcome and the structured failure reasons (spec 14 / 23 / 59).
        """
        started = time.perf_counter()
        try:
            registry_candidate = self._to_strategy_candidate(candidate)
            result = self.research_pipeline.validate_candidate(
                registry_candidate,
                dataset,
                run_id=run_id,
            )
            duration_ms = (time.perf_counter() - started) * 1000.0

            lifecycle = str(result.get("lifecycle", "GENERATED"))
            score = result.get("score") or {}
            oos = result.get("oos") or {}
            rob = result.get("robustness") or {}
            result.get("walkforward") or {}

            failed_reasons = self._derived_failure_reasons(result, candidate)
            self._persist_candidate(
                candidate,
                lifecycle=lifecycle,
                verdict=None,
                preserve_structural=True,
            )
            for reason in failed_reasons:
                record_failure(
                    self.audit_repo,
                    {
                        "failure_id": f"fail_{uuid.uuid4().hex[:16]}",
                        "candidate_id": candidate.candidate_id,
                        "strategy_id": str(result.get("strategy_id", "")),
                        "generation_id": candidate.generation_id,
                        "stage": self._stage_for_reason(reason),
                        "reason": reason,
                        "detail": {
                            "score": float(score.get("final_score", 0.0) or 0.0),
                            "verdict": score.get("verdict", ""),
                            "oos_status": oos.get("status", ""),
                            "robustness_status": rob.get("status", ""),
                            "trades": (result.get("backtest") or {}).get("total_trades", 0),
                        },
                        "created_at": _now().isoformat(),
                    }
                )

            self._tally_operator_survival(candidate, lifecycle)
            emit_event(
                self.audit_repo,
                {
                    "event_id": _event_id(),
                    "generation_id": candidate.generation_id,
                    "candidate_id": candidate.candidate_id,
                    "event_type": "CANDIDATE_EVALUATED",
                    "message": f"{candidate.candidate_id} -> {lifecycle}",
                    "payload": {
                        "lifecycle": lifecycle,
                        "score": float(score.get("final_score", 0.0) or 0.0),
                        "duration_ms": round(duration_ms, 1),
                    },
                },
            )
            return result
        except Exception as e:
            logger.error(
                "[STRATEGY_FACTORY] candidate evaluation failed",
                candidate_id=candidate.candidate_id,
                error=str(e),
                exc_info=True,
            )
            self._persist_candidate(candidate, lifecycle="FAILED")
            record_failure(
                self.audit_repo,
                {
                    "failure_id": f"fail_{uuid.uuid4().hex[:16]}",
                    "candidate_id": candidate.candidate_id,
                    "strategy_id": candidate.candidate_id,
                    "generation_id": candidate.generation_id,
                    "stage": "BACKTEST",
                    "reason": "INVALID_SCHEMA",
                    "detail": {"error": str(e)},
                    "created_at": _now().isoformat(),
                },
            )
            return None

    def _to_strategy_candidate(self, candidate: FactoryCandidate) -> Any:
        """Builds the research StrategyCandidate from a factory candidate.

        The research candidate is the interface to the deterministic engine;
        its context_definition carries the full DSL so the pipeline's
        family-select validation has the information it needs.
        """
        from nexus_scalp.research.candidates import StrategyCandidate
        from nexus_scalp.research.models import CandidateLifecycle

        dsl = candidate.dsl.model_dump()
        strategy_id = candidate.candidate_id
        symbols = dsl.get("market", {}).get("symbols") or self.symbols
        return StrategyCandidate(
            strategy_id=strategy_id,
            strategy_version="",
            feature_schema_id="scalp_v3",  # canonical 70D (schema_contract)
            feature_dimension=70,
            context_definition={
                "dsl": dsl,
                "family": candidate.family.value,
                "symbol": symbols[0] if symbols else "XAUUSD",
                "fingerprint": f"{candidate.family.value}|{dsl.get('market', {}).get('timeframes', ['M1'])[0]}",
            },
            entry_logic=dsl.get("entry", {}),
            exit_logic=dsl.get("exit", {}),
            risk_assumptions=dsl.get("risk", {}),
            parent_strategy_ids=candidate.parent_ids,
            discovery_method=f"factory:{candidate.source.value.lower()}",
            lifecycle=CandidateLifecycle.DISCOVERED,
            discovery_evidence={
                "source": "strategy_factory",
                "definition_hash": candidate.definition_hash,
                "generation_id": candidate.generation_id,
                "operator": candidate.operator.value,
                "dsl": dsl,
            },
        )

    def _derived_failure_reasons(self, result: dict[str, Any], candidate: FactoryCandidate) -> list[str]:
        """Maps the pipeline result to structured failure reasons (spec 23)."""
        reasons: list[str] = []
        score = result.get("score") or {}
        oos = result.get("oos") or {}
        rob = result.get("robustness") or {}
        wf = result.get("walkforward") or {}
        bt = result.get("backtest") or {}

        verdict = score.get("verdict", "")
        if verdict == "REJECTED":
            if oos.get("status") != "PASS":
                reasons.append(FailureReason.OOS_FAILURE.value)
            if rob.get("status") != "PASS":
                reasons.append(FailureReason.ROBUSTNESS_FAILURE.value)
            if wf and not wf.get("passed"):
                reasons.append(FailureReason.WALK_FORWARD_FAILURE.value)
        else:
            # VERDICT VALIDATED / INCONCLUSIVE; check hard gate floors.
            trades = int(bt.get("total_trades", 0) or 0)
            if trades < self.config.min_trades:
                reasons.append(FailureReason.INSUFFICIENT_TRADES.value)
            exp = float(bt.get("expectancy_r", 0.0) or 0.0)
            if exp <= 0.0:
                reasons.append(FailureReason.NEGATIVE_EXPECTANCY.value)
            dd = float(bt.get("max_drawdown_r", 0.0) or 0.0)
            if dd > self.config.max_drawdown_r:
                reasons.append(FailureReason.EXCESSIVE_DRAWDOWN.value)
            pf = float(bt.get("profit_factor", 0.0) or 0.0)
            if 0.0 < pf < self.config.min_profit_factor:
                reasons.append(FailureReason.LOW_PROFIT_FACTOR.value)
            if oos.get("status") != "PASS":
                reasons.append(FailureReason.OOS_FAILURE.value)
            if rob.get("status") != "PASS":
                reasons.append(FailureReason.ROBUSTNESS_FAILURE.value)
        if not reasons and verdict != "VALIDATED":
            reasons.append(FailureReason.INSUFFICIENT_TRADES.value)
        return reasons

    @staticmethod
    def _stage_for_reason(reason: str) -> str:
        stage_map = {
            FailureReason.INVALID_SCHEMA.value: FactoryStage.DSL_VALIDATION.value,
            FailureReason.UNSUPPORTED_FEATURE.value: FactoryStage.FEATURE_VALIDATION.value,
            FailureReason.LOOKAHEAD_RISK.value: FactoryStage.CAUSALITY_VALIDATION.value,
            FailureReason.EXCESSIVE_COMPLEXITY.value: FactoryStage.COMPLEXITY_VALIDATION.value,
            FailureReason.DUPLICATE.value: FactoryStage.DEDUPLICATION.value,
            FailureReason.OOS_FAILURE.value: FactoryStage.OOS.value,
            FailureReason.WALK_FORWARD_FAILURE.value: FactoryStage.WALK_FORWARD.value,
            FailureReason.ROBUSTNESS_FAILURE.value: FactoryStage.ROBUSTNESS.value,
        }
        return stage_map.get(FailureReason(reason), FactoryStage.SCORING.value)

    def _tally_operator_survival(self, candidate: FactoryCandidate, lifecycle: str) -> None:
        op = candidate.operator.value
        stats = self._operator_stats.setdefault(op, {"generated": 0, "survived": 0, "elite": 0})
        if lifecycle in ("VALIDATED", "SHADOW", "ACTIVE"):
            stats["survived"] = stats.get("survived", 0) + 1
            stats["elite"] = stats.get("elite", 0) + 1

    # ------------------------------------------------------------------
    # Generation completion + summary
    # ------------------------------------------------------------------

    def complete_generation(self, generation_id: str) -> dict[str, Any]:
        """Finalizes a generation: ranking + elite + summary + memory."""
        import json as _json

        gen = get_generation(self.audit_repo, generation_id) or {}
        candidates = list_candidates(self.audit_repo, generation_id=generation_id, limit=2000)
        registry_entries = self._registry_rows_for_generation(candidates)
        summary = build_summary(
            gen,
            candidates,
            registry_entries,
            operator_stats=self._operator_stats,
            runtime_ms=0.0,
        )
        ranked = rank_strategies(registry_entries, limit=100)
        elite = [e for e in ranked if (e.get("score") or {}).get("verdict") == "VALIDATED"][
            : self.config.elite_size
        ]

        raw_config = gen.get("config") or {}
        if isinstance(raw_config, str):
            try:
                raw_config = _json.loads(raw_config) if raw_config.strip() else {}
            except Exception:
                raw_config = {}
        upsert_generation(
            self.audit_repo,
            {
                **gen,
                "status": "COMPLETED",
                "completed_at": _now().isoformat(),
                "config": {**raw_config, "summary": summary.model_dump()},
            },
        )
        self._last_run_summary = summary.model_dump()
        emit_event(
            self.audit_repo,
            {
                "event_id": _event_id(),
                "generation_id": generation_id,
                "event_type": "GENERATION_COMPLETED",
                "message": f"Generation {generation_id} completed",
                "payload": summary.model_dump(),
            },
        )
        self._send_telegram("GENERATION_COMPLETED", {"generation_id": generation_id, "summary": summary.model_dump()})
        return {"summary": summary.model_dump(), "ranked": ranked, "elite": elite}

    def _registry_rows_for_generation(
        self, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Registry rows for the strategies evaluated in this generation."""

        from nexus_scalp.research.store import get_registry_entry

        rows: list[dict[str, Any]] = []
        for c in candidates:
            entry = get_registry_entry(self.audit_repo, c.get("candidate_id", ""))
            if entry is not None:
                rows.append(_decode_registry_row(entry))
        return rows

    def build_memory(self) -> dict[str, Any]:
        """Structured evolution memory from all completed generations."""
        gens = list_generations(self.audit_repo, limit=50)
        summaries: list[Any] = []
        for g in gens:
            cfg = (g.get("config") or {})
            s = cfg.get("summary")
            if s:
                summaries.append(s)
        from nexus_scalp.research.store import list_registry

        entries = list_registry(self.audit_repo, limit=200)
        elite = [e for e in entries if (e.get("score") or {}).get("verdict") == "VALIDATED"]
        return memory_summary(summaries, elite, entries)

    # ------------------------------------------------------------------
    # Autonomous loop (spec 55)
    # ------------------------------------------------------------------

    def run_generation_cycle(
        self,
        size: int | None = None,
        memory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One full generation cycle: create -> generate -> validate ->
        evaluate -> complete. Returns the generation summary payload."""
        if self._kill_requested:
            return {"status": "CANCELLED", "reason": "kill switch"}
        gen = self.create_generation(size=size, mode="MANUAL")
        generation_id = gen["generation_id"]
        population = self.generate_population(generation_id, size=size, memory=memory)
        validation = self.validate_population(population)

        dataset = self._build_dataset()
        evaluated = 0
        for candidate in validation["passed"]:
            if self._kill_requested:
                break
            self.evaluate_candidate(candidate, dataset)
            evaluated += 1

        completion = self.complete_generation(generation_id)
        completion["generation_id"] = generation_id
        completion["evaluated"] = evaluated
        completion["population"] = len(population)
        completion["passed_structural"] = len(validation["passed"])
        return completion

    def _build_dataset(self) -> Any:
        """Builds the research dataset from the immutable ledger."""
        return self.research_pipeline.dataset_builder.build()

    def resume_generation(self, generation_id: str) -> dict[str, Any]:
        """Crash recovery: reloads a PENDING/RUNNING generation and continues
        evaluating the candidates that have no recorded evaluation (spec 74)."""
        gen = get_generation(self.audit_repo, generation_id) or {}
        if not gen:
            return {"status": "NOT_FOUND"}
        candidates = list_candidates(self.audit_repo, generation_id=generation_id, limit=2000)
        pending = [
            c
            for c in candidates
            if c.get("lifecycle") in ("GENERATED", None, "")
        ]
        dataset = self._build_dataset()
        resumed = 0
        for c in pending:
            candidate = self._candidate_from_row(c)
            if candidate is None:
                continue
            self.evaluate_candidate(candidate, dataset)
            resumed += 1
        return {"status": "RESUMED", "generation_id": generation_id, "resumed": resumed}

    def _candidate_from_row(self, row: dict[str, Any]) -> FactoryCandidate | None:
        try:
            import json as _json

            from nexus_scalp.strategies.factory.dsl import canonicalize_dsl

            raw_dsl = row.get("dsl") or {}
            if isinstance(raw_dsl, str):
                raw_dsl = _json.loads(raw_dsl) if raw_dsl.strip() else {}
            dsl = canonicalize_dsl(raw_dsl)

            raw_parents = row.get("parent_ids") or []
            if isinstance(raw_parents, str):
                raw_parents = _json.loads(raw_parents) if raw_parents.strip() else []
            return FactoryCandidate(
                candidate_id=str(row.get("candidate_id", "")),
                definition_hash=str(row.get("definition_hash", "")),
                generation_id=str(row.get("generation_id", "")),
                source=CandidateSource(str(row.get("source", "TEMPLATE"))),
                operator=EvolutionOperator(str(row.get("operator", "NONE"))),
                parent_ids=raw_parents if isinstance(raw_parents, list) else [],
                dsl=dsl,
                family=StrategyFamily(str(row.get("family", "HYBRID"))),
                population_index=int(row.get("population_index", 0) or 0),
                llm_response_id=str(row.get("llm_response_id", "") or ""),
            )
        except Exception as e:
            logger.warning("[STRATEGY_FACTORY] row rehydrate failed", error=str(e))
            return None


def _decode_registry_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Decodes JSON-text columns of a raw registry row into nested dicts.

    store.get_registry_entry returns raw TEXT columns ('backtest', 'score',
    ...) for UI safety; the factory summarizer/ranking need the decoded
    objects. Unknown/empty -> {} (canonical empty, BUG-075 discipline).
    """
    import json as _json

    out = dict(entry)
    for col in ("backtest", "walkforward", "oos", "robustness", "score", "context_definition", "parent_strategy_ids"):
        raw = out.get(col)
        if raw is None:
            out[col] = {}
            continue
        if isinstance(raw, dict):
            continue
        text = str(raw).strip()
        if text == "" or text.lower() in ("null", "none"):
            out[col] = {} if col != "parent_strategy_ids" else []
            continue
        try:
            parsed = _json.loads(text)
            out[col] = parsed if isinstance(parsed, (dict, list)) else {}
        except Exception:
            out[col] = {}
    return out


def _ensure_family_coverage(
    candidates: list[FactoryCandidate], generation_id: str
) -> list[FactoryCandidate]:
    """Ensures every strategy family appears in Generation 0 (diversity)."""
    import random as _r

    from nexus_scalp.strategies.factory.dsl import _template_dsl

    present = {c.family for c in candidates}
    missing = [f for f in StrategyFamily if f not in present]
    rng = _r.Random(RANDOM_SEED + 3)
    extra: list[FactoryCandidate] = []
    for fam in missing:
        dsl = _template_dsl(fam, rng)
        digest = dsl_hash(dsl)
        extra.append(
            FactoryCandidate(
                candidate_id=candidate_id_from_hash(digest),
                definition_hash=digest,
                generation_id=generation_id,
                source=CandidateSource.TEMPLATE,
                operator=EvolutionOperator.NONE,
                dsl=dsl,
                family=fam,
                population_index=len(candidates) + len(extra),
            )
        )
    return candidates + extra


MAX_GENERATIONS_READ = 1000


__all__ = [
    "EvolutionConfig",
    "GenerationMode",
    "LoopState",
    "StrategyFactory",
]