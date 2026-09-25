# TASK-02 BASELINE SNAPSHOT (STEP 0)

> Agent: AGENT-02 (Hermes-LiquidityIntegration) · 2026-08-19
> Captured BEFORE any TASK-02 modification.

## Current state
- branch: `main`
- HEAD: `04cbecd` (Hermes-ProdRel: TASK-9 forensic deployment audit)
- origin/main: `04cbecd` (in sync after fetch)
- TASK-01 commits on origin: `b91b8c9` (foundation), `111f16e` (handoff) — VERIFIED remote
- TASK-01 tests available: `tests/unit/test_liquidity_engine_{contract,causality,features}.py` (60 tests) — PASS (re-verified this run: 90 tests incl. swarm's integration suite)
- TASK-01 handoff exists: `docs/agent_handoffs/TASK-01-60D-LIQUIDITY.md`
- TASK-01 feature registry exists: `scalp_liquidity_v1` in `features/schema.py`

## 50D status
- Authoritative contract: `FEATURE_NAMES` in `features/scalp_features.py` (50 dims)
- Schema `scalp_v1` ACTIVE; `LiveEngine.FEATURE_DIM = active_dimension() = 50`
- 50D untouched by TASK-01 (proven by TEST-60D-BASE-01)

## Current model
- Champion: `primary_scalp` (scalp_v1, 50D) — legacy 4-logit baseline; artifact
  `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt`
- No 60D/70D model trained or promoted (TASK-5 candidates all REJECTED)

## Current Liquidity status
- Config flag `model.liquidity_features_enabled` exists (default **false**)
- `LiquidityGovernor` (features/liquidity_runtime.py — 70D-oriented, from the
  parallel 70D swarm) wired in live_engine at startup + `/api/liquidity/state`,
  `/api/liquidity/toggle`, `/api/liquidity/features` endpoints exist
- Liquidity is DISABLED by default everywhere (no persisted settings yet)

## Real data available
- `data/raw/XAUUSD_M1.parquet`: 100,000 rows, 2026-05-01 17:15 → 2026-06-18,
  columns time/open/high/low/close/tick_volume/spread/real_volume/time_utc
- Also M5/M15/H1/H4/D1 parquets + 4 existing dataset artifacts

## Current tests (re-run this session)
- Liquidity engine suites (TASK-01): 60 passed
- Swarm integration suite (test_liquidity_runtime_integration_phase18.py): 30 passed
- Total this run: 90 passed, 0 failed

## Known failures (foreign WIP / pre-existing, NOT TASK-02 scope)
- test_forensic_monitoring_task11.py (untracked foreign) — FAILS (5 cases)
- TestGovernance70 in test_model_governance_phase16.py (foreign 70D) — FAILS (5)
- test_web_security.py — pre-existing; repo-wide mypy blocked by web/server.py
  parse quirk at HEAD (39 errors, unrelated)
- test_shadow70_safety.py — foreign collection error (fixed upstream by conftest
  commit 2a30b14)

## UI config state
- Web UI has 70D-agent's liquidity section (state rendered from API); no
  TASK-02-specific 60D toggle verified yet — STEP 4 target

## API state
- `/api/status` embeds `liquidity: _liquidity_state_section(engine)` (70D-report)
- `/api/liquidity/state` + `/api/liquidity/toggle` + `/api/liquidity/features` exist