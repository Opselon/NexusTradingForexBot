# ML-UI-003 — AI Hub: Model Registry, SQLite Catalog & Live Runtime Hot-Loader

STREAM: STREAM L — OBSERVABILITY, UX & INTEGRATION
PRIORITY: P1
STATUS: DONE
DEPENDENCIES: ML-UI-001, ML-UI-002, ML-DATA-001
BLOCKS: None
AGENT_ROLE: AGENT-UI
OWNERSHIP_SCOPE: src/nexus_scalp/model_generation/model_registry.py, src/nexus_scalp/web/model_studio_routes.py, src/nexus_scalp/cli/model_studio_commands.py, Web/index.html, Web/model_studio_ui.js, frontend/src/features/model-studio/
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE
TYPE: Feature & UI Pipeline
RISK: Low
ESTIMATED_SCOPE: 7 files, ~1,200 LOC

## OBJECTIVE
Deliver a comprehensive, API-first AI Hub architecture enabling operators to:
1. Persist model checkpoints and associated metadata in an ACID SQLite database catalog (`models.db`).
2. Dynamically hot-load neural model weights (50D/70D PyTorch ScalpNet) into the live running trading engine without restarting the process.
3. Automatically load and calibrate scaler sidecars (`.scaler.npz`) alongside model weights.
4. Gate fine-tuning execution with an explicit UI toggle and support fine-tuning with frozen backbone layers.
5. Provide a 1-click model rollback to the previous champion from the persistent audit trail.
6. Run a 5-point pre-load integrity verification battery (file existence, safe deserialization, numerical finiteness, weight variance, and smoke inference).
7. Inspect scaler vector parameters (means, standard deviations, clamping, and zero-variance detection) in both UI dashboards and CLI.
8. Deliver full support across REST API, Typer CLI, classic Web UI, and the modern React frontend.

## ARCHITECTURE & CORE CAPABILITIES (15 API-FIRST FEATURES)
1. **Model Catalog Persistence (`ModelRegistry`)**:
   - SQLite backed (`models.db`) with schema versioning (`schema_version=1`).
   - Tracks checkpoint ID, architecture, dimension (50D/70D), SHA-256 hash, paths to `.pt` and `.scaler.npz`, training metrics (loss, val_loss, epochs), lifecycle stage (`STAGING`, `CHAMPION`, `ARCHIVED`), and fine-tuning flag.
   - Maintains an immutable audit history table of every hot-load, rollback, and state transition with timestamps and operator tags.
2. **Dynamic Hot-Loader (`execute_hot_load`)**:
   - Thread-safe model weight injection into `_StudioBundleHolder` and `engine.scalp_model`.
   - Automatic dimension detection (50D vs 70D) from PyTorch state dict tensor dimensions.
   - Synchronous warmup execution with microsecond latency measurement.
3. **Scaler Sidecar Pairing (`execute_get_scaler`)**:
   - Detects and loads companion `.scaler.npz` files matching `<checkpoint_stem>.scaler.npz`.
   - Exposes per-feature statistics (mean, std, zero-variance status, clamp bounds).
4. **Pre-Load Verification Battery (`execute_verify`)**:
   - Checks file accessibility and non-empty byte size.
   - Verifies `weights_only=True` safe deserialization without arbitrary code execution.
   - Verifies numerical finiteness (detects NaN and Inf in model weights).
   - Validates non-zero weight variance across projection and hidden layers.
   - Executes single-batch forward pass smoke test to guarantee inference stability.
5. **Atomic 1-Click Rollback (`execute_rollback`)**:
   - Queries historical audit log for the most recent preceding active champion.
   - Restores previous weights and scaler into memory atomically.
6. **Gated Fine-Tuning (`execute_fine_tune`)**:
   - Supports frozen feature extraction backbones (`freeze_backbone=True`).
   - Produces new versioned checkpoint and companion scaler, registering them into SQLite.
7. **Canary Shadow Deployment (`execute_canary`)**:
   - Loads secondary model into canary slot for live shadow evaluation.
8. **Audit Trail Inspection (`execute_history`)**:
   - Returns chronological load and promotion events.
9. **Single-Bundle Checkpoint Archival Export (`execute_export_model`)**:
   - Packs weights, scaler, and manifest into a distributable `.zip` archive.
10. **Lifecycle Promotion & Tagging (`execute_tag_model`)**:
    - Updates model stages (`STAGING`, `CANARY`, `CHAMPION`, `ARCHIVED`).
11. **Live Inference Benchmarking (`execute_benchmark_live`)**:
    - Evaluates P50/P90/P99 latency of currently hot-loaded model.
12. **Distribution Drift Check (`execute_drift_check`)**:
    - Compares incoming market candle features against scaler baseline distributions.
13. **Active Model Inspector (`execute_active_model`)**:
    - Real-time telemetry of loaded model ID, dimension, SHA256, scaler status, and inference count.
14. **Protected Deletion (`execute_delete_model`)**:
    - Deletes checkpoints and metadata while forbidding deletion of active champion.
15. **Cross-Platform UI & CLI Support**:
    - REST API: `/api/model-studio/models/*`
    - CLI: `nexus model-list`, `nexus model-hot-load`, `nexus model-active`, `nexus model-verify`, `nexus model-rollback`
    - Classic Web: `Web/index.html` + `Web/model_studio_ui.js`
    - React: `frontend/src/features/model-studio/`

## ACCEPTANCE CRITERIA
- [x] SQLite database `models.db` stores model checkpoints, sidecars, and audit logs.
- [x] Hot-load dynamically updates running model weights and scalers in memory without restarting.
- [x] UI includes model dropdown selector, Fine-Tune toggle, and Scaler toggle.
- [x] 1-click rollback restores prior champion from history.
- [x] Verification battery validates weights finiteness, variance, and smoke inference.
- [x] Scaler vector table inspects 50D/70D feature means, standard deviations, and zero-variance flags.
- [x] Both classic Web dashboard and React frontend are updated and verified.
- [x] All 42 unit tests pass in `test_model_studio.py`, `test_model_studio_pipeline.py`, and `test_model_registry_hot_load.py`.
- [x] Frontend passes `npm run typecheck` and `npm run build`.
