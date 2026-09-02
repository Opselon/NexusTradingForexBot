# tests/integration/test_news_api.py

- GUARDS: PHASE 12 News Intelligence — end-to-end: FastAPI news endpoints return REAL persisted data (no synthetic), news worker wiring in LiveEngine is failure-isolated, SSE news events flow.
- KEY ASSERTIONS:
  - `TestNewsApiEndpoints`: /api/news endpoints serve persisted rows, keyword highlighting, no fabricated sources; `TestLiveEngineWiring`: worker starts with engine, tolerates source failures, news context reaches signals (61 asserts).
- PITFALLS IT ENCODES: worker failure isolation (a dead news source must not kill the engine); persisted-only truth.
- NOTES: Pairs with unit test_news_phase12.py / test_news_bridge_* (not in this slice).
