# src/nexus_scalp/strategies/base.py

- PURPOSE: PHASE 15C Strategy Framework — base contracts & the import-time
  built-in registry. A `Strategy` is a PURE signal generator: it consumes
  completed bars and produces directional signals (BUY/SELL/NONE) with
  optional confidence; it holds no adapter, no risk engine, NEVER places
  orders — exactly like the research layer's safety contract. Registration
  produces deterministic `StrategyCandidate` objects (content-addressed
  versions) so the research pipeline, Experience StrategyContext, and future
  AI-vs-strategy alignment all reference the same stable identities.
- ARCHITECTURE LAYER: Domain/Strategy framework (pure; no I/O; no order
  authority).
- RESPONSIBILITY: define the Strategy protocol + StrategySignal +
  BarLike contracts, the module-level `BUILTIN_STRATEGIES` registry,
  register_strategy / builtin_candidates / make_candidate, and the shared
  bar helpers (OHLC extraction, Donchian midpoint).
- DEPENDENCIES: `research.candidates` (StrategyCandidate); stdlib (dataclass,
  Protocol, datetime).
- CONNECTS TO: ichimoku.py (registers two strategies at import), seeder.py
  (seeds registry), research pipeline (candidates), strategies package
  __init__ (registrations run on import).

- KEY CONCEPTS:
  - `BUILTIN_STRATEGIES` (line 25): dict keyed by stable strategy_id; filled
    at import time via `register_strategy`.
  - `BarLike` Protocol (lines 28-36): minimal OHLCV contract —
    timestamp/open/high/low/close/tick_volume (structural typing; strategies
    only need these attributes).
  - `StrategySignal` (lines 39-48): frozen dataclass — strategy_id,
    direction ("BUY"|"SELL"|"NONE"), bar_index, optional timestamp,
    confidence, metadata dict.
  - `Strategy` Protocol (lines 51-66): strategy_id / version / display_name
    + evaluate(bars) → list[StrategySignal] + context_definition() /
    entry_logic() / exit_logic() / risk_assumptions() → dicts. This is the
    contract the seeder and the research pipeline rely on.
  - `make_candidate` (lines 73-90): builds a StrategyCandidate with empty
    version, fills definition dicts from the strategy methods,
    discovery_method=`builtin:<display_name slug>`, lifecycle DISCOVERED,
    discovery_evidence {source: builtin_seed, definition}, then assigns the
    content-derived canonical version — determinism via candidates.py.
  - `register_strategy` (93-96): idempotent insert into BUILTIN_STRATEGIES
    (later registrations overwrite earlier ones per id).
  - `builtin_candidates()` (99-101): [make_candidate(s) for s in registry].
  - `_bars_to_lists` (104-113): OHLC extraction, never mutates the input.
  - `donchian_mid` (116-123): (max high + min low)/2 over the trailing
    `length` bars ending at `end`; window clamped to available bars; 0.0
    when empty (the Ichimoku engines use 0.0 as the "not enough bars"
    sentinel).
- HOT PATH / PERFORMANCE: signal evaluation is per-bar O(n·windows); the
  donchian helper is O(window) per call — ichimoku calls it once per bar
  per line; fine for research/backtest use, not intended for per-tick live
  evaluation (no such path exists in this repo).
- EDGE CASES & PITFALLS:
  - `make_candidate` calls strategy.entry_logic() TWICE (definition + the
    discovery_evidence "definition" copy) — any strategy with stateful
    definition methods gets inconsistent hashes vs evidence; the protocol
    implies pure methods, so the double call is harmless by contract but
    wasteful.
  - StrategySignal.confidence defaults 0.0 with no bounds/validation — the
    ichimoku engines emit 0.6; consumers must not assume calibrated
    probabilities.
  - `register_strategy` overwrites by id silently — a second module
    registering the same id replaces the first (no warning).
  - `donchian_mid` returns 0.0 for empty windows, which is a VALID price
    level for instruments near 0 — the Ichimoku engines defensively skip
    bars where lead lines are == 0.0, coupling correctness to this sentinel.