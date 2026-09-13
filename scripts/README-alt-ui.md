# Standalone Alt-Console Host — `scripts/serve_alt_ui.py`

**Lane:** standalone-server (BACKEND-1/5). A dependency-free host that runs
the alternative React console (`frontend/`) on its own port, decoupled from
the engine's FastAPI process, while the **engine backend stays the single
source of truth**.

Stdlib only (`http.server` + `threading` + `http.client`) — no Node runtime
in production (DEC-0002), no new Python dependencies. Runs with the repo
venv python.

## What it does

| Concern | Behavior |
|---|---|
| Port / bind | `--port` (default **8088**), binds **127.0.0.1** unless `--host`/`--allow-remote` |
| Static | serves `--dist` (default `<repo>/frontend/dist`, `NEXUS_ALT_UI_DIR` fallback); the vite `base:"/alt/"` prefix is accepted and transparently stripped (`/alt/assets/x.js` → `dist/assets/x.js`) |
| SPA fallback | unknown non-API path + `Accept: text/html` → `index.html` (deep links like `/positions` work); otherwise 404 JSON envelope |
| Proxy | `/api`, `/health`, `/healthz` (+ subpaths) → `--backend` (default: the launcher-recorded `NSE_WEB_ACTUAL_PORT` from the repo `.env`, else `http://127.0.0.1:8080`; `NSE_API_ORIGIN` overrides). BUG-266: `/app.js` + `/api_client.js` are proxied too — they carry the backend's bootstrap `Set-Cookie`. Methods GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS, body (1 MiB `Content-Length` guard), query string |
| Header passthrough | **only** `content-type`, `accept`, `x-nse-token`, `cookie`, `authorization` + `X-Request-ID` + a corrected `Host`. Everything else is dropped |
| Response relay | upstream status/headers/body; hop-by-hop headers stripped (`connection`, `keep-alive`, `transfer-encoding`, …); `Content-Length` corrected to the relayed body |
| SSE | any upstream `text/event-stream` (primary route `/api/ticks/stream`) is relayed **unbuffered**: `read1(≤64 KiB)` → write → `flush`, per chunk; `X-Accel-Buffering: no` + `Connection: close`; client disconnect tears the upstream down immediately |
| Correlation | incoming `X-Request-ID` honored (else `req_<10 hex>` minted), forwarded upstream, echoed on every response, present in every log line and error envelope |
| Fail-closed | backend unreachable → **502** `{"ok":false,"available":false,"success":false,"error":{"code":"BACKEND_UNREACHABLE","message":…,"request_id":…}}` (v1 envelope style, `web/errors.safe_error_payload` shape). API answers **never** come from disk/cache — even when `dist/api/...` files exist and even for `Accept: text/html` |
| Traversal | `..` (literal, `%2e`, double-encoded `%252e`), backslashes, NUL → 404 envelope for **all** routes (proxy included); `realpath` + `commonpath` containment re-checked at resolution; symlink escapes refused; no directory listing ever |
| Caching | `index.html`: `no-store`; hashed `assets/*-<hash>.<ext>`: `max-age=31536000, immutable`; everything else `no-store`; `X-Content-Type-Options: nosniff` |
| Logging | one access line per request, `?token=` redacted; Authorization / Cookie / X-NSE-Token **values are never logged** |

## Run

```bash
# repo root, with the engine backend on its default port:
./.venv/Scripts/python.exe scripts/serve_alt_ui.py

# explicit:
./.venv/Scripts/python.exe scripts/serve_alt_ui.py \
    --port 8088 --backend http://127.0.0.1:8080 --dist frontend/dist

# this dev box note: NVIDIA Broadcast occupies 127.0.0.1:8080 — point at
# wherever the test engine runs (same knob vite dev uses):
NSE_API_ORIGIN=http://127.0.0.1:8099 ./.venv/Scripts/python.exe scripts/serve_alt_ui.py
```

Then open `http://127.0.0.1:8088/alt` (or `/`). Authenticate exactly as the
console does against the engine: the login/token the backend issues (Bearer /
`X-NSE-Token` / the HttpOnly `nse_web_auth` cookie) passes through untouched.

## Test

```bash
./.venv/Scripts/python.exe -m pytest -p no:cacheprovider \
    tests/unit/test_alt_ui_standalone_server.py
```

Fully offline: an in-test stub upstream (SSE frames with timed gaps, JSON
echo, 401 route, chunked route) + a temp dist dir. Covers routing, MIME +
cache policy, SPA fallback, 9 traversal vectors, header allowlist +
credential passthrough, query forwarding, request-id echo, POST body guard
(413 without consuming the upload), chunked re-framing, SSE per-chunk
arrival timing + upstream teardown on client hangup, the 502 fail-closed
envelope with a dead backend, and log redaction (via `caplog`).

## Auth model — read before `--allow-remote`

This host enforces **nothing**. WEB-AUTH-P0 (`src/nexus_scalp/web/auth.py`)
lives in the backend; the proxy simply carries the caller's credentials to
it. The static SPA bundle is public by design (the backend allowlists
`/assets/` identically). Loopback binding keeps the trust boundary identical
to the engine's own. `--allow-remote` (bind `0.0.0.0`) prints a loud warning
and should be treated as: *exposing an unauthenticated, cleartext port to
the control plane* — trusted LAN only, never with the engine in LIVE mode.

## Limitations

- **No TLS** (neither direction) — keep it localhost-only. `https://`
  backends are refused at startup on purpose.
- Thread-per-connection (`ThreadingHTTPServer`): fine for a console; under
  many simultaneous SSE streams + large static downloads it is a bottleneck
  compared to uvicorn. Long upstream keeps (many hours) pin one thread each.
- Non-SSE proxy responses are buffered in memory (bounded by backend
  behavior, not by this host) so `Content-Length` can be corrected; huge
  streaming downloads with a content-type other than `text/event-stream`
  are not relayed incrementally.
- Chunked request uploads are refused (400); the 1 MiB body guard rejects
  oversized uploads with 413 without reading them.
- HTTP/1.1 only; no HTTP/2, no compression applied by this host (upstream
  `content-encoding` passes through untouched).
- `/ws` and `/web` are NOT proxied: production uvicorn boots with `ws="none"`
  and the realtime transport is SSE (`/api/ticks/stream`).
