# ML API Matrix — `/ml`

Every endpoint the page requests, probed independently with `curl` against the
live server (`127.0.0.1:8089`, `X-NSE-Token` bearer [REDACTED]) plus the code path
each response comes from. "Live" = measured on the running instance.

| # | Endpoint | Method | Live HTTP | Live response (summary) | Backend source | Runtime dependency | Failure behaviour |
|---|---|---|---|---|---|---|---|
| 1 | `/api/v1/model/identity` | GET | 200 | `model_id=primary_scalp_scalp_v3_70d`, `artifact_id=c9982ddde1755591`, `schema_id=scalp_v3`, `version=v1.0`, schema hash `235b8fccc96b7e0e` | v1 router → model registry | registry lookup | explicit `available:false` on lookup miss |
| 2 | `/api/models/integrity` | GET | 200 | `{"available":false,"state":"UNAVAILABLE"}` — **stable across 3 probes** | `model_governance_routes.get_models_integrity` | `app.state.engine` → `champion_manager.champion_or_none()` | `available:false` + `state` reason; `NO_CHAMPION` when manager exists but model is `None` |
| 3 | `/api/models/summary` | GET | 200 | `{"available":false}` | `get_models_summary` | engine + `model_lifecycle_orchestrator` attr | `{"available":false}` |
| 4 | `/api/models/champion` | GET | 200 | `{"available":false}` | `model_governance_routes` | champion manager | `{"available":false}` |
| 5 | `/api/models/shadow70/summary` | GET | 200 | `available:false` + store summary | `model_governance_routes` + `Shadow70Store.summary()` | audit repo (ops_shadow domain) | `available:false`; **D2 adds `monitor_state`** |
| 6 | `/api/models/shadow70/health` | GET | 200 | `{"available":false}` | `get_shadow70_health` | engine `_shadow70_health`/`_shadow70_drift`/`_shadow70_store` | `{"available":false}` |
| 7 | `/api/v1/shadow/status` | GET | 200 | `shadow_60d.runs`/`decisions` REAL; `shadow_70d` branch | `api_v1/shadow.py:shadow_status` | both stores' `summary()` | per-branch `_try` → `null` |
| 8 | `/api/v1/shadow/runs?page=1&page_size=15..20` | GET | 200 | real items (`run_id`, `status`) | `api_v1/shadow.py:shadow_runs` | `ShadowStore.list_runs` | `DEPENDENCY_UNAVAILABLE` |
| 9 | `/api/v1/shadow/70d` | GET | 200 | summary + disagreements + drift + health | `api_v1/shadow.py:shadow_70d` | `Shadow70Store` | per-branch `_try` → `null` |
| 10 | `/api/operator/calibration` | GET | 200 | `NOT_CALIBRATED`, artifact ABSENT, `INSUFFICIENT_EVIDENCE`, 32/22 vs 30 needed | `web/calibration_monitor.py` | calibration identity + audit | explicit state, never fake CALIBRATED |

### Stubs verified as stubs (not `/ml` contracts)

`/api/models/shadow/summary` → `{"available":false}`;
`/api/models/shadow70/health` → `{"available":false}`;
`/api/debug/snapshot` → 404; `/api/debug/state` → 200 with `engine_attached: true`;
`/api/trace/integrity` → `traces_scanned: 0`.

### Latency separation (Phase 16)

`requestMs: 15_000` / `mutationMs: 30_000` (`frontend/src/core/config.ts`) — the
15s figure is the transport budget, **not** an inference latency. Inference
latency is the backend `latency_ms` recorded per shadow70 observation
(`Shadow70Observation.latency_ms`), covering feature-build + tensor-prep +
forward + post-processing. API latency (round trip) is not reported anywhere as
model latency.

### Verdict per endpoint

* Truthful and real: 1, 7, 8, 9, 10.
* Truthful but blocked behind the D1 route-scope bug: 2, 3, 4, 5, 6 — these
  returned `available:false` because they resolved the **wrong app object**,
  not because the model/integrity/shadow subsystem was down. Fixed.
