# src/nexus_scalp/candle_intelligence/__init__.py

- PURPOSE: Candle Intelligence Subsystem (BUG-061) package facade — the
  local, isolated, database-backed candlestick analysis and trade-
  decision module for the Nexus Scalp Engine.
- ARCHITECTURE LAYER: Package facade / entry surface.
- RESPONSIBILITY: re-export CandleIntelligenceConfig,
  CandleIntelligenceEngine, CandleOutput, and the domain contracts
  (CandleCloseClass, CandleCloseSummary, CandleDecision, DecisionType,
  PatternDetection, RegimeState, RiskEvaluation, RiskState, TradeBias).
- DEPENDENCIES: config, engine, models (sibling modules of the
  package).
- CONNECTS TO: live_engine _on_new_bar wiring (the engine is invoked
  on new-bar cadence — never per tick), tests, web/status consumers.
- KEY CONCEPTS:
  - Docstring (lines 1-23) carries the architectural contract: SAFETY
    — "this package only analyzes, scores and recommends. It holds no
    adapter, no order manager and no risk engine; it can never place,
    modify or close an order. All persistence is local
    (artifacts/candle_intel.db); no network calls, no cloud services,
    no remote telemetry."
  - "The candle close is a GATE. Weak, contradictory or invalid closes
    downgrade confidence, block entry, or accelerate exit — before any
    pattern logic runs."
  - Module map (docstring): config.py (conservative safety
    thresholds), models.py (immutable contracts), classifier.py
    (close-quality GATE), patterns.py (29-pattern engine + context
    weights), decision.py (rule hierarchy entry/hold/exit/no-trade),
    store.py + store_writes.py (isolated 12-table DB with audit
    columns), engine.py (orchestrator + spec §11 output contract).
- HOT PATH / PERFORMANCE: import-time only; runtime entry is
  engine.process_candle_close on new-bar — never per tick.
- EDGE CASES & PITFALLS: advisory-only contract — nothing in this
  package may ever hold execution capability; keep __all__ synced with
  the imports (they are currently identical).
- NOTE: the package is self-contained — its only external touchpoint
  is the engine interface consumed by live_engine; deleting it removes
  a weighted advisor, never a trading block.

- RELATED ARTIFACTS:
  - src/nexus_scalp/candle_intelligence/engine.py — the orchestrator
    and spec §11 output contract (CandleOutput).
  - src/nexus_scalp/candle_intelligence/store.py — the isolated
    artifacts/candle_intel.db with its 12 tables and audit columns.
- REVISION NOTES: BUG-061 follow-ups (RAM-ring + batched writer +
  precompiled patterns) changed internals without altering this public
  surface.
