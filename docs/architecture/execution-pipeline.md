---
title: Execution Pipeline
description: OrderManager — the 60-scenario router, position state machine, protection ledger, recovery budget, and every hard clamp.
lang: en
---

# Execution Pipeline

`execution/order_manager.py` is the single dispatch authority (INV-004). It is
large by design (convention-locked hot path) and is being decomposed
surgically, seam by seam, with golden tests.

## Path of an order

```text
TradeProposal (frozen domain contract, execution_id trace)
  → policy/confluence checks (SMC matrix, Regime Guardian, confidence gate)
  → RiskEngine.evaluate_proposal() → dynamic volume (fractional Kelly)
  → OrderManager router (60 scenarios: fills, rejections, partials, errors)
  → IMT5Port adapter (Win32 IPC / ZMQ / paper)
  → position lifecycle (11 states with hysteresis)
  → protective exits (SL/TP first-touch, breakeven lock, profit giveback,
      adaptive exit protection)
  → broker reconciliation (broker truth wins — INV-011)
  → accounting + autopsy (immutable ledger)
```

## Hard clamps (never bypassed)

| Clamp | Value |
| :--- | :--- |
| `HARD_MAX_LOTS` | 10.0 |
| `MAX_TOTAL_EXPOSURE` | 1 position |
| free-margin clamp | ≤ 20% |
| pending re-quote lock | 30 s |
| exit drift guard | 1.0 × ATR |
| circuit breaker | SAFE_MODE after 3 rejections |

## Extracted seams (zero-behavior-change decomposition)

- `position_state_machine.py` — 11-state machine with hysteresis + emergency
  bypass (golden-tested).
- `protection_ledger.py` — per-ticket protection state (monotonic peak, NaN
  guard).
- `recovery_budget.py` — per-ticket recovery envelopes (ATR fallback, horizon
  clamp 30–600 s, exhaustion verdicts).

## Forensic traceability

Every proposal carries an `execution_id`; `[EXEC_TRACE]` logging, dispatch
reason embedding, and `/api/debug/trace` expose the full decision path. Exit
classification (`EXIT_CLASSIFICATION v3`) never invents a reason: UNKNOWN
stays UNKNOWN; MT5 DEAL_REASON 4 = SL (INV-012/013).

## Safety properties

- No second concurrency path around OrderManager.
- Teardown of per-ticket state is an **atomic bundle** (state machine +
  protection ledger + recovery budget + tombstones dropped together).
- Churn throttle + duplicate-execution guards (INV-005/006 idempotency).
