# NSE ML System Master Task Board (v2 — Swarm Architecture)

> **Definitive Operational Task Board for the Nexus Scalp Engine Quant ML Subsystem**
> Designed for Autonomous Multi-Agent Swarm Parallel Execution at HEAD `c6e7bffe`.

---

## 1. Canonical Documents
- [01_SYSTEM_CONTRACT.md](01_SYSTEM_CONTRACT.md) — 12 Promotion Gates, 3 State Machines, Dual-Path Ingestion Contract
- [02_DATA_CONTRACT.md](02_DATA_CONTRACT.md) — 50D Microstructure Schema, Triple-Barrier Geometry, Purge/Embargo Boundaries
- [03_MODEL_ARCHITECTURE.md](03_MODEL_ARCHITECTURE.md) — ScalpNet Tensor Invariants, TCN Receptive Field, 3-Class Head
- [04_TRAINING_VALIDATION_BENCHMARK.md](04_TRAINING_VALIDATION_BENCHMARK.md) — Walk-Forward Protocol, Purged Folds, Benchmark Metrics
- [05_INFERENCE_FORWARDTEST_GOVERNANCE.md](05_INFERENCE_FORWARDTEST_GOVERNANCE.md) — Live Tick Forward Pass, Shadow Outcomes, Promotion Pipeline
- [06_TASK_LEDGER.md](06_TASK_LEDGER.md) — Master Swarm Backlog (30 Tasks), Dependency DAG, Wave Scheduling & Collision Analysis

---

## 2. Active Swarm Backlog (30 Tasks Across 12 Streams)

### STREAM A: DATA FOUNDATION
- [ML-DATA-001](tasks/ML-DATA-001.md) — Historical M1 Market Data Ingest & Parquet Storage Harness `[P0 | DONE | AGENT-DATA]`
- [ML-DATA-002](tasks/ML-DATA-002.md) — Dataset Integrity, Missing Value Sanitization & Manifest Hashing `[P1 | DONE | AGENT-DATA]`

### STREAM B: FEATURE ENGINEERING
- [ML-FEAT-001](tasks/ML-FEAT-001.md) — 50D Feature Normalization End-to-End Parity Verification `[P0 | DONE | AGENT-FEATURE]`
- [ML-FEAT-002](tasks/ML-FEAT-002.md) — Feature Importance, Collinearity Clustering & Redundancy Pruning `[P2 | DONE | AGENT-FEATURE]`
- [ML-FEAT-003](tasks/ML-FEAT-003.md) — 70D Feature Feasibility, Feed Uptime & Gating Criteria `[P2 | BLOCKED | AGENT-FEATURE | HUMAN DECISION]`

### STREAM C: LABELING
- [ML-LABEL-001](tasks/ML-LABEL-001.md) — Friction-Aware Triple Barrier Horizon & ATR Multiplier Calibration `[P1 | DONE | AGENT-LABEL]`
- [ML-LABEL-002](tasks/ML-LABEL-002.md) — Sample Uniqueness Weighting & Label Overlap Anti-Leakage `[P1 | DONE | AGENT-LABEL]`

### STREAM D: MODEL ARCHITECTURE
- [ML-ARCH-001](tasks/ML-ARCH-001.md) — ScalpNet Dual-Path Tensor Contract & 3-Class Head Sunset `[P0 | EVIDENCE-COMPLETE | AGENT-ML-ARCH | HUMAN DECISION]`
- [ML-ARCH-002](tasks/ML-ARCH-002.md) — Causal TCN Dilation & Receptive Field Optimization `[P2 | DONE 2026-09-22 | AGENT-ML-ARCH]`
- [ML-ARCH-003](tasks/ML-ARCH-003.md) — Temporal Multihead Attention vs Positional Encoding Ablation `[P2 | DONE 2026-09-22 | AGENT-ML-ARCH]`

### STREAM E: TRAINING ENGINE & REGULARIZATION
- [ML-TRAIN-001](tasks/ML-TRAIN-001.md) — Deterministic Training Engine, Seed Harness & AMP Precision `[P1 | DONE | AGENT-ML-TRAIN]`
- [ML-TRAIN-002](tasks/ML-TRAIN-002.md) — Loss Function Exploration: Class-Weighted Focal Loss vs Label Smoothing `[P2 | DONE | AGENT-ML-TRAIN]`
- [ML-TRAIN-003](tasks/ML-TRAIN-003.md) — Optimizer & Learning Rate Schedule Exploration (AdamW + Cosine Restarts) `[P2 | DONE | AGENT-ML-TRAIN]`

