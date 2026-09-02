# Nexus API Platform v1 — Developer Reference

The versioned developer surface of the Nexus Scalp Engine: **68 documented
operations** under `/api/v1/*`, read-dominant, additive to the existing
dashboard (the 257 legacy routes are untouched and remain the UI's own surface).

- Spec of record (architecture + endpoint inventory): `docs/api/API_PLATFORM_V1.md`
- This document: how to *use* the platform (getting started, contracts, examples)

---

## 1. Getting Started

1. Start the engine (the dashboard server mounts `/api/v1` automatically):
   `nexus start` (or your usual dev launch).
2. Browse the machine-readable contract: `http://127.0.0.1:8080/api/v1/system/capabilities`
3. Call your first endpoint:

```bash
curl http://127.0.0.1:8080/api/v1/system/version
```

```json
{
  "data": {"product": "NexusScalpEngine", "version": "9.0.6", "...": "..."},
  "meta": {"request_id": "req_ab12cd34", "generated_at": "2026-09-02T10:00:00+00:00"}
}
```

## 2. Authentication

None for local use — the API binds to localhost and the mutation surface is
deliberately near-zero (see §8 of the spec of record). Do **not** expose the
port beyond localhost without adding an authenticating proxy; the platform
performs no broker mutations by design.

## 3. Versioning

- All endpoints live under `/api/v1/`. Incompatible changes will ship as
  `/api/v2/` — v1 is never broken in place.
- Additive changes (new fields, new endpoints) are backward compatible and are
  detected/documented via the snapshot+diff toolchain (§10).
- `meta.api_version` (`"v1"`) is present on envelopes where the platform runs.

## 4. Envelope & Errors

Every response is one of:

```json
{"data": <payload>, "meta": {"request_id": "req_...", "generated_at": "...+00:00"}}
```

```json
{"error": {"code": "...", "message": "...", "details": {},
           "request_id": "req_...", "retryable": false}}
```

| code | HTTP | retryable |
|---|---|---|
| VALIDATION_ERROR | 422 | no |
| METHOD_NOT_ALLOWED | 405 | no |
| PAYLOAD_TOO_LARGE | 413 | no |
| CONFLICT | 409 | no |
| RESOURCE_NOT_FOUND | 404 | no |
| ENGINE_UNAVAILABLE | 503 | yes |
| DEPENDENCY_UNAVAILABLE | 503 | yes |
| TIMEOUT | 504 | yes |
| INTERNAL_ERROR | 500 | no |

Never present in an error body: stack traces, filesystem paths, SQL, secrets.
Full detail goes to the structured log under the same `request_id`.

## 5. Request & Correlation IDs

Send `X-Request-ID: req_mytrace001` — it is echoed in `meta.request_id`, the
`X-Request-ID` response header, and all server-side logs for that request.
POSTs may also send `Idempotency-Key`; it is echoed in `meta.idempotency_key`.

## 6. Pagination & Filtering

One pagination model everywhere:

```bash
curl "http://127.0.0.1:8080/api/v1/decisions?page=2&page_size=50"
```

```json
{"data": {"items": [...], "page": 2, "page_size": 50, "has_more": true}, "meta": {...}}
```

- `page` ≥ 1; `page_size` 1..200 (out of range → 422). No total counts (protects
  the DB); `has_more` is the contract.
- Filters (where defined): `symbol`, `action`, `status`, `execution_mode`,
  `decision_stage`, `severity`, `category`, `component`, `strategy_id`,
  `run_id`, `hours_back` (≤ 720). All parameterized; unbounded queries are
  impossible.

## 7. Timestamps & Units

- Transport timestamps are ISO-8601, timezone-aware, UTC (`+00:00`).
- Money/price fields are broker-native floats; volumes in lots; latency in ms.

## 8. Domain Highlights (full inventory in the spec §7)

| Domain | Examples |
|---|---|
| system | `/system/status`, `/system/health`, `/system/readiness`, `/system/capabilities`, `/system/workers`, `POST /system/refresh`, `/system/diagnostics` |
| runtime | `/runtime/mode`, `POST /runtime/mode/validate`, `POST /runtime/mode/preview`, `/runtime/freshness` |
| market | `/market/snapshot`, `/market/quote`, `/market/bars`, `/market/regime`, `/market/symbols` |
| signals/decisions | `/signals/latest`, `/signals/history`, `/decisions/latest`, `/decisions`, `/decisions/{id}`, `/decisions/{id}/evidence|gates|explanation`, `/decisions/stats`, `/decisions/no-trade`, `/decisions/no-trade/reasons` |
| positions/execution | `/positions`, `/positions/{ticket}`, `/positions/history/{ticket}`, `/execution/status`, `/execution/history` |
| risk | `/risk/status`, `/risk/summary` |
| model/features | `/model/status|identity|contracts|champion`, `/features/status|contract|groups|current` |
| research | `/research/status`, `/research/strategies`, `/research/runs`, `/research/datasets` |
| shadow | `/shadow/status`, `/shadow/runs`, `/shadow/runs/{id}`, `/shadow/decisions`, `/shadow/70d` |
| incidents | `/incidents`, `/incidents/stats`, `/incidents/{id}`, `/incidents/{id}/timeline` |
| observability/audit | `/observability/events`, `/observability/metrics`, `/audit/events` |
| database | `/database/status`, `/database/integrity`, `/database/tables` |
| config | `/config` (sanitized), `/config/schema`, `POST /config/validate` (no apply) |

## 9. Python Client

```python
from nexus_scalp.api_client import NexusApiClient

client = NexusApiClient("http://127.0.0.1:8080")
print(client.system_status()["data"])
print(client.decisions_latest()["data"])
print(client.decision_gates("req_ab12cd34")["data"])

# errors raise a typed exception carrying the v1 error object:
from nexus_scalp.api_client import NexusApiError
try:
    client.decision_detail("req_missing")
except NexusApiError as e:
    print(e.code, e.retryable, e.request_id)
```

The client performs no logic — it is a typed convenience wrapper over the same
HTTP contracts.

## 10. CLI

```bash
nexus api status                  # human-readable operational summary
nexus api health                  # full health JSON
nexus api version
nexus api capabilities
nexus api mode
nexus api signals                 # latest signal
nexus api decisions --latest      # latest decision
nexus api decisions --id req_x --gates
nexus api model
nexus api features
nexus api diagnostics --run
nexus api get incidents --page-size 10   # any v1 path
nexus api smoke                   # domain-by-domain smoke -> API_SMOKE = PASS
```

Override the target with `--base-url http://host:port` or `NEXUS_API_BASE`.
The CLI uses `NexusApiClient` — the same contracts as every other client.

## 11. Developer Tools (scripts/dev/)

| tool | purpose |
|---|---|
| `api_smoke.py` | `--embedded` (in-process, no server) or `--live BASE_URL`; prints `API_SMOKE = PASS` |
| `api_contract_check.py` | OpenAPI quality gate: route/spec parity, summaries, tags, schemas, secret-shape scan; exit 1 on drift |
| `api_openapi_snapshot.py` | deterministic `artifacts/api/openapi_snapshot.json` (sorted, timestamp-free) |
| `api_openapi_diff.py` | snapshot-vs-snapshot drift: removed endpoints/methods, request-schema changes → exit 2 on breaking |
| `api_benchmark.py` | bounded local perf smoke (median/p95/p99 per route) |

CI drift recipe:

```bash
python scripts/dev/api_openapi_snapshot.py --out new.json
python scripts/dev/api_openapi_diff.py artifacts/api/openapi_snapshot.json new.json --fail-on-breaking
```

## 12. Troubleshooting

| symptom | meaning |
|---|---|
| 503 `ENGINE_UNAVAILABLE` | the API server is running without an engine attached — market/positions/model routes report truthfully instead of faking data |
| 503 `DEPENDENCY_UNAVAILABLE` | a backing store/registry read failed; details in logs under `meta.request_id` |
| 404 on a live resource | the id does not exist (or the table is empty — e.g. no incidents yet) |
| `api smoke` fails on a running engine | check `nexus api status`; if `health_verdict` is `NOT READY`, fix the failing layer before integrations |

## 13. For Agents (A2A / automation)

The platform is designed for machine consumption: bounded outputs, stable
identifiers (`request_id`, `incident_id`, `ticket`, `run_id`), explicit
capability discovery (`/system/capabilities`), truthful unavailability (503
with `retryable`), and no hidden state. A read-only inspector can consume
every GET without any ability to mutate runtime state.
