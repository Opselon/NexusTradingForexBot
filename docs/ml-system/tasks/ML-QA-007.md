# ML-QA-007 — Push-Gate Latency Determinism: MT5 Parity Suite (Roster Candidate #4)

STREAM: STREAM L — CI/CD & Verification
PRIORITY: P2
STATUS: DONE (2026-09-23, AGENT-QA)
DEPENDENCIES: ML-QA-004 (DONE, PR #402)
AGENT_ROLE: AGENT-QA
OWNERSHIP_SCOPE: tests/, scripts/ci/
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Remediate roster candidate #4 from `docs/ml-system/test_determinism_roster.md` §6:
`tests/integration/test_mt5_adapter_parity.py` carried 6 `time.perf_counter()`
probes inside latency *measurement* asserts, is in the push-gate manifest
(`tests/critical_suite.txt` line 264), and executes on the cross-OS matrix
(`tests-os.yml` FULL lane on windows-latest + macos-latest for every main push,
Monday schedule, and dispatch). A wall-clock p99 under `-n auto --dist
loadgroup` saturation measures the scheduler, not the serialization cost —
the exact shape that forced 9279f1ea to DROP the absolute 1ms threshold
entirely (restoring the SLA as a measured contract, not an abandoned one,
is this task's headline).

## DELIVERED
- `tests/integration/test_mt5_adapter_parity.py` (test-only; ZERO production
  code, ZERO adapter-contract change — RemoteMT5GatewayAdapter untouched):
  - `test_order_serialization_latency_under_1ms`: 5-iteration warmup loop +
    `calls.clear()` before the timed window; probes moved from
    `time.perf_counter()` to `time.process_time()` (CPU time — co-tenant
    preemption excluded by construction); deterministic invariants kept hard
    (1000 calls, `["SEND_ORDER"] * 1000`, sorted, p99 ordering); the <1ms SLA
    RE-ATTACHED as `mean_cpu_ms < 1.0` on CPU time.
  - `test_market_order_serialization_p99_under_1ms`: same shape (warmup,
    `calls.clear()`, `process_time()`, ordering invariants, CPU mean < 1.0ms).
  - `test_round_trip_through_local_bridge_is_bounded`: warmup RPC, then
    `budget_cpu_ms(500.0)` from the shared `tests/e2e/chain_clock.py` helper
    (one measurement implementation tree-wide) replacing the raw
    `perf_counter` wall-clock arithmetic.
- `tests/unit/test_ml_qa_007_parity_latency_determinism.py` — 19-test
  contract battery (textual analysis, runs in the slim venv — no torch import):
  - no wall-clock measurement anchors/asserts remain in the module;
  - every remaining probe is `process_time`;
  - warmup precedes the first timed probe in BOTH SLA tests, and warmup calls
    are cleared so the 1000-call invariant is honest;
  - load-independent invariants (call count, action identity, percentile
    ordering, CPU mean bound, budget helper use) are still asserted exactly;
  - adapter-contract untouched (instance-level `_send_request` monkeypatch
    seam preserved, no module-level patching).
  Registered in `tests/critical_suite.txt` (239 paths) — rides the required
  Code Quality & Tests / Pytest check, keeping the CHG-0049 gate-parity
  contract honest (no CI-only gate class).
- Roster §6 candidate #4 annotated REMEDIATED with evidence pointer.

## VERIFICATION (all at worktree branch `agent/qa/parity-latency-determinism`, Python 3.11.16)
- Parity module serial (slim venv): `pytest tests/integration/
  test_mt5_adapter_parity.py` -> **49 passed** in 12.39s (baseline: 49 passed
  at origin/main 52aaa8b0 — no behavioral regression)
- The 3 touched latency tests: 3/3 pass in 1.88s
- New contract battery: `pytest tests/unit/test_ml_qa_007_parity_latency_
  determinism.py` -> **19/19 pass** in 0.10s
- Prior determinism suites still green: `test_ml_qa_004_determinism_
  remediation.py` -> 24/24 pass
- NEGATIVE CONTROL (pre-remediation text of the module): 3 bare
  `t0 = time.perf_counter()` anchors present, zero warmup loops before the
  first timed probe, no `calls.clear()` — all three fail the new contract
  battery's invariants, proving the tests are live, not tautological.

## NOT_CHANGED (deliberately)
- No `.github/workflows/*` touched (HARD CONSTRAINT).
- No production source touched (tests/ only).
- `time.sleep(latency_seconds)` in the gateway harness is a *stimulus* knob,
  not a measurement — left alone.
