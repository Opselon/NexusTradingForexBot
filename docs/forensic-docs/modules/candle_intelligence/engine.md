# src/nexus_scalp/candle_intelligence/engine.py

- PURPOSE: CandleIntelligenceEngine — orchestrator for the local, isolated
  candle-close analysis module (BUG-061). Ingests ticks/OHLC bars/
  candle-close events, runs classifier + pattern engine + decision engine,
  persists every intermediate and final result to the isolated SQLite
  store, and produces the spec §11 output contract for every processed
  candle. Wired into live_engine _on_new_bar.
- ARCHITECTURE LAYER: Application service (advisory orchestrator — holds
  no adapter / order manager; it can never place, modify or close an
  order).
- RESPONSIBILITY: window maintenance, complete-bar gating, pipeline
  orchestration, counters, veto audit logging, bounded query facade.
- DEPENDENCIES: CandleCloseClassifier, CandleDecisionEngine, PatternEngine
  (Candle), CandleIntelStore, RegimeState/RiskEvaluation/TradeBias models,
  config.
- CONNECTS TO: live_engine._on_new_bar (new-bar cadence — never per tick),
  web/status queries (recent_decisions/recent_closures/recent_vetoes,
  db_size_bytes); store persistence.
- KEY CONCEPTS:
  - `CandleOutput` (line 37): the spec §11 output contract per processed
    candle — candle_close_summary, detected_patterns, regime_state,
    trade_bias, confidence_score, entry_allowed, hold_allowed,
    fast_exit_required, no_trade_reason, database_write_status;
    `to_dict` serializes (close summary via model_dump_for_db).
  - `ingest_bar` (line 119): appends to the sliding _window (max 12
    candles); stores raw candle; FULL pipeline runs ONLY on COMPLETE bars
    (is_complete=True) — forming bars are stored raw, not decided
    (docstring). Returns CandleOutput | None.
  - `process_candle_close` (line 169): the gate — classify -> record
    closure -> derive/default regime (RegimeState with empty fields if
    none passed) + record_regime -> pattern_engine.detect(self._window,
    regime) -> record_patterns -> risk_eval (default RiskEvaluation when
    none) + record_risk -> decision_engine.decide(holding_position,
    position_pnl) -> record_decision -> status WRITTEN/WRITE_FAILED.
    Counters incremented; NO_TRADE decisions with a reason also write a
    rule_vetoes row (level=3, rule=no_trade_reason).
  - `ingest_tick` (line 247): explicit no-op placeholder — "ticks feed
    the bar aggregator upstream; no decision here."
  - Query facade: recent_decisions/recent_closures/recent_vetoes via
    store.query_recent (RAM ring fast path); db_size_bytes.
- HOT PATH / PERFORMANCE: invoked on new-bar (not per tick); all DB
  writes are O(1) enqueues; pattern detection is O(29 patterns x window)
  per bar — trivial at bar cadence.
- EDGE CASES & PITFALLS: the window is a flat list (not keyed by symbol/
  timeframe) — interleaving multiple symbols/timeframes in one engine
  corrupts multi-candle pattern inputs; a defaulted RegimeState
  (regime="UNKNOWN", atr=0, spread=0) routes to the decision engine's
  BLOCKED_REGIMES (UNKNOWN) -> NO_TRADE — safe by construction; veto rows
  are only written for NO_TRADE, not for FAST_EXIT/EXIT paths; the
  DECISION record can report WRITE_FAILED while closures/patterns still
  persisted.