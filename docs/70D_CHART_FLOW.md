# 70D Chart Flow Forensics (TASK-70D-SYSTEM-FLOW-FORENSICS)

> Agent: Hermes-Forensic-70D · 2026-08-19
> Reconstructed from actual code at absorbed HEAD (2babe15).

## 1. Chart data source (canonical)

```
Browser (Web/app.js + Web/api_client.js, window.NX contract)
   │ GET /api/chart/history?count=900 (default 900, bounded 1..5000)
   ▼
server.py get_chart_history (web/server.py:2294)
   │ 1) engine.adapter.get_rate_history(symbol, timeframe, count)
   │    → MT5 official copy_rates_* (BROKER_NATIVE), validated OHLC
   │    → RESYNC: engine.aggregator.reseed(seeded) + sync_chart_state
   │      (BUG-054/058: REPLACE + ALIGN, dedupe, sort, completed-only,
   │      forming bar seeded at next-minute boundary)
   │ 2) fallback: engine.aggregator.get_completed_bars() + forming bar
   │    → source = ENGINE_STATE (explicit provenance)
   ▼
   {bars, source, symbol, timeframe, requested, returned,
    first_timestamp, last_timestamp, generated_at, error, visual_overlays}
```

Chart source is BROKER-FIRST with explicit ENGINE_STATE fallback — NEVER
synthetic. The reseed alignment is verified in
`market_data/bar_aggregator.py::reseed()` (dedupe → sort → completed-only →
forming bar at +timeframe boundary so the first live tick never mints a
duplicate completed bar).

## 2. OHLC validation

- MT5 provider path validates bars (`validate_ohlc_bars`: dup/descending ts,
  finite OHLC, high/low bounds, non-negative volume) in
  `adapters/mt5/providers.py`.
- Chart endpoint marks every broker bar `is_complete: True`; the forming bar
  (engine fallback) is `is_complete: False`.
- No silent fallback: the response `source` field distinguishes MT5 vs
  ENGINE_STATE; `error` carries MT5_RATE_HISTORY_FAILED on broker failure.

## 3. Timeframe aggregation

- Chart timeframe comes from `engine.config.execution.timeframe` (M1 default).
- The engine aggregator builds M1 bars from ticks; HTF bars (H1/H4/D1) are
  derived for features only — the chart serves the configured timeframe.

## 4. Feature / Structure / Liquidity overlay state

- Overlays come from the real engine: `visual_overlays` (rectangles,
  bos_lines, midlines, liq_markers, order_lines) built from
  `signal_policy.extract_live_chart_overlays()` (real SMC state) + liquidity
  pool markers from the governor (TASK-02).
- Liquidity state section is embedded in /api/status + /api/live/state + SSE
  (`_liquidity_state_section` — real report, never fabricated).

## 5. Live update flow (SSE)

Protocol (`server.py:5999-6039`, LiveUiState.2):
- `event: state` — full canonical snapshot (on connect, after idle gap, every
  30 versions)
- `event: tick` — incremental update carrying state_version + changed
  sections; UI merges (bars/features/predictions stripped from tick frames)
- `event: heartbeat` — keepalive every 5s
- Out-of-order guard: UI drops any version <= last seen.

Reconnect: on reconnect the client receives a full `state` event — one
canonical state restored.

## 6. Chart / feature timestamp alignment

- Liquidity pools carry `candidate_at` / `confirmed_at` (causal timestamps);
  chart overlays derive from the confirmed pool state only.
- SSE state_version is monotonic; the frontend merges incrementally.
- The 70D shadow observations use `tick.timestamp` (broker/server UTC) — the
  same timestamp the champion decision used (causal alignment).

## 7. Timeframe switch (TEST-FLOW-12)

- Chart timeframe is config-driven (execution.timeframe). Switching restarts
  the fetch path; no stale overlays are retained because overlays rebuild
  from the engine's current SMC/liquidity state on each full state event.

## 8. Verification status

| Check | Result |
| :--- | :--- |
| Broker-first + explicit fallback | 🟢 |
| OHLC validated | 🟢 |
| Reseed REPLACE+ALIGN (BUG-058) | 🟢 |
| SSE versioned + out-of-order guard | 🟢 |
| Overlays from real state | 🟢 |
| No synthetic bars | 🟢 |
| Timestamp alignment (causal pools) | 🟢 |