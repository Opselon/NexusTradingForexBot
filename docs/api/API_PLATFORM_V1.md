# Nexus API Platform v1 — Design & Contract Spec of Record

TASK-API-PLATFORM · CHG-0043 · Owner: Hermes-Main (API Platform + Developer Experience)
Status: ACTIVE · This document is the binding spec for the `/api/v1` platform surface.
Every implementation detail below was verified against the actual codebase on 2026-09-02.

---

## 1. Purpose & Non-Goals

`/api/v1` is the **new, clean, typed, versioned API platform** for WEB CLIENT, CLI,
automation, internal tools, research, monitoring, AI agents and external developers.

- It is **additive**: zero changes to legacy endpoints (`/api/status`, `/api/models/*`, …).
  Legacy keeps its `{"available": .., "success": ..}` envelope and consumers.
- v1 uses its **own modern envelope** (§3) and its **own pagination model** (§4).
- **No fake data, ever.** If a dependency is unavailable the API says so truthfully
  (`RESOURCE_UNAVAILABLE` / `ENGINE_UNAVAILABLE` / `DEPENDENCY_UNAVAILABLE`, HTTP 503)
  with `retryable: true` where appropriate. No empty-success fabrication.
- No arbitrary SQL / filesystem / shell exposure. Reads go through the existing
  repository/store layer only.

## 2. Architecture

```
src/nexus_scalp/web/api_v1/
  __init__.py          # exports create_api_v1_router
  common.py            # envelope, errors, pagination, sanitization, engine/db accessors
  app.py               # build_api_v1_router(engine_ref) -> APIRouter (aggregates modules)
  system.py runtime.py market.py signals.py decisions.py positions.py
  risk.py execution.py model.py features.py shadow.py research.py
  incidents.py database.py config.py
```

- One router per domain, mounted under prefix `/api/v1/<domain>`, tags = domain names.
- Shared concerns live ONLY in `common.py` (no god router, no god schema).
- Wiring: `server.create_app` calls `app.include_router(create_api_v1_router(engine_ref))`
  once, immediately before `return app`. That is the ONLY `server.py` edit (2–4 lines).
- `api_v1` modules must NOT import `web.server` at module import time (cycle safety);
  late-import inside functions if ever needed.
- `app.state.audit_v1_repo`: lazily-built shared `AuditRepository(_default_audit_config())`
  (late import `from nexus_scalp.web.server import _default_audit_config`), cached on app state.

## 3. Envelope (success & error)

Success:
```json
{"data": <payload>, "meta": {"request_id": "req_ab12cd34", "generated_at": "2026-09-02T10:00:00+00:00"}}
```

Error (all non-2xx; never contains stack traces, paths, SQL or secrets):
```json
{"error": {"code": "RESOURCE_NOT_FOUND", "message": "...", "details": {},
           "request_id": "req_ab12cd34", "retryable": false}}
```

Codes → HTTP: `VALIDATION_ERROR`→422, `RESOURCE_NOT_FOUND`→404, `CONFLICT`→409,
`FORBIDDEN`→403, `ENGINE_UNAVAILABLE`→503, `DEPENDENCY_UNAVAILABLE`→503,
`RESOURCE_UNAVAILABLE`→503, `TIMEOUT`→504, `INTERNAL_ERROR`→500.
`retryable` is `true` only for the 503/504 family.

- `request_id`: reused from `request.state.request_id` (existing correlation middleware
  in `web/errors.py` sets it; response header `X-Request-ID` already flows).
- Timestamps: ISO-8601, timezone-aware, UTC (`+00:00`) in transport.
- Response helpers live in `common.py`: `ok(request, data)` and `fail(request, code, ...)`
  returning `JSONResponse`.

## 4. Pagination (one model, everywhere)

Query params: `page` (default 1) and `page_size` (default 50, cap 200; out-of-range →
`VALIDATION_ERROR` 422). A route fetches `page_size + 1` rows to compute:

```json
"data": {"items": [...], "page": 1, "page_size": 50, "has_more": true}
```

