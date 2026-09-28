# STRATEGY_LIFECYCLE_MAP.md — Forensic Baseline (Phase 0)

## 1. Executive Summary
This document provides the canonical forensic map of the Nexus Scalp Engine strategy lifecycle, data provenance, storage layers, event flows, execution eligibility gates, and observability hooks. It implements **Phase 0** of the Strategy Command Center mandate.

---

## 2. Canonical State Machine
The strategy lifecycle is governed by `CandidateLifecycle` (defined in `nexus_scalp.research.models` and enforced by `nexus_scalp.research.lifecycle`).

```
DISCOVERED
  │
  ▼
BACKTESTING
  │
  ▼
VALIDATING
  │
  ▼
OOS_TESTING
  │
  ▼
ROBUSTNESS_TESTING
  │
  ▼
VALIDATED ──(Operator Approval / Spec 21)──► ACTIVE (Live Execution Boundary)
  │
  ├──► SHADOW (Sandbox / Paper)
  │
  ├──► DEGRADED
  │
  └──► RETIRED / REJECTED
```

### State Definitions & Invariants

| State | Purpose | Entry Condition | Exit Condition | Allowed Transitions | Forbidden Transitions | Blocking Conditions | AI Involvement | Human Involvement | Required Evidence | Failure Conditions | Recovery Path | Persistence | Events | Existing Tests | Execution Implications |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DISCOVERED** | Raw hypothesis / feature cluster from discovery engine | Feature cluster discovery from experience dataset | Candidate candidate record upserted | BACKTESTING, REJECTED | VALIDATED, ACTIVE, SHADOW | Insufficient samples (< MIN_EVIDENCE_SAMPLES), low variance | High (AI hypothesis / feature discovery) | Low / review | Experience sample set, discovery window | Zero samples, empty family distribution | Re-run discovery pipeline | `strategy_registry` | `candidate_discovered` | `test_lifecycle_e2e_v3.py` | Ineligible for live/shadow execution |
| **BACKTESTING** | Deterministic friction backtest (spec 13) | Transition from DISCOVERED | BacktestResult successfully generated | VALIDATING, REJECTED | ACTIVE, VALIDATED, SHADOW | Missing dataset, invalid feature dimension | None (deterministic simulator) | None | Dataset ID, ExecutionAssumptions | Negative expectancy, extreme drawdown | Adjust hyperparameters or re-sample | `strategy_registry` (backtest json) | `backtest_completed` | `test_lifecycle_e2e_v3.py` | Ineligible |
| **VALIDATING** | Walk-forward split validation (spec 14) | Transition from BACKTESTING | WalkForwardResult generated | OOS_TESTING, REJECTED | ACTIVE, SHADOW | Unstable folds, high degradation | None (algorithmic split) | None | Walk-forward folds, train/val/oos metrics | Walk-forward failure, degradation > threshold | Re-optimize search space | `strategy_registry` (walkforward json) | `walk_forward_completed` | `test_lifecycle_e2e_v3.py` | Ineligible |
| **OOS_TESTING** | Hard out-of-sample evaluation (spec 15) | Transition from VALIDATING | OOSResult generated | ROBUSTNESS_TESTING, REJECTED | ACTIVE, SHADOW | OOS expectancy <= 0 | None | None | OOS samples, in-sample vs out-of-sample comparison | OOS failure, negative OOS expectancy | Re-run data split | `strategy_registry` (oos json) | `oos_completed` | `test_lifecycle_e2e_v3.py` | Ineligible |
| **ROBUSTNESS_TESTING** | Perturbation / stress testing (spread, slippage, latency) (spec 16) | Transition from OOS_TESTING | RobustnessResult generated | VALIDATED, REJECTED, DEGRADED | ACTIVE, SHADOW | Excessive sensitivity to spread/latency | None | None | Stress expectations, degradation metrics | Robustness failure, breakdown under latency | Widen search space tolerance | `strategy_registry` (robustness json) | `robustness_completed` | `test_lifecycle_e2e_v3.py` | Ineligible |
| **VALIDATED** | All research gates passed; scored (spec 17) | Successful robustness pass + StrategyScore computed | Operator promotion or shadow transition | SHADOW, ACTIVE, REJECTED, DEGRADED | DISCOVERED, BACKTESTING | None | Low (score synthesis) | Required for ACTIVE promotion | Complete Backtest, WF, OOS, Robustness + Score | Post-validation degradation | Self-heal or re-test | `strategy_registry` | `strategy_validated` | `test_lifecycle_e2e_v3.py` | Eligible for shadow/paper; eligible for ACTIVE **only** via deliberate operator approval (spec 21) |
| **SHADOW** | Sandbox / paper trading evaluation | Transition from VALIDATED | Shadow evaluation running | ACTIVE, DEGRADED, REJECTED | DISCOVERED, BACKTESTING | Live risk limit breach, broker disconnect | None | Oversight | Shadow trade ledger, fill latency, slippage tracking | Shadow expectancy divergence from backtest | Pause shadow, inspect telemetry | `strategy_registry` + shadow ledger | `shadow_started`, `shadow_update` | `test_model_lifecycle_phase10.py` | Paper execution only; NO live capital routing |
| **ACTIVE** | Live production execution | Deliberate operator-gated promotion (spec 21) from SHADOW or VALIDATED | Retirement or degradation | DEGRADED, RETIRED | DISCOVERED, BACKTESTING, VALIDATING | Risk engine circuit breaker, drawdown limit | None | Strict (mandatory manual signoff) | Production fills, live risk approval, audit trace | Drawdown breach, data feed corruption, latency spike | Emergency stop, fallback to SHADOW/RETIRED | `strategy_registry` + `experience_model_registry` | `model_promoted_active` | `test_model_lifecycle_api.py` | Fully trade-eligible in production |
| **DEGRADED** | Performance decay or anomaly detected | Operational anomaly, runtime regression, expectancy drop | Self-heal or retirement | RETIRED, REJECTED, VALIDATED | DISCOVERED | Unresolved degradation | None | Recommended review | Anomaly score, health metrics, recent losses | Persistent underperformance | Re-test or retire | `strategy_registry` | `strategy_degraded` | `test_nse_lifecycle_regression_matrix.py` | Blocked from new live allocation |
| **REJECTED** | Failed a research gate or regression guard | Gate failure (OOS, Robustness, WF) or regression refusal | None (terminal state) | None (terminal) | Any except DISCOVERED | Terminal state | None | None | Failure reason, failing gate metrics | N/A (terminal) | Create new candidate version | `strategy_registry` | `strategy_rejected` | `test_lifecycle_e2e_v3.py` | Strictly ineligible |
| **RETIRED** | Superseded or manually decommissioned | Operator action or permanent replacement by new Champion | None (terminal state) | None (terminal) | Any except DISCOVERED | Terminal state | None | Required | Retirement reason, replacement strategy ID | N/A (terminal) | N/A | `strategy_registry` | `strategy_retired` | `test_model_lifecycle_api.py` | Strictly ineligible |

