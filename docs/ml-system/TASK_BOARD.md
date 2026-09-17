# NSE ML System Master Task Board

> **Definitive Operational Task Board for the Nexus Scalp Engine ML Subsystem**
> Grounded in Forensic Source Code Evidence at HEAD `c6e7bffe`.

---

## Canonical Documentation Suite

| Document | Description | Status |
|---|---|---|
| [`01_SYSTEM_CONTRACT.md`](./01_SYSTEM_CONTRACT.md) | Single architectural source of truth, 12-gate system, 3 state machines | **COMPLETE & VERIFIED** |
| [`02_DATA_CONTRACT.md`](./02_DATA_CONTRACT.md) | Feature schemas (50D/70D), normalization, dataset geometry, purge/embargo | **COMPLETE & VERIFIED** |
| [`03_MODEL_ARCHITECTURE.md`](./03_MODEL_ARCHITECTURE.md) | ScalpNet 2D/3D dual path, 3-class contract, model vs strategy hierarchy | **COMPLETE & VERIFIED** |
| [`04_TRAINING_VALIDATION_BENCHMARK.md`](./04_TRAINING_VALIDATION_BENCHMARK.md) | WalkForwardTrainer, candidate trainers, OOS economic expectancy, robustness | **COMPLETE & VERIFIED** |
| [`05_INFERENCE_FORWARDTEST_GOVERNANCE.md`](./05_INFERENCE_FORWARDTEST_GOVERNANCE.md) | Live inference pipeline, policy gates, shadow mode, promotion API, freeze lifecycle | **COMPLETE & VERIFIED** |
| [`06_TASK_LEDGER.md`](./06_TASK_LEDGER.md) | Master task inventory, dependency graph, execution phases, and risk analysis | **COMPLETE & VERIFIED** |

---

## Active Task Board

### Phase A: Ready for Immediate Execution (No Blockers)
- [ ] **[TASK-001](./tasks/TASK-001.md):** 50D Feature Normalization Parity Verification `[P0]`
- [ ] **[TASK-002](./tasks/TASK-002.md):** Deterministic Market Data Ingest & Dataset Pipeline Harness `[P0]`
- [ ] **[TASK-011](./tasks/TASK-011.md):** Live Shadow Outcome Real-Time Resolution & Holding Metrics `[P2]`
- [ ] **[TASK-014](./tasks/TASK-014.md):** MT5 Runtime Boundaries & Remote Gateway Linux Parity `[P2]`

### Phase B: Human Architectural & Product Decisions Required
- [ ] **[TASK-003](./tasks/TASK-003.md):** Strict 3-Class Contract Enforcement & Legacy 4-Logit WAIT Sunset `[P0]` `[HUMAN DECISION]`
- [ ] **[TASK-006](./tasks/TASK-006.md):** Single Source of Truth Model State Machine Unification `[P1]` `[HUMAN DECISION]`
- [ ] **[TASK-009](./tasks/TASK-009.md):** 2D Snapshot vs 3D Sequence Routing Benchmark & Decision `[P2]` `[HUMAN DECISION]`
- [ ] **[TASK-010](./tasks/TASK-010.md):** 50D vs 70D Production Feasibility Audit & Migration Roadmap `[P2]` `[HUMAN DECISION]`
- [ ] **[TASK-012](./tasks/TASK-012.md):** Online Fine-Tuning Safe Sandbox & Degradation Circuit Breakers `[P3]` `[HUMAN DECISION]`

### Phase C: Pipeline & Governance Hardening (Unblocked after Phase A & B)
- [ ] **[TASK-004](./tasks/TASK-004.md):** Model Promotion Pipeline Pre-Flight & OOS Economic Gate Verification `[P0]` *(Executable after TASK-001)*
- [ ] **[TASK-005](./tasks/TASK-005.md):** Purge & Embargo Boundary Audit & Monotonicity Verification `[P1]` *(Executable after TASK-002)*
- [ ] **[TASK-008](./tasks/TASK-008.md):** Persistent Governance Emergency Freeze Across Process Restarts `[P1]` *(Executable after TASK-006)*

### Phase D: Distribution & Continuous Integration Gates
- [ ] **[TASK-007](./tasks/TASK-007.md):** Signed Official Model Bundle Verification & Staging Slot Isolation `[P1]` *(Executable after TASK-004)*
- [ ] **[TASK-013](./tasks/TASK-013.md):** CI Model Training Smoke vs Real Validation Gap & Benchmark Harness `[P2]` *(Executable after TASK-002, TASK-005)*
- [ ] **[TASK-015](./tasks/TASK-015.md):** ML Contract & Canonical Documentation Drift Prevention CI Gate `[P3]` *(Executable after TASK-003, TASK-007)*

---

## Human Approval Boundaries Summary

1. **TASK-003 (3-Class Contract):** Formalize complete removal of legacy 4-logit WAIT support vs maintaining backwards compatibility shims.
2. **TASK-006 (State Machine Authority):** Designate whether `PromotionState` (governance) or `ModelStatus` (lifecycle) is the single canonical source of model truth.
3. **TASK-009 (Sequence Path):** Confirm whether 3D sequence modeling (TCN+Attention) should be retained as active research or deprecated in favor of 2D MLP ResNet.
4. **TASK-010 (70D Migration):** Decide timeline and criteria for migrating live trading from 50D base features to 70D (Base + News + Liquidity).
5. **TASK-012 (Online Learning):** Decide under what risk controls and operator supervision online fine-tuning should be enabled in live environments.
