# ML-UI-003 Handoff — AI Hub: Model Registry, SQLite Catalog & Live Runtime Hot-Loader

## Task Summary
- **Task ID**: `ML-UI-003`
- **Agent**: `AGENT-UI` / `AGENT-ML`
- **Date**: `2026-09-18`
- **Status**: `DONE` (Ready for PR & Merge)
- **Goal**: Implement complete API-first AI Hub architecture enabling persistent SQLite checkpoint registry (`models.db`), dynamic hot-loading of neural model weights (50D/70D PyTorch ScalpNet) and companion scaler sidecars (`.scaler.npz`) into the live running trading engine without restart, gated fine-tuning with frozen backbones, 1-click rollback to prior champion from audit history, pre-load verification battery, and vector inspection of scalers across REST API, CLI, classic Web UI, and modern React frontend.

---

## 1. Scope of Changes

### 1. Persistent Model Catalog (`src/nexus_scalp/model_generation/model_registry.py`)
- SQLite database persistence (`models.db`) with automatic schema initialization and schema versioning.
- Models table tracks ID, name, version, dimension (50D/70D), architecture, weights path, scaler path, manifest path, SHA256, training loss, val_loss, epochs, dataset path, fine-tune status, lifecycle stage (`STAGING`, `CHAMPION`, `ARCHIVED`), and timestamps.
- Audit history table logs every hot-load, rollback, and stage transition with timestamp and operator ID.
- Atomic rollback helper retrieves the previous champion from audit records.

### 2. Backend REST Endpoints (`src/nexus_scalp/web/model_studio_routes.py`)
- `GET /api/model-studio/models`: Lists all registered checkpoints and active champion ID.
- `POST /api/model-studio/models/hot-load`: Hot-loads checkpoint weights and companion scaler into live engine memory; measures microsecond warmup latency.
- `GET /api/model-studio/models/active`: Telemetry on currently hot-loaded model, scaler readiness, and inference count.
- `POST /api/model-studio/models/rollback`: 1-click restoration of preceding champion model from audit history.
- `POST /api/model-studio/models/verify`: 5-point integrity battery (file existence, safe deserialization, numerical finiteness, weight variance, and smoke inference).
- `GET /api/model-studio/models/{model_id}/scaler`: Vector inspector for scaler means, standard deviations, zero-variance status, and clamping.
- `POST /api/model-studio/models/fine-tune`: Fine-tuning from base model with frozen feature extractor layers.
- `POST /api/model-studio/models/canary`: Registers secondary model in shadow canary evaluation slot.
- `GET /api/model-studio/models/history`: Audit log of hot-load and promotion events.
- `POST /api/model-studio/models/export`: Distributable `.zip` archive packager.
- `POST /api/model-studio/models/tag`: Lifecycle stage updates (`STAGING`, `CHAMPION`, `ARCHIVED`).
- `POST /api/model-studio/models/benchmark-live`: Real-time P50/P90/P99 latency benchmarking on live active model.
- `POST /api/model-studio/models/drift-check`: Input feature distribution drift comparison against scaler baseline.
- `POST /api/model-studio/models/register`: Manual checkpoint registration.
- `DELETE /api/model-studio/models/{model_id}`: Protected deletion (blocks deleting active champion).

### 3. CLI Management Surface (`src/nexus_scalp/cli/model_studio_commands.py`)
- `nexus model-list`: Displays rich table or raw JSON of registered models.
- `nexus model-hot-load`: Loads model weights and scaler with optional `--fine-tune` and `--no-scaler` flags.
- `nexus model-active`: Displays active champion telemetry and stats.
- `nexus model-rollback`: Restores previous champion model.
- `nexus model-verify`: Executes verification battery and reports table of checks.

### 4. Legacy Web UI (`Web/index.html`, `Web/model_studio_ui.js`)
- Added **AI Hub: Model Registry, Hot-Loader & Lifecycle Management** panel.
- Live Active Champion badge, Fine-Tune badge, Scaler status badge.
- Model selector dropdown populated dynamically from SQLite registry.
- Checkboxes for "Enable Fine-Tune Mode" and "Auto-Load Scaler Sidecar".
- Action buttons: "⚡ Hot-Load Model", "✓ Verify Integrity Battery", "↺ Rollback to Previous Champion", "📊 Inspect Scaler Vectors".
- Diagnostic tables for verification battery and scaler vector distributions.

### 5. Modern React Frontend (`frontend/src/features/model-studio/`)
- Added TypeScript DTO interfaces in `model.ts`.
- Added API client functions in `api.ts`.
- Built full interactive UI panel in `ModelStudioPage.tsx` with loading states, notifications, and telemetry.

### 6. Automated Unit & Integration Tests (`tests/unit/test_model_registry_hot_load.py`)
- 9 test suites covering SQLite CRUD, atomic rollback, 50D/70D hot-loading, verification battery, scaler inspection, fine-tuning with frozen backbones, REST endpoints, and CLI commands.
