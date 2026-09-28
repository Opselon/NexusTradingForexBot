# Neural Studio — Architecture Map (Phase 0)

Evidence base: live engine probe `127.0.0.1:8089` (2026-09-28), `origin/main` @ `77eb21b1`,
`artifacts/models.db` registry dump (70 rows).

## What "Neural Studio" actually is

Two surfaces plus one backend module:

| Layer | Path | Notes |
|---|---|---|
| React feature | `frontend/src/features/model-studio/` | 6 panels + header + data hook; served at `/alt/model-studio` |
| Legacy console | `Web/model_studio_ui.js` (+ `Web/model_studio.html`) | served at `/model_studio_ui.js`; older parallel surface |
| Backend routes | `src/nexus_scalp/web/model_studio_routes.py` | all `/api/model-studio/**` endpoints |
| CLI | `src/nexus_scalp/cli/model_studio_commands.py` | thin wrapper over the same executors |
| Registry | `src/nexus_scalp/model_generation/model_registry.py` | SQLite `artifacts/models.db` (tables `model_checkpoints`, `model_load_history`) |
| Model | `src/nexus_scalp/models/scalp_net.py` | `ScalpNet(num_features, num_classes, hidden_dim, num_heads, dropout_rate)` |
| Feature contract | `src/nexus_scalp/features/schema_contract.py` + `features/schema.py` | `scalp_v1`=50D, `scalp_v3`=70D; `canonical_feature_names()` is the ordered 70-name tuple |

## Component inventory (all located)

- **Frontend page** — `ModelStudioPage.tsx` (458 lines, thin orchestrator)
- **API client** — `model-studio/api.ts` (134 lines)
- **Backend routes** — `model_studio_routes.py` (2789 lines, 36 endpoints)
- **Training service** — `execute_train()` in the routes module; **blocking**, synchronous
- **Trainer** — inline in `execute_train()`; no separate trainer object
- **Model factory** — `ScalpNet` constructor; `_resolve_head_classes()` for legacy 4-logit heads
- **Model loader** — `execute_hot_load()`; `_StudioBundleHolder` (active + canary slots)
- **Registry** — `ModelRegistry` (SQLite, `register/list/get/active/stage/history/delete/sync_filesystem_checkpoints`)
- **Scaler** — `.scaler.npz` sidecar (`mean`, `std`, `dimension`); `_StudioLoadedScaler`
- **Dataset service** — `_dataset_candidates()` / `_scan_available_datasets()` / `_safe_dataset_path()`
- **Normalization** — `execute_inspect_features()`, `extract_dataset_features()`
- **Inference** — `execute_predict()` (probabilities, entropy, OOD, policy simulation)
- **Explainability** — `_capture_layer_activations()` (forward hooks, removed in `finally`), `_compute_saliency()` (real backprop)
- **Persistence** — SQLite only. **No PostgreSQL path exists for the registry.**
- **Runtime router** — `web/server.py:2831` calls `register_model_studio_routes(app, _err, _log_err)`
- **Tests** — `tests/unit/test_model_studio.py`, `test_model_studio_pipeline.py`, `test_model_studio_dataset_order.py`, `test_model_registry_hot_load.py`, `test_model_lifecycle_phase10.py`

## Live runtime truth (probe, 2026-09-28)

```
GET /api/model-studio/overview
  active_schema_id      = scalp_v3
  effective_dimension   = 70
  model_source          = HOT_LOADED:train_studio_1789951009_70d
  architecture          = ScalpNet
  parameter_count       = 267,459
  device                = cpu
  scaler_stats.status   = READY  (clamped_cols = 25)

GET /api/model-studio/models → count 70, active_champion_id = train_studio_1789951009_70d
  dimension distribution: 65 × 50D, 5 × 70D
```

## Proven defects (this wave's scope)

1. **9 models report `final_loss = 0.0000`** — proven not a real metric: all 9 have
   `epochs = 0`, `dataset_path = ''`, `metrics = {}`, `manifest_path = ''`. They are
   register-but-never-trained records whose `0.0` *default* is displayed as a measurement.
   The other 61 models carry real losses (0.8–1.05) with epochs 2–3.
2. **No model builder.** There is no endpoint that accepts a full model configuration
   (architecture/hidden dims/dropout/optimizer/scheduler/early stopping/…) and no UI for it.
   `ModelStudioTrainRequest` exposes only dataset, dimension, epochs, batch_size, lr, seed.
3. **No OOS split.** `execute_train()` splits 80/20 train/val only.
4. **Training is blocking.** `POST /api/model-studio/train` runs the whole fit inline and
   returns only at completion; `train/progress` is a visual poll of a module-level dict.
5. **No state machine.** "LOADED" / "READY" / "INFERENCE AVAILABLE" / "ENGINE RUNNING" are
   not distinguished anywhere; the header derives all status from a single `activeModel`.
6. **No 50D/70D cross-contamination rejection at the contract boundary.** `execute_predict`
   silently zero-pads a 50D live vector to 70D (`features + [0.0]*20`).
7. **No switch-with-confirm**, no feature-contract endpoint, no per-model detail endpoint.
8. **232 lines of `model_studio_routes.py` changes are stranded on the stale
   `agent/feature/EUML-STUDIO` branch** (SEC stack-trace-exposure hardening). Its frontend
   changes already landed at main; the backend ones did not.

## What genuinely does not exist (documented gaps, out of scope)

- **Async training-job queue** (Phase 8–10). No run registry, no worker, no cancellation.
- **PostgreSQL persistence for registry/metrics/lineage** (Phase 36–37). `ModelRegistry`
  is SQLite-only; `DatabaseConfig.for_sqlite("models")` is the only constructor path used.
