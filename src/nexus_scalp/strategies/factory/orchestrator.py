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
from nexus_scalp.strategies.factory.benchmark import (
    benchmark_subset_for_candidate,
    build_benchmark_artifact,
    candidate_coverage_stats,
)
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
    mutate_with_action,
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


def _score_dict(entry: dict[str, Any]) -> dict[str, Any]:
    """Parse the registry row's ``score`` column defensively.

    ``list_registry`` returns score as a JSON TEXT string (registry row safe
    normalization keeps it text); registry row dicts may arrive pre-parsed.
    Either shape must decode without crashing (BUG-130: AttributeError
    'str' object has no attribute 'get').
    """
    score = entry.get("score")
    if isinstance(score, dict):
        return score
    if score is None:
        return {}
    if isinstance(score, str):
        try:
            import json as _json

            parsed = _json.loads(score) if score.strip() else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


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
        store: Any | None = None,
    ) -> None:
        self.audit_repo = audit_repo
        # Isolated strategy research store (2026-08-20): generated-
        # strategy memory lives in its OWN database (artifacts/
        # strategies.db / PostgreSQL), never in the audit DB. When no
        # store is injected, the factory falls back to the audit-repo
        # background queue (legacy behavior).
        self.store: Any = store
        self.research_pipeline = research_pipeline
        self.config = config or EvolutionConfig()
        self.provider = provider
        self.symbols = symbols or ["XAUUSD"]
        self.notifier = notifier
        self.loop_state: str = LoopState.STOPPED.value
        self.current_generation_id: str = ""
        # Operator accounting (G28 TARGET 2): cumulative across restarts.
        # Structure:
        #   "operators":  {op: {generated, valid, survived, elite, wf_pass,
        #                       oos_pass, improved, improvement_delta}}
        #   "actions":    {action: {same shape}} for the 8 mutation/crossover
        #                 actions the handoff requires per-action attribution of
        #   "clone_clusters": {behavioral_signature: {"members", "oos_passes"}}
        # Loaded from factory_loop_state at construction, saved on every tally.
        self._operator_stats: dict[str, dict[str, int]] = {}
        self._operator_actions: dict[str, dict[str, float]] = {}
        self._clone_clusters: dict[str, dict[str, int]] = {}
        self._candidate_action: dict[str, dict[str, str]] = {}
        self._load_operator_accounting()
        self._last_run_summary: dict[str, Any] = {}
        self._kill_requested = False

    @property
    def _research_backend(self) -> Any:
        """Persistence backend for factory research memory.

        Returns the isolated strategy research store when injected, else the
        audit repository (legacy background-queue path).
        """
        return self.store if self.store is not None else self.audit_repo

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
            self._research_backend,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        return True

    def pause_loop(self) -> bool:
        """Pauses the loop between candidates (persisted)."""
        if self.loop_state != LoopState.RUNNING.value:
            return False
        self.loop_state = LoopState.PAUSED.value
        set_loop_state(
            self._research_backend,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        emit_event(
            self._research_backend,
            {
                "event_id": _event_id(),
                "event_type": "LOOP_PAUSED",
                "message": "Autonomous loop paused",
            },
        )
        return True

    def resume_loop(self) -> bool:
        if self.loop_state != LoopState.PAUSED.value:
            return False
        self.loop_state = LoopState.RUNNING.value
        set_loop_state(
            self._research_backend,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        emit_event(
            self._research_backend,
            {
                "event_id": _event_id(),
                "event_type": "LOOP_RESUMED",
                "message": "Autonomous loop resumed",
            },
        )
        return True

    def stop_loop(self) -> bool:
        """Kill switch (spec 106): no new generation, no new LLM requests."""
        if self.loop_state in (LoopState.STOPPED.value, LoopState.STOPPING.value):
            return False
        self.loop_state = LoopState.STOPPING.value
        self._kill_requested = True
        set_loop_state(
            self._research_backend,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        emit_event(
            self._research_backend,
            {
                "event_id": _event_id(),
                "event_type": "LOOP_STOPPED",
                "message": "Kill switch engaged",
            },
        )
        self.loop_state = LoopState.STOPPED.value
        # Persist the FINAL state (not the transient STOPPING) so a crash
        # after stop never resumes into a half-stopped loop (spec 73).
        set_loop_state(
            self._research_backend,
            {"state": self.loop_state, "generation_id": self.current_generation_id},
        )
        return True

    def loop_status(self) -> dict[str, Any]:
        # G28: operator evidence is cumulative (persisted to factory_loop_state
        # and reloaded at construction) — restart no longer resets accounting.
        pathological = sum(
            1 for c in self._clone_clusters.values() if self._is_pathological_clone(c)
        )
        return {
            "state": self.loop_state,
            "current_generation": self.current_generation_id,
            "operator_stats": self._operator_stats,
            "action_stats": self._operator_actions,
            "clone_clusters_tracked": len(self._clone_clusters),
            "clone_clusters_pathological": pathological,
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
        population = max(
            1, min(int(size or cfg.generation_size), cfg.max_candidates_per_generation)
        )
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
        upsert_generation(self._research_backend, gen)
        emit_event(
            self._research_backend,
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
        gens = list_generations(self._research_backend, limit=MAX_GENERATIONS_READ)
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
        gen = get_generation(self._research_backend, generation_id) or {}
        number = int(gen.get("number", 0))

        upsert_generation(
            self._research_backend,
            {**gen, "status": "RUNNING"},
        )

        if number <= 1:
            base = self._generation_zero_population(generation_id, population)
        else:
            base = self._evolved_population(generation_id, population, memory)

        deduped = self._dedupe_population(base)
        self._record_provider_usage()
        return deduped

    def _record_provider_usage(self) -> None:
        """Persists the provider usage/cost ledger after a generation run."""
        if self.provider is None or self.provider.usage is None:
            return
        try:
            from nexus_scalp.strategies.factory.store import record_provider_usage

            usage = self.provider.usage.snapshot()
            record_provider_usage(
                self._research_backend,
                {
                    "usage_id": f"u_{int(time.time())}",
                    "generation_id": self.current_generation_id,
                    "requests": usage.get("requests", 0),
                    "failures": usage.get("failures", 0),
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "estimated_cost_usd": usage.get("estimated_cost_usd", 0.0),
                    "last_latency_ms": usage.get("last_latency_ms", 0.0),
                    "last_error": usage.get("last_error", ""),
                    "created_at": _now().isoformat(),
                },
            )
        except Exception as e:
            logger.warning("[STRATEGY_FACTORY] provider usage record failed", error=str(e))

    def _generation_zero_population(
        self, generation_id: str, population: int
    ) -> list[FactoryCandidate]:
        """Generation 0 mixture: templates / diversity / regime / random +
        optional LLM slice (spec 6)."""
        from nexus_scalp.strategies.factory.dsl import generate_generation_zero

        candidates = generate_generation_zero(population, seed=RANDOM_SEED)
        candidates = [c.model_copy(update={"generation_id": generation_id}) for c in candidates]
        # LLM-assisted slice (spec 6): the provider fills the LLM slot when
        # configured; deterministic generators are ALWAYS the correctness base.
        if self.provider is not None and self.provider.available():
            llm = self._llm_candidates(generation_id, population)
            candidates = self._merge_llm_slice(candidates, llm, population)
        else:
            # Provider unconfigured/unavailable: the DSL generator already
            # reserved LLM slots as placeholders (source=LLM) - re-tag them
            # as TEMPLATE so the population NEVER contains fake LLM rows.
            candidates = [
                c.model_copy(update={"source": CandidateSource.TEMPLATE})
                if c.source == CandidateSource.LLM
                else c
                for c in candidates
            ]
        # Family diversity injection: ensure all families present in G0.
        candidates = _ensure_family_coverage(candidates, generation_id)
        return candidates

    def _merge_llm_slice(
        self,
        base: list[FactoryCandidate],
        llm: list[FactoryCandidate],
        population: int,
    ) -> list[FactoryCandidate]:
        """Replaces the LLM-sourced slice (source == LLM) of the deterministic
        population with the provider-generated candidates.

        The deterministic generators reserve the LLM slot (last ~30%); the
        provider output REPLACES those placeholder candidates so the final
        population keeps its size, every LLM candidate is content-addressed,
        and the deterministic base is untouched (spec 6 / 13).
        """
        if not llm:
            return base
        out: list[FactoryCandidate] = []
        llm_idx = 0
        for c in base:
            if c.source == CandidateSource.LLM and llm_idx < len(llm):
                out.append(llm[llm_idx])
                llm_idx += 1
            else:
                out.append(c)
        # Overflow LLM candidates (provider returned more than the slot)
        # are dropped: the population size is fixed by the caller.
        return out[:population]

    def _llm_candidates(self, generation_id: str, n: int) -> list[FactoryCandidate]:
        """Requests `n` strategy DSLs from the configured LLM provider and
        rehydrates them into content-addressed FactoryCandidates.

        Provider output is UNTRUSTED DATA: every candidate still goes through
        the full structural gate chain (validators) before any evaluation is
        scheduled (spec 9 / 34 / 90). Never raises.
        """
        if self.provider is None or not self.provider.available():
            return []
        try:
            raw_dsls = self.provider.generate_dsls(self._llm_prompt_context(), n)
        except Exception as e:
            logger.warning("[STRATEGY_FACTORY] LLM generation failed (isolated)", error=str(e))
            return []
        if not raw_dsls:
            return []
        from nexus_scalp.strategies.factory.dsl import (
            candidate_id_from_hash,
            canonicalize_dsl,
            dsl_hash,
        )

        out: list[FactoryCandidate] = []
        for raw in raw_dsls:
            try:
                dsl = canonicalize_dsl(raw)
            except Exception as e:
                logger.warning("[STRATEGY_FACTORY] LLM DSL rejected (invalid schema)", error=str(e))
                continue
            digest = dsl_hash(dsl)
            out.append(
                FactoryCandidate(
                    candidate_id=candidate_id_from_hash(digest),
                    definition_hash=digest,
                    generation_id=generation_id,
                    source=CandidateSource.LLM,
                    operator=EvolutionOperator.NONE,
                    dsl=dsl,
                    family=dsl.family,
                    population_index=0,
                )
            )
        return out

    def _llm_prompt_context(self) -> dict[str, Any]:
        """Builds the prompt context the LLM provider consumes.

        Feature catalog is derived AT RUNTIME from the canonical 70D schema
        contract — a generated strategy can never invent or change the
        feature vector dimension (spec 10).
        """
        try:
            from nexus_scalp.strategies.factory.dsl import feature_ids
            from nexus_scalp.strategies.factory.summarizer import format_summary_for_prompt

            feature_list = feature_ids()
        except Exception:
            feature_list = []
        memory: dict[str, Any] = {}
        try:
            memory = self.build_memory()
        except Exception:
            memory = {}
        cfg = self.config
        context: dict[str, Any] = {
            "feature_ids": feature_list,
            "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
            "symbols": self.symbols,
            "max_conditions": cfg.max_conditions,
            "max_features": cfg.max_features,
            "max_timeframes": cfg.max_timeframes,
            "generation_objective": "Produce diverse, robust, causally-clean strategy hypotheses.",
        }
        if memory:
            try:
                context["research_memory"] = format_summary_for_prompt(memory)
            except Exception:
                context["research_memory"] = memory
        return context

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
            action: str | None = None
            if roll < probs["mutation_rate"] and elite_pool:
                base = rng.choice(elite_pool)
                # G28 TARGET 2: per-action attribution (which mutation fired).
                child, action = mutate_with_action(base, rng=rng, budgets=self._budgets())
                op = "MUTATION"
            elif roll < probs["mutation_rate"] + probs["crossover_rate"] and len(elite_pool) >= 2:
                a, b = rng.sample(elite_pool, 2)
                child = crossover(a, b, rng=rng, budgets=self._budgets())
                op = "CROSSOVER"
                action = "crossover"
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
                action = None
            self._tally_operator(op, len(out))
            if action is not None:
                # Per-action accounting: generated (+ parent linkage for later
                # valid / wf_pass / oos_pass / improved attribution).
                self._tally_action(action, "generated")
                self._candidate_action[child.candidate_id] = {
                    "action": action,
                    "parent_id": (child.parent_ids or [""])[0],
                }
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
            if _score_dict(e).get("verdict") == "VALIDATED"
            and float(_score_dict(e).get("final_score", 0.0) or 0.0) >= 0.6
        ]
        elites.sort(
            key=lambda e: float(_score_dict(e).get("final_score", 0.0) or 0.0),
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
        self._persist_operator_accounting()

    # ------------------------------------------------------------------
    # Operator accounting persistence + semantic clone pre-screen
    # (G28 TARGET 1 / TARGET 2, forensic 2026-08-24)
    # ------------------------------------------------------------------

    #: Bounded cluster registry: at most this many signatures are tracked.
    MAX_CLONE_CLUSTERS = 512

    def _load_operator_accounting(self) -> None:
        """Restores cumulative operator evidence from factory_loop_state.

        Never raises: a missing/corrupt row degrades to a fresh in-memory
        registry (the legacy behavior) — restart must not brick the factory.
        """
        try:
            from nexus_scalp.strategies.factory.store import get_operator_stats

            persisted = get_operator_stats(self._research_backend) or {}
        except Exception as e:
            logger.warning(
                "[STRATEGY_FACTORY] operator accounting load failed (fresh start)",
                error=str(e),
            )
            return
        operators = persisted.get("operators")
        if isinstance(operators, dict):
            for op, stats in operators.items():
                if isinstance(stats, dict):
                    self._operator_stats[str(op)] = {
                        str(k): int(v) for k, v in stats.items() if isinstance(v, (int, float))
                    }
        actions = persisted.get("actions")
        if isinstance(actions, dict):
            for action, stats in actions.items():
                if isinstance(stats, dict):
                    self._operator_actions[str(action)] = {
                        str(k): int(v) for k, v in stats.items() if isinstance(v, (int, float))
                    }
        clusters = persisted.get("clone_clusters")
        if isinstance(clusters, dict):
            for sig, info in clusters.items():
                if isinstance(info, dict):
                    self._clone_clusters[str(sig)] = {
                        "members": max(0, int(info.get("members", 0) or 0)),
                        "oos_passes": max(0, int(info.get("oos_passes", 0) or 0)),
                    }

    def _persist_operator_accounting(self) -> None:
        """Writes cumulative operator evidence to factory_loop_state."""
        try:
            from nexus_scalp.strategies.factory.store import set_operator_stats

            set_operator_stats(
                self._research_backend,
                {
                    "operators": self._operator_stats,
                    "actions": self._operator_actions,
                    "clone_clusters": self._clone_clusters,
                    "updated_at": _now().isoformat(),
                },
            )
        except Exception as e:
            logger.warning(
                "[STRATEGY_FACTORY] operator accounting persist failed (isolated)",
                error=str(e),
            )

    def _behavior_cluster(self, signature: str) -> dict[str, int]:
        """Returns (creating if needed) the cluster record for one signature."""
        cluster = self._clone_clusters.setdefault(signature, {"members": 0, "oos_passes": 0})
        # Bound the registry: drop the OLDEST-inserted signatures first.
        while len(self._clone_clusters) > self.MAX_CLONE_CLUSTERS:
            oldest = next(iter(self._clone_clusters))
            self._clone_clusters.pop(oldest, None)
        return cluster

    def _is_pathological_clone(self, cluster: dict[str, int]) -> bool:
        """A known cluster is pathological when it has >= min members and has
        NEVER produced an OOS pass (the exact shape of the 345-clone cluster)."""
        return (
            int(cluster.get("members", 0)) >= self.config.clone_cluster_min_members
            and int(cluster.get("oos_passes", 0)) <= 0
        )

    def _record_behavior_outcome(self, signature: str, oos_pass: bool) -> None:
        """Records one REAL pipeline outcome against its behavioral cluster."""
        cluster = self._behavior_cluster(signature)
        cluster["members"] = int(cluster.get("members", 0)) + 1
        if oos_pass:
            cluster["oos_passes"] = int(cluster.get("oos_passes", 0)) + 1
        self._persist_operator_accounting()

    #: Per-action counter keys (G28 TARGET 2 handoff contract).
    ACTION_COUNTER_KEYS = (
        "generated",
        "valid",
        "wf_pass",
        "oos_pass",
        "improved",
        "improvement_delta",
    )

    def _tally_action(self, action: str, key: str, amount: float = 1.0) -> None:
        """Bumps one per-action counter and persists the accounting state."""
        stats = self._operator_actions.setdefault(
            action, dict.fromkeys(self.ACTION_COUNTER_KEYS, 0)
        )
        stats[key] = float(stats.get(key, 0) or 0) + float(amount)
        self._persist_operator_accounting()

    def _attribute_action_outcome(
        self,
        candidate: FactoryCandidate,
        result: dict[str, Any],
    ) -> None:
        """Attributes wf_pass / oos_pass / improved to the producing action.

        Uses ONLY validation-tier outcomes (WF pass flag, OOS pass FLAG — not
        scores) plus the parent-vs-child in-sample expectancy delta for
        `improvement_delta`. No OOS score ever feeds probabilities (leakage
        boundary, SEARCH_LEARNING_BOUNDARIES.md).
        """
        linkage = self._candidate_action.pop(candidate.candidate_id, None)
        if not linkage:
            return
        action = str(linkage.get("action", ""))
        if not action:
            return
        wf = result.get("walkforward") or {}
        oos = result.get("oos") or {}
        if bool(wf.get("passed")):
            self._tally_action(action, "wf_pass")
        if oos.get("status") == "PASS":
            self._tally_action(action, "oos_pass")
            child_exp = float((result.get("backtest") or {}).get("expectancy_r", 0.0) or 0.0)
            parent_exp = self._parent_expectancy(str(linkage.get("parent_id", "")))
            if parent_exp is not None:
                delta = child_exp - parent_exp
                self._tally_action(action, "improvement_delta", delta)
                if delta > 0:
                    self._tally_action(action, "improved")

    def _parent_expectancy(self, strategy_id: str) -> float | None:
        """Parent's recorded IN-SAMPLE backtest expectancy (baseline for
        improvement deltas). None when the parent has no registry row."""
        if not strategy_id:
            return None
        try:
            from nexus_scalp.research.store import get_registry_entry

            entry = get_registry_entry(self.audit_repo, strategy_id)
            bt = _decode_registry_row(entry or {}).get("backtest") or {}
            raw = bt.get("expectancy_r")
            return float(raw) if raw is not None else None
        except Exception:
            return None

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
            self._research_backend,
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
        failure_reasons: list[str] | None = None,
    ) -> None:
        if preserve_structural:
            # Keep the existing structural verdict from the first upsert
            # (immutability: evaluation must not erase the validator result).
            from nexus_scalp.strategies.factory.store import get_candidate_structural

            existing = get_candidate_structural(self._research_backend, candidate.candidate_id)
            structural = (
                existing if existing is not None else (verdict.model_dump() if verdict else None)
            )
        else:
            structural = verdict.model_dump() if verdict else None
        if failure_reasons is None:
            failure_reasons = (
                [verdict.failure_reason.value] if verdict and verdict.failure_reason else []
            )
        upsert_candidate(
            self._research_backend,
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
                "failure_reasons": failure_reasons,
                "llm_response_id": candidate.llm_response_id,
                "created_at": candidate.created_at.isoformat(),
            },
        )

    def _persist_failure(self, candidate: FactoryCandidate, verdict: Any) -> None:
        record_failure(
            self._research_backend,
            {
                "failure_id": f"fail_{uuid.uuid4().hex[:16]}",
                "candidate_id": candidate.candidate_id,
                "strategy_id": candidate.candidate_id,
                "generation_id": candidate.generation_id,
                "stage": verdict.stage.value,
                "reason": verdict.failure_reason.value
                if verdict.failure_reason
                else "INVALID_SCHEMA",
                "detail": {"message": verdict.reasons, **verdict.details},
                "created_at": _now().isoformat(),
            },
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

        SEMANTIC CLONE PRE-SCREEN (G28 TARGET 1):
        Before running the expensive research pipeline, computes the
        behavioral-preview signature from the candidate's DSL + projected sample
        subset. If it matches a known cluster with >= clone_cluster_min_members
        members and 0 OOS passes, skips evaluation and records CLONE_SKIPPED.
        Pure budget rescue (~30% budget saved); does NOT alter structural dedup
        hashes and never weakens validation gates.

        BENCHMARK (2026-08-21): stamps a strategy-aware benchmark artifact
        (coverage + per-gate explainability) onto factory_runs and emits it
        in the candidate event so the API/AI can rank without re-running the
        pipeline.
        """
        started = time.perf_counter()
        try:
            from nexus_scalp.strategies.factory.benchmark import behavioral_preview_signature

            snapshot = self._ledger_snapshot_for_filter()
            sig = behavioral_preview_signature(candidate, snapshot)
            cluster = self._behavior_cluster(sig)

            if self.config.clone_prescreen_enabled and self._is_pathological_clone(cluster):
                # CLONE PRE-SCREEN TRIGGERED: known behavioral clone with high
                # volume and 0 historical OOS passes. Skip evaluation.
                duration_ms = (time.perf_counter() - started) * 1000.0
                self._persist_candidate(
                    candidate,
                    lifecycle="REJECTED",
                    verdict=None,
                    preserve_structural=True,
                    failure_reasons=[FailureReason.CLONE_SKIPPED.value],
                )
                record_failure(
                    self._research_backend,
                    {
                        "failure_id": f"fail_{uuid.uuid4().hex[:16]}",
                        "candidate_id": candidate.candidate_id,
                        "strategy_id": candidate.candidate_id,
                        "generation_id": candidate.generation_id,
                        "stage": FactoryStage.OOS.value,
                        "reason": FailureReason.CLONE_SKIPPED.value,
                        "detail": {
                            "behavioral_signature": sig,
                            "cluster_members": cluster.get("members", 0),
                            "cluster_oos_passes": cluster.get("oos_passes", 0),
                            "message": "Semantic clone pre-screen skipped evaluation (known zero-edge behavior cluster)",
                        },
                        "created_at": _now().isoformat(),
                    },
                )
                emit_event(
                    self._research_backend,
                    {
                        "event_id": _event_id(),
                        "generation_id": candidate.generation_id,
                        "candidate_id": candidate.candidate_id,
                        "event_type": "CLONE_SKIPPED",
                        "message": f"{candidate.candidate_id} -> CLONE_SKIPPED (sig {sig[:12]}..., members={cluster.get('members', 0)})",
                        "payload": {
                            "behavioral_signature": sig,
                            "cluster_members": cluster.get("members", 0),
                            "duration_ms": round(duration_ms, 1),
                        },
                    },
                )
                return {
                    "lifecycle": "REJECTED",
                    "failure_reasons": [FailureReason.CLONE_SKIPPED.value],
                    "score": {"verdict": "REJECTED", "final_score": 0.0},
                    "oos": {"status": "SKIPPED_CLONE"},
                    "backtest": {},
                }

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

            # Record outcome against the behavioral clone cluster
            oos_passed = oos.get("status") == "PASS"
            self._record_behavior_outcome(sig, oos_passed)
            # Per-action attribution (G28 TARGET 2) — no-op for non-evolved
            # candidates (no linkage recorded at generation time).
            self._attribute_action_outcome(candidate, result)
            if candidate.candidate_id in self._candidate_action:
                linkage = self._candidate_action[candidate.candidate_id]
                action = str(linkage.get("action", ""))
                if lifecycle not in ("GENERATED", "", "DISCOVERED"):
                    self._tally_action(action, "valid")

            failed_reasons = self._derived_failure_reasons(result, candidate)

            # PHASE 25 (2026-08-25) EVIDENCE LIFECYCLE: a backtest that
            # fails ONLY on low trade count / small sample while
            # expectancy stays positive is NOT terminal. Park the
            # candidate in EVIDENCE_BUILDING with INSUFFICIENT_EVIDENCE
            # so it can be re-tested on more data instead of being
            # discarded as REJECTED. No gate threshold changes.
            if lifecycle == "REJECTED" and self._is_evidence_only_failure(result, candidate):
                lifecycle = "EVIDENCE_BUILDING"
                failed_reasons = [FailureReason.INSUFFICIENT_EVIDENCE.value]
            # BENCHMARK ARTIFACT (pure, never mutates the pipeline result);
            # reuses the ledger snapshot already taken by the clone pre-screen.
            try:
                coverage = candidate_coverage_stats(candidate, snapshot)
                benchmark = build_benchmark_artifact(candidate, result, coverage)
            except Exception:
                benchmark = {}
                coverage = {}
            # Persist benchmark into factory_runs (reproducibility, AI surface)
            try:
                from nexus_scalp.strategies.factory.store import record_run as _record_run

                _record_run(
                    self._research_backend,
                    {
                        "run_id": (
                            run_id or result.get("run_id") or f"run_{uuid.uuid4().hex[:12]}"
                        ),
                        "generation_id": candidate.generation_id,
                        "candidate_id": candidate.candidate_id,
                        "strategy_id": candidate.candidate_id,
                        "lifecycle": lifecycle,
                        "score": score,
                        "benchmark": benchmark,
                        "created_at": _now().isoformat(),
                    },
                )
            except Exception:
                pass
            self._persist_candidate(
                candidate,
                lifecycle=lifecycle,
                verdict=None,
                preserve_structural=True,
            )
            for reason in failed_reasons:
                record_failure(
                    self._research_backend,
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
                    },
                )

            self._tally_operator_survival(candidate, lifecycle)
            emit_event(
                self._research_backend,
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
                        "benchmark": benchmark,
                        "coverage": coverage.get("coverage", {})
                        if isinstance(coverage, dict)
                        else {},
                    },
                },
            )
            return {**result, "benchmark": benchmark}
        except Exception as e:
            logger.error(
                "[STRATEGY_FACTORY] candidate evaluation failed",
                candidate_id=candidate.candidate_id,
                error=str(e),
                exc_info=True,
            )
            self._persist_candidate(candidate, lifecycle="FAILED")
            record_failure(
                self._research_backend,
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

    def _ledger_snapshot_for_filter(self) -> list[dict[str, Any]]:
        """Pure snapshot of the ledger needed for DSL-aware filtering.

        Returns a list of dicts: {idempotency_key, feature_values}.
        Sourced from the audit DB's audit_experiences.feature_snapshot.
        Bounded (5000 rows) to keep per-candidate filtering cheap.
        """
        rows: list[dict[str, Any]] = []
        try:
            import json as _json
            import sqlite3 as _sq

            repo = self.audit_repo
            db_path = getattr(repo, "_db_path", None)
            if not db_path:
                return rows
            conn = _sq.connect(str(db_path), timeout=3.0)
            conn.row_factory = _sq.Row
            try:
                for r in conn.execute(
                    "SELECT idempotency_key, payload FROM audit_experiences "
                    "ORDER BY decision_timestamp DESC LIMIT 5000"
                ):
                    try:
                        payload = _json.loads(r["payload"] or "{}")
                        vals = (payload.get("feature_snapshot") or {}).get("values") or []
                        if isinstance(vals, list) and vals:
                            rows.append(
                                {
                                    "idempotency_key": str(r["idempotency_key"]),
                                    "feature_values": vals,
                                }
                            )
                    except Exception:
                        continue
            finally:
                conn.close()
        except Exception:
            pass
        return rows

    def _to_strategy_candidate(self, candidate: FactoryCandidate) -> Any:
        """Builds the research StrategyCandidate from a factory candidate.

        FIX 2026-08-21 (BENCHMARK): populates discovery_evidence.sample_ids
        via DSL-aware replay over the ledger's real 50D snapshots. Without
        this the research pipeline's _select_family falls back to the full
        dataset, so every SF-* grades the SAME 90-sample losing book
        (expectancy -0.06R, OOS -0.14R, score 0.3516). With sample_ids the
        pipeline restricts backtest / walk-forward / OOS / robustness to the
        candidate's OWN filtered slice — scores DIVERGE and benchmarks become
        strategy-aware (the "help AI decides" surface).
        """
        from nexus_scalp.research.candidates import StrategyCandidate
        from nexus_scalp.research.models import CandidateLifecycle

        dsl = candidate.dsl.model_dump()
        strategy_id = candidate.candidate_id
        symbols = dsl.get("market", {}).get("symbols") or self.symbols
        snapshot = self._ledger_snapshot_for_filter()
        sample_ids = benchmark_subset_for_candidate(candidate, snapshot)
        return StrategyCandidate(
            strategy_id=strategy_id,
            strategy_version="",
            feature_schema_id="scalp_v3",
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
                "sample_ids": sample_ids,
                "sample_coverage": candidate_coverage_stats(candidate, snapshot)["coverage"],
            },
        )

    def _derived_failure_reasons(
        self, result: dict[str, Any], candidate: FactoryCandidate
    ) -> list[str]:
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

    def _is_evidence_only_failure(
        self, result: dict[str, Any], candidate: FactoryCandidate
    ) -> bool:
        """PHASE 25 (2026-08-25): True when the evaluation failed ONLY because
        of low trade count / small sample while expectancy stayed positive.

        Such candidates are NOT terminally rejected - they are parked in
        lifecycle EVIDENCE_BUILDING with FailureReason.INSUFFICIENT_EVIDENCE so
        they can be re-tested once more data accrues. Gate thresholds
        (min_trades, drawdown, profit factor, WF/OOS/robustness floors) are
        UNCHANGED; only the disposition of a small-sample-positive-expectancy
        outcome changes from REJECT to EVIDENCE_BUILDING.
        """
        reasons = self._derived_failure_reasons(result, candidate)
        if not reasons:
            return False
        hard_failures = {
            r
            for r in reasons
            if r
            not in (
                FailureReason.INSUFFICIENT_TRADES.value,
                FailureReason.INSUFFICIENT_EVIDENCE.value,
            )
        }
        if hard_failures:
            return False
        bt = result.get("backtest") or {}
        exp = float(bt.get("expectancy_r", 0.0) or 0.0)
        return exp > 0.0

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
            FailureReason.INSUFFICIENT_TRADES.value: FactoryStage.BACKTEST.value,
            FailureReason.INSUFFICIENT_EVIDENCE.value: FactoryStage.BACKTEST.value,
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

        gen = get_generation(self._research_backend, generation_id) or {}
        candidates = list_candidates(
            self._research_backend, generation_id=generation_id, limit=2000
        )
        registry_entries = self._registry_rows_for_generation(candidates)
        summary = build_summary(
            gen,
            candidates,
            registry_entries,
            operator_stats=self._operator_stats,
            runtime_ms=0.0,
        )
        ranked = rank_strategies(registry_entries, limit=100)
        elite = [e for e in ranked if _score_dict(e).get("verdict") == "VALIDATED"][
            : self.config.elite_size
        ]

        raw_config = gen.get("config") or {}
        if isinstance(raw_config, str):
            try:
                raw_config = _json.loads(raw_config) if raw_config.strip() else {}
            except Exception:
                raw_config = {}
        upsert_generation(
            self._research_backend,
            {
                **gen,
                "status": "COMPLETED",
                "completed_at": _now().isoformat(),
                "config": {**raw_config, "summary": summary.model_dump()},
            },
        )
        self._last_run_summary = summary.model_dump()
        emit_event(
            self._research_backend,
            {
                "event_id": _event_id(),
                "generation_id": generation_id,
                "event_type": "GENERATION_COMPLETED",
                "message": f"Generation {generation_id} completed",
                "payload": summary.model_dump(),
            },
        )
        self._send_telegram(
            "GENERATION_COMPLETED",
            {"generation_id": generation_id, "summary": summary.model_dump()},
        )
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
        gens = list_generations(self._research_backend, limit=50)
        summaries: list[Any] = []
        for g in gens:
            cfg = g.get("config") or {}
            s = cfg.get("summary")
            if s:
                summaries.append(s)
        from nexus_scalp.research.store import list_registry

        entries = list_registry(self.audit_repo, limit=200)
        elite = [e for e in entries if _score_dict(e).get("verdict") == "VALIDATED"]
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
        logger.info(
            "[STRATEGY_FACTORY] event=GENERATION_STARTED generation_id=%s size=%s "
            "mode=MANUAL source=%s",
            generation_id,
            size,
            "LLM" if (self.provider is not None and self.provider.available()) else "DETERMINISTIC",
        )
        population = self.generate_population(generation_id, size=size, memory=memory)
        logger.info(
            "[STRATEGY_FACTORY] event=GENERATED generation_id=%s population=%s",
            generation_id,
            len(population),
        )
        validation = self.validate_population(population)
        logger.info(
            "[STRATEGY_FACTORY] event=VALIDATED generation_id=%s passed=%s rejected=%s "
            "structural_gates=ENFORCED",
            generation_id,
            len(validation["passed"]),
            len(validation.get("rejected", [])),
        )

        dataset = self._build_dataset()
        evaluated = 0
        for candidate in validation["passed"]:
            if self._kill_requested:
                break
            self.evaluate_candidate(candidate, dataset)
            evaluated += 1

        logger.info(
            "[STRATEGY_FACTORY] event=BACKTESTED generation_id=%s evaluated=%s "
            "pipeline=BACKTEST+WALKFORWARD+OOS+ROBUSTNESS",
            generation_id,
            evaluated,
        )
        completion = self.complete_generation(generation_id)
        logger.info(
            "[STRATEGY_FACTORY] event=COMPLETED generation_id=%s evaluated=%s elite=%s status=%s",
            generation_id,
            evaluated,
            completion.get("elite_count", completion.get("elite", 0)),
            completion.get("status", "COMPLETED"),
        )
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
        gen = get_generation(self._research_backend, generation_id) or {}
        if not gen:
            return {"status": "NOT_FOUND"}
        candidates = list_candidates(
            self._research_backend, generation_id=generation_id, limit=2000
        )
        pending = [
            c
            for c in candidates
            if c.get("lifecycle") in ("GENERATED", None, "", "DISCOVERED", "RUNNING")
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
    for col in (
        "backtest",
        "walkforward",
        "oos",
        "robustness",
        "score",
        "context_definition",
        "parent_strategy_ids",
    ):
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
