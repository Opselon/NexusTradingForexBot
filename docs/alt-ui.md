# ALT-UI — the Alternative React Console (`/alt`)

The **alternative console** (`frontend/`, React + Vite + TanStack Query +
react-router) is a second, parallel UI for the Nexus Scalp Engine control
plane. It is served **by the same Python process** as the legacy dashboard —
no Node runtime in production (DEC-0002 lineage: Node/Vite is a build/dev tool
only; the shipped artifact is a static `dist/`).

| Surface | Served by | Notes |
| --- | --- | --- |
| `http://<host>:<port>/` | legacy `Web/` dashboard (plain JS bundle) | primary UI until parity lands — unchanged by this document |
| `http://<host>:<port>/alt/` | alt React console (`frontend/dist`) | this page |
| `http://<host>:<port>/api/*` | the one authoritative backend | shared by both UIs; full token auth |

## Enabling it

The `/alt` mount is **additive and disabled-by-default**. `create_app` mounts it
only when a built bundle is resolvable (`_resolve_alt_ui_dir`):

1. `NEXUS_ALT_UI_DIR` points at a `dist/` folder containing `index.html`
   (packaged releases opt in explicitly this way), otherwise
2. `<repo>/frontend/dist/index.html`, otherwise
3. `$(cwd)/frontend/dist/index.html`

If none resolve, the mount does not exist and `/alt/*` 404s exactly as before —
the legacy UI is untouched. Build with:

```powershell
cd frontend ; npm ci ; npm run build     # -> frontend/dist
```

The startup banner in `NexusTradingForexBot.py` advertises
`Alternative console: http://localhost:<port>/alt/` only when the same bundle
resolves (otherwise it prints a "build frontend/ to enable /alt/" hint), so the
banner and the mount can never disagree.

## Deep links (SPA history fallback)

The console routes are client-side (`/`, `/trading`, `/positions`, `/risk`,
`/ml`, `/intelligence`, `/audit` in `AppShell.tsx`), so a browser reload or a
pasted URL hits the **server** with e.g. `/alt/positions`. A plain
`StaticFiles(html=True)` mount 404s every subpath, which makes deep links and
reloads unusable.

The mount is therefore wrapped by `_AltSpaHistory` (in
`src/nexus_scalp/web/server.py`, inside the `/alt` block only). It is a pure ASGI
wrapper around the untouched `StaticFiles` app and does exactly two things:

