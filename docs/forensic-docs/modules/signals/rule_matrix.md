# src/nexus_scalp/signals/rule_matrix.py

- **PURPOSE:** The DB-driven 30+ rule matrix engine — the configurable
  scalping rule set (HFT, SMC, risk, exit rules) that can VETO or FORCE a
  signal. Rules are enabled/disabled and parameterized from the
  `trading_rules_config` table (toggled live from the UI), with a
  5-second TTL cache.
- **ARCHITECTURE LAYER:** Signals (rules). Reads DB config; holds zero order
  authority.
- **RESPONSIBILITY:** (a) `refresh_cache(force, ttl=5s)` — load rule
  enablement + params from the DB (bounded TTL — the audit/DB hot-path
  invariant: no per-tick DB read); (b) `is_enabled` / `get_params` — the
  per-rule gate accessors; (c) evaluation entry points called by policy:
  `evaluate_pre_trade_entry`, `evaluate_pre_trade_filters`,
  `evaluate_in_trade_exits`, `evaluate_risk_and_safeguards` — each returns
  the aggregated rule verdicts (pass/veto/trigger with reasons).
- **DEPENDENCIES:** `adapters.database.audit_repository.AuditRepository`
  (config table), typing/collections, logging.
- **CONNECTS TO:** SignalPolicy (the decision cascade invokes these four
  stage evaluators), web UI rule toggles (`/api/rules`), tests
  (test_rule_matrix, test_policies etc.).
- **KEY CONCEPTS:**
  - The engine re-exports the rule EVALUATOR implementations from the
    sibling modules (`_rule_engine`, `_rule_evals_hft`, `_rule_evals_rest`,
    `_rule_evals_smc`) — this file is the orchestration surface
    (cache + stage dispatch + verdict aggregation). The underscore-prefixed
    modules hold the per-rule `evaluate(ctx)` classes (FlashMomentumScrape,
    TickImbalanceReversal, SpreadSqueezeOnly, RejectionWallBlocker,
    BidAskSpoof, HitAndRunExit, ZeroDrawdownTrail, TimeDecayChopExit,
    AtrExpansionRatchet, HedgeOnAiFlip, LondonNyKillzoneOnly,
    AsianRangeFakeout, NewsSpikeFade, DeadZoneBlocker, EndOfHourSqueeze,
    ConsecutiveLossFreeze, DailyTargetLock, AiMacroAlignment,
    TurboConfidenceMultiplier, FvgSniperFill, JudasSwingFade,
    LiquiditySweepConfirm, OrderBlockTapReserve, WickAbsorptionPlay, ...).
  - **Rule catalog** (`rule_catalog.py`) — the name→ParameterSpec registry
    (defaults, value ranges, timeframe field) the DB config is validated
    against; a rule not in the catalog cannot be toggled.
  - VETO semantics: a triggered veto rule → NO_TRADE with the rule's id in
    `blocked_by`; FORCE semantics: some rules (e.g. DailyTargetLock,
    ConsecutiveLossFreeze) BLOCK regardless of other confluence — the
    stage gate layout encodes the priority hierarchy (hard veto → regime →
    close validation → pattern → risk → execution).
- **HOT PATH / PERFORMANCE:** Each stage evaluation runs the *enabled*
  rule set only; config snapshot cached 5s; rule params are plain dicts —
  no DB on the tick path.
- **EDGE CASES & PITFALLS:** Fresh test DBs default all rules DISABLED —
  a bare `SignalPolicy()` with `rule_matrix=None` runs NO rule filters
  (only model probabilities decide); to test a rejection you must enable
  the rule AND `refresh_cache(force=True)` (documented test-construction
  pitfall in the skill).