No total counts (protects against full-table scans); `has_more` is the contract.
Meta stays at envelope level. Implemented once in `common.py` (`paginate()`).

## 5. Filters (bounded, indexed-column only)

`symbol`, `action`, `status`, `execution_mode`, `decision_stage`, `lifecycle`,
`severity`, `category`, `component`, `strategy_id`, `hours_back` (bounded ≤ 720).
Filters translate to repository/store parameters or a parameterized `WHERE` clause —
never string-concatenated SQL. Unbounded queries are impossible (LIMIT always set).

## 6. Sanitization (secrets & leakage)

- `common.sanitize_config(obj)` recursively masks values of keys matching
  `token|password|secret|apikey|api_key|credential|login|account_(number|id)` → `"***"`.
  Applied to config/runtime/config-validate responses.
- Error paths: reuse `log_web_error` for internal detail; public body only carries
  `code/message/request_id`.
- No endpoint returns: broker credentials, tokens, filesystem paths (except the
  documented `db.filename` in `/api/v1/database/status`), or account login numbers.

## 7. Endpoint Inventory (all data sources verified)

UNAVAILABLE semantics: `None` values are honest "unknown"; when a whole source is
missing → 503 envelope. GET unless marked POST.

### system.py — prefix `/api/v1/system`
| # | Path | Source |
|---|------|--------|
|01| `GET /status` | composite: health verdict + version + mode + freshness.overall + counts |
|02| `GET /health` | `release.health.HealthEngine().overall()` (verbatim checks) |
|03| `GET /readiness` | HealthEngine: `ready = verdict != NOT READY and no FAIL`; layers list |
|04| `GET /version` | `release.metadata.get_version_info()` |
|05| `GET /runtime` | engine: mode/`_running`/`warmup_state`/`_inference_enabled` + freshness |
|06| `GET /capabilities` | static-true inventory of v1 domains + count + api version (built from the real mounted route table) |
|07| `GET /workers` | engine worker refs (accounting/incident/hygiene/model-lifecycle) state via getattr, honest when absent |
|08| `POST /refresh` | re-read adapter account/symbol snapshots; bump `versioner`; idempotency-key honored (Idempotency-Key header echoed in meta) |
|09| `POST /diagnostics/run` | `observability.selftest.run_observability_selftest()` (bounded, offline) |
|10| `GET /diagnostics` | adapter `diagnostics_summary()` + last selftest result (cached in app.state) + db quick facts |

### runtime.py — prefix `/api/v1/runtime`
|11| `GET /mode` | engine.config.execution.mode + effective `_runtime_mode` + replay state |
|12| `POST /mode/validate` | proposed mode ∈ ExecutionMode; transition validity vs current; warnings; **no apply** |
|13| `POST /mode/preview` | impact matrix (which subsystems a change would touch: execution, inference, data feed) |
|14| `GET /freshness` | `engine.compute_live_freshness()` full contract |

### market.py — prefix `/api/v1/market` (503 `ENGINE_UNAVAILABLE` when no engine)
|15| `GET /snapshot` | `adapter.get_account_snapshot()` + `get_symbol_snapshot(symbol)` + position count |
|16| `GET /quote` | `adapter.get_broker_tick(symbol)` (bid/ask/spread/freshness/stale) |
|17| `GET /bars` | `aggregator.get_completed_bars()` (engine-local, bounded `limit` ≤ 500) — truthful scope note in response |
|18| `GET /regime` | `engine._last_regime_state` + `regime_classifier` id when available; UNAVAILABLE before first inference |
|19| `GET /symbols` | configured symbol(s) from engine.config.mt5 + spec block |

### signals.py — prefix `/api/v1/signals` (AuditRepository)
|20| `GET /latest` | `get_recent_predictions(limit=1)` → row + parsed payload |
|21| `GET /history` | paginated `audit_signals` via repo query w/ `symbol`/`action` filters |

