# LIFECYCLE PROPAGATION FORENSIC INVESTIGATION REPORT

**Document ID:** `LIFECYCLE_PROPAGATION_FORENSIC_V1`  
**Target Repository:** `C:\Users\Capsizer\source\repos\NexusTradingForexBot`  
**Branch:** `main`  
**Author:** Forensic Lifecycle Investigator (Agent 1)  
**Date:** August 24, 2026  

---

## 1. Executive Summary

An end-to-end forensic trace of strategy progression through the Nexus Scalp Engine research pipeline (`artifacts/audit.db`) reveals why middle pipeline states (`BACKTESTING`, `VALIDATING`, `OOS_TESTING`, `ROBUSTNESS_TESTING`, `VALIDATED`) show zero entries while `DISCOVERED` and `REJECTED` dominate.

### Key Forensic Findings
1. **Zero Intermediate State Persistence:** The state machine (`nexus_scalp/research/lifecycle.py`) defines 11 stages (`DISCOVERED` through `ACTIVE`, plus `REJECTED`, `DEGRADED`, `RETIRED`). However, `StrategyRegistry.upsert()` and `ResearchPipeline.validate_candidate()` evaluate all gates synchronously within a single execution block and persist **only the final verdict** (`DISCOVERED` or `REJECTED`, and theoretically `VALIDATED`).
2. **100% Rejection Rate at Rigorous Gates:** Out of **1,379 executed research runs** (`research_runs`), **zero candidates** successfully passed all quantitative evaluation gates simultaneously. Specifically:
   - Walk-forward validation failed **1,361 out of 1,379 times** (`WALK_FORWARD` gate status `FAILED`).
   - Out-of-sample (OOS) testing rejected all candidates failing positive expectancy or degradation thresholds (`OOS` gate status `FAILED` in 1,309 runs, with 70 inconclusive).
   - Consequently, **no candidate ever reached `VALIDATED`**, resulting in 1,093 terminal `REJECTED` entries and 72 `DISCOVERED` entries in `strategy_registry`.
3. **UI vs. Backend Model Mismatch:** The Command Center UI and Spatial 2.5D Layout (`spatial_layout.py`, `command_center.html`, `command_center_spatial.js`) query `strategy_registry.lifecycle` expecting persistent rows across all 8 pipeline zones. Because the backend only writes terminal verdicts (`REJECTED` or `DISCOVERED`), all middle zones naturally display **0**.

---

## 2. Pipeline Trace: Birth to Terminal State

Tracing a real candidate lifecycle (`SF-889A5B96D0` / `RUN-07695D`):
1. **Generation (`discovery.py`):** Candidate discovered from experience dataset and upserted into `strategy_registry` with lifecycle = `DISCOVERED`.
2. **Registration & Scheduling (`pipeline.py`):** Operator or background worker triggers `validate_candidate()`. A unique `run_id` (`RUN-07695D`) is minted, and a run snapshot is captured.
3. **Execution (`research_gates`):**
   - **Static Validation:** PASSED.
   - **Backtest Engine:** PASSED (`trades > 0`).
   - **Walk-Forward Engine:** FAILED (degradation threshold exceeded across folds).
   - **OOS Gate:** Evaluated, but because upstream or OOS expectancy failed, marked FAILED.
   - **Robustness:** Evaluated.
   - **Scoring & Verdict (`scoring.py`):** Multi-dimensional score computed; final verdict resolved to `REJECTED`.
4. **Lifecycle Transition & Persistence:** `pipeline.py` registers the final state (`CandidateLifecycle.REJECTED`) and upserts the entry into `strategy_registry`. The intermediate execution states (`BACKTESTING`, `VALIDATING`, etc.) are recorded transiently in `research_gates` and `research_runs`, but **never update the strategy's persistent registry lifecycle**.

---

## 3. Evaluation State Model vs. Real Lifecycle Model

| Dimension | Visual Pipeline / State Machine | Backend Implementation |
| :--- | :--- | :--- |
| **Model Type** | 11-step sequential progression pipeline | Binary/Terminal outcome mapping (`DISCOVERED` → `REJECTED` or `VALIDATED`) |
| **Persistence** | Assumed persistent storage per stage | Transient execution phase; only initial and final states are stored in `strategy_registry` |
| **Telemetry** | UI counts rows per lifecycle state | `research_gates` tracks gate execution (`QUEUED` → `RUNNING` → `PASSED`/`FAILED`) |

---

## 4. Answers to the 14 Required Root-Cause Questions

### 1. Why were 55 strategies visible in DISCOVERED?
*In the active database audit (`artifacts/audit.db`), there are actually **72** DISCOVERED strategies (the figure 55 reflected an earlier dataset sampling snapshot). These represent raw generated hypotheses/seeds (such as `STRAT-ICHIMILI-FINAL` and unvalidated discovery items) that have not yet undergone full pipeline validation or remain in initial discovery state.*

