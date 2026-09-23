/**
 * Handbook — the canonical gate-chain order, split out of ./index so pages
 * that only render the chain (ResearchPage's hero pipeline map) do not
 * bundle every gate's handbook prose (~35 kB) into their landing chunk.
 *
 * Values are IDENTICAL literals to before the split — ./index re-exports
 * this as GATE_CHAIN, so every existing consumer (including tests/js/
 * research_handbook.test.js, which loads gates/index.ts and compares the
 * order against evidence.py::GATE_CHAIN verbatim) sees the same array.
 * Never edit the order here without that test passing.
 */

/** Canonical chain order — mirrors evidence.py::GATE_CHAIN verbatim. */
export const GATE_CHAIN = [
  "STATIC_VALIDATION",
  "BACKTEST",
  "WALK_FORWARD",
  "OOS",
  "ROBUSTNESS",
  "SCORING",
] as const;
