# src/nexus_scalp/candle_intelligence/store_writes.py

- PURPOSE: Candle Intelligence Store — Write API: the record_* methods for
  CandleIntelStore (BUG-061). Every INSERT is built from a column list +
  values dict so placeholder counts can never drift from the argument
  tuple: placeholders are generated from the same `cols` list.
- ARCHITECTURE LAYER: Persistence (write path; attached onto CandleIntelStore
  by store._attach_writes).
- RESPONSIBILITY: build bounded, validated (finite numbers only),
  deterministically serialized inserts with the common audit columns for
  all 12 tables' write surfaces.
- DEPENDENCIES: models, store (_common_kwargs, _now_iso); stdlib math.
- CONNECTS TO: engine (record_candle, record_candle_closure,
  record_patterns, record_regime, record_risk, record_decision,
  record_veto, record_audit_log).
- KEY CONCEPTS:
  - `_safe_float` (line 30): non-finite/non-numeric -> default 0.0 —
  -  writes never crash on a bad number.
  - `_insert` (line 44): delegates to store.enqueue — O(1) RAM op, no
    disk on the caller's thread; returns 1/0 (accepted/dropped).
  - `record_candle` (line 53): raw OHLC (idempotent per symbol/timeframe/
    ts via UNIQUE(bar_ts)); rejects non-finite inputs.
  - `record_candle_closure` (line 116): the full close classification —
    all geometry + ratios + scores + close_class.value as
    candle_close_classification; raw_payload carries OHLC.
  - `record_patterns` (line 205): one row per PatternDetection (bar_ts +
    direction/raw/context/confidence/requires_confirmation); pattern_name/
    pattern_score in the audit columns; returns rows written.
  - `record_regime` (line 270): market_regimes row with volatility_state,
    atr, spread (safe-floated); raw_payload holds atr/spread.
  - `record_risk` (line 318): risk_evaluations row (risk_allowed int,
    bar_ts from the passed timestamp).
  - `record_decision` (line 364): THE core audit record —
    trade_decisions row with trade_bias, confidence_score, all action
    flags (entry/hold/fast_exit/exit/modify/cancel as ints), no_trade_
    reason + full audit columns from the decision's real values.
  - `record_veto` (line 430): rule_vetoes row (veto_level, veto_rule,
    veto_reason; reason_codes defaults to [rule]).
  - `record_audit_log` (line 482): generic audit_log event/detail row.
- HOT PATH / PERFORMANCE: all record_* are enqueue-only (O(1)); the store
  worker does the batched SQLite persistence.
- EDGE CASES & PITFALLS: on a full queue every record_* silently returns
  False/0 — callers treat this as "not persisted" (engine surfaces
  database_write_status WRITE_FAILED); `_bar_ts` stringifies non-datetime
  inputs rather than rejecting them; record_risk stores risk_notes as ""
  always (field exists in schema); computed_payload is "{}" for most
  record methods (only record_decision carries real values).