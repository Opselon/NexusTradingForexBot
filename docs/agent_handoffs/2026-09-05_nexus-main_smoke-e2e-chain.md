# Handoff — SMOKE-E2E Whole-Application Chain (Nexus-Main)

**Agent:** Nexus-Main
**Role:** Orchestrator / final assembler (master-contract loop: Research -> Coder -> Reviewer -> QA, peers offline -> direct execution)
**Date:** 2026-09-05
**Branch:** main
**Starting HEAD:** 9edbfe7a (Agent 6 BUG-249-A6)
**Ending HEAD:** 75ce90e9 (wire), handoff at 17d43ad1 push via 75ce90e9 ancestry
**Task:** User brief: "create end to end smoketest for our app and add to CI tests very pretty and long and complete dont create many files for smoke test its a chain in whole app"

## Task

Build ONE file end-to-end smoketest that walks the WHOLE application as a chain, pretty output, long & complete, and wire it into CI so every push proves the system still wires together.

## Scope / Files Changed

| File | Change | Notes |
|---|---|---|
| `tests/e2e/test_smoke_chain.py` | NEW (838 lines, 4 tests) | Single-file whole-app chain; hermetic (tmp_path + mock paper adapter), no MT5/network/sleep |
| `.github/workflows/ci.yml` | + smoke step in quality job + smoke in fail-job/review-status lists | Own `ci-results/smoke` lane; RUFF_FORMAT_RC gated; mirrors pytest lane pattern |
| `tests/critical_suite.txt` | + SMOKE-E2E entry | LF pinned, local beforePush parity via gate_parity |
| `agents/change_control.md` | + CHG-0065 | See below |
| `agents/taskboard.md` | + TASK-SMOKE-E2E row | VERIFIED |

Unchanged (intentionally): no production `src/` touched.

## Functions / Classes Touched

- None in production. The smoketest exercises (real code, not mocks) `TickData`, `BarAggregator`, `ScalpFeatureEngine.compute_from_bars`, `FeatureVector.to_tensor_input`, `features70.assemble_70d` + `validate_70d_vector` + `feature_schema_hash`, `ScalpNet`, `SignalPolicy.evaluate_probabilities`, `RiskEngine.evaluate_proposal`, `OrderLifecycleManager.dispatch_order`/`_clamp_dispatch_volume`/`_processed_orders`, `AuditRepository` (signals/executions/ledger/snapshots), `web.server.create_app` + `web.api_v1_wiring.create_v1_app`.

## Contracts / Invariants

- **FEATURE_SCHEMA v1** (scalp_v1 50D active, scalp_v3 70D + hash), **TRADE_EXECUTION_CONTEXT** (EXEC- id stamp), **API_V1_ENVELOPE v1** ({data,meta}, X-Request-ID) — verified, not changed
- INV-001/004/008/009 respected (no hot-path DB, execution authority via OrderLifecycleManager, causal features, schema hash). Tests-only change.

## Chain Detail (10 stages, one file)

```
01 TickData (UTC, spread_points) + BarAggregator (69 boundaries from 70 M1 bars)
02 ScalpFeatureEngine -> FeatureVector -> 50D tensor (finite, [-3,+3])
03 assemble_70d (Base|News|Liquidity, neutral sentinels) + schema hash (16-char, deterministic) + base/news/liquidity slices
04 ScalpNet(num_features=50, num_classes=4) -> 4-logit softmax (sum ~1.0, deterministic)
05 SignalPolicy -> BUY_MARKET (0.97 conf, AGGRESSIVE_SCALP_BUY, EXEC- id) + NO_TRADE branch on range vector (past 60s throttle)
06 RiskEngine (1% -> ~0.50 lots, HARD_MAX_LOTS=10.0, NO_TRADE refusal is None)
07 OrderLifecycleManager dispatch_order (paper, dispatched True, duplicate request_id False, clamp 999->10.0)
08 AuditRepository (signals=1, executions=1, ledger OPENED=1, snapshots=1, SMOKE_CHAIN row)
09 FastAPI create_app + create_v1_app (5 system routes 200, envelope + meta.request_id + X-Request-ID)
10 Wall summary banner
```

