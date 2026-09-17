# 01 — ML System Contract (Current State)

> **Status:** READ-ONLY forensic classification of NSE at HEAD
> `bb9ce84ddd22bd193ff419c309181f67e8b3164d`. Source wins over documentation.
> Every classification carries an evidence label:
> **VERIFIED FROM SOURCE** / **NOT VERIFIED FROM EXECUTED DATA** /
> **NOT PROVEN FROM CURRENT REPOSITORY** / **CONTRADICTED BY SOURCE** /
> **DOCUMENTATION-ONLY**.

## 1. Purpose

The single, current-state architectural contract for the ML/AI subsystem. It
answers: "What exactly exists in the NSE ML system today, and where is each
piece on the spectrum from production to dead?"

It does **not** answer: "What should we build?" — that lives in
`06_TASK_LEDGER.md`.

## 2. Evidence Hierarchy (binding)

1. Executable source code
2. Tests that actually run
3. Runtime / configuration paths
4. Model / checkpoint metadata consumed by code
5. Datasets / manifests consumed by code
6. CI / workflows
7. Existing technical documentation
8. README / comments

If (1)-(6) and (7) disagree, **document the contradiction**; never silently
override executable behavior.

## 3. Classification Taxonomy

| Tag | Meaning |
|---|---|
| **PRODUCTION** | Code on the verified live tick path with a reachable caller in shipped config |
| **RESEARCH** | Reachable through a documented entry point (CLI / API / worker) but not in default live flow |
| **CANDIDATE** | Functionality exists, has a wired caller, but a config gate disables it by default |
| **LEGACY** | Code that exists for backward compat with old artifacts / old formats |
| **CAPABILITY ONLY** | Class/function exists but has no verified production caller in current HEAD |
| **UNVERIFIED** | Not yet traced end-to-end; do not claim |
| **CONTRADICTORY** | Two or more sources disagree; both are documented |

## 4. Repository Topology (verified)

- Working tree: `/home/ubuntu/nse_audit/repo`, branch `main`, HEAD
  `bb9ce84ddd22bd193ff419c309181f67e8b3164d`. (`git rev-parse HEAD`)
- Source root: `src/nexus_scalp/` (17 subpackages + `web/`, `cli/`).
  `find src/nexus_scalp -name '*.py' | wc -l` ≈ 540.
- Subpackages: `features`, `models`, `training`, `research`, `labeling`,
  `signals`, `risk`, `execution`, `experience`, `intelligence`, `strategies`,
  `model_generation`, `model_lab`, `model_lifecycle`, `shadow`, `news`,
  `accounting`.
- Frontend: `Web/*.js` + `Web/*.html`; backend web: `src/nexus_scalp/web/`.
- Tests: 540 Python files under `tests/` (suites: `unit/`, `integration/`,
  `e2e/`, `cli/`, `ci/`, `acceptance/`, `release/`, `runtime/`, `slow/`,
  `js/`, `manual/`, `installer/`, `e2e_client/`, `fixtures/`, `helpers/`).
- CI: 15 workflows under `.github/workflows/` (e.g. `ci.yml`, `ci-summary.yml`,
  `official-model.yml`, `nightly-e2e.yml`, `release.yml`).
- CLI: `src/nexus_scalp/cli/` (facade `main.py` + decomposed modules:
  `app_factory`, `doctor`, `engine_boot`, `provision_commands`, `update_cli`,
  `wizard`, `analyze_commands`, `api_commands`, `db_commands`,
  `dependency_commands`, `gateway_commands`, `incident_commands`,
  `risk_commands`, `smoke_commands`).

## 5. The Three Top-Level Truths (from source)

### 5.1 Schema contract — TWO schemas exist, only ONE is live

- `ACTIVE_SCHEMA_ID = "scalp_v1"` (50D) in
  `src/nexus_scalp/features/schema.py:95`. **VERIFIED FROM SOURCE.**
- `SCHEMA_ID = "scalp_v3"` (70D) in
  `src/nexus_scalp/features/schema_contract.py:63` with
  `DIMENSION: int = 70` and family layout `[0..49 Base | 50..59 News |
  60..69 Liquidity]` (lines 65–73). **VERIFIED FROM SOURCE.**
- The 50D contract is described as
  "the engine's canonical live contract" in `features/schema.py:7-8`. The
  70D contract is described in `features/schema_contract.py:1-42` as
  "the shared, immutable specification" with a strict hash identity
  (`feature_schema_hash`, line 195). **VERIFIED FROM SOURCE.**
- The 50D dimension is enforced at import time:
  `NUM_FEATURES == active_dimension()` assertion in
  `features/scalp_features.py:221-226`. **VERIFIED FROM SOURCE.**

### 5.2 Model class contract — 3 trained, 1 dead

