# NSE ML System Task Board

> **Master Planning Board for Machine Learning Subsystem Hardening**
> Grounded in Forensic Audit at HEAD `d9a3a829`.

---

## Canonical Documentation Suite

| Document | Purpose | Status |
|---|---|---|
| [`01_SYSTEM_CONTRACT.md`](./01_SYSTEM_CONTRACT.md) | Single architectural source of truth for ML subsystem | **COMPLETE & VERIFIED** |
| [`02_DATA_CONTRACT.md`](./02_DATA_CONTRACT.md) | Feature schemas (50D/70D), normalization, dataset geometry, purge/embargo | **COMPLETE & VERIFIED** |
| [`03_MODEL_ARCHITECTURE.md`](./03_MODEL_ARCHITECTURE.md) | ScalpNet layer-by-layer breakdown, 2D/3D routing, 3-class contract, model vs strategy | **COMPLETE & VERIFIED** |
| [`04_TRAINING_VALIDATION_BENCHMARK.md`](./04_TRAINING_VALIDATION_BENCHMARK.md) | WalkForwardTrainer, CandidateTrainer, 12 Gates, OOS gate, robustness stress | **COMPLETE & VERIFIED** |
| [`05_INFERENCE_FORWARDTEST_GOVERNANCE.md`](./05_INFERENCE_FORWARDTEST_GOVERNANCE.md) | Live inference pipeline, policy gates, shadow mode, promotion API, freeze lifecycle | **COMPLETE & VERIFIED** |
| [`06_TASK_LEDGER.md`](./06_TASK_LEDGER.md) | Complete task inventory, status, dependencies, and execution tracks | **COMPLETE & VERIFIED** |

---

## Active Task Board

### P0 — Critical Pre-Flight (Immediate Action)
- [ ] **[TASK-001](./tasks/TASK-001.md):** 50D Normalization End-to-End Parity Verification
- [ ] **[TASK-002](./tasks/TASK-002.md):** Market Data Ingest & Dataset Generation Pipeline
- [ ] **[TASK-003](./tasks/TASK-003.md):** Strict 3-Class Contract Enforcement & Legacy 4-Logit Deprecation `[HUMAN DECISION]`
- [ ] **[TASK-004](./tasks/TASK-004.md):** Model Promotion & OOS Economic Expectancy Gate Audit

### P1 — High Operational Integrity
- [ ] **[TASK-005](./tasks/TASK-005.md):** Purge & Embargo Boundary Audit & Verification Tests
- [ ] **[TASK-006](./tasks/TASK-006.md):** Unified Model State Machine Root `[HUMAN DECISION]`
- [ ] **[TASK-007](./tasks/TASK-007.md):** Model Artifacts Inventory & Signed Bundle Distribution
- [ ] **[TASK-008](./tasks/TASK-008.md):** Governance Freeze Persistence Across Process Restarts

### P2 — Research & Observability Expansion
- [ ] **[TASK-009](./tasks/TASK-009.md):** 2D Snapshot vs 3D Sequence Routing Verification & Benchmark `[HUMAN DECISION]`
- [ ] **[TASK-010](./tasks/TASK-010.md):** 50D vs 70D Architectural Reconciliation & Migration Roadmap `[HUMAN DECISION]`
- [ ] **[TASK-011](./tasks/TASK-011.md):** Live Shadow Outcome Real-Time Resolution Wiring

### P3 — Safety Polish & Controlled Learning
- [ ] **[TASK-012](./tasks/TASK-012.md):** Online Fine-Tuning Safe Sandbox & Guardrail Hardening `[HUMAN DECISION]`

---

## Human Approval Boundaries Summary

1. **TASK-003 (3-Class Contract):** Formalize complete removal of legacy 4-logit WAIT support vs maintaining backwards compatibility shims.
2. **TASK-006 (State Machine Authority):** Select whether `ModelStatus` (lifecycle) or `PromotionState` (governance) is the single canonical source of model truth.
3. **TASK-009 (Sequence Path):** Confirm whether 3D sequence modeling (TCN+Attention) should be retained as active research or deprecated in favor of 2D MLP ResNet.
4. **TASK-010 (70D Migration):** Decide timeline and criteria for migrating live trading from 50D base features to 70D (Base + News + Liquidity).
5. **TASK-012 (Online Learning):** Decide under what risk controls and operator supervision online fine-tuning should be enabled in live environments.
