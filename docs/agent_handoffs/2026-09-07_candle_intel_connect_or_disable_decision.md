# Candle Intelligence — Connect-or-Disable Decision (Phase 3)

**Owner:** Hermes-NewsMission (market-context mission)
**Date:** 2026-09-07
**Verdict:** OPTION B — REMOVE FROM RUNTIME (config-gated OFF by default),
preserving all code + historical DB for research. Runtime cost removed; no
demonstrated consumer exists.

## Evidence (verified at HEAD, not from comments)

1. **Call graph — writer only, no consumer.**
   - Writer: `application/live/bar_handler.py::on_new_bar` feeds every
     completed M1 bar into `CandleIntelligenceEngine.ingest_bar` and stores
     the verdict on `om._last_candle_decision`.
   - Readers of `_last_candle_decision`: NONE. The only references to that
     attribute are the two writer sites (bar_handler.py:66, live_engine
     attr init). No policy/order-manager/feature/persistence/API/UI read.
   - `_last_candle_decision` is not exposed via debug_snapshot or any
     /api route; Web/app.js touches candle_intel only as a DB-console
     LABEL (line 3156).
2. **Store cost — real and continuous.**
   - `artifacts/candle_intel.db`: 15 tables, ~33.5k rows written over
     21 days (2026-08-17 → 2026-09-07) — candles, closures, patterns,
     decisions, regimes all recorded per M1 bar (~1.4k decisions/day),
     via the store's RAM ring + async writer (off-path, but still work).
   - Decision distribution (ENTRY 2952 / NO_TRADE 1295 / HOLD 180 /
     FAST_EXIT 78) proves the engine COMPUTES entry/exit opinions that
     nothing consumes — 4505 computed decisions, 0 applied.
3. **Bench artifacts** (`results_candle_intelligence_*.json` under
   `_cleanup_hold_20260819/`) document the original BUG-061 spec, but no
   subsequent task (policy/risk/execution waves) ever wired the gate.

## Decision per acceptance criterion

Criterion: "REAL P&L-RELEVANT USE **OR** RUNTIME COST REMOVED".
No P&L-relevant use can be demonstrated (nothing consumes the verdicts;
no OOS evaluation of candle-intel-as-filter exists — and fabricating a
consumer now, without evidence, is exactly what the mission forbids).

Therefore: **remove runtime cost** —
- `CandleIntelligenceConfig(enabled=...)` becomes the authority: LiveEngine
  constructs the engine ONLY when `config.candle_intel.enabled` is true.
- Default flips to `False` (base.yaml + config default) with the decision
  rationale recorded here; the subsystem stays importable, tested, and its
  historical DB preserved (research/audit can still read candle_intel.db).
- Re-enabling is a one-line config flip; re-connecting requires a design
  with provenance, tests, and OOS evaluation per mission Phase 3 Option A.
