# src/nexus_scalp/features/liquidity_runtime.py

- **PURPOSE:** The thread-safe LIVE wrapper around the liquidity engine
  (TASK-2): `LiquidityGovernor` computes liquidity features on new-bar
  cadence (info-only per INV-020), exposes ENABLED/DISABLED/DEGRADED/
  UNAVAILABLE status + causal VALID/STALE/INVALID state, tracks latency, and
  builds the canonical `build_70d_vector` (strict — never pad/truncate).
- **ARCHITECTURE LAYER:** Features (runtime bridge: Features ↔ LiveEngine ↔
  Settings). Holds NO order authority.
- **RESPONSIBILITY:** (a) snapshot lifecycle (compute on new bar, cache,
  TTL-based STALE detection); (b) toggle persistence via SettingsService
  `model.liquidity_features_enabled` (HOT_RESTRICTED, INV-010); (c) model
  compatibility resolution (`resolve_model_compatibility` — does the loaded
  artifact expect 70D?); (d) 60D runtime vector assembly for the legacy
  candidate path.
- **DEPENDENCIES:** `liquidity_engine` (compute + features),
  `schema_contract` (70D geometry/hash), settings service (toggle),
  `observability.logging`; `asyncio`/threading primitives for the engine
  binding.
- **CONNECTS TO:** LiveEngine (`bind_engine`, new-bar hook), web server
  (`/api/liquidity/state|features|toggle` + status/live-state sections),
  SSE stream, UI Liquidity Intelligence tab, shadow70 runtime, tests
  (test_liquidity_runtime_integration_phase18, test_liquidity_task02_integration).
- **KEY CONCEPTS:**
  - `LiquiditySnapshot` — frozen, with `as_vector()` returning the canonical
    10D; `to_dict()` for API/SSE. `CausalState` (VALID/STALE/INVALID) is an
    explicit honesty contract: STALE = cached beyond TTL (compute on next
    new bar), INVALID = engine not bound / disabled — consumers see truth,
    never fabricated zeros.
  - `build_70d_vector(features50, news10, liquidity10)` — ASSEMBLES strictly:
    dimension checks on all three blocks, raises on mismatch (no padding, no
    truncation — per schema_contract point 6). This is THE runtime constructor
    of the 70D tensor for live inference.
  - `set_enabled(value, actor)` — persists through the settings service (the
    only sanctioned path per INV-010/BUG-080: never write live.yaml directly),
    returns the actor-labelled result dict for audit.
  - `compute_from_engine` is designed to be called from the LiveEngine's
    new-bar path via `asyncio.to_thread` (liquidity math off the tick loop).
- **HOT PATH / PERFORMANCE:** New-bar cadence ONLY (not per-tick) — a 10D
  compute on ~200 bars is trivial, but the deliberate cadence keeps the
  tick path clean. Latency is recorded (`last_latency_ms`) and surfaced in
  the API for degradation diagnosis.
- **EDGE CASES & PITFALLS:** Governor with `enabled=False` still exists but
  reports DISABLED (never pretends); binding happens AFTER engine construction
  (`bind_engine`) — code touching `compute_from_engine` before bind must
  handle INVALID; toggle changes propagate on next new-bar, not instantly
  (documented; UI shows current status).