### decisions.py — prefix `/api/v1/decisions` (audit_signals; decision_id = `request_id`)
|22| `GET /latest` | latest row, full summary (action, confidence, regime, gates, risk_checks from payload) |
|23| `GET /` | paginated list, filters: symbol/action/execution_mode/decision_stage/hours_back |
|24| `GET /{decision_id}` | detail by request_id |
|25| `GET /{decision_id}/evidence` | `payload_parsed` verbatim (sanitized of secret-shaped keys) |
|26| `GET /{decision_id}/gates` | structured gate trace: decision_stage, blocked_by, reason_code, guardian_status, risk_allowed → ordered gate list |
|27| `GET /{decision_id}/explanation` | human-readable sentence composition from the SAME real fields (no invented rationale) |
|28| `GET /stats` | SQL GROUP BY action/decision_stage over bounded window |
|29| `GET /no-trade` | NO_TRADE analytics: counts + `rejection_reason`/`reason_code` distribution |

### positions.py — prefix `/api/v1/positions` (adapter + audit_ledger)
|30| `GET /` | `adapter.get_all_positions()` (PositionSnapshot fields) |
|31| `GET /{ticket}` | single position lookup |
|32| `GET /history` | paginated `get_ledger_trades(status_filter=…)` |
|33| `GET /pending` | `adapter.get_pending_orders_snapshot()` |
|34| `GET /{ticket}/context` | `get_ledger_row(ticket)` + `get_broker_deals_for_position(ticket)` |

### risk.py — prefix `/api/v1/risk`
|35| `GET /status` | last proposal `risk_checks` + guard telemetry + engine risk config block (sanitized) |
|36| `GET /summary` | aggregate exposure from open positions (volume/profit by symbol) + account margin metrics |

### execution.py — prefix `/api/v1/execution`
|37| `GET /status` | mode, adapter connection state, pending order count, last order event |
|38| `GET /history` | paginated `get_recent_order_events()` |

### model.py — prefix `/api/v1/model`
|39| `GET /status` | engine._bundle identity + `_inference_enabled` + load state |
|40| `GET /identity` | bundle manifest fingerprint (feature_schema_hash, training_dataset_id, artifact path-shape only) |
|41| `GET /contracts` | `features.schema_contract`: SCHEMA_ID, expected_schema_ids, registry hash |
|42| `GET /champion` | governance registry champion entry (read-only) |

### features.py — prefix `/api/v1/features`
|43| `GET /status` | warmup + `_last_fv` availability + missing_features from last proposal |
|44| `GET /contract` | canonical feature names by family (base/news/liquidity) + dimension + hash |
|45| `GET /current` | `engine._last_fv` dimension + family breakdown (values vector, honest absence) |

### shadow.py — prefix `/api/v1/shadow` (ShadowStore + Shadow70Store)
|46| `GET /status` | both stores' `summary()` |
|47| `GET /runs` | paginated `list_runs()` |
|48| `GET /runs/{run_id}` | `get_run` + `get_comparison` + `get_promotion` |
|49| `GET /decisions` | paginated `list_decisions(run_id=…)` |
|50| `GET /70d` | shadow70 summary + disagreement_counts + latest drift alerts + feature health |

### research.py — prefix `/api/v1/research` (research/store + registry)
|51| `GET /status` | `research_health_summary` + `registry_summary` |
|52| `GET /strategies` | paginated `StrategyRegistry.list(lifecycle=…)` |
|53| `GET /strategies/{strategy_id}` | `StrategyRegistry.get` + invariant_check |
|54| `GET /runs` | paginated `list_research_runs(strategy_id=…)` |
|55| `GET /datasets` | distinct dataset_id + provenance derived from research_runs (real rows only) |

### incidents.py — prefix `/api/v1/incidents` (IncidentStore)
|56| `GET /` | paginated `list_incidents(status/severity/category/component)` |
|57| `GET /{incident_id}` | `store.get` |
|58| `GET /{incident_id}/timeline` | incident.timeline events (as_dict) |
|59| `GET /stats` | `count()` + `stats_by_component()` + `recurring_fingerprints()` |

