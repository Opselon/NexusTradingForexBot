# ML-PLAT-001 — MT5 Runtime Boundaries & Remote Gateway Linux Parity Verification

STREAM: STREAM K — PLATFORM/MT5
PRIORITY: P2
STATUS: DONE (2026-09-20; 49/49 parity tests green on Linux — PR #322)
DEPENDENCIES: None
AGENT_ROLE: AGENT-PLATFORM (task file said AGENT-QA; ledger line for ML-PLAT-001
  designates AGENT-PLATFORM — the ledger is the ownership SSOT, so the platform
  persona was adopted; the parity test itself is an AGENT-QA-shaped artifact,
  which is why the task originally named AGENT-QA)
OWNERSHIP_SCOPE: src/nexus_scalp/adapters/mt5/remote_gateway_adapter.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Verify that Linux deployments using RemoteMT5GatewayAdapter and PaperAdapter achieve identical tick ingestion, order payload serialization, and state tracking as native Windows MT5Adapter.

## WHY_IT_EXISTS
MetaTrader5 Python API is proprietary and Windows-only (sys.platform == 'win32'). Linux production servers must rely on RemoteMT5GatewayAdapter or PaperAdapter. Any protocol drift or timestamp parsing discrepancy between the adapters risks divergent execution behavior.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/adapters/mt5/mt5_adapter.py` (Lines: `73`)
  - **Symbol:** `MT5Adapter`
  - **Behavior:** Imports MetaTrader5; win32 only
  - **Classification:** `WINDOWS-ONLY`
  - **Confidence:** 100%
  - **Contradiction:** Cannot run natively on Linux servers
- **Path:** `src/nexus_scalp/adapters/mt5/remote_gateway_adapter.py` (Lines: `45-120`)
  - **Symbol:** `RemoteMT5GatewayAdapter`
  - **Behavior:** Connects via REST/TCP to remote Windows MT5 gateway; Linux compatible
  - **Classification:** `PRODUCTION CAPABILITY`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/adapters/mt5/paper_adapter.py` (Lines: `35-95`)
  - **Symbol:** `PaperAdapter`
  - **Behavior:** In-memory order book simulation; Linux compatible
  - **Classification:** `PRODUCTION CAPABILITY`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- Native MT5 is win32 only.
- Remote gateway and paper adapters provide Linux execution.

## UNKNOWNs
- Websocket reconnect latency under unstable remote gateway network connections.

## SCOPE
Write tests/integration/test_mt5_adapter_parity.py comparing order payloads emitted by RemoteMT5GatewayAdapter and PaperAdapter against the contract specification.

## NON_GOALS
Do not modify the RemoteMT5GatewayAdapter network protocol or client contract.

## SOURCE_AREAS
- `src/nexus_scalp/adapters/mt5/remote_gateway_adapter.py`
- `src/nexus_scalp/adapters/mt5/paper_adapter.py`

## FILES_LIKELY_TO_CHANGE
- `tests/integration/test_mt5_adapter_parity.py`

## INVESTIGATION_PLAN
Inspect RemoteMT5GatewayAdapter error handling on HTTP 502/504 connection timeouts.

## IMPLEMENTATION_PLAN
1. Create tests/integration/test_mt5_adapter_parity.py.
2. Mock MT5 gateway REST endpoints (/tick, /order_send, /positions).
3. Execute order proposals via RemoteMT5GatewayAdapter and PaperAdapter.
4. Assert generated order payloads match expected MT5 schema.
5. Test network timeout and assert clean reconnect retry logic.

## TEST_PLAN
- `pytest tests/integration/test_mt5_adapter_parity.py -v`

## BENCHMARK_PLAN
Measure order serialization latency across 1,000 mock orders (< 1ms).

## EVIDENCE_REQUIRED
- Test file: tests/integration/test_mt5_adapter_parity.py
- Passing pytest output

## ACCEPTANCE_CRITERIA
1. tests/integration/test_mt5_adapter_parity.py passes cleanly on Linux. — [x] VERIFIED: 49 passed in 12.4s, Python 3.11.16, pytest 9.1.1, on origin/main HEAD 463f5e1f in an isolated worktree.
2. Remote gateway adapter serializes orders conforming strictly to MT5 execution contracts. — [x] VERIFIED: SEND_ORDER payload asserted to be EXACTLY the 10-key contract set (order_id/symbol/order_type/volume/price/stop_loss/take_profit/magic_number/comment/idempotency_key) with strict types and monotone BUY/SELL economics; EXECUTE_MARKET_ORDER and PLACE_PENDING_ORDER asserted to be EXACTLY the 6-key typed set (symbol/order_type/volume/price/stop_loss/take_profit), all floats. No adapter source was modified (NON_GOALS honored — zero production diff).

## VERIFICATION_EVIDENCE (2026-09-20)
- Gate: `pytest tests/integration/test_mt5_adapter_parity.py` → 49 passed in 12.37s (slim Linux venv, PYTHONPATH=src:.).
- Gate: `ruff check .` (repo-wide) → All checks passed!
- Gate: `ruff format --check .` (repo-wide) → 2202 files already formatted (ruff 0.16.6 formats ```python fences in .md — repo-wide check is the real gate).
- Gate: `mypy tests/integration/test_mt5_adapter_parity.py` → Success: no issues found in 1 source file.
- Gate: `scripts/ci/verify_critical_suite_manifest.py` → CRITICAL_SUITE_MANIFEST_OK: 211 paths all exist (new file registered).
- Gate: `scripts/ci/check_dependency_drift.py` → OK, 98 pins (no pyproject change in this task).
- Benchmark (BENCHMARK_PLAN "measure order serialization latency across 1,000 mock orders (< 1ms)"): p99 client-side serialization measured over 1,000 orders with `_send_request` stubbed to a recorder (network factored out — end-to-end latency is bounded by the RPC round-trip, not the contract surface). Both the `send_order` (TradeOrder) path and the typed `execute_market_order` path measured. Also asserted a loopback round-trip through the in-process bridge stays well under the adapter's 3.0s timeout.
- Neighbours: test_remote_gateway.py + test_gateway_e2e.py + test_mt5_api_probes.py + test_mt5_accounting_api_contract.py all green (mt5_api_probes' native-MT5 cases SKIP on Linux by design — MetaTrader5 is Windows-only).
- PRE-EXISTING RED (not caused by this task, reproduced on the untouched shared tree at the same HEAD): tests/unit/test_paper_parity.py::TestPaperParity::test_stats_are_honest and ::test_snapshot_measured_with_both_sides fail (`assert stats["attempts"] == 2` → 0). This task touches zero production code and does not import test_paper_parity; flagged for the owner of `risk/paper_parity.py`.

## CONTRACT_DISCOVERIES (agent-facing)
1. The task file's SOURCE_AREAS/OWNERSHIP_SCOPE paths are STALE: `src/nexus_scalp/adapters/mt5/remote_gateway_adapter.py` and `.../paper_adapter.py` do not exist. The real modules are `src/nexus_scalp/adapters/mt5/remote_gateway.py:38` (`RemoteMT5GatewayAdapter`) and `src/nexus_scalp/adapters/paper/paper_adapter.py:93` (`PaperMT5Adapter`). A new parity test should NOT be written against the file names in the task file.
2. There is no `tests/integration/` collection breakage risk from a third adapter: `DirectMT5Adapter` (mt5_adapter.py:143) imports `MetaTrader5` only under `sys.platform == 'win32'` (mt5_adapter.py:71), so on Linux `HAS_NATIVE_MT5 is False` and `mt5 is None`. Native parity is therefore asserted STRUCTURALLY (import-guard + HAS_NATIVE_MT5), not by instantiation — the CI matrix's Linux leg cannot exercise the win32 path.
3. `connect()` is NOT connection-state-free: it issues a live `PING` RPC (remote_gateway.py:60 -> `_sync_ping` at :427). Any assertion counting gateway-side requests must account for the session PING (3 B017/PLW0108-class test bugs came from this).
4. The remote gateway has TWO order-write surfaces with DIFFERENT payload contracts: legacy `send_order(order: TradeOrder) -> bool` (10-key payload incl. idempotency_key, remote_gateway.py:199) and the typed tri-state `write_market_order(...) -> WriteResult` (remote_gateway.py:241, 6-key payload incl. `idempotency_fingerprint` from execution/order_write.py:74). A parity suite that tests only one does not cover the contract. `execute_market_order`/`place_pending_order` use the 6-key shape WITHOUT any idempotency field — the fingerprint exists only on `write_market_order`.
5. Fail-closed behavior is exactly as the contract requires: HTTP 502/504 and timeout both resolve to `WriteOutcome.UNKNOWN` (never REJECTED/FAILED) via the `except Exception` at remote_gateway.py:289 — broker state unobserved. A broker `{"status": "REJECTED"}` resolves to `WriteOutcome.REJECTED` with retcode preserved. The bridge-side 401 on a tampered HMAC reaches the client as `RuntimeError` (via `_send_request`'s blanket except at :468), so tests can assert on the concrete type.
6. Timestamp parity is a real contract: naive ISO timestamps are promoted to UTC (`dt.replace(tzinfo=UTC)`, remote_gateway.py:133-134 and :158-159) and aware timestamps normalize to the UTC instant. The parity suite pins both (including a +02:00 offset case) — a naive-vs-aware drift here would silently desync tick chronology between Linux and Windows deployments.
7. `PaperMT5Adapter`'s `send_order` duplicate-order_id guard (paper_adapter.py:1448-1470) is idempotency, not a bug: the second identical order returns False and ledgers a `duplicate_order_id` rejection. Parity tests must treat this as the expected behavior.

## ABORT_CONDITIONS
If adapter modifies contract payload structure, STOP and report client contract violation.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/integration/test_mt5_adapter_parity.py`

## SHARED_FILE_RISK
Low. AGENT-QA owns parity test.