1. **History fallback** — when `StaticFiles` raises its 404 and the request is a
   `GET`/`HEAD` whose `Accept` explicitly asks for `text/html`
   (`application/xhtml+xml` also counts) and whose last path segment is not
   file-shaped, it answers `200` with **the same `index.html` bytes**, so React
   Router resolves the route. Everything else is unchanged:
   * `/alt/nope` with `Accept: application/json` (or no `Accept`) → plain JSON `404`
   * `/alt/assets/<missing-hash>.js` → `404` (a broken bundle never masquerades as HTML)
   * `POST /alt/positions` → `405` (StaticFiles' own method contract)
   * any path **outside** `/alt` → untouched, so no shell leaks into legacy routes
   Consequence to expect: a *typo'd* route navigated from the address bar
   (`/alt/nope`) also returns the shell — the console's own `path="*"` catch-all
   renders "Unknown route". That is the standard SPA contract (the alternative to
   it is a dead JSON 404 page for every reload).
   The shell response carries `Vary: Accept` + `no-store` so a shared cache can
   never reuse it for a non-HTML probe of the same URL.
2. **Cache policy** — the HTML shell (directory URLs and `index.html`) is
   `Cache-Control: no-store`, so a rebuild lands on the next navigation; the
   content-hashed bundle under `/alt/assets/` is
   `public, max-age=31536000, immutable`.

Realtime data still flows over `/api/ticks/stream` (SSE — production uvicorn
boots `ws="none"`, so `/ws` is not a transport), exactly as the runtime
contract tests pin.

> **Companion frontend requirement.** Serving the shell at `/alt/positions` is
> the *server* half of deep-linking. `frontend/src/main.tsx` currently mounts
> `<BrowserRouter>` with **no `basename`**, so React Router reads the location as
> `/alt/positions` while its routes are declared `/positions`, and the in-app
> catch-all renders "Unknown route" (client navigation from `/alt/` also drops
> the `/alt` prefix). The one-line frontend follow-up is
> `<BrowserRouter basename={import.meta.env.BASE_URL}>` (Vite `base: "/alt/"`).
> It is outside this backend lane's edit scope; the mount deliberately does not
> rewrite paths, so nothing about it blocks that change and every route works
> once it lands.

## Auth model (unchanged by the fallback)

`WEB-AUTH-P0` is installed as the **last** step of `create_app` and wraps the
whole app, so it sits **outside/above** the `/alt` mount: the fallback can never
become an auth bypass. The split is the documented one from
TASK-ALT-UI-HARDENING (B2) + the `089fb10b` allowlist:

| Request | With auth enabled (default) |
| --- | --- |
| `GET /alt/`, `GET /alt/positions` (HTML shell) | **401** without a token — `Bearer` / `X-NSE-Token` / `?token=` / first-party cookie |
| `GET /alt/assets/<hashed>` | public static (`/alt/assets/` is in `PUBLIC_PREFIXES`) — browsers send no credentials on `<script>`/`<link>` subresources |
| `GET /api/*` | token required |

`NSE_WEB_AUTH_DISABLE=1` (trusted LAN only, **never** with LIVE) turns the gate
off. The fallback path adds no new public path: a tokenless deep link 401s
before routing, and a *file-shaped* fallback inside `/alt/assets/` stays 404 —
pinned by `tests/unit/test_alt_ui_mount.py`.

## Running the console standalone on :8088

Useful when the main launcher's port probe lands elsewhere (this dev box:
`NVIDIA Broadcast.exe` occupies `127.0.0.1:8080`, so the launcher shifts ports).
Serving the console on its own port needs **no new code** — the mount travels
with `create_app`:

```powershell
# repo root; --factory + create_app so the engine stays detached (read-only UI)
$env:NSE_WEB_AUTH_DISABLE = "1"        # trusted-LAN dev only — see Auth model above
.\.venv\Scripts\python.exe -m uvicorn --factory nexus_scalp.web.server:create_app `
    --host 127.0.0.1 --port 8088 --ws none

# with auth ON (recommended off-LAN): set NSE_WEB_AUTH_TOKEN, drop the disable flag,
# then open http://127.0.0.1:8088/alt/ and authenticate (the shell needs the token;
# /alt/assets/* load without it by design).
```

Verified against that exact command: `/alt/` and `/alt/positions` → `200 text/html`
with `cache-control: no-store`; `/alt/assets/index-*.js` → `200` immutable;
`/alt/nope` → `404 application/json` with `Accept: application/json`; legacy `/`
→ `200` legacy dashboard. With no engine attached, the snapshot reports
`engine_running: false` and the v1 API returns the honest
`ENGINE_UNAVAILABLE` 503 envelope — the console surfaces that rather than
inventing state.

For front-end work itself, prefer `npm run dev` (Vite on :5173 proxying
`/api`, `/health`, `/ws`, `/web` to the backend; override the target with
`NSE_API_ORIGIN=http://127.0.0.1:<port>`). `vite.config.ts` sets
`base: "/alt/"` so dev and the served bundle emit identical asset URLs.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_alt_ui_mount.py -p no:cacheprovider -q
.\.venv\Scripts\python.exe -m pytest tests/unit/test_alt_ui_runtime_contract.py -p no:cacheprovider -q
```

`test_alt_ui_mount.py` is offline (TestClient over `create_app`) and skips
cleanly when `frontend/dist` is absent. `test_alt_ui_runtime_contract.py` keeps
its live-backend class skipped unless `ALT_UI_LIVE_BACKEND=1`.
