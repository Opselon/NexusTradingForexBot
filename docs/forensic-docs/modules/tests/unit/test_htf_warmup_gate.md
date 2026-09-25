# tests/unit/test_htf_warmup_gate.py

- GUARDS: Production-grade HTF warmup, fallback & console observability (all 11 mandatory requirements in LiveEngine): higher-timeframe context must be warmed before trading decisions rely on it.
- KEY ASSERTIONS:
  - warmup completes before live reads; missing/empty HTF data falls back explicitly (never silent zeros); console/observability surfaces warmup state; gate blocks decisions until warm (27 asserts).
- PITFALLS IT ENCODES: HTF fallback must be explicit and observable — silent fallback is a monitoring failure (see TestMonitor24SilentFallback).
- NOTES: FakeMT5Adapter harness; gates LiveEngine behavior.
