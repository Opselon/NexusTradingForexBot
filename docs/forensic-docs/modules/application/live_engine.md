# src/nexus_scalp/application/live_engine.py

- **PURPOSE:** The async orchestrator of the ENTIRE live system — 3,829
  lines that wire every subsystem together: tick ingestion, bar
  aggregation, 50D feature computation, regime classification, inference,
  the full gate cascade (experience → intelligence → news), shadow/challenger
  recording, 70D observation, position management, dispatch, online
  fine-tuning, worker lifecycle (accounting/intelligence/research/factory/
  training/shadow/news/incident), history sync, warmup/readiness gates,
  model bundle load/atomic-swap, governance health, survival state, and
  chart/SSE state publishing.
- **ARCHITECTURE LAYER:** Application — the composition root and the tick
  hot path. CRITICAL: this loop must NEVER block (INV-001: no sync I/O, no
  training, no DB reads inside `_process_tick_pipeline`).
- **RESPONSIBILITY:** (a) `run_loop` — the async main loop driving ticks
  from the adapter, with watchdog/telemetry and worker scheduling;
  (b) `_process_tick_pipeline` — the per-tick decision chain (below);
  (c) model lifecycle: `_load_or_create_bundle` / `_load_scaler_artifacts` /
  `_save_model_weights_atomic` (bundle_lock-protected hot swap),
  champion registry sync, collapse detection + reinit
  (`_detect_model_collapse` / `_reinitialize_collapsed_model`);
  (d) online fine-tune orchestration
  (`_trigger_async_online_fine_tune` — off-thread, quality-gated,
  atomic hot-swap); (e) worker lifecycle (start/stop/restart-safe
  background workers); (f) warmup gate (`evaluate_warmup_readiness` —
  HTF history required before inference; protective position management
  NEVER paused during warmup); (g) broker resync
  (`_resync_from_broker` — REPLACE + ALIGN history, BUG-058: never
  blind-append broker history which includes the forming bar);
  (h) survival state + kill switch propagation;
  (i) chart state (`sync_chart_state`, 900-bar window) → server_state.
- **DEPENDENCIES:** everything in src/nexus_scalp — domain, features
  (scalp_features/regime/schema/latency_tracer), models (scalp_net),
  signals (policy), risk (risk_engine), execution (order_manager),
  experience, intelligence, news, shadow, model_lifecycle (champion/
  registry), governance, market_data (bar_aggregator), audit_repository,
  settings, observability (telegram/logging).
- **CONNECTS TO:** CLI (start), web server (server_state, debug snapshot),
  all workers, all tests (test_live_state_contract, test_70d_runtime_hook,
  test_shadow*, phase suites).
- **KEY CONCEPTS:**
  - **`_process_tick_pipeline` ORDER (the canonical flow):**
    (1) `_sync_runtime_config` (cheap snapshot assignments — UI save
    reflects next tick, no DB read per tick); (2) aggregator.process_tick →
    new-bar detection, 4000-bar cap (O(1) amortized slice); (3) 50D
    features (`compute_from_bars`); (4) `_on_new_bar` side-effects
    (candle intel, rolling feature record, retrain trigger at 300 records
    via `asyncio.create_task`); (5) regime classification; (6) position
    management BEFORE inference gating — `manage_active_positions` gets
    `probs` + `regime_state` (Phase 15 audit fix: previously omitted →
    AI-flip exit dead + adaptive scores degraded to heuristics);
    inference failure is isolated (positions still managed);
    (7) warmup gate — if not READY: block inference with
    HTF_WARMUP_INCOMPLETE NO_TRADE (re-check on new bar/15s), but never
    block position protection; (8) inference (reused from step 6 when
    available — model runs ONCE per tick); (9) policy
    (`evaluate_probabilities` with survival flag);
    (10) experience gate (down-rank only, TTL-cached);
    (11) intelligence gate (WARN/PENALIZE/REJECT only, never upgrade);
    (12) news gate (bounded ±confidence adjustment, never forces
    direction, failure = no-op); (13) audit.log_signal;
    (14) shadow recording (Challenger on the SAME vector, observational);
    (15) shadow70 observation hook (independent, isolated, INV-018);
    (16) chart overlays + server_state bars (900-bar window);
    (17) dispatch: AI reversal (close-then-flip, NEVER stack) OR
    entry (risk sizing → clamp → setup snapshot capture → dispatch);
    (18) liquidity governor update on new-bar cadence (pure numpy,
    SourceKind.LIVE_MARKET_STATE — BUG-111 provenance fix).
  - **`_infer_probabilities` — the model hot path:** honest staged latency
    trace (LatencyTracer T0..T10); `_validate_50d_tensor` (finite,
    [-3,+3] clip, zero-fill non-finite); scaler transform; 
    `torch.nan_to_num` guard; sampled debug input capture (every 64th —
    `_last_model_input_tensor`, observability only, INV-018);
    **torch intra-op thread pinning:** `torch.set_num_threads(1)` around
    the forward (measured ~60ms vs 0.25ms under host contention for a
    267k-param net — same logits), restored in finally, safe under
    bundle_lock; `torch.inference_mode()` for zero autograd overhead.
    Latency breakdown published to the API/UI (`_last_latency_breakdown`).
  - **`run_loop`** — async main loop: consumes ticks (with account sync),
    schedules workers (accounting/intelligence/research/factory/training/
    shadow/news/incident — each start/stop restart-safe), periodic
    governance health snapshot, survival-state refresh, radar logging.
  - **Model hot-swap discipline:** EVERY read of the model/scaler goes
    through `self._bundle_lock` → `self._bundle`; fine-tune builds a NEW
    bundle and swaps atomically under the lock — zero tick drops, zero
    torn reads (the Phase-5.8 invariant).
  - **`_validate_50d_tensor` (classmethod)** — the serving-side contract:
    length 50, finite, clip [-3,+3]; non-finite → 0.0 (never NaN into
    torch).
  - Warmup gate cost control: HTF bar fetch only on new bar/15s, bounded.
- **HOT PATH / PERFORMANCE:**
  - The pipeline is the "50ms hot path": everything is in-memory math +
    cached lookups; DB writes are queued (audit worker thread); news/
    experience/intelligence gates are TTL-cached + rate-limited; the only
    blocking risks are the adapter calls (broker IPC) which are inherent.
  - Every hook (shadow, shadow70, liquidity, news, candle) is wrapped in
    try/except — a hook failure is ISOLATED (logged, trading unaffected).
    This is the "failure isolation" invariant (INV-018) enforced by design.
  - Bar cap 4000 keeps aggregation O(1) amortized; 900-bar chart window
    bounds the SSE payload.
- **EDGE CASES & PITFALLS:**
  - Inference runs at most ONCE per tick (reused from position management)
    — a regression that double-inferred would double latency.
  - The retrain trigger fires only when `not self._retrain_inflight`
    (no overlapping fine-tunes) and ≥300 records; the scheduled task is
    fire-and-forget with in-task error isolation.
  - `import time as _time` INSIDE `_infer_probabilities` — function-local
    imports are otherwise forbidden in this file (the UnboundLocalError
    class, BUG-074) but here it's a deliberate local alias; the module
    top-level time import must remain.
  - Debug input capture samples every 64th inference — the Debug Console
    shows the exact post-scaler pre-softmax tensor, never fabricated.