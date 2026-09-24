# NSE API → Golang Migration — Forensic Inventory & Plan

> Branch: `agent/api-golang-migration` · Worktree: `../nse-api-golang-migration`
> Base: `origin/main` @ `cbabadae` (2026-09-24)
> Task ID: **API-GO-MIGRATION-001**
> Status: PHASE 4 (forensic inventory) COMPLETE — Python reference implementation
> UNTOUCHED. No production routing modified.

## 1. Evidence base (how these numbers were produced)

Every number below comes from **executed code**, not documentation:

| Evidence | Method | Artifact |
|---|---|---|
| Ground-truth route table | Instantiated `create_app()` + `create_v1_app()` and dumped `app.router.routes` | `routes_ground_truth.json` |
| Source map | AST scan of all 663 `src/**/*.py` for route decorators + imperative `add_api_route`/`mount` | `api_registry.json` |
| React consumers | Path-literal scan of `frontend/src/api/*.ts` + `core/*.ts` (centralized barrel) | `react_endpoints.json` |
| Unified manifest | Merge of the three, keyed on (method, path) | `api/migration/inventory.yaml` |

The route dump is authoritative: 14 router modules register **imperatively**
(`register_*(app, …)` factories that build routers internally and call
`app.include_router` / `app.add_api_route` at runtime), so a decorator-only AST
scan sees ~50% of the surface. The merge closes that gap.

## 2. Original API inventory

| Surface | Count |
|---|---|
| **Total unique operations** (HEAD/OPTIONS excluded) | **442** |
| Unique paths | 437 |
| HTTP operations | 438 |
| SSE endpoints | 2 (`/api/ticks/stream`, `/api/trace/…` SSE route) |
| WebSocket endpoints | 2 (`/web`, `/ws` — one handler, two paths) |
| Static mounts | 2 (`/alt` SPA, plus root static bundle) |
| Domains | 99 |
| Operations with ≥1 React consumer | 108 |

Method distribution (dashboard+v1): GET 396 · POST 133 · DELETE 2 · PUT 1 · WS 2 · MOUNT 2.

### React compatibility surface

The React frontend (`frontend/`, served at `/alt`) is the client of record. It
has a **centralized API barrel** — raw `fetch()` never appears outside
`core/transport.ts` + `core/middleware.ts` — with 24 typed modules:

```
frontend/src/api/{accounting,audit,commandCenter,config,db,debug,engine,
governance,incidents,indicators,intelligence,liquidity,market,marketplace,
ml,news,positions,replay,research,risk,rules,trading}Api.ts
```

Two envelope families, which Go MUST reproduce byte-for-byte:

- `/api/v1/*` → `{data, meta}` envelope, unwrapped by `getV1`
- `/api/*` (legacy) → raw JSON, unwrapped by `getLegacy`
- SSE `/api/ticks/stream` → `event: state|tick|heartbeat`, monotonic
  `state_version`, out-of-order drop guard, 5s heartbeat, full-state every 30 cycles

**Coverage gap: ZERO.** All 4 "orphan" paths flagged by the extractor were
proven to be artifacts (base-URL constants used with suffix concatenation,
plus one template-literal suffix), resolved by prefix matching against the
real route table.

## 3. Contract freeze (must be replicated exactly)

### Auth (WEB-AUTH-P0) — `web/auth.py`
Fail-closed. Token sources, first wins: (1) `NSE_WEB_AUTH_TOKEN` env,
(2) `SecureSecretStore["web_auth_token"]` (DPAPI on Windows), (3) generated
once + persisted + logged at WARNING. No anonymous mode. Constant-time
compare (`hmac.compare_digest`). Accepted on: `Authorization: Bearer`,
`X-NSE-Token`, `?token=` (kept for EventSource). **LIVE mode always requires
auth**, never fails open. `PUBLIC_PATHS` allowlist = static shells only
(`/`, `/index.html`, `/alt`, `/alt/`, `/app.js`, `/styles.css`, fonts…);
every `/api/**` route enforces. Bootstrap cookie issued on the public
documents.

