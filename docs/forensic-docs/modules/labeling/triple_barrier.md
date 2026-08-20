# src/nexus_scalp/labeling/triple_barrier.py

- **PURPOSE:** The training-label engine — a cost-aware, purged,
  triple-barrier labeler (Lopez de Prado method) with MAE safeguards,
  converting OHLCV+ATR history into the 3-Class outcome taxonomy
  (0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET). This is where raw market
  history becomes supervised-learning targets.
- **ARCHITECTURE LAYER:** Labeling/ML research (training-time only; not on
  the live hot path).
- **RESPONSIBILITY:** For each eligible bar, evaluate the path-dependent
  barrier walk (TP/SL touches with spread-friction realism), apply purging
  (embargo + stride) to guarantee serial independence, and emit
  `label`/`is_eval_sample`/`is_purged` columns a trainer consumes directly.
- **DEPENDENCIES:** polars (DataFrame I/O), numpy (vectorized
  conversions + the label lookup via array indexing — deliberately NOT
  `replace_strict` for cross-version polars stability), domain.enums
  (ActionType values), observability.logging.
- **CONNECTS TO:** walk_forward_trainer (online fine-tune labeling of the
  live 300-record rolling buffer), model_generation dataset builders,
  research dataset paths, tests (test_walk_forward_trainer,
  test_research_task4_dataset).
- **KEY CONCEPTS:**
  - **Spread-friction realism:** entries are spread-adjusted (BUY at
    ask=close+half_spread, SELL at bid), exits use the FUTURE bar's spread
    (sell exits at ask = bid-low + step_spread), and the effective friction
    is `max(friction_usd, entry_spread)` — the label only means "profitable
    AFTER costs". TP feasibility guard: if `tp_dist <= effective_friction`
    the sample is skipped (a TP narrower than friction can never be real).
  - **Path-dependent step walk:** for each of the (≤15) forward bars, in
    order: check long/short TP/SL touches with the neutralization rule —
    ANY simultaneous dual-touch (spike bar hitting both sides) → NO_TRADE
    (eliminates bullish bias from a 2-sided spike), and any SL touch →
    NO_TRADE (a stop-out is never a win, mirroring accounting invariant 2).
    First-touch-wins within the horizon.
  - **MAE safeguard at the vertical barrier:** a time-expiry with no touch
    still yields a BUY/SELL label IF net PnL after friction > 0.5·ATR AND
    max adverse excursion ≤ 0.75·SL distance — a "crawled to profit within
    the risk envelope" outcome. This is the labeler's answer to the
    time-barrier default (heuristic, documented in the MAE ratio).
  - **Purging:** after a labeled sample, advance `exit_step + embargo_bars`
    (serial-independence embargo); after NO_TRADE advance
    `no_trade_stride_bars` (3) — the class-imbalance mitigation. Rows not
    visited stay `is_purged=True`.
  - ATR validity: rows with NaN/≤0.20 ATR are skipped (counted, logged).
- **EDGE CASES & PITFALLS:**
  - The `horizon = min(max_holding, n-1-i)` tail bound — end-of-dataset
    samples evaluate within a SHORTER horizon (documented adaptive tail);
    `horizon <= 0` breaks the loop.
  - Label 0 conflates "never touched" with "stop-out" — the semantics are
    both NO_TRADE, by design (the model learns: don't trade chop/stops).
  - Polars discipline: `~`/`&` only (never `not`/`and`) — though this file
    works in numpy, the repo rule stands for any polars filtering.
  - `label` column stores ActionType STRINGS (NO_TRADE/BUY_MARKET/
    SELL_MARKET) — trainers must map to ints 0/1/2 (the 4-class head adds
    WAIT=3 at the model, weight 1.0 in loss — see walk_forward_trainer).