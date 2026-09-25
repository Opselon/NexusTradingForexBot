# src/nexus_scalp/configuration/config.py

- **PURPOSE:** The declarative configuration schema (AppConfig +
  section models) — the bootstrap/import/export/compatibility layer
  ("live.yaml role"). Pydantic-validated defaults with env-var override
  (NSE_ prefix, `__` nested delimiter).
- **ARCHITECTURE LAYER:** Configuration (bootstrap schema — NOT the
  authoritative runtime state; see runtime_config.py).
- **RESPONSIBILITY:** (a) declare every configurable section with typed
  constraints: execution (mode/timeframe/magic/slippage), risk
  (drawdown/risk%/concurrency/spread/lots), telegram (enabled/token/admin),
  mt5 (account/password/server/timeout/portable), model (threshold/schema/
  artifact path/liquidity switch), algo (dynamic quantitative params +
  state-machine + recovery + adaptive weights — the hot-tunable set),
  forensic_report (TASK-12 telegram reports), database_hygiene (TASK-22
  scheduler cadence; the worker stays AUDIT_ONLY by construction);
  (b) `load_from_yaml` — combine YAML + env into a validated AppConfig;
  (c) serve as the import/export projection for the runtime config store.
- **DEPENDENCIES:** pydantic + pydantic_settings, yaml, domain enums
  (ExecutionMode), candle_intelligence.config + news.config (nested
  optional sections).
- **CONNECTS TO:** CLI (startup load), runtime_config (builds immutable
  snapshots FROM this schema), every service that takes section models.
- **KEY CONCEPTS:**
  - The module docstring is the authority: these models are PURE
    DECLARATIVE — consumers MUST read the CURRENT runtime snapshot via
    `RuntimeConfigStore.get_snapshot()` for LIVE_IMMEDIATE/NEXT_DECISION
    params; constructor-time values are bootstrap only. UI saves flow
    config API → validation → persistent store → versioned snapshot →
    engine sync (`_sync_runtime_config` each tick).
  - `AlgoConfig` constraints encode the engine's operational envelope:
    atr_sl_buffer 1.5 (0.5..4), min RR 1.8, high-confidence 0.95,
    fvg sensitivity 0.5, weight vector (profit retention .30, trajectory
    .15, drawdown velocity .15, reversal .20, recovery .10, hold .10).
  - `model.liquidity_features_enabled` (default False = exact 50D behavior;
    True exposes the 60D/70D family to candidate pipelines; NEVER silently
    alters live schema expectations — manifests record the dimension).
  - `DatabaseHygieneConfig` drives the SCHEDULER only (interval/depth/
    reporting); destructive decisions remain opt-in (`apply_deletes`,
    `aggressive_cleanup` default False). Retention: telemetry 7d, cache
    24h, failed jobs 14d, audit_days=0 (financial truth NEVER purged).
- **EDGE CASES & PITFALLS:** `load_from_yaml` raises FileNotFoundError for
  missing files — callers must handle; telegram bot_token is blanked in
  API responses (masking lives in the service layer); adding a section is
  additive here (contract §15) but requires the runtime config snapshot to
  expose it for hot-reload.