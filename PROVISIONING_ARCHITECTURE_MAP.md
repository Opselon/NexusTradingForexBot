# PROVISIONING_ARCHITECTURE_MAP.md

Model Setup control plane — architecture map for the NSE model lifecycle.
Authoritative scope: `http://localhost:8089/provisioning` and every component
required to make it a real model-management system.

Measured at `77eb21b1` (origin/main tip) from an isolated worktree. Live probes
were taken against the running engine at `127.0.0.1:8089`.

## 1. UI

```
frontend/src/features/provisioning/
├── api.ts                     typed transport (getLegacy/send over /api/provisioning)
├── model.ts                   response/request types (TrainBackend, EnvCheck, DatasetRoot…)
├── index.ts                   public exports
├── i18n.ts                    feature message catalog
└── ui/
    ├── ProvisioningPage.tsx   the /provisioning page (1449 lines)
    ├── useTrainPoll.ts        event-tail polling hook
    ├── kit.ts                 CANDLE_OPTIONS, slotTone, recommendedActionOf, fmt*
    └── provisioning.css       hand-written CSS on theme tokens (NO framework)
```

Registered in `frontend/src/app/featureRegistry.ts` (route `/provisioning`).
Served at `/alt/provisioning` by the StaticFiles mount in `web/server.py`
(enabled when `frontend/dist/index.html` exists or `NEXUS_ALT_UI_DIR` is set).
The legacy vanilla console is `Web/` — there is no legacy provisioning page, so
the React surface is the ONLY operator UI for this feature.

## 2. API

`src/nexus_scalp/web/provisioning_routes.py` — `register_provisioning_routes()`.
All routes are legacy RAW-JSON (never v1 envelopes); typed failures arrive
in-band as `{success:false, code, message?, detail?, remedy?}`.

| Method | Path | Handler | Purpose |
| --- | --- | --- | --- |
| GET | `/api/provisioning/status` | `provisioning_status` | slot classification + recommended action |
| GET | `/api/provisioning/environment` | `provisioning_environment` | python/torch/cuda/GPU discovery (read-only) |
| POST | `/api/provisioning/environment/install` | `provisioning_environment_install` | explicit pinned-stack install |
| POST | `/api/provisioning/official` | `provisioning_official` | download + verify + install official bundle |
| POST | `/api/provisioning/train/start` | `provisioning_train_start` | background local training run |
| GET | `/api/provisioning/train/progress` | `provisioning_train_progress` | incremental event tail |
| POST | `/api/provisioning/train/cancel` | `provisioning_train_cancel` | request cancel at epoch boundary |
| GET | `/api/provisioning/datasets` | `provisioning_datasets` | allowed-root dataset browser (read-only) |

Module-level single-flight registries: `_ACTIVE` (train run), `_INSTALL_ACTIVE`,
`_OFFICIAL_ACTIVE`, all guarded by `_ACTIVE_LOCK`.

## 3. Service

`src/nexus_scalp/model_provisioning/`

| File | Role |
| --- | --- |
| `service.py` | `FirstRunCoordinator` — `slot()`, `recommended_action()`, `download_official()`, `train_local()`, `ensure_serving_model()`; `read/write_provisioner_state()` |
| `pipeline.py` | `train_local_model()` — the actual local training pipeline |
| `training_env.py` | `TrainingEnvironmentManager` — resolve python/venv/torch/gpu, `status()` discovery, `install()` |
| `training_dispatch.py` | `worker_script()` — the training worker entrypoint |
| `training_worker.py` | worker loop |
| `dataset_source.py` | `validate_dataset_request()`, `prepare_training_dataset()` |
| `official.py` | `OfficialBundleSource.download_and_verify()`, `install_verified_bundle()`, `cleanup_dir()` |
| `official_contract.py` | official manifest contract |
| `official_install.py` | atomic install / slot lock |
| `official_runtime.py` | runtime verification |
| `official_staging.py` | staged temp download dir |
| `official_transport.py` | transport (download) |
| `states.py` | `LifecycleState` enum |

Adjacent model-management services (the EXISTING lifecycle — not part of provisioning today):