### STREAM F: EXPERIMENTATION & ABLATION
- [ML-EXP-001](tasks/ML-EXP-001.md) — Immutable Experiment Registry & Artifact Manifest Schema `[P1 | DONE | AGENT-ML-EXP]`
- [ML-EXP-002](tasks/ML-EXP-002.md) — Architectural Ablation Harness: 2D MLP vs 3D TCN vs Attention `[P2 | BLOCKED | AGENT-ML-EXP | HUMAN DECISION]`
- [ML-EXP-003](tasks/ML-EXP-003.md) — Bounded Hyperparameter Grid Search Runner `[P3 | DONE | AGENT-ML-EXP]`

### STREAM G: VALIDATION, OOS & ROBUSTNESS
- [ML-VAL-001](tasks/ML-VAL-001.md) — Purged Walk-Forward Monotonicity & Embargo `[P1 | DONE | AGENT-ML-VALIDATION]`
- [ML-VAL-002](tasks/ML-VAL-002.md) — Probability Calibration & Expected Calibration Error (ECE) Evaluation `[P2 | DONE | AGENT-ML-VALIDATION]`
- [ML-VAL-003](tasks/ML-VAL-003.md) — Robustness Stress Testing: Friction Clamp, Slippage & Spread Perturbation `[P2 | DONE | AGENT-ML-VALIDATION]`

### STREAM H: REAL-TIME INFERENCE
- [ML-INF-001](tasks/ML-INF-001.md) — Inference Preprocessing & Scaler Latency SLA (< 10ms) Verification `[P1 | DONE | AGENT-INFERENCE]`

### STREAM I: TRADING INTEGRATION & BACKTESTING
- [ML-BT-001](tasks/ML-BT-001.md) — Trading Quality Metric Suite: Expectancy R, Profit Factor & Slippage Decay Curves `[P1 | DONE (PR #316) | AGENT-BACKTEST]`
- [ML-RISK-001](tasks/ML-RISK-001.md) — Position Replay & Economic Dataset Generation Engine `[P0 | DONE | AGENT-RISK]`

### STREAM J: MODEL GOVERNANCE & LIFECYCLE
- [ML-GOV-001](tasks/ML-GOV-001.md) — Promotion API Pre-Flight & OOS Gate Audit `[P0 | DONE | AGENT-GOVERNANCE]`
- [ML-GOV-002](tasks/ML-GOV-002.md) — Single Source of Truth Model State Machine Unification `[P1 | READY | AGENT-GOVERNANCE | HUMAN DECISION]`
- [ML-GOV-003](tasks/ML-GOV-003.md) — Persistent Governance Emergency Freeze Across Process Restarts `[P1 | BLOCKED | AGENT-GOVERNANCE]`