Four tests: `test_smoke_full_chain` (orchestration), `test_smoke_risk_one_percent_not_ten_percent`, `test_smoke_exposure_guard_and_idempotency`, `test_smoke_feature_cold_start_and_schema`.

## Tests Added / Run

- Added: 4 tests in `tests/e2e/test_smoke_chain.py`
- Run (local, this HEAD):
  - `pytest tests/e2e/test_smoke_chain.py -v` -> 4 passed (7-15s wall)
  - `ruff check` + `ruff format --check` -> clean on the file
  - `gate_parity.py --json` -> PASS (6/6)
- Critical suite now includes the smoke file; full critical suite was not re-run in this session due to foreign WIP that currently breaks beforePush's whole-tree ruff (10 fixed / 6 remaining) — that WIP is not ours.

## Runtime Verification

- No live engine / MT5. Hermetic by design. CI run on 75ce90e9 (origin/main) is the final authority — quality job now executes the smoke lane and gates on it.

## Coordination / Absorption Events (master-contract disclosure)

Two absorptions due to parallel swarm churn on main:
1. The `tests/e2e/test_smoke_chain.py` file first staged on a main working tree was caught in a branch swap to `agent/nexus-main/agent5-decision-risk-forensics` (dirty WIP there hid it); it then landed via the concurrent `924a40a1` / `ed551dd1` amalgam (Agent-4 BUG-243) that absorbed the untracked-but-present file. Byte-verified identical to our backup (`$LOCALAPPDATA/Temp/smoke-restor/smoke_file.py`).
2. The `ci.yml` + `critical_suite.txt` wiring first staged alongside was absorbed into the concurrent `59f39256` (same Agent-4), itself re-landed as `17d43ad1`. Re-wired surgically via `wire2.py` into `75ce90e9` and verified (grep -c smoke == 9, manifest tail carries the entry, origin/main shows all three at 75ce90e9).

No foreign files were staged by this agent (explicit `git restore --staged` after every add that picked up foreign M files).

## Bugs Fixed / Discovered

- None fixed. No new bugs discovered.

## Risks / Remaining Work

- Foreign `src/` WIP from parallel agents (application/live_engine, execution/order_manager, signal/policy, training/emission_gate, web/*, tests/unit/test_a2_*) sits as unstaged `M` on this working tree — not staged, not committed by this agent. Their beforePush state remains failing until their owners fix the RUF043/B017 residues already visible in `ruff check .`.
- A2A peers (nexus-coder 9901, nexus-reviewer 9902, nexus-researcher 9903, nexus-qa 9904, nexus-devops 9905) are all offline (HTTP 000, gateway_state stopped, needs_attention since 2026-08-22). The master loop was executed directly by Nexus-Main with adversarial self-review instead of delegated review/QA.

## Exact Next-Agent Instructions

1. Watch the CI run on `75ce90e9` (origin/main) — the quality job must show `smoke=passed` in its summary and produce `ci-results/smoke/junit.xml`. On red, read `ci-results/smoke/pytest.txt` first (the chain stage banners pinpoint the failed stage).
2. Do not add helper files for the smoke chain — user constraint is one file. Extend stages inside `tests/e2e/test_smoke_chain.py` only.
3. The three sentinels (`test_smoke_risk_one_percent_not_ten_percent`, `test_smoke_exposure_guard_and_idempotency`, `test_smoke_feature_cold_start_and_schema`) are PIN tests — never relax their bounds.
4. A2A peers remain offline — if retrying delegation, first restart gateways (`hermes gateway restart` per profile) and re-probe `127.0.0.1:9901..9905/.well-known/agent.json` until HTTP 200 before calling `a2a_call`.
