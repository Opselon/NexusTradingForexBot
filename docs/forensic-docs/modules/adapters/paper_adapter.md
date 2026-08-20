# src/nexus_scalp/adapters/paper/paper_adapter.py

- **PURPOSE:** The in-memory simulated broker — `PaperMT5Adapter`
  implementing `IMT5Port` with realistic spread/latency/slippage
  simulation. Powers PAPER mode (default safe start) and REPLAY/BACKTEST
  determinism, and is the primary test double for the whole engine.
- **ARCHITECTURE LAYER:** Adapters (simulation boundary).
- **RESPONSIBILITY:** (a) simulate account state (initial_balance 10,000,
  equity/margin math, snapshots with provenance); (b) simulate market
  data (ticks with realistic spreads per symbol; `_symbol_is_metal`/
  `_quote_digits` derive XAUUSD gold conventions vs FX); (c) simulate
  order execution (fills with spread cost + latency, partial fills),
  position lifecycle, pending orders, history; (d) broker-calc snapshots
  (order_calc_margin/profit via the same formulas the risk engine
  estimates — with explicit FALLBACK_ESTIMATE provenance).
- **DEPENDENCIES:** `ports.mt5_port`, `adapters.mt5.providers` (snapshots),
  `adapters.mt5.diagnostics` (connection state), domain models, numpy
  (optional randomness), logging.
- **CONNECTS TO:** LiveEngine (PAPER mode), CLI (`nexus start` default),
  replay mode, the ENTIRE test suite (mt5_fixtures build paper adapters),
  integration tests.
- **KEY CONCEPTS:**
  - Simulation must be DETERMINISTIC when seeded (tests assert exact
    behaviors) yet realistic enough to exercise the engine's decision
    paths (spread cost changes outcomes; latency surfaces in the
    execution-quality metrics).
  - The adapter reports the SAME snapshot provenance contract as the real
    adapter — a consumer cannot accidentally tell "paper" from "real"
    except via the source field (deliberate: the engine must behave
    identically in both).
- **EDGE CASES & PITFALLS:** The paper account must not drift from the
  real-account invariants (equity ≥ 0, margin math consistent — tests
  assert); splitting fills must follow the same ticket/family semantics as
  the real broker (split-fill context inheritance is tested against this
  adapter); paper must NEVER pretend to be a real account (is_real_account
  must be false / provenance UNAVAILABLE where the real adapter would
  report BROKER_NATIVE).