### v1 error envelope — `web/api_v1/common.py` + `errors.py`
`{data, meta}` success / `{error}` failure. Error code family (code →
HTTP status, retryable): `METHOD_NOT_ALLOWED` 405, `PAYLOAD_TOO_LARGE` 413,
`VALIDATION_ERROR` 422, `RESOURCE_NOT_FOUND` 404, `CONFLICT` 409,
`FORBIDDEN` 403, `ENGINE_UNAVAILABLE` 503✓, `DEPENDENCY_UNAVAILABLE` 503✓,
`RESOURCE_UNAVAILABLE` 503✓, `TIMEOUT` 504✓, `INTERNAL_ERROR` 500.
Fields: `code`, `message`, `details`, `request_id`, `retryable`. **No stack
traces, no internal text.** Validation errors bounded to 20 field summaries
(`field`, `issue`, `input_present`). Legacy routes keep FastAPI's default
422 `{"detail": [...]}` body — path-guarded, do not unify.

### Serialization — `web/server.py` `canonical_json`
datetime → ISO-8601 (naive stamped UTC), date → isoformat, Enum → `.value`,
bytes → utf-8/replace, Decimal → float, UUID → str, Path → str, numpy
scalar/ndarray → item()/tolist(), `.isoformat()` duck-type → str, unknown →
`TypeError` (SSE emits `SSE_SERIALIZATION_ERROR` rather than corrupt JSON).

### Correlation
`X-Request-ID` response header on every request. Resolution order:
middleware `request.state.request_id` → `X-Request-ID` request header → new id.

### Database — canonical provider resolution (DO NOT bypass)
`database.provider` + `database.postgresql_config` persisted in
`application_settings` (table `application_settings`, **not** `settings`) in
`AppData/Local/NexusScalpEngine/databases/app_settings.db`. Resolution order:
env → persisted setting → default. Go must never silently fall back
PostgreSQL→SQLite, never create duplicate schemas, never bypass canonical
migrations. `nexus db status` CLI is a SEPARATE engine from the runtime PG
fabric path — version numbers are not comparable across them.

### MT5 / model safety
PAPER remains the safe default; LIVE is explicit and always token-authenticated.
Go never creates live trading behavior, never bypasses risk gates or execution
policy. Python stays the authoritative ML runtime; Go only exposes/controls
it through a stable boundary.

## 4. Migration order (phases)

| Phase | Scope | Ops | Rationale |
|---|---|---|---|
| **A** | health/readiness/version/metadata, `/api/v1/system` | ~14 | zero dependencies, pins framework + envelope + auth first |
| **B** | read-only APIs (`/api/v1/*` read routes, `/api/status`, `/api/mt5/status`, market/indicators) | ~200 | highest React value, no side effects |
| **C** | configuration/control (`/api/algo/config` GET/PUT, runtime-config, settings) | ~20 | mutation — needs DB write parity |
| **D** | runtime state (`/api/engine/*`, `/api/live/*`, diagnostics) | ~30 | engine-bound, needs Python↔Go boundary |
| **E** | event APIs (`/api/ticks/stream` SSE, `/api/trace/*` SSE) | 2 | streaming: bounded buffers, backpressure, reconnect |
| **F** | WebSocket (`/web`, `/ws`) | 2 | dual transport; broadcast + cleanup |
| **G** | model/control-plane (`/api/models/*`, model-studio, governance) | ~80 | ML boundary — Python remains authoritative |
| **H** | database/control-plane (`/api/db/*`, db-console) | ~25 | canonical DB access, read-only first |
| **I** | MT5/runtime integration (`/api/mt5/*`, replay, simulation) | ~12 | highest care; no live trading |
| **J** | high-risk mutations (`/api/positions/close|modify`, engine toggle) | ~10 | irreversible; shadow-mode only |
| **K** | remaining legacy/edge (static bundle, command-center, debug) | ~35 | lowest risk |

Phases may reorder after contract extraction per endpoint. Every deviation
will be recorded in `api/migration/` with its reason.

## 5. Endpoint state machine

`DISCOVERED → CONTRACT_LOCKED → GO_IMPLEMENTED → UNIT_TESTED →
CONTRACT_TESTED → PARITY_VERIFIED → BENCHMARKED → INTEGRATED →
SHADOW_VERIFIED → CUTOVER_READY → MIGRATED`