- `TRAINED_CLASS_COUNT: int = 3`,
  `TRAINED_CLASS_NAMES = ("NO_TRADE", "BUY_MARKET", "SELL_MARKET")` in
  `src/nexus_scalp/model_lifecycle/model_class_contract.py:50-52`.
  **VERIFIED FROM SOURCE.**
- `LEGACY_HEAD_CLASSES: int = 4` and `WAIT_LOGIT_INDEX: int = 3` — the 4th
  logit is "contractually DEAD" (line 26): masked to `-1e4` before softmax
  by `mask_wait_logit()` (lines 101-121) and `masked_softmax()`
  (lines 124-127). **VERIFIED FROM SOURCE.**
- `ScalpNet.__init__` defaults `num_classes` to `TRAINED_CLASS_COUNT`
  (`models/scalp_net.py:127-132`). **VERIFIED FROM SOURCE.**
- Live inference path applies the WAIT mask in
  `application/live/inference.py:309-` (explicit `mask_wait_logit` import and
  call inside `infer_probabilities`). **VERIFIED FROM SOURCE.**

### 5.3 Training / inference geometry — 2D training, 2D live

- Training pipelines (WalkForwardTrainer, CandidateTrainer, online
  fine-tune) feed `ScalpNet` with shape `(B, 50)`; `forward()` then
  `unsqueeze(1)` to `(B, 1, 50)` at `models/scalp_net.py:196`, taking the
  2D MLP branch at lines 203-209. **VERIFIED FROM SOURCE.**
- `LiveSequenceService` defaults to `trained_mode="2d"` and
  `seq_len=32`; the 3D TCN+Attention branch is gated by
  `state.trained_mode != "sequence"` at
  `application/live_sequence.py:98-99` (returns `None`, falling through
  to the 2D path in `inference.py:268-271`).
  **VERIFIED FROM SOURCE.**
- The 3D sequence path is only reachable when an artifact's
  `model.meta.json` declares `trained_mode: "sequence"` (set by
  `SequenceCandidateTrainer` at
  `model_generation/sequence_training.py:305`).
  **VERIFIED FROM SOURCE.**
- `LiveEngine` initializes `self._live_sequence_trained_mode = "2d"` at
  `application/live_engine.py:1520`. **VERIFIED FROM SOURCE.**

## 6. Component Classification Matrix

| Component | Path | Class | Evidence |
|---|---|---|---|
| `ScalpNet` (PyTorch) | `models/scalp_net.py` | PRODUCTION | Verified live input; constructor at line 104, `forward` 2D/3D at 172-241 |
| `TRAINED_CLASS_COUNT=3` / `WAIT mask` | `model_lifecycle/model_class_contract.py` | PRODUCTION | The single source of truth, used by `inference.py` |
| `FeatureSchema.ACTIVE_SCHEMA_ID = scalp_v1` | `features/schema.py:95` | PRODUCTION | Hard-coded active schema; import-time assertion enforces 50D in `scalp_features.py:221-226` |
| `scalp_v3` 70D contract | `features/schema_contract.py` | RESEARCH | Used by training / replay / shadow70, NOT on the live 50D path |
| `LiveSequenceService` | `application/live_sequence.py` | CAPABILITY ONLY (default 2D) | Sequence path gated `trained_mode == "sequence"`; default `"2d"` |
| `WalkForwardTrainer` | `training/walk_forward_trainer.py` | PRODUCTION | Primary training entry; CLI `model-provision` / `train-once` |
| `CandidateTrainer` | `model_generation/training.py` | RESEARCH | Alternate trainer; reachability to be re-verified per pipeline (see `04`) |
| `SequenceCandidateTrainer` | `model_generation/sequence_training.py` | RESEARCH | 70D / sequence-mode trainer; only caller is benchmark / research paths |
| `OOSGate` / `BacktestEngine` | `research/oos.py`, `research/backtest.py` | PRODUCTION | Used by the promotion flow; thresholds in `oos.py:34-43` |
| `ModelBundleStore` (load + integrity) | `application/live/model_bundle_store.py` | PRODUCTION | Every serving path reads from here; integrity probe `_check_artifact_coherence` at line ~190 |
| `ModelGovernanceEngine.promotion_frozen` | `governance/engine.py:108` | PRODUCTION | Hard emergency stop; raised at line 481 |
| `POST /api/models/promotion/execute` | `web/model_governance_routes.py:1060-1379` | PRODUCTION | The governed promotion surface (file verified in summary; re-confirm exact route in `04`) |
| `model-provision` / `train-once` | `cli/provision_commands.py` | PRODUCTION | Registered in `cli/main.py` |
| `online_finetune.enabled = False` (default) | `model_lifecycle/learning_config.py:56` | CANDIDATE | Code path exists; default config gate is closed; safe-by-default per `docs/agent_handoffs/...` |
| `LearningConfig.enabled = False` (default) | `model_lifecycle/learning_config.py:96` | CANDIDATE | Same: exists, gated off |
| `PromotionConfig.enabled = False` (default) | `model_lifecycle/learning_config.py:90` | CANDIDATE | Auto-promotion forbidden; manual only |
| `ShadowEngine` | `shadow/engine.py` | PRODUCTION | Reachable from live tick path via `LiveEngine._record_shadow_decision` |
| `Runtime70Hook` (shadow70 runtime) | `shadow/shadow70/runtime.py` | CAPABILITY ONLY | Subagent reports show `shadow70.READY` gate; not part of default live inference |
| `ExperienceLedger` (audit_experiences) | `experience/ledger.py` | PRODUCTION | Append-only decision/outcome log; `record_experience` at line 167 |
| `IntelligenceEngine` (autopsy / evolution) | `intelligence/*.py` | RESEARCH / OBSERVABILITY | Reads outcomes; does NOT mutate the artifact (re-verify per `05`) |
| `MLFix.MD` §11 referenced in module docstring | `model_class_contract.py:1-38` | DOCUMENTATION-ONLY | Module docstring cites an external narrative; not enforced as code |
| `docs/70D_*.md` (set) | `docs/70D_*.md` | CONTRADICTORY | Existing narrative treats 70D as production; code keeps 50D live |