### STREAM K: PLATFORM & DISTRIBUTION
- [ML-PLAT-001](tasks/ML-PLAT-001.md) — MT5 Runtime Boundaries & Remote Gateway Linux Parity `[P2 | DONE 2026-09-20 | AGENT-PLATFORM]` (PR #322: 49/49 parity tests; zero production diff — NON_GOALS honored)
- [ML-PLAT-002](tasks/ML-PLAT-002.md) — Signed Official Model Bundle Verification & Staging Slot Isolation `[P1 | DONE | AGENT-PLATFORM]`

### STREAM L: OBSERVABILITY, SAFETY & CI/CD
- [ML-OBS-001](tasks/ML-OBS-001.md) — Live Shadow Outcome Real-Time Resolution & Holding Metrics Wiring `[P2 | DONE (PR pending) | AGENT-OBSERVABILITY]`
- [ML-OBS-002](tasks/ML-OBS-002.md) — Online Fine-Tuning Safe Sandbox, Quarantine Buffer & Circuit Breakers `[P3 | BLOCKED | AGENT-OBSERVABILITY | HUMAN DECISION]`
- [ML-CI-001](tasks/ML-CI-001.md) — CI Model Training Smoke vs Real Validation Gap `[P2 | DONE (PR pending) | AGENT-QA]`
- [ML-CI-002](tasks/ML-CI-002.md) — ML Contract & Canonical Documentation Drift Prevention CI Gate `[P3 | DONE (PR #346, squash 6bb1ffbe) | AGENT-QA]`
- [ML-QA-003](tasks/ML-QA-003.md) — Test Determinism Census & Push-Gate Exposure Roster `[P2 | DONE (PR #402) | AGENT-QA]`
- [ML-QA-004](tasks/ML-QA-004.md) — Push-Gate Timing Determinism Remediation (injected clock + CPU-time budgets) `[P2 | DONE (PR #402) | AGENT-QA]`
- [ML-QA-005](tasks/ML-QA-005.md) — Merge-Marker Residue Guard (diff3 arm leak on SSOT metadata) `[P2 | DONE (PR #405) | AGENT-QA]`
- [ML-QA-006](tasks/ML-QA-006.md) — Duplicate Canonical Task-Row Detector (SSOT table gate) `[P2 | DONE (PR pending) | AGENT-QA]`
- [ML-QA-007](tasks/ML-QA-007.md) — Push-Gate Latency Determinism: MT5 Parity Suite (roster candidate #4, CPU-time probes + warmup + SLA re-attached) `[P2 | DONE (PR pending) | AGENT-QA]`
- [ML-QA-008](tasks/ML-QA-008.md) — Push-Gate Latency Determinism: Experiment-Registry Benchmark (roster candidate #6, CPU-time legs + warmup, 50ms query budget kept hard) `[P2 | DONE (PR pending) | AGENT-QA]`
- [ML-QA-009](tasks/ML-QA-009.md) — Push-Gate Bounded-Wait Determinism: Audit-Flush (roster candidate #7, CPU-time budget, no-deadlock contract kept hard) `[P2 | DONE (PR #422 open) | AGENT-QA]`
- [ML-QA-010](tasks/ML-QA-010.md) — Push-Gate Wall-Clock Determinism: BUG-262 Close-Time Evidence (roster recount #1, injected fixed clock + tmp_path, 15 sources removed) `[P2 | DONE (PR #432, squash 6a7e2e7e) | AGENT-QA]`
- [ML-QA-011](tasks/ML-QA-011.md) — Push-Gate Wall-Clock Determinism: Shadow70 Safety Suite (roster recount #2, one frozen instant + tmp_path + CPU-budget flush wait, spec 13/14 idempotency now provable) `[P2 | DONE (PR #457, squash 33205de2) | AGENT-QA]`
- [ML-QA-012](tasks/ML-QA-012.md) — Push-Gate Wall-Clock Determinism: BUG-140 Outcome-Flush Race Suite (roster candidate #3, one frozen instant + CPU-time poll bounds, read drift removed) `[P2 | DONE (PR #466, squash 2b707636) | AGENT-QA]`
- [ML-QA-013](tasks/ML-QA-013.md) — Push-Gate Monotonic-Clock Determinism: BUG-285 Overflow-Drain Cadence Suite (roster candidate #4, optional-argument `now` seam, both strict-`<` boundary edges provable to the nanosecond) `[P2 | DONE (PR pending) | AGENT-QA]`
- [ML-UI-001](tasks/ML-UI-001.md) — End-to-End Model UX & CLI Verification (Training, Predict & Confidence Trust) `[P1 | DONE | AGENT-UI]`
- [ML-UI-002](tasks/ML-UI-002.md) — Model Studio E2E Dataset Ingestion, 50D/70D Normalization & Layer-2 Position Management Generator `[P1 | DONE | AGENT-UI]`
- [ML-UI-003](tasks/ML-UI-003.md) — AI Hub: Model Registry, SQLite Catalog & Live Runtime Hot-Loader `[P1 | DONE | AGENT-UI]`

---

## 3. Human Decision Gates Matrix

The following tasks are strictly frozen from implementation until the human operator signs off on the corresponding architectural decision:

1. **[ML-ARCH-001](tasks/ML-ARCH-001.md)**: Approve formal sunset and retirement of legacy 4-logit output (`WAIT` logit) across all future training runs and models. **Evidence complete (2026-09-22):** the audit this task required is done and automated — `scripts/audit/audit_legacy4_surface.py` (torch-free, 12 tests) proves zero committed 4-wide artifacts and zero `num_classes=4` on the serving path, and `agents/decisions/DEC-0010-scalpnet-3class-head-sunset.md` recommends **Option B** (strict sunset + legacy adapter). Needs an operator A/B/C selection to close AC-3.
2. **[ML-GOV-002](tasks/ML-GOV-002.md)**: Designate the single canonical root model state machine: `PromotionState` (`governance.db`) vs `ModelStatus` (`lifecycle.db`).
3. **[ML-EXP-002](tasks/ML-EXP-002.md)**: Empirical comparison decision: Retain and wire 3D TCN+Attention sequence buffer in live execution or officially retire 3D sequence modeling to favor 2D MLP ResNet.
4. **[ML-FEAT-003](tasks/ML-FEAT-003.md)**: Approve live production gating criteria and infrastructure requirements for 70D schema adoption.
5. **[ML-OBS-002](tasks/ML-OBS-002.md)**: Authorize activation of online fine-tuning on live broker demo/paper accounts under quarantined shadow buffers.
