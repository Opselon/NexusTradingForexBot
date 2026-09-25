# src/nexus_scalp/strategies/ichimoku.py

- PURPOSE: PHASE 15C Ichimoku (Ichimili) seeded built-in strategies — Pine
  Script `Ichimili - Final Version` and `Ichimili` translated 1:1 into pure
  Python bar-based signal engines. Variant A = displaced-kumo break + future
  cloud momentum + ALTERNATING signals; Variant B = current-kumo close break
  + rising/falling spans + minimum bar gap between signals.
- ARCHITECTURE LAYER: Strategies (pure bar-based signal generators; no I/O;
  no order authority — same safety contract as research).
- RESPONSIBILITY: implement the Pine math exactly —
  conversion = donchian(9), base = donchian(26), span A = (conv+base)/2,
  span B = donchian(52), displacement = 26 (Pine `offset = displacement - 1`
  for display; the SIGNAL reads the displaced value leadLine1[displacement-1]
  — the visible cloud over the current candle).
- DEPENDENCIES: `strategies.base` (BarLike, StrategySignal, _bars_to_lists,
  donchian_mid, register_strategy).
- CONNECTS TO: strategies package __init__ (import-time registration),
  seeder (builtin candidates), research pipeline (the generated
  StrategyCandidate gets backtested/validated like any discovered
  candidate).

- KEY CONCEPTS:
  - Constants (lines 40-50): CONVERSION=9, BASE=26, LAGGING_SPAN2=52,
    DISPLACEMENT=26, MIN_CANDLES_BETWEEN_SIGNALS=6; ids
    STRAT-ICHIMILI-FINAL / STRAT-ICHIMILI-SPACED.
  - `_ichimoku_lines` (53-71): per-bar conversion/base/lead1/lead2 series via
    donchian_mid — O(n × window) with the 0.0 sentinel for warm-up bars.
  - `IchimiliFinalStrategy.evaluate` (134-213): skips bars with
    `i < shifted` (displacement-1), `i < 1`, or lead1[i-shifted] == 0.0.
    Visible cloud over bar i: top/bottom of lead1/lead2[i-shifted]. Candle
    body (open-close range) fully above/below the cloud. Future-cloud
    momentum from the PREVIOUS displaced value (i-shifted-1): both lines
    rising → future_bullish, both falling → future_bearish. Signal rule:
    ALTERNATING one-in-a-row — a BUY only emitted while the last emitted
    signal was not BUY (last_signal_type state), so consecutive bull bars
    emit once; confidence fixed 0.6; metadata carries visible_top/bottom +
    variant. Exit logic: OPPOSITE_SIGNAL.
  - `IchimiliSpacedStrategy.evaluate` (279-335): uses the CURRENT (unshifted)
    kumo (lead1/lead2[i]); skips bars with either line 0.0; span A/B
    rising/falling vs prior bar; close above/below kumo top/bottom; signal
    allowed only when >= min_candles_between_signals bars elapsed since the
    last signal (last_signal_bar); metadata gap_bars reports the actual gap
    (buggy — see pitfalls). Exit: OPPOSITE_SIGNAL_OR_GAP.
  - Registration (lines 341-342): both strategies register at import time —
    importing nexus_scalp.strategies runs the side effect.
  - context_definition: family "ichimoku", variant, symbol_agnostic /
    timeframe_agnostic True, regime TRENDING, trend_state
    BULLISH_OR_BEARISH. risk_assumptions: directional_only,
    stop_model kumo_opposite_edge, min_expectancy_r 0.10.
- HOT PATH / PERFORMANCE: O(n) bars with per-bar donchian windows; the
  helper is recomputed for every bar of every line (3 lines) — fine for
  research/backtest, no live per-tick path.
- EDGE CASES & PITFALLS:
  - Spaced variant metadata `gap_bars` computes `i - last_signal_bar` AFTER
    assigning `last_signal_bar = i` (lines 319, 331) — the reported gap is
    ALWAYS 0 for every emitted signal; the field is misleading.
  - Final variant's alternation tracks EMITTED signal type, not raw
    condition state: two bull conditions separated by a neutral bar still
    emit only one BUY until a SELL/NONE-bar breaks the alternation state —
    consistent with the Pine one-in-a-row translation but easy to
    misread as "any bar flips state".
  - The 0.0 sentinel coupling: donchian_mid returns 0.0 for warm-up windows;
    on symbols priced near 0 the lead lines would be skipped (paths guard
    `lead == 0.0`), suppressing signals — irrelevant for XAUUSD but a
    latent assumption.
  - `_bars_to_lists` requires tick_volume on BarLike? No — only
    timestamp/open/high/low/close are read by the engines; the volume field
    is part of the protocol but unused by both strategies.
  - No bar timestamps are validated/ordered — bar_list order is the caller's
    contract (oldest → newest).