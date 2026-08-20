# src/nexus_scalp/signals/_rule_engine.py

- **PURPOSE:** The modern rule-matrix core: `RuleMatrixEngine` with a
  registry-driven rule set (`_register_all`), typed snapshots
  (`RuleConfigurationSnapshot`), per-rule `evaluate_rule(ctx)` dispatch,
  evaluation recording (`_record` → `RuleEvaluationResult`), context
  assembly (`_build_context`), the four stage gates, decision tracing
  (`_trace`, `get_decision_trace`, `get_contribution_trace`), and health/
  registry reports.
- **ARCHITECTURE LAYER:** Signals (rules engine). This is the engine that
  `rule_matrix.py` (the older surface) may delegate to or coexist with —
  both expose `RuleMatrixEngine` with refresh_cache/is_enabled/get_params/
  four-stage evaluation; this module adds the typed snapshot + trace
  observability (debug/contribution analysis), the newer evolution.
- **RESPONSIBILITY:** (a) register the full rule set (with rule ids + eval
  classes from the _evals_* modules); (b) evaluate a rule against a
  context (tick, bars, position, account, config) → typed result with
  status (PASS/VETO/TRIGGER/…) + payload; (c) stage composition
  (pre_trade_entry, pre_trade_filters, in_trade_exits, risk_and_safeguards);
  (d) trace every evaluation for the Debug Hub "rule contribution" view.
- **DEPENDENCIES:** AuditRepository (rule config table), the `_evals_*`
  evaluator classes, rule_catalog (ParameterSpec), logging, pydantic
  (snapshot types).
- **CONNECTS TO:** SignalPolicy (stage consumers), web debug surfaces
  (health/trace endpoints), tests (test_rule_matrix evolution suites).
- **KEY CONCEPTS:** Determinism + observability: the same context yields the
  same result (pure evaluators), and every result is recorded with its rule
  id so the UI can decompose WHY a signal passed/failed; `config_version()`
  bumps on refresh so consumers can detect stale snapshots.
- **EDGE CASES & PITFALLS:** Rule registration ORDER determines evaluation
  order — first-veto-wins semantics in the stage aggregation; a rule whose
  evaluator raises must be isolated (logged + PASS-with-warning) so one buggy
  rule cannot take down the whole gate; keep `refresh_cache` name as the
  compat alias (older callers use it).