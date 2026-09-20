# 06 — ML System Task Ledger & Master Roadmap (v2 — Swarm Architecture)

> **Status:** Definitive Quant ML / Deep Learning Engineering Backlog at HEAD `c6e7bffe`.
> Replaces the previous governance-heavy 15-task skeleton with a 30-task, multi-stream, swarm-safe Deep Learning engineering plan.
> Grounded in source code evidence across 12 logical ML streams.

---

## 1. Historical Migration Map (Old 15 Tasks → New 30 Tasks)

Every task from the legacy governance/smoke backlog has been preserved and mapped to its exact domain stream, while missing Deep Learning capabilities have been added:

| Legacy ID | Legacy Title | New Task ID | New Stream | Classification | Value |
|---|---|---|---|---|---|
| `TASK-001` | 50D Normalization Parity Verification | `ML-FEAT-001` | STREAM B (Features) | VALIDATION_RESEARCH | HIGH |
| `TASK-002` | Market Data Ingest Pipeline | `ML-DATA-001` | STREAM A (Data) | DATA_ENGINEERING | HIGH |
| `TASK-003` | 3-Class Contract & 4-Logit Sunset | `ML-ARCH-001` | STREAM D (Architecture) | CORE_ML / ARCH | HIGH |
| `TASK-004` | Model Promotion Pre-Flight & OOS Gate | `ML-GOV-001` | STREAM J (Governance) | GOVERNANCE | MEDIUM |
| `TASK-005` | Purge & Embargo Boundary Audit | `ML-VAL-001` | STREAM G (Validation) | VALIDATION_RESEARCH | HIGH |
| `TASK-006` | Unified State Machine Root | `ML-GOV-002` | STREAM J (Governance) | GOVERNANCE | MEDIUM |
| `TASK-007` | Signed Model Bundle Verification | `ML-PLAT-002` | STREAM K (Platform) | PLATFORM / SECURITY | MEDIUM |
| `TASK-008` | Persistent Governance Freeze | `ML-GOV-003` | STREAM J (Governance) | GOVERNANCE | MEDIUM |
| `TASK-009` | 2D vs 3D Sequence Benchmark | `ML-EXP-002` | STREAM F (Experimentation) | DEEP_LEARNING | HIGH |
| `TASK-010` | 50D vs 70D Feasibility Roadmap | `ML-FEAT-003` | STREAM B (Features) | TRADING_INTEGRATION | MEDIUM |
| `TASK-011` | Live Shadow Outcome Real-Time Resolution | `ML-OBS-001` | STREAM L (Observability) | OBSERVABILITY | LOW |
| `TASK-012` | Online Fine-Tuning Safe Sandbox | `ML-OBS-002` | STREAM L (Observability) | RUNTIME_SAFETY | LOW |
| `TASK-013` | CI Model Training Validation Gap | `ML-CI-001` | STREAM L (CI/CD) | PLATFORM / CI | MEDIUM |
| `TASK-014` | MT5 Runtime Boundaries & Linux Parity | `ML-PLAT-001` | STREAM K (Platform) | PLATFORM | LOW |
| `TASK-015` | ML Contract Drift Prevention CI Gate | `ML-CI-002` | STREAM L (CI/CD) | GOVERNANCE / CI | MEDIUM |
| *NEW* | Dataset Integrity, Missing Values & Hashing | `ML-DATA-002` | STREAM A (Data) | DATA_ENGINEERING | HIGH |
| *NEW* | Feature Importance & Collinearity Clustering | `ML-FEAT-002` | STREAM B (Features) | CORE_ML / FEATURES | HIGH |
| *NEW* | Triple Barrier Horizon & ATR Calibration | `ML-LABEL-001` | STREAM C (Labeling) | QUANT_RESEARCH | HIGH |
| *NEW* | Sample Uniqueness & Overlap Anti-Leakage | `ML-LABEL-002` | STREAM C (Labeling) | QUANT_RESEARCH | HIGH |
| *NEW* | Causal TCN Dilation & Receptive Field | `ML-ARCH-002` | STREAM D (Architecture) | DEEP_LEARNING | HIGH |
| *NEW* | Temporal Attention vs Positional Encoding | `ML-ARCH-003` | STREAM D (Architecture) | DEEP_LEARNING | HIGH |
| *NEW* | Deterministic Training, Seeds & AMP Precision | `ML-TRAIN-001` | STREAM E (Training) | DEEP_LEARNING | HIGH |
| *NEW* | Loss Functions: Focal Loss vs Label Smoothing | `ML-TRAIN-002` | STREAM E (Training) | DEEP_LEARNING | HIGH |
| *NEW* | Optimizers & Schedulers (AdamW + Cosine) | `ML-TRAIN-003` | STREAM E (Training) | DEEP_LEARNING | HIGH |
| *NEW* | Immutable Experiment Registry Schema | `ML-EXP-001` | STREAM F (Experimentation) | ML_ENGINEERING | HIGH |
| *NEW* | Bounded Hyperparameter Grid Search Runner | `ML-EXP-003` | STREAM F (Experimentation) | DEEP_LEARNING | MEDIUM |
| *NEW* | Probability Calibration & ECE Evaluation | `ML-VAL-002` | STREAM G (Validation) | QUANT_RESEARCH | HIGH |
| *NEW* | Robustness Stress: Slippage & Spread Perturb | `ML-VAL-003` | STREAM G (Validation) | QUANT_RESEARCH | HIGH |
| *NEW* | Inference Preprocessing Latency SLA (<10ms) | `ML-INF-001` | STREAM H (Inference) | INFERENCE | HIGH |
| *NEW* | Trading Quality Metrics vs Classification | `ML-BT-001` | STREAM I (Backtest) | QUANT_RESEARCH | HIGH |