`api/migration/inventory.yaml` carries the per-endpoint `state:` field.
Nothing advances without executable evidence. "Compiles" is not MIGRATED.

## 6. Python ↔ Go boundary (decision)

**Chosen: localhost HTTP + shared database, no gRPC, no per-request subprocess.**

Evidence: the FastAPI app already binds a single port with SSE/WebSocket on
the same process; the React client is transport-agnostic; and the DB provider
is resolved from a canonical settings row both runtimes can read.

- **Phase A–C** (Go standalone): Go serves the read-only + envelope + auth
  surface directly, reading the canonical DB and shared artifacts. No Python
  process needed.
- **Phase D onward**: Go proxies engine-bound calls to the Python runtime over
  localhost HTTP with bounded timeouts + circuit breaker. Go never spawns a
  Python subprocess per request.
- **Shadow mode**: `API_BACKEND=shadow` fans safe read requests to both,
  compares outputs, logs parity diffs. Mutation endpoints are shadowed by
  explicit design only — never double-executed (no duplicate trades, no
  duplicate irreversible actions).

Rollback at any point: `API_BACKEND=python`.

## 7. Go architecture (go-api/)

```
go-api/
  cmd/nexus-api/main.go              # single entrypoint, graceful shutdown
  internal/
    api/{handlers,middleware,routes,validation}/
    application/{services,commands,queries}/
    domain/{models,errors}/
    infrastructure/{postgres,sqlite,mt5,python,filesystem,config}/
    events/{broker,subscriptions}/    # bounded queues, backpressure
    runtime/{lifecycle,readiness,health}/
    observability/{logging,metrics,tracing}/
    security/{auth,redaction}/        # constant-time, PUBLIC_PATHS allowlist
  pkg/contracts/                      # frozen request/response types (pkg/)
  tests/{contract,integration,parity,benchmark}/
```

Stack: `net/http` + `http.ServeMux` (Go 1.22+ pattern routing) — no heavy
framework without measured justification. Every dependency must earn its place.

## 8. Performance discipline

Baseline measured FIRST (Python p50/p95/p99, rps, RSS, CPU), then target, then
achieved. Benchmark artifacts land in `api/migration/benchmarks/`. Fair-comparison
rules apply (same machine/DB/dataset/payload/auth/cache/concurrency/counts).
`NOT BENCHMARKED` is an acceptable answer; invented numbers are not. Streaming
measures setup latency, event latency, events/sec, concurrent clients,
memory/client, dropped events, queue depth, reconnect behavior.

## 9. Hard rules honored

1. **The Python API is NOT deleted.** It is the reference implementation until
   parity is proven. Removal is a separate, evidence-backed cleanup phase.
2. **No production routing is modified** by this branch. Nothing here affects
   the running engine; all work is in the isolated worktree.
3. Foreign work untouched: `agents/locks.yaml` SEC-WAVE locks respected
   (`web/provisioning_routes.py`, `web/model_studio_routes.py`, `web/errors.py`,
   `database/drivers/sqlite_driver.py`, `position_adviser/trainer.py`,
   `model_generation/*`, `scripts/data/*` — read-only reuse only).
4. The shared checkout stays on its own branch; this work never commits to it.

## 10. Toolchain

Go 1.27.1 (windows/amd64) installed at `C:/Users/Capsizer/go-toolchain/go`,
verified SHA-256 against go.dev's official release manifest. `GOROOT` +
`GOPATH` set per-session; `GOPROXY=https://proxy.golang.org` reachable.
Downloaded via an alternate mirror because `dl.google.com` is unreachable from
this box (404 on all Go binaries); the hash match proves authenticity.

## 11. Next

- [ ] Contract extraction for Phase A endpoints → `api/migration/contracts/`
- [ ] Go module skeleton + health/metadata handlers (Phase A)
- [ ] Auth middleware (constant-time, PUBLIC_PATHS, token resolution)
- [ ] v1 envelope + error family
- [ ] Contract tests + parity harness against a live `create_v1_app()`
