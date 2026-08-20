# tests/integration/test_research_api.py

- GUARDS: PHASE 09B Strategy Research Engine — REST API, worker & LiveEngine wiring for the backtest/research subsystem; endpoints must surface real research state and results.
- KEY ASSERTIONS:
  - `TestResearchAPI`: research status/dataset/result endpoints serve persisted truth; worker triggers research on demand; unknown/missing backtests reported honestly (32 asserts).
- PITFALLS IT ENCODES: no fabricated research numbers; worker trigger discipline (triggers produce work, unchanged dataset does not retrain).
- NOTES: Companion to unit test_research_phase09b.py at the HTTP layer.
