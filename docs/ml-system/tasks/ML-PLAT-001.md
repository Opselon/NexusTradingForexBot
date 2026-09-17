# ML-PLAT-001 — MT5 Runtime Boundaries & Remote Gateway Linux Parity Verification

STREAM: STREAM K — PLATFORM/MT5
PRIORITY: P2
STATUS: READY
DEPENDENCIES: None
AGENT_ROLE: AGENT-QA
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
1. tests/integration/test_mt5_adapter_parity.py passes cleanly on Linux.
2. Remote gateway adapter serializes orders conforming strictly to MT5 execution contracts.

## ABORT_CONDITIONS
If adapter modifies contract payload structure, STOP and report client contract violation.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/integration/test_mt5_adapter_parity.py`

## SHARED_FILE_RISK
Low. AGENT-QA owns parity test.