---

## 2. Master Backlog Table (30 Tasks)

| Task ID | Stream | Priority | Title | Status | Agent Role | Human Decision | Dependencies | Parallel Class |
|---|---|---|---|---|---|---|---|---|
| `ML-DATA-001` | A: Data | P0 | Historical M1 Market Data Ingest & Parquet Storage | **DONE** (PR #259, `d52bcde6`) | `AGENT-DATA` | NO | None | `PARALLEL_SAFE` |
| `ML-DATA-002` | A: Data | P1 | Dataset Integrity, Sanitization & Hashing | **DONE** | `AGENT-DATA` | NO | `ML-DATA-001` | `PARALLEL_SAFE` |
| `ML-FEAT-001` | B: Features | P0 | 50D Normalization End-to-End Parity | **DONE** (PR #254, `bd61deb5`) | `AGENT-FEATURE` | NO | None | `PARALLEL_SAFE` |
| `ML-FEAT-002` | B: Features | P2 | Feature Importance & Collinearity Clustering | **BLOCKED** | `AGENT-FEATURE` | NO | `ML-DATA-001`, `ML-FEAT-001` | `PARALLEL_SAFE` |
| `ML-FEAT-003` | B: Features | P2 | 70D Feasibility, Feed Uptime & Gating | **BLOCKED** | `AGENT-FEATURE` | **YES** | `ML-FEAT-001`, `ML-DATA-001` | `SERIAL_ONLY` |
| `ML-LABEL-001` | C: Labeling | P1 | Friction-Aware Triple Barrier Horizon & ATR | **DONE** | `AGENT-LABEL` | NO | `ML-DATA-001` | `PARALLEL_SAFE` |
| `ML-LABEL-002` | C: Labeling | P1 | Sample Uniqueness & Overlap Anti-Leakage | **DONE** | `AGENT-LABEL` | NO | `ML-LABEL-001` | `PARALLEL_SAFE` |
| `ML-ARCH-001` | D: Architecture | P0 | ScalpNet Dual-Path & 3-Class Head Sunset | **READY** | `AGENT-ML-ARCH` | **YES** | None | `SERIAL_ONLY` |
| `ML-ARCH-002` | D: Architecture | P2 | Causal TCN Dilation & Receptive Field | **BLOCKED** | `AGENT-ML-ARCH` | NO | `ML-ARCH-001`, `ML-DATA-001` | `PARALLEL_SAFE` |
| `ML-ARCH-003` | D: Architecture | P2 | Temporal Attention vs Positional Encoding | **BLOCKED** | `AGENT-ML-ARCH` | NO | `ML-ARCH-002` | `PARALLEL_SAFE` |
| `ML-TRAIN-001` | E: Training | P1 | Deterministic Training, Seeds & AMP Precision | **BLOCKED** | `AGENT-ML-TRAIN` | NO | `ML-ARCH-001`, `ML-DATA-002` | `PARALLEL_SAFE` |
| `ML-TRAIN-002` | E: Training | P2 | Loss Functions: Focal Loss vs Label Smoothing | **BLOCKED** | `AGENT-ML-TRAIN` | NO | `ML-TRAIN-001`, `ML-LABEL-002` | `PARALLEL_SAFE` |
| `ML-TRAIN-003` | E: Training | P2 | Optimizers & Schedulers (AdamW + Cosine) | **BLOCKED** | `AGENT-ML-TRAIN` | NO | `ML-TRAIN-001` | `PARALLEL_SAFE` |
| `ML-EXP-001` | F: Experimentation | P1 | Immutable Experiment Registry Schema | **BLOCKED** | `AGENT-ML-EXP` | NO | `ML-DATA-002` | `PARALLEL_SAFE` |
| `ML-EXP-002` | F: Experimentation | P2 | Architectural Ablation: 2D vs 3D vs Attention | **BLOCKED** | `AGENT-ML-EXP` | **YES** | `ML-EXP-001`, `ML-ARCH-003`, `ML-TRAIN-003` | `SERIAL_ONLY` |
| `ML-EXP-003` | F: Experimentation | P3 | Bounded Hyperparameter Grid Search Runner | **BLOCKED** | `AGENT-ML-EXP` | NO | `ML-EXP-001`, `ML-TRAIN-003` | `PARALLEL_SAFE` |
| `ML-VAL-001` | G: Validation | P1 | Purged Walk-Forward Monotonicity & Embargo | **DONE** | `AGENT-ML-VALIDATION` | NO | `ML-DATA-001` | `PARALLEL_SAFE` |
| `ML-VAL-002` | G: Validation | P2 | Probability Calibration & ECE Evaluation | **BLOCKED** | `AGENT-ML-VALIDATION` | NO | `ML-EXP-002` | `PARALLEL_SAFE` |
| `ML-VAL-003` | G: Validation | P2 | Robustness Stress: Slippage & Spread Perturb | **DONE** | `AGENT-ML-VALIDATION` | NO | `ML-VAL-001` | `PARALLEL_SAFE` |
| `ML-INF-001` | H: Inference | P1 | Inference Preprocessing Latency SLA (<10ms) | **DONE** | `AGENT-INFERENCE` | NO | `ML-FEAT-001`, `ML-ARCH-001` | `PARALLEL_SAFE` |
| `ML-BT-001` | I: Backtest | P1 | Trading Quality Metrics vs Classification | **BLOCKED** | `AGENT-BACKTEST` | NO | `ML-VAL-001`, `ML-VAL-003` | `PARALLEL_SAFE` |
| `ML-RISK-001` | I: Risk & Position | P0 | Position Replay & Economic Dataset Generation Engine | **DONE** | `AGENT-RISK` | NO | `ML-DATA-001`, `ML-UI-003` | `PARALLEL_SAFE` |
| `ML-GOV-001` | J: Governance | P0 | Promotion Pre-Flight & OOS Gate Audit | **DONE** (PR #262, `266d3ae6`) | `AGENT-GOVERNANCE` | NO | `ML-FEAT-001` | `PARALLEL_SAFE` |
| `ML-GOV-002` | J: Governance | P1 | Unified Model State Machine Root | **READY** | `AGENT-GOVERNANCE` | **YES** | None | `SERIAL_ONLY` |
| `ML-GOV-003` | J: Governance | P1 | Persistent Governance Freeze Across Restarts | **BLOCKED** | `AGENT-GOVERNANCE` | NO | `ML-GOV-002` | `PARALLEL_SAFE` |
| `ML-PLAT-001` | K: Platform | P2 | MT5 Runtime Boundaries & Linux Parity | **READY** | `AGENT-PLATFORM` | NO | None | `PARALLEL_SAFE` |
| `ML-PLAT-002` | K: Platform | P1 | Signed Official Bundle Verification & Slots | **BLOCKED** | `AGENT-PLATFORM` | NO | `ML-GOV-001` | `PARALLEL_SAFE` |
| `ML-OBS-001` | L: Observability | P2 | Live Shadow Outcome Real-Time Resolution | **READY** | `AGENT-OBSERVABILITY` | NO | None | `PARALLEL_SAFE` |
| `ML-OBS-002` | L: Observability | P3 | Online Fine-Tuning Safe Sandbox | **BLOCKED** | `AGENT-OBSERVABILITY` | **YES** | `ML-GOV-001`, `ML-GOV-003` | `SERIAL_ONLY` |
| `ML-CI-001` | L: CI/CD | P2 | CI Model Training Validation Gap | **BLOCKED** | `AGENT-QA` | NO | `ML-DATA-001`, `ML-VAL-001` | `PARALLEL_SAFE` |
| `ML-CI-002` | L: CI/CD | P3 | ML Contract Drift Prevention CI Gate | **BLOCKED** | `AGENT-QA` | NO | `ML-ARCH-001`, `ML-PLAT-002` | `PARALLEL_SAFE` |
| `ML-UI-001` | L: UX & Integration | P1 | End-to-End Model UX & CLI Verification (Train/Predict/Trust) | **DONE** | `AGENT-UI` | NO | None | `PARALLEL_SAFE` |
| `ML-UI-002` | L: UX & Integration | P1 | Model Studio Dataset Ingestion, 50D/70D Normalization & Layer-2 Position Dataset Pipeline | **DONE** | `AGENT-UI` | NO | `ML-UI-001`, `ML-DATA-001` | `PARALLEL_SAFE` |
| `ML-UI-003` | L: UX & Integration | P1 | AI Hub: Model Registry, SQLite Catalog & Live Runtime Hot-Loader | **DONE** | `AGENT-UI` | NO | `ML-UI-001`, `ML-UI-002` | `PARALLEL_SAFE` |

---

## 3. Dependency DAG (Directed Acyclic Graph)

```
[DATA INGEST & CONTRACTS]
ML-DATA-001 (Market Ingest) ──┬──> ML-DATA-002 (Integrity & Hashing) ──┬──> ML-VAL-001 (Purge/Embargo) ──┬──> ML-VAL-003 (Robustness) ──┬──> ML-BT-001 (Trading Metrics)
                              │                                        │                                 │                               │
                              ├──> ML-LABEL-001 (Triple Barrier) ──────┼──> ML-LABEL-002 (Uniqueness) ───┤                               │
                              │                                        │                                 │                               │
                              └──> ML-FEAT-002 (Importance/Cluster)    └──> ML-EXP-001 (Registry) ───────┤                               │
                                                                                                         │                               │
[MODEL ARCHITECTURE & TRAINING]                                                                          │                               │
ML-ARCH-001 (3-Class Sunset) ─┬──> ML-ARCH-002 (TCN Dilation) ───> ML-ARCH-003 (Attention) ─────────────┼──> ML-EXP-002 (Ablation) ─────┤
                              │                                                                          │        (2D vs 3D)             │
                              └──> ML-TRAIN-001 (Deterministic) ─┬──> ML-TRAIN-002 (Focal Loss) ─────────┤                               │
                                                                 │                                       │                               │
                                                                 └──> ML-TRAIN-003 (AdamW / Schedulers) ─┴──> ML-EXP-003 (Hyperparams)   │
                                                                                                                                         │
[INFERENCE & RUNTIME]                                                                                                                    │
ML-FEAT-001 (Norm Parity) ────┬──> ML-INF-001 (Inference SLA)                                                                            │
                              │                                                                                                          │
                              └──> ML-GOV-001 (Promotion Gate) ──> ML-PLAT-002 (Signed Bundles) ─────────────────────────────────────────┴──> ML-CI-002 (Drift Gate)

[GOVERNANCE ROOT]
ML-GOV-002 (Unified State Machine) ──> ML-GOV-003 (Persistent Freeze) ──> ML-OBS-002 (Online FT Sandbox)

[SUPPORTING & OBSERVABILITY]
ML-PLAT-001 (MT5 Linux Parity) [INDEPENDENT]
ML-OBS-001 (Shadow Outcomes)   [INDEPENDENT]
ML-CI-001 (CI Training Gap)    [Depends on ML-DATA-001, ML-VAL-001]
```

---

## 4. Execution Waves (Parallel Swarm Scheduling)

### WAVE 0: Research & Parity Baselines (Current READY Tasks)
The following tasks have ZERO upstream dependencies and completely disjoint ownership scopes. They can execute in parallel:
- `ML-DATA-001` (Owner: `AGENT-DATA`)
- `ML-FEAT-001` (Owner: `AGENT-FEATURE`)
- `ML-ARCH-001` (Owner: `AGENT-ML-ARCH` — *requires Human Decision on 3-class*)
- `ML-GOV-002` (Owner: `AGENT-GOVERNANCE` — *requires Human Decision on Root State*)
- `ML-PLAT-001` (Owner: `AGENT-PLATFORM`)
- `ML-OBS-001` (Owner: `AGENT-OBSERVABILITY`)

### WAVE 1: Data Integrity, Labeling & Architecture Core
*Unblocks after Wave 0 completion:*
- `ML-DATA-002` (Owner: `AGENT-DATA`)
- `ML-LABEL-001` (Owner: `AGENT-LABEL`)
- `ML-ARCH-002` (Owner: `AGENT-ML-ARCH`)
- `ML-INF-001` (Owner: `AGENT-INFERENCE`)
- `ML-GOV-001` (Owner: `AGENT-GOVERNANCE`)
- `ML-GOV-003` (Owner: `AGENT-GOVERNANCE`)

### WAVE 2: Sample Uniqueness, Training Engine & Validation Foundation
*Unblocks after Wave 1 completion:*
- `ML-LABEL-002` (Owner: `AGENT-LABEL`)
- `ML-ARCH-003` (Owner: `AGENT-ML-ARCH`)
- `ML-TRAIN-001` (Owner: `AGENT-ML-TRAIN`)
- `ML-EXP-001` (Owner: `AGENT-ML-EXP`)
- `ML-VAL-001` (Owner: `AGENT-ML-VALIDATION`)
- `ML-PLAT-002` (Owner: `AGENT-PLATFORM`)

### WAVE 3: Loss Functions, Schedulers & Feature Pruning
*Unblocks after Wave 2 completion:*
- `ML-FEAT-002` (Owner: `AGENT-FEATURE`)
- `ML-TRAIN-002` (Owner: `AGENT-ML-TRAIN`)
- `ML-TRAIN-003` (Owner: `AGENT-ML-TRAIN`)
- `ML-VAL-003` (Owner: `AGENT-ML-VALIDATION`)
- `ML-CI-001` (Owner: `AGENT-QA`)

### WAVE 4: Deep Learning Experiments & Trading Benchmarks
*Unblocks after Wave 3 completion:*
- `ML-EXP-002` (Owner: `AGENT-ML-EXP` — *Architectural Decision*)
- `ML-EXP-003` (Owner: `AGENT-ML-EXP`)
- `ML-VAL-002` (Owner: `AGENT-ML-VALIDATION`)
- `ML-BT-001` (Owner: `AGENT-BACKTEST`)

### WAVE 5: Drift Prevention, Feasibility & Safe Online Sandbox
*Unblocks after Wave 4 completion:*
- `ML-FEAT-003` (Owner: `AGENT-FEATURE` — *Human Decision on 70D*)
- `ML-OBS-002` (Owner: `AGENT-OBSERVABILITY` — *Human Decision on Online FT*)
- `ML-CI-002` (Owner: `AGENT-QA`)

---

## 5. Swarm Ownership & Shared-File Collision Analysis

| Agent Role | Primary Module Scope | Contested Files | Collision Risk Mitigation Strategy |
|---|---|---|---|
| `AGENT-DATA` | `src/nexus_scalp/model_generation/dataset_factory.py`, `scripts/data/` | `dataset_factory.py` | Exclusive lock in `agents/locks.yaml`. Other agents use generated Parquet files as read-only. |
| `AGENT-FEATURE` | `src/nexus_scalp/features/` | `src/nexus_scalp/features/schema.py` | `schema.py` is frozen during feature importance analysis. All experiments read schema via read-only import. |
| `AGENT-LABEL` | `src/nexus_scalp/labeling/` | `triple_barrier.py` | Sole owner of labeling algorithms. Outputs label arrays to dataset files. |
| `AGENT-ML-ARCH` | `src/nexus_scalp/models/` | `src/nexus_scalp/models/scalp_net.py` | `scalp_net.py` can only be modified by `AGENT-ML-ARCH`. `AGENT-INFERENCE` consumes class interface without modifying weights. |
| `AGENT-ML-TRAIN` | `src/nexus_scalp/training/` | `src/nexus_scalp/training/walk_forward_trainer.py` | High collision file. `AGENT-ML-TRAIN` implements loss/optimizer hooks via modular injection (`loss_factory.py`), leaving trainer core intact. |
| `AGENT-ML-EXP` | `src/nexus_scalp/model_lab/`, `src/nexus_scalp/model_generation/benchmark.py` | `model_lab/registry.py` | Owns experiment tracking SQLite database. Reads models as black boxes. |
| `AGENT-ML-VALIDATION` | `src/nexus_scalp/research/` | `src/nexus_scalp/research/oos.py` | Read-only evaluation of checkpoints against OOS folds. Never modifies model code. |
| `AGENT-INFERENCE` | `src/nexus_scalp/application/live/inference.py` | `inference.py` | Exclusive owner of live forward pass latency and tensor checking. |
| `AGENT-BACKTEST` | `src/nexus_scalp/research/trading_metrics.py` | None | Independent new evaluation module. Consumes trade logs and fold predictions. |
| `AGENT-GOVERNANCE` | `src/nexus_scalp/governance/` | `governance/engine.py` | Exclusive owner of promotion transactions, state transitions, and emergency freeze. |
| `AGENT-PLATFORM` | `src/nexus_scalp/adapters/mt5/` | `remote_gateway_adapter.py` | Isolated to MT5 adapters and packaging scripts. |
| `AGENT-OBSERVABILITY`| `src/nexus_scalp/shadow/` | `shadow/outcomes.py` | Observability and shadow telemetry only. Zero trade execution authority. |
| `AGENT-QA` | `tests/`, `scripts/ci/` | `tests/critical_suite.txt` | Independent test authoring. Cannot implement features or fix bugs directly. |

---

## 6. Hard Swarm Execution Rules

1. **One Task, One Owner:** Every task has exactly one primary agent owner.
2. **One Logical Source File, One Implementing Agent:** If file $F$ is being edited by Agent $A$, Agent $B$ cannot edit $F$.
3. **Exclusive Worktree Isolation:** Every agent operates in its own isolated worktree (`/tmp/wt-<agent>-<task_id>`) on branch `agent/<agent_name>/<task_id>`. No agents share working directories.
4. **Locking via `agents/locks.yaml`:** Before touching any file in `src/`, an agent must register an exclusive path lock.
5. **No Opportunistic Cleanup or Drive-By Refactoring:** Only edit code strictly within the task `SCOPE`. Do not format or rename unrelated functions.
6. **Zero Architectural Fabrication:** Human decisions (`HUMAN_DECISION_REQUIRED: YES`) require explicit operator sign-off. If encountered, STOP and produce decision package.
7. **Empirical Evidence First:** No claims of "better", "improved", or "production-ready" without verifiable benchmark metrics and SHA-256 artifacts.
8. **Pre-Push Quality Gate:** Every PR branch must pass `ruff`, formatting checks, `mypy`, and the critical test suite before merging.
9. **GitReleaseGuardian Non-Implementing:** Release and CI bots coordinate branch synchronization and never write feature logic.
10. **Reversibility:** Every database schema migration or state transition must include a rollback mechanism.
