# LIFECYCLE PROPAGATION ARCHITECTURE & REPAIR SPECIFICATION

**Document ID:** `LIFECYCLE_PROPAGATION_ARCHITECTURE_V1`  
**Target Repository:** `C:\Users\Capsizer\source\repos\NexusTradingForexBot`  
**Branch:** `main`  
**Role:** Domain / Pipeline Repair Architect (Agent 2)  
**Date:** August 24, 2026  

---

## 1. Core Architectural Distinction

A major root cause identified in Phase 4 forensics was the conflation of two distinct conceptual models in the backend projection and UI:

1. **Persistent CandidateLifecycle** (`CandidateLifecycle` enum):
   - Represents the enduring, governance-controlled state of a strategy in the registry (`strategy_registry.lifecycle`).
   - States: `DISCOVERED`, `VALIDATED`, `SHADOW`, `ACTIVE`, `REJECTED`, `DEGRADED`, `RETIRED`.
   - Invariant: State transitions are strictly enforced by the state machine (`lifecycle.py`). Promotion to `ACTIVE` is strictly operator-gated. Rejections and validations are terminal/persistent truths.

2. **Transient EvaluationPipeline** (Evaluation / Research Runs & Gates):
   - Represents the execution progress and outcomes of quantitative evaluation stages (`research_runs`, `research_gates`).
   - Stages: `BACKTESTING`, `WALK_FORWARD`, `OOS_TESTING`, `ROBUSTNESS_TESTING`, `SCORING`.
   - Invariant: Research execution runs synchronously through the evaluation gate chain, recording fine-grained telemetry in `research_gates` and `research_evidence`, while committing final verdicts (`VALIDATED` or `REJECTED`) to the registry.

---

## 2. Gate Semantics & Legitimate Rejection Rate

- **Strict Gating:** Quantitative gates (specifically Walk-Forward split degradation thresholds and Out-of-Sample positive expectancy requirements) enforce anti-overfitting constraints.
- **Forensic Finding:** 100% of generated candidates failed rigorous walk-forward or OOS gating in the audit database (`audit.db`), yielding 0 `VALIDATED` and 1,093 `REJECTED` entries.
- **Repair Decision:** **Do NOT weaken gates.** Lowering gate standards to manufacture artificial validations would compromise production execution safety. Instead, evaluation progress and gate results (`research_gates`) are cleanly exposed via the API and overview projection.

---

## 3. Projection & API Enhancements (`CommandCenterAPI`)

To bridge the backend persistence model with frontend expectations without inventing fake persistent database states:
- `CommandCenterAPI.overview()` now returns both `by_lifecycle` (authoritative persistent states) and `evaluation_pipeline` (transient execution and pass/fail counts across Backtest, Walk-Forward, OOS, Robustness, and Scoring gates).
- `validation_pipeline(strategy_id)` provides per-candidate gate execution breakdown (`NOT_RUN`, `PASS`, `FAIL`).

---
*Signed,*  
*Domain / Pipeline Repair Architect (Agent 2)*