### 2. Why were 0 shown in BACKTESTING?
*The `BACKTESTING` state is a transient execution phase inside `ResearchPipeline.validate_candidate()`. When backtesting completes, the pipeline immediately proceeds to walk-forward, OOS, and robustness tests before committing the final score. It never persists a strategy row with `lifecycle = 'BACKTESTING'`.*

### 3. Why were 0 shown in VALIDATING?
*Similarly, `VALIDATING` represents the walk-forward split validation phase. Because the pipeline executes all gates in one uninterrupted invocation and immediately assigns a terminal verdict (`REJECTED` or `VALIDATED`), no candidate remains parked in `VALIDATING`.*

### 4. Why were 0 shown in OOS TESTING?
*OOS testing is executed as Gate 4 of the pipeline. Successful or failed OOS metrics are stored in `research_gates` and `strategy_registry.oos`, but the candidate lifecycle is updated directly to `REJECTED` upon failure or `VALIDATED` upon success.*

### 5. Why were 0 shown in ROBUSTNESS TESTING?
*Robustness testing is Gate 5. Like OOS, it is a synchronous validation step in `pipeline.py` rather than a persistent resting lifecycle state.*

### 6. Why were 0 shown in VALIDATED?
*Zero candidates out of 1,379 validation runs satisfied all strict evaluation gates simultaneously (specifically, walk-forward degradation thresholds and OOS expectancy floors). Therefore, no strategy ever achieved a `VALIDATED` verdict.*

### 7. Why were 445 shown in REJECTED?
*In the current production database, **1,093** strategies are persisted with `lifecycle = 'REJECTED'` (the figure 445 reflected an earlier checkpoint). This massive rejection count reflects the strictness of the research gates (especially walk-forward stability and out-of-sample positive expectancy requirements).*

### 8. Were the middle stages real lifecycle states or evaluation-stage concepts?
*They are **hybrid concepts**: formally defined states in `CandidateLifecycle` and spatial zones in `SpatialLayout`, but implemented in the backend as **transient evaluation phases** rather than persistent resting states.*

### 9. Did successful evaluation results propagate into CandidateLifecycle?
*Yes, evaluation metrics (BacktestResult, WalkForwardResult, OOSResult, RobustnessResult, StrategyScore) successfully propagate into the `strategy_registry` entry columns and `research_runs` / `research_gates` tables. However, the `lifecycle` column only receives `DISCOVERED`, `REJECTED`, or `VALIDATED`.*

### 10. Did the UI incorrectly assume evaluation stages were persistent lifecycle states?
*Yes. The Command Center UI (`command_center.html`), spatial canvas (`command_center_spatial.js`), and route overview (`command_center_routes.py`) query `strategy_registry.lifecycle` expecting strategies to occupy middle pipeline states, leading to zeroes across all intermediate zones.*

### 11. Did any candidate actually satisfy all gates?
*No. Out of 1,379 recorded research runs in `audit.db`, zero runs achieved `PASSED` across Backtest, Walk-Forward, OOS, and Robustness simultaneously.*

### 12. If yes, why did it not reach VALIDATED?
*N/A (none satisfied all gates).*

### 13. If no, is the rejection rate legitimate?
*Yes. The 100% rejection rate is mathematically legitimate under current parameter bounds: walk-forward split validation (`walkforward.py`) and out-of-sample testing (`oos.py`) enforce stringent anti-overfitting constraints that correctly filter out unrobust generated strategies.*

### 14. Is any counter stale or incorrectly scoped?
*Yes. UI overview counters and spatial node distribution aggregate `strategy_registry.lifecycle` directly. Because intermediate lifecycle states are never written to the registry, these counters report 0 for all middle stages.*

---

## 5. Recommended Repair Roadmap

1. **Projection Layer Mapping:** Update `CommandCenterAPI.overview()` and `SpatialLayout.compute()` to optionally derive intermediate pipeline status from active `research_gates` / `research_runs` (e.g., showing candidates currently executing or last tested in a given gate) rather than solely relying on `strategy_registry.lifecycle`.
2. **Progressive Persistence (Optional):** If persistent middle states are desired in the UI, modify `ResearchPipeline` to update `strategy_registry.lifecycle` asynchronously as each gate completes (e.g., transitioning `DISCOVERED` → `BACKTESTING` → `VALIDATING` → `OOS_TESTING` → `ROBUSTNESS_TESTING` → `VALIDATED`/`REJECTED`).
3. **Strategy Generator Calibration:** Review strategy generator parameters (`NexusTradingForexBot/src/nexus_scalp/strategies/factory/`) to improve initial candidate quality so that at least a subset of generated strategies can clear walk-forward and OOS gates.

---
*Signed,*  
*Forensic Lifecycle Investigator (Agent 1)*
