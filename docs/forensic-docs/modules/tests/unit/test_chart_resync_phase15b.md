# tests/unit/test_chart_resync_phase15b.py

- GUARDS: Chart resync & 900-bar downtime recovery (BUG-054 follow-up): `BarAggregator.reseed()` atomically replaces history with broker truth on reconnect; cold-start / reconnect resynchronization contract.
- KEY ASSERTIONS:
  - reseed swaps in broker history atomically; disconnected period reconstructs missing 900-bar window; no gaps/dupes after resync; /api chart reflects reseeded data (44 asserts).
- PITFALLS IT ENCODES: reseed must be atomic (no partial state visible); stale pre-reconnect bars must never survive into the resynced feed.
- NOTES: FakeResyncAdapter/ChartEngineAdapter harness; drives LiveEngine/BarAggregator/web server in-process.