## 7. Architectural Capability vs Production Behavior

ARCHITECTURAL CAPABILITY:
- Both 2D and 3D forward paths exist in `ScalpNet.forward`
  (`models/scalp_net.py:172-241`).
- A `LiveSequenceService` is wired through `LiveEngine` and is reachable
  when an artifact declares `trained_mode="sequence"`
  (`live_sequence.py:74-99`).

TRAINING:
- Walk-Forward, Candidate, and online fine-tune paths all produce
  per-row `(N, 50)` arrays → `unsqueeze(1)` → `(B, 1, 50)` inside
  `ScalpNet.forward`. Sequence length is 1 (trivial).

PRODUCTION INFERENCE:
- `LiveEngine._process_tick_pipeline` →
  `tick_pipeline.compute_from_bars` → 50D `FeatureVector.to_tensor_input()`
  → `bundle.scaler.transform` → `maybe_build_live_sequence_tensor` returns
  `None` (because `trained_mode == "2d"`) → fall through to
  `torch.tensor(x_np, dtype=torch.float32)` shape `(1, 50)`.

VERIFIED EXECUTION PATH:
- `inference.py:243-271` is the verified live tensor assembly.

TENSOR SHAPE (live):
- `(1, 50)` 2D input → `unsqueeze(1)` → `(1, 1, 50)` →
  `MLP path` → `(1, 3)` softmax (4-wide legacy head is masked at index 3).

TEMPORAL CONTEXT ACTUALLY USED:
- One row at a time. The bar-aligned buffered tensor exists in code but is
  gated off by `trained_mode` for the live 50D champion.

STATUS:
- The 3D sequence path is **not on the verified production inference path**.

CONFIDENCE: **HIGH** for the 2D-live claim; **MEDIUM** for the "never reached
in practice" claim (depends on the champion artifact's `trained_mode`
metadata).

## 8. Contradictions Logged

1. `docs/70D_*.md` narrative vs code reality (50D live). To be enumerated
   in `06_TASK_LEDGER.md` once subagent reports are read.
2. Module docstring of `model_class_contract.py` references
   `MLFix.MD §11 / PINC Exp2` — external narrative, not enforced as code.
3. Promotion surface mentions "no UI button" in some prior audit notes,
   but the full route table in `model_governance_routes.py` was not
   independently re-grepped end-to-end here. See `05`.

## 9. Unknowns / Open Investigations

- Whether the Web/Command Center UI exposes any model-promotion control
  (operator console). See `05` for re-verification.
- Whether any `Research Factory` / `Strategy Factory` orchestrator
  end-to-end invokes `SequenceCandidateTrainer` or only the WalkForward
  trainer. See `04`.
- Whether `Runtime70Hook` is reachable from the live tick path or only
  from shadow70 worker. See `05`.
- Whether `intelligence/evolution.py` ever mutates the artifact (default
  expectation: NO — to verify per file:line in `05`).
- Exact `OOSGate` economic threshold in the current default
  (`MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02`, `research/oos.py:41`). To
  document the OOS lifecycle stages (cycle-N vs cycle-N+1) in `04`.

## 10. What is *not* in this document

- Code changes. None proposed.
- New architecture. None proposed.
- Decisions about 50D/70D promotion, sequence contract, output classes,
  promotion criteria. All flagged HUMAN DECISION REQUIRED in the
  task ledger.
