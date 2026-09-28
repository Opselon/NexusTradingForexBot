# Neural Studio — API Matrix (Phase 1)

Enumerated from `register_model_studio_routes()` at `origin/main` @ `77eb21b1`.
DB column = the persistence actually consulted. Runtime column = whether the live
engine process is consulted (vs a stateless fresh instance).

## Existing endpoints (28)

| # | Method | Path | Real backend operation | DB source | Runtime source | UI consumer |
|---|---|---|---|---|---|---|
| 1 | GET | `/api/model-studio/overview` | Architecture summary, weights SHA256, scaler stats, dataset inventory | — (reads fs) | engine bundle + hot-loaded slot | Header KPI deck |
| 2 | GET | `/api/model-studio/fetch-70d` | Assembles live Base50+News10+Liq10 vector | — | engine `_last_fv`, news engine, liquidity governor | Inference panel (70D) |
| 3 | POST | `/api/model-studio/predict` | Real inference: probs, entropy, OOD, policy sim, layer norms, saliency | — | hot-loaded slot / LIVE_BUNDLE / fresh random-init | Inference panel |
| 4 | GET | `/api/model-studio/datasets` | Lists selectable parquet/csv (granularity-first ranking) | — (fs scan) | — | Dataset selectors |
| 5 | POST | `/api/model-studio/datasets/download` | Ingest candles (synthetic/mt5/csv) | — | — | Dataset pipeline |
| 6 | POST | `/api/model-studio/datasets/inspect-features` | Per-feature μ/σ, clamp, zero-variance, NaN | — | — | Feature inspector |
| 7 | POST | `/api/model-studio/position-dataset/generate` | Layer-2 position-state dataset build | — | optional model+scaler | Position dataset panel |
| 8 | POST | `/api/model-studio/position-dataset/validate` | Position dataset structural/causal validation | — | — | Position dataset panel |
| 9 | POST | `/api/model-studio/train` | **BLOCKING** full fit + save + register | models.db (write) | — | Train controls |
| 10 | GET | `/api/model-studio/train/progress` | Reads module-level `_STUDIO_TRAIN_STATE` | — | — | Train progress |
| 11 | POST | `/api/model-studio/stress-test` | Adversarial battery (zero-variance, shock, NaN, dim boundary) | — | hot-loaded/fresh | Stress bench |
| 12 | POST | `/api/model-studio/benchmark` | N-pass latency P50/P90/P99/throughput | — | hot-loaded/fresh | Stress bench |
| 13 | GET | `/api/model-studio/models` | Registry catalog + fs sync | models.db (R/W) | hot-loaded slot for `is_active` | Registry panel |
| 14 | POST | `/api/model-studio/models/hot-load` | Load weights+scaler, warm, atomic slot swap | models.db (R/W) | engine `_bundle` also swapped | Registry panel |
| 15 | GET | `/api/model-studio/models/active` | Active slot; auto-restores from DB champion | models.db (R/W) | hot-loaded slot | Header champion ribbon |
| 16 | POST | `/api/model-studio/models/rollback` | Restore previous champion from history | models.db (R/W) | — | Registry panel |
| 17 | POST | `/api/model-studio/models/fine-tune` | Fine-tune from base, freeze policy, new identity | models.db (write) | — | Registry panel |
| 18 | POST | `/api/model-studio/models/verify` | 6-check battery (file, deser, finite, variance, load, smoke) | models.db (read) | — | Registry panel |
| 19 | POST | `/api/model-studio/models/register` | Register an external checkpoint | models.db (write) | — | — |
| 20 | DELETE | `/api/model-studio/models/{model_id}` | Delete record (guarded vs active) | models.db (write) | — | — |
| 21 | GET | `/api/model-studio/models/{model_id}/scaler` | Scaler μ/σ per slot | models.db (read) | — | Registry panel |
| 22 | POST | `/api/model-studio/models/canary` | Load candidate into canary slot | models.db (write) | canary slot | — |
| 23 | GET | `/api/model-studio/models/history` | Hot-swap/rollback audit trail | models.db (read) | — | — |
| 24 | POST | `/api/model-studio/models/export` | Zip weights+scaler+manifest | models.db (read) | — | — |
| 25 | POST | `/api/model-studio/models/tag` | Stage / fine-tune permission | models.db (write) | — | — |
| 26 | POST | `/api/model-studio/models/benchmark-live` | Latency of the **hot-loaded** model | — | hot-loaded slot | — |
| 27 | POST | `/api/model-studio/models/drift-check` | Scaler-vs-dataset drift | — | hot-loaded slot | — |
| 28 | GET | `/api/model-studio/artifact-locations` | Where artifacts land on disk | — | — | Artifact panel |

## Failure-state contract (today)

- Routes 9 (`train`), 10 (`progress`), 17 (`fine-tune`), 19, 21, 24, 25, 27 return
  structured `HTTPException` JSON (4xx) — good.
- Route 3 (`predict`) catches `ValueError` → 422. **Other exceptions propagate as 500
  with a raw message** — the stranded EUML-STUDIO hardening fixes this class for
  `_compute_saliency` and `fetch_70d_components`.
- Route 14 (`hot-load`) is wrapped in try/except → 500 JSON.
- Route 15 (`active`) **silently swallows** auto-restore failure into a `logger.warning`
  and returns `NO_ACTIVE_MODEL` — a failed champion is indistinguishable from "none set".
- Route 10 (`train/progress`) can only reflect the LAST run; no run registry, so it cannot
  distinguish "run X cancelled" from "run X never existed".

## New endpoints added by this wave

| # | Method | Path | Purpose | New DB |
|---|---|---|---|---|
| N1 | GET | `/api/model-studio/feature-contract` | The actual schema (names/families/dtype/ordering) — no frontend hardcoded copy | — |
| N2 | GET | `/api/model-studio/model-builder/options` | Only the options the backend supports (real ScalpNet + trainer capabilities) | — |
| N3 | POST | `/api/model-studio/model-builder/preflight` | Validate a full config: schema/dim/dataset/scaler compatibility + dataset compatibility | models.db (read) |
| N4 | POST | `/api/model-studio/model-builder/save` | Persist the exact config (reproducibility bundle) | models.db (write) |
| N5 | GET | `/api/model-studio/model-builder/configs` | List saved configs | models.db (read) |
| N6 | GET | `/api/model-studio/models/{model_id}/detail` | Full per-model record + metrics + training config + lineage | models.db (read) |
| N7 | POST | `/api/model-studio/models/switch` | Verify-then-activate with explicit confirmation token | models.db (R/W) |
| N8 | GET | `/api/model-studio/models/switch/preview` | Pre-switch validation report (schema/scaler/load compatibility) | models.db (read) |
| N9 | GET | `/api/model-studio/tensor/inspect` | Raw / normalized / model-input triple with slot-level validity | — | hot-loaded slot |
| N10 | GET | `/api/model-studio/runtime/state` | **The three-state machine**: engine / model / inference as separate fields | models.db (read) | engine + hot-loaded slot | Header + registry |

## Database dependency summary

- **SQLite** (`artifacts/models.db`) — registry, metrics, load history. Only store.
- **PostgreSQL** — **not used by Neural Studio at all.** The registry has no PG path.
  This is a documented gap (Phase 36–37), not a silent omission.
