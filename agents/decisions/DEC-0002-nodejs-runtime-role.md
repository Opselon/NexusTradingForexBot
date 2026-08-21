# DEC-0002 — Node.js Runtime Role in Nexus Scalp Engine

- **Date:** 2026-08-22
- **Agent:** Hermes-Forensic-Node
- **Decision:** Node.js is a **build/dev/test-only** dependency. It is NOT part of the
  production or local runtime architecture (Outcome B).
- **Status:** ACCEPTED

## Question
> What exactly is Node.js doing in this project today, and should it be a runtime
> dependency given the app opens locally without Node installed?

## Evidence (forensic)
- No `package.json` / `package-lock.json` / lockfiles anywhere (confirmed in git
  history too — it was never committed).
- `node_modules/` contains only `playwright`/`playwright-core` (E2E test habitat,
  `.gitignore`d, CI-only) — no `tailwindcss`, no bundler runtime.
- `.dockerignore` / `.gitignore` reference `node_modules` / `Web/node_modules` that do
  not exist → stale artifacts of an assumed Node frontend.
- `Web/` is a **buildless vanilla-JS SPA**: `index.html` + `app.js` + `*.js` includes
  loaded via `<script>` tags. No bundler, no `import`/`require`, no CDN (BUG-047
  localized Tailwind + FontAwesome; `test_no_tailwind_cdn` guards it).
- The Web UI is served entirely by the **Python FastAPI process**:
  `GET /`→`index.html`, `/app.js`, `/styles.css`, `/api_client.js`, `/tailwind.css`
  (routes in `src/nexus_scalp/web/server.py`). No Node HTTP/WS/API server exists.
- The canonical launchers (`python NexusTradingForexBot.py`, `nexus start`,
  `docker compose`) start FastAPI (uvicorn) + the async `LiveEngine` loop. None spawn
  Node. `scripts/start.ps1|sh` are thin docker-compose wrappers (dev containers only).
- A repo-wide `grep` for `node`/`npm`/`npx`/`vite`/`webpack`/`esbuild` in `src/` returns
  zero runtime invocations.
- `node --check` and `tests/js/*.test.js` (run by `js-tests.yml`) use node purely as a
  dev/test runner against already-precompiled browser JS with a DOM shim.

## Why the project runs without Node
The Web UI is static HTML + browser JavaScript + a committed, pre-compiled
`Web/tailwind.css`. At runtime it is served by FastAPI over a single port (8080 local /
9090 docker). Node only ever (a) compiles Tailwind at build time and (b) runs the JS
syntax/test gate in CI. Neither is on the runtime critical path.

## Decision
Node.js is explicitly isolated as a **build/dev/test** tool:
- Build: `scripts/build/build_tailwind.py` (pins `tailwindcss@3`, ephemeral `npx`,
  no committed `node_modules`). Rebuild only when the theme/palette changes.
- Dev/test: `node --check` + `tests/js/*.test.js` via `.github/workflows/js-tests.yml`.
- Runtime: the engine/UI requires **no Node**. The official launcher must not spawn Node.

## Port model (confirmed)
- Local source: `127.0.0.1:8080` (FastAPI: REST + SSE + WebSocket + static UI).
- Docker: `:9090` (single user-facing endpoint; redis/postgres are internal compose
  services, not user-facing).
- No separate Node port exists (and must not be created).

## Consequences / guardrails
- Do NOT add a Node dev server, production web server, WebSocket gateway, or API proxy to
  the runtime.
- Do NOT make the launcher depend on `npm install` / `npm run`.
- The committed `Web/tailwind.css` is the runtime artifact; treat `scripts/build/build_tailwind.py`
  as the single source of truth for regenerating it.
- Regression guards: `tests/unit/test_node_runtime_role.py` (12 tests).

## Packaging strategy
For the packaged Windows EXE, Node is NOT bundled and NOT required. The UI ships with the
pre-compiled `tailwind.css`; Tailwind rebuild is a developer-only action. This satisfies
the audit's Outcome B and avoids the licensing/size/update burden of a bundled runtime.

## Tests / CI
- CI validates the *build/dev/test* Node role only: `js-tests.yml` (node --check + JS unit
  tests) and a reproducible Tailwind build. CI does NOT pretend Node is a production runtime.
- `test_node_runtime_role.py` locks the "Node is not a runtime dependency" contract.