| File | Role |
| --- | --- |
| `model_lifecycle/registry.py` | `ModelLifecycleRegistry` — lifecycle facade over canonical `experience_model_registry` |
| `model_lifecycle/champion.py` | champion manager (active model) |
| `model_lifecycle/orchestrator.py` | lifecycle orchestrator |
| `model_lifecycle/store.py` | lifecycle store |
| `governance/engine.py` | `ModelGovernanceEngine.rollback()` (line 519), `rollback_preview()` (777) |
| `application/command_intent.py` | `_intent_rollback` — the Telegram/operator rollback intent |
| `experience/provenance.py` | `ModelRegistry`, `fingerprint_artifact` |
| `web/model_governance_routes.py` | `/api/models/*` — champion, challengers, integrity, registry, shadow |

## 4. Worker

`training_dispatch.worker_script()` returns a script run by
`training_env.run_application_probe()` (subprocess, `--requirements-stdin
--probe`, 180s timeout). Live training runs are in-process `threading.Thread`
named `nexus-local-train` (daemon), driven by `_TrainRun._worker()` in the web
route file — NOT a durable OS process. Progress is an in-memory event list per
run (`_TrainRun.events`), tailed by index via `?after=`.

## 5. Artifact

Bundle layout (verified against the live champion):

```
artifacts/models/scalp/XAUUSD/70d_liquidity/
├── model.pt                  torch weights
├── model.scaler.npz          scaler, SIBLING (same stem) — not model.pt.scaler.npz
├── manifest.json             signed; names input_dim, carries NO model_id
└── model.meta.json           training declaration (num_features, model_head_classes…)
```

The serving model path is resolved by `service.serving_model_path()`.

## 6. Registry

Two registries exist and are deliberately distinct:

1. **`experience_model_registry`** (canonical, PostgreSQL via provider_store) —
   model identity, artifact path/hash, schema, dims, training run. Extended
   additively by `model_lifecycle/registry.py` with lifecycle columns
   (`lifecycle_status`, `training_run_id`, `parent_model_id`…).
2. **Provisioner state** — `read/write_provisioner_state()` (service.py), a
   small lifecycle-state record for the provisioning flow (DOWNLOADING /
   VERIFYING / READY / REJECTED / TRAINING / CANDIDATE / VALIDATION_FAILED /
   MISSING), NOT a competing model registry.

`/api/models` answers `{"available":true,"models":[]}` live — the governance
registry rows are empty while `champion` (`/api/models/champion`) reports the
serving 70D bundle. The `models[]` lane is the inventory seam.

## 7. Runtime

```
LiveEngine (application/live_engine.py)
 ├─ champion_manager        (model_lifecycle/champion.py)  — WHO IS ACTIVE
 ├─ governance_engine       (governance/engine.py)         — rollback authority
 ├─ adapter                 (MT5 / paper)
 └─ model bundle            (model.pt + model.scaler.npz + manifest)
```

`engine.effective_feature_dim` / `engine.effective_feature_schema_id` are the
RUNTIME contract (70D), NOT the `FEATURE_DIM`/`FEATURE_SCHEMA_ID` class
constants (50D bootstrap) and NOT `FEATURE_SCHEMAS.resolve(None)`.

## 8. Database

- **PostgreSQL** is the live provider (`/api/models/summary` →
  `"provider":"postgresql"`): training runs, comparisons, registry,
  `experience_model_registry`.
- **SQLite** is the packaged/default provider (audit + settings domains).
- Settings live in the `application_settings` table (keys `database.provider`,
  `database.postgresql_config`); `settings_audit.SOURCE` records WHO chose it.
- `nexus db status|migrate` uses a SEPARATE SQLite engine
  (`database/engine.py`) from the runtime PG fabric path — do not trust its
  version number for the live schema.

## Observed topology (this host)

```
:8089  →  NexusTradingForexBot.py  in  nse-review-main   (REVIEW worktree, locked)
           launcher bootstrap: <script-dir>/src → WORKTREE code wins
           imports/deps:       NexusTradingForexBot/.venv  (editable .pth pin)
           torch there:        2.13.0+cu126, CUDA available
```
