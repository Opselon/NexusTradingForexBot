# src/nexus_scalp/configuration/runtime_config.py

- **PURPOSE:** The hot-reload core (skill §13b): a versioned, IMMUTABLE
  runtime configuration store. UI saves → validation → persistent store →
  ConfigurationChanged event → new immutable snapshot → atomic swap; ALL
  new evaluations read the current snapshot (no constructor caches).
  live.yaml is DEMOTED to bootstrap/import/export/compatibility only —
  never the authoritative runtime source.
- **ARCHITECTURE LAYER:** Configuration (authoritative runtime state).
- **RESPONSIBILITY:** (a) `RuntimeConfiguration` — frozen snapshot with
  typed sub-snapshots (Execution/Risk/Algorithm/Model/Telemetry/News/
  RuleMatrix) + convenience accessors (atr_sl_buffer_multiplier,
  risk_per_trade_pct, confidence_threshold, model_artifact_path, ...);
  (b) `RuntimeConfigStore` — thread-safe in-memory provider: lock-free
  snapshot reads, atomic swap, monotonic versioning, event bus
  (ConfigurationChanged); (c) `ConfigChangeEvent` — what changed/from
  which source; (d) `ConfigurationApplyReport` — UI-facing result
  (persisted/applied/version/runtime status); (e) `PersistentConfigStore`
  — settings-DB-backed durable projection (secrets never stored here);
  (f) `build_runtime_configuration` — validated immutable snapshot from
  bootstrap AppConfig / YAML import / partial field update.
- **DEPENDENCIES:** configuration.config (schema models), settings DB
  (persistent store), threading/uuid/hashlib (atomicity/versioning).
- **CONNECTS TO:** LiveEngine (`_sync_runtime_config` per tick),
  signal policy / risk / execution / rule matrix / news / model services,
  web Config API, CLI, tests (versioning/apply/event suites).
- **KEY CONCEPTS:**
  - Immutability + atomic swap: a reader NEVER sees a half-applied config —
  all-or-nothing by construction (hash-versioned snapshot identity).
  - Hot-reload rule: after a successful apply, ALL NEW evaluations use the
  new snapshot; EXISTING positions are NOT retroactively rewritten
  (effective scope per setting audit — the documented behavior, not a bug).
  - Versioning is monotonic: consumers can detect staleness
  (config_version) and the UI shows the applied version.
  - `to_algo_config` / `to_app_config` — projections for legacy consumers
  that still take section models.
- **HOT PATH / PERFORMANCE:** Reads are lock-free (immutable reference
  swap); `_sync_runtime_config` is two attribute assignments per tick —
  the reason UI saves reflect on the NEXT tick without restart and without
  a per-tick DB read.
- **EDGE CASES & PITFALLS:** Secrets must never enter the persistent
  projection (redaction contract); a failed validation must produce a
  report with reason (never a half-swap); the event bus must be
  bounded/observable (a listener exception must not corrupt the swap).