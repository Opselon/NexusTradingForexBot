# 2026-09-20 — AGENT-PLATFORM — ML-PLAT-001

**Task:** ML-PLAT-001 — MT5 Runtime Boundaries & Remote Gateway Linux Parity Verification
**Role:** AGENT-PLATFORM (ledger line 77 is the ownership SSOT; the task file named AGENT-QA — reconciled in the task file)
**Status:** DONE-PR-OPENED (#322)
**Branch:** `agent/platform/ml-plat-001` off `origin/main` @ `463f5e1f`
**Zero production code changed** — the client contract was verified, never modified (NON_GOALS).

## What was built

`tests/integration/test_mt5_adapter_parity.py` — 49 tests pinning the Linux execution
boundary. A real in-process HMAC-signed RPC bridge (`stdlib http.server`, bound to a free
loopback port) speaks the documented wire contract so `RemoteMT5GatewayAdapter` is exercised
end-to-end with no source patching of the adapter itself.

## Acceptance criteria — both satisfied

1. `tests/integration/test_mt5_adapter_parity.py` passes cleanly on Linux → **49 passed in 12.37s**.
2. Remote gateway order serialization conforms strictly to the MT5 execution contract →
   payloads asserted to be EXACTLY the contract key sets (no extra/missing fields), strict
   types, monotone BUY/SELL economics.

## Gate evidence (all run at the worktree HEAD)

| Gate | Result |
|---|---|
| `pytest tests/integration/test_mt5_adapter_parity.py` | 49 passed in 12.37s (Python 3.11.16, pytest 9.1.1) |
| `ruff check .` (repo-wide) | All checks passed! |
| `ruff format --check .` (repo-wide) | 2202 files already formatted |
| `mypy tests/integration/test_mt5_adapter_parity.py` | Success: no issues found in 1 source file |
| `scripts/ci/verify_critical_suite_manifest.py` | CRITICAL_SUITE_MANIFEST_OK: 211 paths all exist |
| `scripts/ci/check_dependency_drift.py` | OK — 98 pins (no pyproject change) |

Neighbours green: `test_remote_gateway.py`, `test_gateway_e2e.py`,
`test_mt5_api_probes.py` (native-MT5 cases SKIP on Linux by design), `test_mt5_accounting_api_contract.py`.

## Contract coverage

- **Port surface:** both Linux adapters are concrete `IMT5Port`; every port-declared primitive
  (`connect`…`close_position`, `execute_market_order`, `place_pending_order`) present on both;
  `RemoteMT5GatewayAdapter` also implements `IGatewayPort`.
- **Native boundary:** `DirectMT5Adapter` imports `MetaTrader5` only under `sys.platform == 'win32'`
  (mt5_adapter.py:71) → on Linux `HAS_NATIVE_MT5 is False`; asserted structurally since the CI
  Linux leg cannot instantiate the win32 path.
- **Payloads:** `SEND_ORDER` → exactly `{order_id, symbol, order_type, volume, price, stop_loss,
  take_profit, magic_number, comment, idempotency_key}`; `EXECUTE_MARKET_ORDER` /
  `PLACE_PENDING_ORDER` → exactly `{symbol, order_type, volume, price, stop_loss, take_profit}`
  (typed floats). BUY: `take_profit > price > stop_loss > 0`; SELL: `stop_loss > price > take_profit`.
- **UNKNOWN != FAILED:** HTTP 502 and request timeout both → `WriteOutcome.UNKNOWN` (ticket 0);
  broker `REJECTED` → `WriteOutcome.REJECTED` with `retcode` preserved; `SUCCESS` without a
  ticket → UNKNOWN (ambiguity, not success).
- **Idempotency:** `write_market_order` carries `idempotency_fingerprint`
  = `888101|XAUUSD|BUY|0.1|2345.6` (order_write.py:74); legacy `send_order` derives its own
  `<order_id>_<epoch_ms>` key.
- **Timestamps:** naive ISO → UTC promotion; offset-aware (`+02:00`) → UTC instant. Pinned for
  ticks and bars on both adapters (a drift here silently desyncs chronology Linux vs Windows).
- **Auth:** every RPC HMAC-SHA256 signed; tampered secret → bridge 401 → client `RuntimeError`;
  constant-time verification pinned.
- **Paper parity:** ticks UTC + ask≥bid>0, market-order ticket matches the created `Position`,
  duplicate `order_id` rejected (idempotency), invalid size fail-closed (no position), modify/close
  honored, bars completed and ascending.
- **Outage:** mid-session 503 → exception, then recovery without adapter re-construction.
- **Benchmark:** 1,000-order client-side serialization p99 < 1ms (network factored out via a
  `_send_request` recorder — end-to-end latency is bounded by the RPC round-trip, not the
  contract surface); typed market-write path measured too; loopback round-trip bounded.

## Contract discoveries (agent-facing)

1. **Task-file paths are stale.** `remote_gateway_adapter.py` / `paper_adapter.py` do not exist;
   real modules are `adapters/mt5/remote_gateway.py:38` and `adapters/paper/paper_adapter.py:93`.
2. **`connect()` is not free**: it issues a live `PING` RPC (remote_gateway.py:60 → `_sync_ping`
   :427). Request-counting assertions must account for it.
3. **Two order-write surfaces with different contracts**: legacy `send_order` (10 keys) vs typed
   `write_market_order` (6 keys + `idempotency_fingerprint`); `execute_market_order` /
   `place_pending_order` carry NO idempotency field.
4. **Fail-closed is exact**: 502/504/timeout → UNKNOWN via the `except` at remote_gateway.py:289;
   the bridge's 401 surfaces as `RuntimeError` (concrete type, not blind `Exception`).
5. **Paper's duplicate-order_id guard** (paper_adapter.py:1448-1470) is idempotency by design, and
   ledgers a `duplicate_order_id` rejection — expected parity behavior.

## Pre-existing red (NOT caused by this task)

`tests/unit/test_paper_parity.py::TestPaperParity::test_stats_are_honest` and
`::test_snapshot_measured_with_both_sides` fail (`assert stats["attempts"] == 2` → 0) — reproduced
on the untouched shared tree at the same HEAD `463f5e1f`. This task touches zero production code
and does not import `test_paper_parity`. Flagged for the owner of `risk/paper_parity.py`.

## Next run candidates

1. `ML-FEAT-002` (P2 | AGENT-FEATURE) — deps ML-DATA-001 + ML-FEAT-001, both DONE.
2. `ML-CI-002` (P3 | AGENT-GIT) — deps ML-ARCH-001 (HUMAN DECISION) + ML-PLAT-002 (DONE): still
   blocked on the human-gate.
3. Still blocked: `ML-TRAIN-001` / `ML-EXP-001` (dep chain through `ML-ARCH-001`, HUMAN DECISION),
   `ML-GOV-002` / `ML-OBS-002` / `ML-FEAT-003` / `ML-EXP-002` (HUMAN_DECISION_REQUIRED=YES),
   `ML-CI-001` (scope is `.github/workflows/`, forbidden to the swarm).
