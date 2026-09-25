# 01 — ML System Contract (Current State)

> **Status:** READ-ONLY forensic classification of NSE at HEAD `d9a3a829` (origin/main `f5ff4576`).
> Source code wins over documentation. Every classification carries an evidence label:
> **VERIFIED FROM SOURCE** / **NOT VERIFIED FROM EXECUTED DATA** /
> **NOT PROVEN FROM CURRENT REPOSITORY** / **CONTRADICTED BY SOURCE** /
> **DOCUMENTATION-ONLY**.

## 1. Purpose

The single, current-state architectural contract for the ML/AI subsystem. It answers:
"What exactly exists in the NSE ML system today, and where is each piece on the spectrum from production to dead?"
It does **not** answer: "What should we build?" — that lives in `06_TASK_LEDGER.md`.

## 2. Evidence Hierarchy (binding)

1. Executable source code
2. Tests that actually run (523 test files, 439 critical suite)
3. Runtime / configuration paths
4. Model / checkpoint metadata consumed by code
5. Datasets / manifests consumed by code
6. CI / workflows (.github/workflows)
7. Existing technical documentation
8. README / comments

If (1)-(6) and (7) disagree, **document the contradiction**; never silently override executable behavior.

## 3. Classification Taxonomy

| Tag | Meaning |
|---|---|
| **PRODUCTION** | Code on the verified live tick path with a reachable caller in shipped configuration |
| **PRODUCTION-COMPATIBLE TRAINING PATH** | Offline training harness whose emitted artifacts conform to production contracts and can be promoted |
| **RESEARCH** | Reachable through a documented entry point (CLI / API / worker) but not in default live flow |
| **CANDIDATE** | Functionality exists, has a wired caller, but a config gate disables it by default |
| **LEGACY** | Code that exists for backward compatibility with old artifacts / old formats |
| **CAPABILITY ONLY** | Class/function exists but has no verified production caller in current live execution |
| **UNVERIFIED** | Not yet traced end-to-end; do not claim |
| **CONTRADICTORY** | Two or more sources disagree; both are documented |

## 4. Repository Topology (verified)

