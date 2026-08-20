# src/nexus_scalp/market_data/bar_aggregator.py

- **PURPOSE:** Aggregates the raw tick stream into M1 OHLCV bars (completed
  + forming) and exposes bar lists to features/chart/strategy layers. The
  boundary between broker tick reality and the bar-based feature universe.
- **ARCHITECTURE LAYER:** Market data (Application-adjacent, on the tick
  hot path).
- **RESPONSIBILITY:** (a) `process_tick(tick) -> bool` — fold a tick into
  the forming bar, mint a completed bar on minute rollover, return
  is_new_bar; (b) `get_completed_bars()` / `get_current_forming_bar()` —
  the two read surfaces (features read completed only — forming-bar
  exclusion is the causality discipline's front gate); (c) history
  ingest/reseed (BUG-058: broker history INCLUDES the forming bar —
  ingestion must REPLACE + ALIGN: dedupe by timestamp, sort ascending,
  drop incomplete bars, seed forming bar from latest close — never
  blind-append).
- **DEPENDENCIES:** domain TickData, BarData dataclass, time handling.
- **CONNECTS TO:** LiveEngine (per-tick), feature engine (bars),
  chart history (bug-058 resync), tests (test_bar_aggregator).
- **KEY CONCEPTS:** The aggregator is the engine's clock: every
  minute-boundary decision (new bar) triggers `_on_new_bar` side-effects
  (candle intel, retrain cadence, liquidity snapshot). Timestamps come
  from the TICK (broker time, UTC-normalized), never host wall clock.
- **EDGE CASES & PITFALLS:** Duplicate same-minute ticks must not mint
  duplicate completed bars; the forming bar's OHLC resets correctly on
  minute rollover; a stale/late tick from a PREVIOUS minute must not
  retro-mutate an already-completed bar (guard in process_tick); the
  4000-bar cap bounds memory and aggregation cost.