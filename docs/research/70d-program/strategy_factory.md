# Strategy Factory — Autonomous Strategy Evolution, Research, Validation, Ranking & Strategy Factory

**Date:** 2026-08-20
**Subsystem:** `src/nexus_scalp/strategies/factory/`
**Phase:** 22 (Strategy Factory)

The Strategy Factory is an autonomous quantitative research laboratory integrated
with the existing Nexus research/backtesting/validation architecture (Phase 09B).
It generates populations of candidate strategies, validates them structurally,
backtests them through the authoritative deterministic research pipeline, ranks
and preserves elite strategies, analyzes failures, and evolves the next
generation from accumulated research memory.

## Architecture (spec 2 — never blur responsibilities)

```
LLM (optional provider)          = Strategy Generation / Analysis Brain
Database (audit.db factory_*)    = Persistent Long-Term Research Memory
Strategy DSL (dsl.py)            = Machine-readable strategy representation
Validators (validators.py)       = Hard structural gates (schema/features/causality/complexity/dedup)
Research Pipeline (research/)    = DETERMINISTIC JUDGE (backtest/WF/OOS/robustness/score)
Evolution Engine (evolution.py)  = Mutation / Crossover / Exploration
Orchestrator (orchestrator.py)   = Coordinates the full lifecycle
Ranking (ranking.py)             = Multi-dimensional scoring + explainable ranks
Summarizer (summarizer.py)       = Research memory / learning context
UI (Web/ tab-factory)            = Research Control Room
Telegram (telegram.py)           = Lifecycle observability + reports
Worker (worker.py)               = Autonomous loop + crash recovery
```

## Safety Contract (spec 61 / 62 / 63 / 105)

- The factory NEVER places, modifies or closes an order. It holds no adapter,
  risk engine or order manager (verified by tests).
- The LLM provider is UNTRUSTED INPUT: every candidate it produces passes the
  same deterministic structural gates as template-generated ones.
- All measured performance comes EXCLUSIVELY from `ResearchPipeline.validate_candidate`
  — the LLM never computes or claims performance (spec 69 / 70).
- A strategy can NEVER become ACTIVE automatically; promotion remains
  operator-gated (`approve_for_live` in research/lifecycle.py).
- Global risk governance always wins; generated strategies only declare risk
  ASSUMPTIONS.
- The 70D feature contract (`scalp_v3`) is never modified by a generated
  strategy; the feature catalog is DERIVED from `features/schema_contract.py`
  (spec 9 / 10).

## Strategy DSL (spec 8)

Every candidate is a structured `StrategyDsl`:

```
schema_version, hypothesis{statement, market_mechanism, expected_regime,
invalidation, abstain_conditions}, family, market{symbols, timeframes},
context, setup, entry{logic, confirmation[]}, filters[{feature, op, value}],
exit{mode}, risk, constraints{no_future_data}
```

- `extra="forbid"` — unknown keys are rejected by Pydantic.
- Canonical identity: `dsl_hash()` → content-addressed `candidate_id` (SF-XXXX).
- Only the 70 approved features from the canonical catalog may be referenced
  (feature governance, spec 9).
- Every candidate MUST declare `no_future_data` / completed-bar semantics
  (causality gate, spec 15 — NON-NEGOTIABLE).

## Hard Gates (validators.py, spec 14 / 59)

| Gate | Failure reason |
|---|---|
| Schema / symbols / timeframes | INVALID_SCHEMA / UNSUPPORTED_SYMBOL / UNSUPPORTED_TIMEFRAME |
| Feature existence in 70D catalog | UNSUPPORTED_FEATURE |
| Causality / lookahead | LOOKAHEAD_RISK |
| Complexity budget (conditions/features/timeframes) | EXCESSIVE_COMPLEXITY |
| Canonical dedup within population | DUPLICATE |

An LLM response that fails ANY gate is REJECTED before scheduling — never
silently repaired or executed.

## Evolution (evolution.py, spec 7 / 98 / 99)

- `mutate()`: add/remove filter, replace indicator, change threshold, change
  timeframe, change condition, simplify. Re-validated; a no-op mutation
  (identical hash) is rejected.
- `crossover()`: combines compatible parts of two candidates; rejects semantic
  contradictions (incompatible symbols) and excessive complexity.
- `explore()`: controlled random exploration with fresh family templates.
- `adapt_probabilities()`: bounded adaptive operator mixing from historical
  operator success + diversity pressure (spec 99).

## Generation Lifecycle (orchestrator.py)

```
create_generation() -> generate_population() -> validate_population()
  -> evaluate_candidate() [research pipeline] -> complete_generation()
```

- Generation 0: 30% templates / 20% feature-combination / 10% regime / 10%
  random / 30% LLM slot (deterministic templates fill when no provider), with
  family-coverage enforcement (all 11 families present).