### database.py — prefix `/api/v1/database`
|60| `GET /status` | provider, db filename, size bytes, table count (via existing db config helpers) |
|61| `GET /integrity` | `PRAGMA quick_check` + bounded table row counts (read-only) |
|62| `GET /tables` | table inventory + row counts (bounded to non-system tables) |

### config.py — prefix `/api/v1/config`
|63| `GET /` | effective `engine.config` serialized + **sanitized** |
|64| `GET /schema` | pydantic model_fields inventory of AppConfig sub-models (name/type/required) |
|65| `POST /validate` | validate proposed partial config dict against the real pydantic models; **no apply**; 422 on invalid |

**Total: 65 v1 capabilities.**

## 8. Mutation Rules

Only three mutations exist in v1 and all are safe/idempotent-friendly:
`POST /system/refresh`, `POST /system/diagnostics/run`, `POST /config/validate`,
`POST /runtime/mode/validate|preview` (pure functions, no state change).
`Idempotency-Key` header is accepted on all POSTs and echoed in `meta.idempotency_key`.
NO trading mutations in v1 (execution toggle/mode-change remain legacy+CLI owned).

## 9. Testing Contract

- `tests/unit/test_api_v1_contracts.py` — envelope/pagination/sanitize/validators (no server).
- `tests/integration/test_api_v1_platform.py` — `TestClient(create_app(engine))`:
  - engine=None paths → truthful 503s for MT5-bound routes, 200 for DB-backed ones
    (repo seeded with deterministic fixtures via real `AuditRepository`).
  - happy path + validation failure (422 envelope) + not found (404) + pagination
    (has_more math, boundary pages no dup/skip) + filters + empty dataset + secret-scan
    (assert no `token/password/secret` literals in any sampled v1 response).
- Bounded generative (no new deps): fixed-seed randomized pagination/filter combos,
  asserting invariants (no dup, no skip, deterministic order, valid envelope).
- Performance smoke: `scripts/dev/api_benchmark.py` (median/p95 on core read routes).
- Do NOT add v1 tests to `tests/critical_suite.txt` (gate budget unchanged).

## 10. Tooling & DX Deliverables

- `scripts/dev/api_smoke.py` — domain-by-domain smoke (system→…→diagnostics), `API_SMOKE = PASS`.
- `scripts/dev/api_contract_check.py` — OpenAPI completeness gate: all 65 paths documented,
  tags, examples present for core routes; exit non-zero on drift.
- `scripts/dev/api_openapi_snapshot.py` — deterministic snapshot (sorted JSON) →
  `artifacts/api/openapi_snapshot.json`.
- `scripts/dev/api_openapi_diff.py` — old vs new snapshot; detects removed endpoints,
  removed required fields, enum narrowing → exit 2 on breaking (CI-usable).
- `scripts/dev/api_benchmark.py` — bounded local load (TestClient, ~200 reqs/route,
  read-only routes only).
- `src/nexus_scalp/api_client.py` — httpx-based `NexusApiClient` (typed methods for the
  domains above; envelope/error aware; `base_url` param).
- CLI `nexus api` group (Typer, registered in `cli/api_commands.py` + app_factory):
  `nexus api status|health|version|mode|capabilities|get PATH|smoke` — consumes the
  running HTTP API via `NexusApiClient` (same contracts; no logic duplication).
  Default base `http://127.0.0.1:8080`, override `--base-url` / `NEXUS_API_BASE`.
- Docs: `docs/api/API_REFERENCE.md` (getting started, auth=none-local, errors,
  pagination, filtering, timestamps, examples verified against TestClient).
- CI: new `api-platform` job in `.github/workflows/ci.yml` running
  `api_contract_check.py` + unit contracts (additive, last step of rollout).

## 11. Definition of Done

1. All 65 endpoints implemented per §7, envelope/pagination/errors per §3–5.
2. `ruff check` + `ruff format --check` + `mypy src` clean.
3. New tests green locally (unit + integration), no legacy test touched.
4. OpenAPI contains all 65 paths with tags + summaries.
5. Tools in §10 exist and run clean; smoke = PASS.
6. `server.py` diff limited to the include_router wiring block.
7. Docs examples actually executed (TestClient) during generation.
