# docs/historical/root-contracts/PROVISIONING_API_MATRIX.md

Every provisioning endpoint, individually contract-tested. Live probes were run
against the running engine at `127.0.0.1:8089` (auth: `NSE_WEB_AUTH_TOKEN` from
the repo-root `.env`, `Authorization: Bearer <token>`).

Legend: **P0** = fix-blocking, **OK** = works as documented.

## Provisioning routes (`web/provisioning_routes.py`)

| Endpoint | Backend exists | UI caller | Test | Success | Error path | DB | Runtime | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `GET /api/provisioning/status` | yes (`provisioning_status`) | `refreshStatus` | live curl ×3 | 200 in **0.10–0.27 s** | `PROVISIONING_STATUS_ERROR` | none | `FirstRunCoordinator.slot()` + `read_provisioner_state()` | **OK** (30 s timeout NOT reproduced — see note) |
| `GET /api/provisioning/environment` | yes (`provisioning_environment`) | `refreshEnv` | live curl, `?backend=auto\|cpu\|cuda` | 200 in **0.20 s** (fast path) / **4.07 s** (full cuda probe with application+smoke) | `TRAIN_BACKEND_INVALID`, typed `TrainingEnvironmentError` | none | subprocess python/torch probes + `run_application_probe` + in-process smoke | **OK** |
| `POST /api/provisioning/environment/install` | yes (`provisioning_environment_install`) | `onInstall` | not exercised (mutating) | n/a | `TRAIN_BACKEND_INVALID`, `TRAIN_ALREADY_RUNNING`, `INSTALL_ALREADY_RUNNING`, `OFFICIAL_ALREADY_RUNNING`, typed install codes | none | pip install in resolved env | **OK** (contract-verified read-only; install not run — explicit operator action) |
| `POST /api/provisioning/official` | yes (`provisioning_official`) | `onOfficial` | live curl | 200, `MODEL_INSTALL_ENGINE_RUNNING` (correct: engine running) | `OFFICIAL_BUNDLE_REJECTED` (typed step code), `PROVISIONING_OFFICIAL_ERROR` | `write_provisioner_state` | download → verify → atomic install under slot lock | **OK** — blocked only by the engine-running guard |
| `POST /api/provisioning/train/start` | yes (`provisioning_train_start`) | `onTrain` | schema validated | 200 `{"success":true,"started":true}` on the happy path | `TRAIN_INPUT_REJECTED`, `TRAIN_BACKEND_INVALID`, `TRAIN_ALREADY_RUNNING`, `TRAIN_WORKER_START_ERROR` | `write_provisioner_state(TRAINING)` | env gate → dataset → `train_local_model` in `nexus-local-train` thread | **GAP** — no `training_run_id` returned; job state is in-memory only |
| `GET /api/provisioning/train/progress` | yes (`provisioning_train_progress`) | `useTrainPoll` (2 s poll) | live curl (idle) | 200 in **0.006 s**, `{active:false, events:[], result:{}}` | none (always 200) | `read_provisioner_state()` on idle | in-memory `_ACTIVE` event list | **GAP** — no run_id / epoch / percent / loss / phase; events carry stage+status+message only |
| `POST /api/provisioning/train/cancel` | yes (`provisioning_train_cancel`) | `onCancel` | live curl (idle) | 200 `{cancel:"NO_ACTIVE_RUN"}` | none | none | sets `threading.Event` | **GAP** — returns `REQUESTED` immediately, NOT after worker ack |
| `GET /api/provisioning/datasets` | yes (`provisioning_datasets`) | dataset browser effect | live curl | 200 in **0.04 s**, 7 files / 2 roots; missing root = `exists:false` | `DATASETS_ROOT_REJECTED`, `PROVISIONING_DATASETS_ERROR` | none | stat-only recursive scan, capped 500/root | **OK** |

## Measured latency budgets (live)

```
status                 0.10 s / 0.27 s        (no expensive scan; no DB)
environment  auto/cpu  0.20 s                (torch import, no smoke)
environment  cuda      4.07 s                (application probe + CUDA smoke)
datasets               0.04 s                (7 files)
train/progress         0.006 s               (idle)
train/cancel           0.008 s               (no run)
```

**Phase 3 note — the 30 s status timeout.** NOT reproduced against the current
process: `status` answers in well under 300 ms and performs no artifact scan and
no DB query. Two confirmed contributors explain the original report:

1. The server that produced it was booted on an interpreter whose torch was
   `2.13.0+cpu` (torch dist-info mtime `Sep 28 02:58`; the process was
   superseded). The environment probe under `cuda` runs an application probe
   plus a CUDA smoke allocation — that is the multi-second path, and a stale
   `TORCH_LOCAL_TAG_MISMATCH` is the tell, not a status timeout.
2. `environment` under `cuda` performs the heavyweight chain by design
   (subprocess python probe → application probe → CUDA smoke). Polling THAT on
   the UI cadence is the real timeout risk; `status` is already fast.

So the durable fix is Phase 4: keep `status` cheap and make the heavyweight
discovery explicit and on-demand — not a per-poll cost.

## Discovered model-management APIs (existing — do NOT duplicate)

These already implement most of the requested lifecycle. The work is to
**surface** them on `/provisioning`, not to re-create them.

| API | Method | Purpose | Live result |
| --- | --- | --- | --- |
| `/api/models` | GET | model inventory | 200 `{"available":true,"models":[]}` — **empty** (the inventory seam is wired but unused) |
| `/api/models/summary` | GET | runs/comparisons/registry/worker/champion roll-up | 200, provider=postgresql, 6 COMPLETED runs, registry empty |
| `/api/models/champion` | GET | WHO IS ACTIVE | 200 `primary_scalp` v1.0, 70D, hash `c9982ddde1755591` |
| `/api/models/integrity` | GET | artifact+scaler+dim verification | 200 `VALID/VALID`, `state=ACTIVE`, dim 70/70 |
| `/api/models/challengers` | GET | challenger list | 200 `[]` |
| `/api/models/governance/registry` | GET | registry categories | 200 `{"categories":{},"models":[]}` |
| `/api/models/runs` | GET | training runs | present |
| `/api/models/shadow/*` | GET/POST | shadow attach/start/stop/detach/promotion | present |
| `/governance/engine.py::rollback()` | — | rollback authority | used by `command_intent._intent_rollback` (Telegram intent, not web) |

## Gaps to close (mapped to contract phases)

| # | Gap | Phase |
| --- | --- | --- |
| G1 | `train/start` returns no `training_run_id`; job state is in-memory | 25, 43 |
| G2 | `train/progress` has no epoch/percent/loss/phase/OOS — only events | 26 |
| G3 | `train/cancel` acknowledges before the worker observes the cancel | 28 |
| G4 | No model inventory on `/provisioning` (`/api/models` is empty + unwired to this page) | 13, 32 |
| G5 | No LOAD / ACTIVATE / SWITCH / ROLLBACK buttons on `/provisioning` (authority exists in `governance/engine.py` + `command_intent`) | 14–17 |
| G6 | No model lifecycle event log surface | 56 |
| G7 | Environment `cuda` probe is heavyweight and runs per UI backend-switch | 4 |
| G8 | `imports` root missing is already correct (`exists:false`) — no defect | 21, 22 |
