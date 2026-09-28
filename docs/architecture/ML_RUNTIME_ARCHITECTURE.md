# ML Runtime Architecture (`/ml`) — verified map

Scope: `http://localhost:8089/ml` and every component the page needs to reflect
the actual ML runtime. Every claim below was traced to source and, where the
brief requires it, to a live probe.

## UI

| Layer | File | Role |
|---|---|---|
| Page | `frontend/src/pages/ML/MLPage.tsx` | Renders 9 polled contracts; no fabrication, markers `probs.available=false — not rendered as zeros` and `backend decides — not inferred from artifact presence` |
| ML API client | `frontend/src/api/mlApi.ts` | Legacy `/api/*` endpoints (no `/api/v1` prefix) |
| Shadow API client | `frontend/src/pages/_shared/edgeApi.ts` (`shadowApi`) | v1 shadow endpoints via `getV1` |
| Base client | `frontend/src/api/client.ts` | `getLegacy` / `getV1` split |
| Types | `frontend/src/types/domain.ts` (`ModelIntegrity`, `Shadow70State`), `frontend/src/pages/_shared/contracts.ts` (`Shadow70Block`, `Shadow70Summary`, `ShadowStatus`) |
| Transport | `frontend/src/core/transport.ts` + `config.ts` | `requestMs: 15_000`, `mutationMs: 30_000` (NOT 5s) |
| Realtime | `frontend/src/hooks/useRealtimeSnapshot.ts`, `frontend/src/core/realtime.ts` | SSE snapshot with `state_version` ordering — a late frame cannot overwrite a newer one |

## API — the 9 polled endpoints

| Endpoint | Backend | Failure behaviour |
|---|---|---|
| `/api/v1/model/identity` | v1 router, registry lookup | explicit `available:false` |
| `/api/models/integrity` | `model_governance_routes.get_models_integrity` | `{"available":false,"state":"UNAVAILABLE"}` |
| `/api/models/summary` | `model_governance_routes.get_models_summary` | `{"available":false}` |
| `/api/models/champion` | `model_governance_routes` | `{"available":false}` |
| `/api/models/shadow70/summary` | `model_governance_routes` + `Shadow70Store.summary()` | `available:false` + `monitor_state` |
| `/api/models/shadow70/health` | `model_governance_routes.get_shadow70_health` | `{"available":false}` |
| `/api/v1/shadow/status` | `api_v1/shadow.py` `shadow_status` | `_try` per store → `null` section |
| `/api/v1/shadow/runs` | `api_v1/shadow.py` `shadow_runs` | `DEPENDENCY_UNAVAILABLE` |
| `/api/v1/shadow/70d` | `api_v1/shadow.py` `shadow_70d` | per-branch `_try` |
| `/api/operator/calibration` | `web/calibration_monitor.py` | `NOT_CALIBRATED` + `INSUFFICIENT_EVIDENCE` |

## Health / Status

Engine attachment is a single site: `web/server.py:573` `app.state.engine = engine_ref`.
`/api/debug/state` reports `engine_attached: true`. **Route-level** engine
resolution is `_get_active_app(app)` in `model_governance_routes.py` — the D1
fix point (see Defects).

## Model Registry / Loader

- Registry: `model_lifecycle/registry.py` (`ModelLifecycleRegistry`) — backed by
  the audit repository.
- Champion: `model_lifecycle/champion.py` `champion_or_none()` — loads +
  verifies the artifact; returns `None` on cold start, never raises.
- Loader gates: `governance/load_gate.py` (`evaluate_load_gate`, `sha256_hex`).
- Serving bundle wiring: `application/live_engine.py` `_bundle`, with
  `_declared_contract_dim_for_path` resolving the sibling `model.scaler.npz`.

## Features / Tensor / Inference

- Contract: `features/schema_contract.py` — `DIMENSION=70`, `family_of(i)`
  → `BASE [0,50)`, `NEWS [50,60)`, `LIQUIDITY [60,70)`; `canonical_registry_json()`
  is the only representation hashed (schema hash `235b8fccc96b7e0e`).
- Vector build: market data → feature builder → families → validation
  (`schema_contract` raises on non-finite or out-of-`[-3,+3]`) → scaler → tensor.
- Model: `models/scalp_net.py` `ScalpNet`; artifact
  `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`.
- Warmup: `application/live/warmup.py`.
- Inference enable: computed in `live_engine` (no `warmup_state =` assignment
  site — derived state).

## Calibration

`model_lifecycle/calibration_identity.py` (`observability_block`) +
`web/calibration_monitor.py`. Artifact is fingerprint-bound to the serving
model; a mismatch is reported as MISMATCH, never as calibrated.

## Shadow

- 60D comparator: `shadow/store.py` (`ShadowStore`), `shadow/engine.py`
  (creates the run, `status="RUNNING"`), `shadow/worker.py`.
- 70D observer: `shadow/shadow70/{store,worker,runtime,health}.py`;
  monitors `Shadow70FeatureHealthMonitor` / `Shadow70DriftMonitor` on the
  engine as `_shadow70_health` / `_shadow70_drift`.
- Drift reference must be set via `Shadow70DriftMonitor.set_reference`;
  without it `summary()` returns `NO_REFERENCE_DISTRIBUTION`.

## Persistence

| State | Store | Provider |
|---|---|---|
| shadow runs / decisions / promotions | `shadow/store.py` | audit domain (SQLite file *and* PG `ops_shadow` domain) |
| shadow70 observations / events / health / drift | `shadow/shadow70/store.py` | audit domain (both) |
| calibration | `model_lifecycle` | audit domain |
| model metadata / registry | `model_lifecycle/registry.py` | audit domain |
| artifact files | filesystem | `artifacts/models/...` |

**Split-brain observed (live):** the local `artifacts/audit.db` (SQLite) and the
configured PostgreSQL audit both carry shadow tables, and they disagree by one
row — `test_run_pg_verify` exists **only in PG**. Both report 55342 decisions.
This is a provider divergence in the same logical store, not a UI bug.