- Subsequent generations: elite preservation + mutation + crossover +
  exploration with adaptive probabilities.
- Every stage is persisted: `factory_generations`, `factory_candidates`,
  `factory_failures`, `factory_events`, `factory_runs`,
  `factory_provider_usage`, `factory_loop_state`.

## Autonomous Loop (worker.py, spec 55 / 73 / 74 / 106)

- Control plane: START / RUNNING / PAUSED / STOPPING / STOPPED / FAILED /
  RECOVERING (persisted in `factory_loop_state`).
- Kill switch (`stop_loop`) stops new generations + LLM requests without
  corrupting history; the final STOPPED state is persisted.
- Crash recovery: `recover()` reloads the active generation and resumes from
  the first candidate without a recorded evaluation (idempotent).
- Stopping conditions: max generations, max runtime, no-improvement
  generations (stagnation detection), target elite count.

## Ranking (ranking.py, spec 21 / 22 / 53)

- `selection_score()`: weighted combination of research score, OOS, robustness,
  walk-forward consistency, complexity, sample, regime, drawdown.
- Components stored individually; every rank position carries `_components`
  for explainability ("why did this strategy rank #7?").
- Dimensions: OVERALL / OOS / ROBUSTNESS / RISK_ADJUSTED / CONSISTENCY /
  REGIME / LOW_DRAWDOWN / HIGH_EXPECTANCY / DIVERSITY.

## LLM Provider (provider.py, spec 33 / 34 / 45 / 86 / 88 / 89 / 90)

- OpenAI-compatible `/chat/completions`, config-driven (base URL, model, key
  from the secure secret store — never hardcoded, never logged).
- OPTIONAL: when unconfigured, `available() == False` and the deterministic
  generator is authoritative — the factory NEVER depends on the LLM.
- Bounded: `max_requests_per_generation`, request timeout, usage/cost ledger
  (`factory_provider_usage`).
- Response: strict JSON (`{"strategies": [...]}`), repair-once-then-reject,
  NEVER executed as code.

## Telegram (telegram.py, spec 46 / 47)

- Bounded lifecycle event types (no per-candidate spam):
  GENERATION_STARTED / GENERATION_COMPLETED / GENERATION_PROGRESS /
  IMPORTANT_STRATEGY_FOUND / ELITE_PROMOTED / STRATEGY_REJECTED /
  RESEARCH_FAILURE / SYSTEM_FAILURE / LOOP_PAUSED / LOOP_RESUMED /
  DEPLOYMENT_GATE.
- Enqueued through the engine's `TelegramNotifier` (never blocks, never raises).

## REST API (`/api/factory/*`)

status, generations, generations/{id}, candidates, events, failures, ranking,
memory, generate (POST), evaluate/{candidate_id} (POST), complete/{id} (POST),
loop/start, loop/pause, loop/resume, loop/stop (POST).

## UI (Web/ tab-factory)

Generation size selector (50–400), mode (Manual/Autonomous), Generate button,
loop controls (Start/Pause/Resume/Stop), live generation state, operator
stats, generations list, event stream, failure analysis, rankings. The section
is a top-level sibling tab (BUG-120 discipline); the frontend asset tests
verify div/section balance.

## Database Tables (audit.db, in audit_repository schema)

| Table | Purpose |
|---|---|
| factory_generations | one row per population (PENDING/RUNNING/COMPLETED) |
| factory_candidates | one row per generated candidate + structural verdict + lifecycle |
| factory_failures | structured rejection reasons per candidate (stage + reason + detail) |
| factory_events | immutable event stream for the UI |
| factory_runs | research-run ledger (reproducibility) |
| factory_provider_usage | LLM requests/tokens/cost ledger |
| factory_loop_state | autonomous loop control-plane state (crash-safe) |

## Tests

`tests/unit/test_strategy_factory_phase22.py` — 19 behavioral tests covering:
feature governance (catalog == canonical 70D), canonicalization, hard gates
(unsupported feature / lookahead / complexity / duplicate), generation
determinism + diversity, persistence, evolution operators, adaptive
probabilities, the full generation cycle (persisted candidates/events/
registry rows), explainable ranking, loop control plane, kill switch,
crash recovery, safety contract (no order authority, no LLM fabrication),
REST route registration.

Run: `pytest tests/unit/test_strategy_factory_phase22.py`

## Operational Notes

- Generation evaluation is computationally expensive (backtest + walk-forward
  + OOS + robustness per candidate); use small generations (50–100) during
  development, 400 for production sweeps.
- The research dataset is rebuilt from the immutable experience ledger; an
  empty registry means insufficient closed experiences, not a factory fault.
- Parallel agents may restore over these files between commits; commit after
  every coherent step and verify `git show <sha>:<file>` before relying on a
  fix.
- LLM cost control: set `max_requests_per_generation` (default 60) and
  monitor `/api/factory/status` provider_usage.