- Working tree: `/home/ubuntu/nse_audit/repo`, branch `main`, commit `d9a3a829` (includes PR #247 training environment and PR #250 official model distribution).
- Source root: `src/nexus_scalp/` (17 subpackages + `web/`, `cli/`).
- Subpackages: `features`, `models`, `training`, `research`, `labeling`, `signals`, `risk`, `execution`, `experience`, `intelligence`, `strategies`, `model_generation`, `model_lab`, `model_lifecycle`, `shadow`, `news`, `accounting`, `model_provisioning`.
- Frontend: `Web/*.js` + `Web/*.html`; backend web: `src/nexus_scalp/web/`.
- Tests: 523 Python files under `tests/` (`tests/critical_suite.txt` contains 439 manifest entries).
- CI: 16 workflows under `.github/workflows/` (`ci.yml`, `ci-summary.yml`, `official-model.yml`, `dependency-lock.yml`, `tests-os.yml`, etc.).

---

## 5. The Top-Level Architectural Truths (from source)

### 5.1 Schema contract — TWO schemas exist, only ONE is live
- `ACTIVE_SCHEMA_ID = "scalp_v1"` (50D) in `src/nexus_scalp/features/schema.py:12` and `src/nexus_scalp/features/schema_contract.py:1`. **VERIFIED FROM SOURCE.**
- `SCHEMA_ID = "scalp_v3"` (70D) in `src/nexus_scalp/features/schema_contract.py:63` with `DIMENSION = 70` and layout `[0..49 Base | 50..59 News | 60..69 Liquidity]`. **VERIFIED FROM SOURCE.**
- The 50D contract is the canonical live contract. The 50D dimension is asserted at import: `if NUM_FEATURES != active_dimension(): raise RuntimeError(...)` in `src/nexus_scalp/features/scalp_features.py:221-226`. **VERIFIED FROM SOURCE.**
- 70D cannot enter live inference by default because `Runtime70Hook` (`features/runtime70.py`) is explicitly observability-only and `live_engine.py` initializes with 50D default.

### 5.2 Model class contract — 3 trained, 1 dead logit
- `TRAINED_CLASS_COUNT = 3`, `TRAINED_CLASS_NAMES = ("NO_TRADE", "BUY_MARKET", "SELL_MARKET")` in `src/nexus_scalp/model_lifecycle/model_class_contract.py:50-52`. **VERIFIED FROM SOURCE.**
- `LEGACY_HEAD_CLASSES = 4` and `WAIT_LOGIT_INDEX = 3` — the 4th logit (`WAIT`) is contractually DEAD: masked to `-1e4` before softmax by `mask_wait_logit()` and `masked_softmax()` (`model_class_contract.py:101-127`). **VERIFIED FROM SOURCE.**
- `ScalpNet.__init__` defaults `num_classes` to `TRAINED_CLASS_COUNT` (3) (`models/scalp_net.py:127-132`). **VERIFIED FROM SOURCE.**
- Directional confidence normalizes strictly over classes 0..2 (`SignalPolicy._directional_confidence`, `signals/policy.py`).

### 5.3 Training / inference geometry — 2D training, 2D live
- Training pipelines (`WalkForwardTrainer`, `CandidateTrainer`, online fine-tuning) feed `ScalpNet` with shape `(B, 50)`. Inside `ScalpNet.forward()`, it executes `x = x.unsqueeze(1)` at `models/scalp_net.py:196` and takes the 2D MLP branch at lines 203-209. **VERIFIED FROM SOURCE.**
- `LiveSequenceService` defaults to `trained_mode = "2d"`; the 3D TCN+Attention branch is gated by `state.trained_mode != "sequence"` at `application/live_sequence.py:98-99` (returns `None`, falling through to the 2D path). **VERIFIED FROM SOURCE.**
- Live inference feeds 2D single-tick snapshot vectors. The 3D sequence buffer is a capability only reachable if an artifact's `model.meta.json` declares `trained_mode: "sequence"`.

---

## 6. The Production Trainer Truth

**Verdict:** `WalkForwardTrainer` (`src/nexus_scalp/training/walk_forward_trainer.py:156`) is a **PRODUCTION-COMPATIBLE TRAINING PATH**, NOT a continuously executing production trainer.

| Characteristic | Detail | Evidence |
|---|---|---|
| **Class** | `WalkForwardTrainer` | `src/nexus_scalp/training/walk_forward_trainer.py:156` |
| **Callers** | 1. `ChallengerTrainer.train()`<br>2. `train_replica()`<br>3. `ThreeModelTrainer.train_variant()`<br>4. CLI `cli.train_model`<br>5. `LiveEngine.self.trainer` (async fine-tuning) | `model_lifecycle/trainer.py:126`<br>`model_lifecycle/final_replicas.py:142`<br>`model_generation/three_model.py:235`<br>`src/cli/train_model.py:171`<br>`live_engine.py:1498, 2961, 3805` |
| **CLI / API Entry** | `python -m cli.train_model --symbol XAUUSD --folds 34` | `src/cli/train_model.py` |
| **Runtime Execution** | Instantiated on `LiveEngine.__init__`, but only called if `config.learning.online_finetune.enabled: True` (which is `False` by default). | `live_engine.py:1498`, `bar_handler.py:311-325` |
| **Artifact Destination** | Writes to isolated staging path (e.g. `candidate/<run_id>/model.pt` or `online_buffer/online_model.pt`). **NEVER** writes directly to Champion serving directory. | `walk_forward_trainer.py:285-301`, `trainer.py:134-135` |
| **Can Become Champion?** | YES, but ONLY after passing the 12 validation gates, OOS expectancy gate, and the formal API promotion transaction (`POST /api/models/promotion/execute`). | `governance/transaction.py:1-450` |
| **Relation to `CandidateTrainer`** | `CandidateTrainer` (`model_generation/training.py:101`) is an offline research trainer using a single train/val split (`_split != test & != purged`), minority oversampling, and `ModelFactory`. It does not execute multi-fold walk-forward validation. | `model_generation/training.py:101-250` |
| **Relation to `SequenceCandidateTrainer`** | `SequenceCandidateTrainer` (`model_generation/sequence_training.py:50`) trains 3D sequence models (TCN+Attention) with explicit sequence length $L=32$. | `model_generation/sequence_training.py:50-120` |

---

## 7. The 12-Gate Validation System (`gates.py`)

All gates are defined in `src/nexus_scalp/model_lifecycle/gates.py` and orchestrated in `src/nexus_scalp/model_lifecycle/orchestrator.py:354-428` (`_evaluate_gates`).

| Gate ID | Name | Source | Default / Threshold | Caller | When Executed | Production / Research | What It Blocks |
|---|---|---|---|---|---|---|---|
| **GATE 1** | `gate_dataset_integrity` | `gates.py:48` | `sample_count > 0`, causal ordering, hash valid | `orchestrator.py:362` | Always in `_evaluate_gates` | Candidate Validation | Rejects training run with invalid or empty dataset |
| **GATE 2** | `gate_schema_compatibility` | `gates.py:73` | Schema ID & dimension match target | `orchestrator.py:363` | Always in `_evaluate_gates` | Candidate Validation | Rejects feature dimension mismatch |
| **GATE 3** | `gate_label_integrity` | `gates.py:96` | All 3 classes represented; no class $\le 0$ | `orchestrator.py:366` | Always in `_evaluate_gates` | Candidate Validation | Rejects single-class degenerate datasets |
| **GATE 4** | `gate_training_stability` | `gates.py:121` | Final loss finite, no NaN/Inf, loss $\le 5.0$ | `orchestrator.py:367` | Always in `_evaluate_gates` | Candidate Validation | Rejects numerical explosion or divergence |
| **GATE 5** | `gate_validation_performance` | `gates.py:150` | Accuracy $\ge 0.35$ (better than random 3-class) | `orchestrator.py:368` | Always in `_evaluate_gates` | Candidate Validation | Rejects sub-random classifiers |
| **GATE 6** | `gate_walkforward` | `gates.py:171` | Mean fold Sharpe proxy $\ge 0.0$, positive folds $\ge 50\%$ | `orchestrator.py:412` | Conditionally if `wf_result` available | Candidate Validation | Rejects unstable walk-forward fold distributions |
| **GATE 7** | `gate_oos` | `gates.py:193` | OOS expectancy $> 0.0$ (or configured floor) | `orchestrator.py:387` | Conditionally if `oos_result` available | Candidate Validation | Rejects models failing out-of-sample testing |
| **GATE 8** | `gate_robustness` | `gates.py:212` | Max degradation $\le 0.50$ under spread/slip stress | `orchestrator.py:414` | Conditionally if `rob_result` available | Candidate Validation | Rejects models fragile to execution friction |
| **GATE 9** | `gate_risk_drawdown` | `gates.py:233` | Max drawdown $\le 0.25$ (25%) | `orchestrator.py:416` | Conditionally if `oos_result` available | Candidate Validation | Rejects severe drawdown candidates |
| **GATE 10** | `gate_champion_comparison` | `gates.py:250` | Candidate score $\ge$ Champion score | `orchestrator.py:185` | When Champion exists & `evaluate_champion=True` | Promotion Eligibility | Rejects candidates inferior to active Champion |
| **GATE 11** | `gate_artifact_integrity` | `gates.py:270` | Checkpoint loadable, scaler present, weights finite | `orchestrator.py:369` | Always when artifact produced | Candidate Validation | Rejects unparseable or corrupted model files |
| **GATE 12** | `gate_reproducibility` | `gates.py:290` | Run ID, dataset ID, schema ID, seed recorded | `orchestrator.py:370` | Always in `_evaluate_gates` | Candidate Validation | Rejects untracked lineage |
| **GATE_COLLAPSE** | `check_model_collapse` | `gates.py:310` | No single class $> 90\%$ of total predictions | `orchestrator.py:394` | When prediction counts present | Candidate Validation | Rejects majority-class collapse |
| **GATE_ELIGIBLE** | `gate_production_eligible` | `gates.py:325` | `production_eligible == True`, not smoke drill | `orchestrator.py:405` | Always when artifact metadata read | Promotion Eligibility | Rejects smoke runs (`smoke=True`) from promotion |

---

## 8. The Three Parallel State Machines

Three distinct state machines govern models across subsystems with un-synchronized vocabularies.

| State Machine | Source | States | Owner / Storage | Readers / Callers | Authoritative? | Production Reachability |
|---|---|---|---|---|---|---|
| **Model Lifecycle** | `src/nexus_scalp/model_lifecycle/models.py:32` | `CANDIDATE`, `CHALLENGER`, `CHAMPION`, `REJECTED`, `ARCHIVED`, `INVALID` | `ModelLifecycleStore` (`audit.db` / `model_registry` table) | `ModelLifecycleOrchestrator`, `ChampionManager`, `worker.py` | Authoritative for training lifecycle | Writes candidate records; ChampionManager points to active model |
| **Model Governance** | `src/nexus_scalp/governance/models.py:170` | `RESEARCH`, `VALIDATED`, `CHALLENGER`, `SHADOW`, `READY_FOR_REVIEW`, `APPROVED`, `CHAMPION`, `REJECTED`, `RETIRED` | `GovernanceStore` (`governance_models` table) | `ModelGovernanceEngine`, `transaction.py`, `model_governance_routes.py` | Authoritative for promotion authorization | Controls atomic file promotion and gate validation |
| **Model Lab** | `src/nexus_scalp/model_lab/registry.py:31` | `CREATED`, `TRAINING`, `COMPLETED`, `FAILED`, `REJECTED`, `VALIDATED`, `PROMOTION_CANDIDATE` | `LabRegistry` (`lab_experiments.db`) | `ExperimentOrchestrator`, `lab_routes.py` | Authoritative for lab/research only | **NO** — production states explicitly unreachable |

### Architectural Contradiction:
There is NO single unified state machine. `ModelStatus.CHALLENGER` in `model_lifecycle` corresponds conceptually to `PromotionState.VALIDATED` or `PromotionState.CHALLENGER` in `governance`. Because these persist in different SQLite tables without foreign key synchronization, states can diverge (e.g. a model can be marked `CHALLENGER` in `model_lifecycle` while remaining `RESEARCH` or `QUARANTINED` in `governance`).

---

## 9. Component Classification Matrix

| Component | Path | Classification | Evidence |
|---|---|---|---|
| `ScalpNet` (PyTorch) | `models/scalp_net.py` | PRODUCTION | Live inference model; 2D MLP path active; input projection + Pre-LN + GeLU |
| `TRAINED_CLASS_COUNT=3` | `model_lifecycle/model_class_contract.py` | PRODUCTION | Canonical class count (NO_TRADE, BUY_MARKET, SELL_MARKET); WAIT masked |
| `FeatureSchema (scalp_v1)` | `features/schema.py:12` | PRODUCTION | Canonical 50D feature schema active in live tick processing |
| `FeatureSchema (scalp_v3)` | `features/schema_contract.py` | RESEARCH / CANDIDATE | 70D schema specification; not consumed by live 50D model |
| `WalkForwardTrainer` | `training/walk_forward_trainer.py` | PRODUCTION-COMPATIBLE TRAINING PATH | Purged walk-forward candidate training; called by CLI, lifecycle, and online retrain |
| `CandidateTrainer` | `model_generation/training.py` | RESEARCH | Single-split candidate trainer with class oversampling |
| `SequenceCandidateTrainer` | `model_generation/sequence_training.py` | RESEARCH | Sequence candidate trainer for 3D TCN+Attention |
| `InferenceService` | `application/live/inference.py` | PRODUCTION | Tick inference coordinator, scaler transformation, and WAIT masking |
| `SignalPolicy` | `signals/policy.py` | PRODUCTION | Gatekeeper: guardian, confidence gate, SMC God Mode, anti-flip |
| `SMC_GOD_MODE` | `signals/policy.py:710-721` | PRODUCTION | Overrides HTF/SR alignment when confidence $\ge 0.60$ and BOS+OB+sweep+FVG pass |
| `ShadowRecorder` | `application/live/shadow_recorder.py` | PRODUCTION (Recording Only) | Records shadow decisions per tick; outcome resolution is not called live |
| `ShadowOutcomeResolver` | `shadow/outcomes.py` | CAPABILITY ONLY (Live) / RESEARCH (Replay) | Realized tick resolution implemented for replay; not wired into live tick pipeline |
| `Promotion API` | `web/model_governance_routes.py:1270` | PRODUCTION | The ONLY promotion path (`POST /api/models/promotion/execute`) |
| `PromotionLock` | `governance/lock.py` | PRODUCTION | Cross-process atomic lock file preventing concurrent promotion overwrites |
| `ModelGovernanceEngine.promotion_frozen` | `governance/engine.py:108` | PRODUCTION (In-Memory) | Emergency stop flag; resets to `False` on process restart |
| `Online Fine-Tuning Dispatch` | `application/live/bar_handler.py:311` | CANDIDATE | Reachable code, but disabled by default (`learning.online_finetune.enabled: False`) |
| `Native MT5 Adapter` | `adapters/mt5/mt5_adapter.py` | PRODUCTION (Windows Only) | Requires `sys.platform == "win32"`; unimportable on Linux |
| `RemoteMT5GatewayAdapter` | `adapters/mt5/remote_gateway_adapter.py` | PRODUCTION (Linux / Distributed) | Communicates with remote Windows MT5 gateway |
| `PaperAdapter` | `adapters/mt5/paper_adapter.py` | PRODUCTION (Simulation) | In-process simulation adapter for testing |

---

## 10. Operational Truths & Human Governance

1. **No UI Promotion Button:** The Control Center UI does NOT have a working promotion button. Promotion is strictly API-driven via authenticated POST request.
2. **Emergency Freeze Persistence:** Calling `freeze_promotions()` sets `self.promotion_frozen = True` in memory. While an audit row is written to SQLite, the flag itself is not restored upon server reboot.
3. **Zero Online Learning by Default:** Live trades generate experience ledger entries and autopsies, but do NOT update neural network weights automatically. Retraining requires manual operator dispatch.