---

## 3. Data Sources, Storage, and Write Paths

1. **Authoritative Source**: The immutable experience ledger (`audit_experiences`, `audit_experience_outcomes`, `audit_ledger`). All research datasets and candidate generations are causally derived from closed experiences.
2. **Research Store**: `nexus_scalp.research.store` and `nexus_scalp.research.registry` (`strategy_registry` table in SQLite).
3. **Model Registry**: `nexus_scalp.model_lifecycle.registry` (`experience_model_registry` table with additive lifecycle columns: `lifecycle_status`, `training_run_id`, `parent_model_id`, `gate_summary`, etc.).
4. **Write Channels**: All mutations flow through `AuditRepository` background queue (`_queue.put_nowait`). The UI and read models never mutate domain state directly.

---

## 4. Execution Eligibility & Safety Rules
- **Rule 1**: A strategy must never trade live unless its lifecycle state is `ACTIVE` (or explicitly gated shadow/paper).
- **Rule 2**: `require_validation_gate(lifecycle)` enforces that only `VALIDATED`, `SHADOW`, or `ACTIVE` can be considered for live use.
- **Rule 3**: Champion / Challenger promotion requires passing Phase 10 model lifecycle gates without skipping steps.
- **Rule 4**: Anti-regression check (`_is_stronger`) prevents silent downgrades of established validation truth.

---

## 5. Observability Gaps Identified
1. **AI Attribution Traceability**: While discovery logs AI hypotheses, attribution weights (e.g., AI vs deterministic rules) are not explicitly stored as first-class decision contribution records.
2. **Real-time Transition Telemetry**: WebSocket broadcast of state transitions currently relies on polling or factory worker ticks; needs a unified event bus projection.
3. **Evidence Completeness Checks**: No automated pre-check ensures all required artifacts (backtest, OOS, robustness) are present before entering validation-gated states.
