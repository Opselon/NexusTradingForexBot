# NSE Alternative UI (React + TypeScript + Vite)

An **alternative** operations console for the Nexus Scalp Engine. The existing
vanilla-JS dashboard in `Web/` remains the primary UI; this app is additive and
must coexist with it until parity is validated.

## Architecture rules (non-negotiable)

- **The backend is the source of truth.** No authoritative NSE state
  (LIVE/PAPER mode, engine running, broker connection, trading permissions)
  is ever decided in the browser. Every value renders from a backend payload
  or is shown as `—`/UNKNOWN.
- **Presentation layer only.** React talks to the existing FastAPI API
  (REST + WebSocket). No direct SQLite/MT5/filesystem access, ever.
- **Node is build/dev-only** (DEC-0002). The production artifact is the static
  `dist/` bundle; it is designed to be served by the NSE backend itself. No
  Node process is required at runtime, and the launcher must not spawn one.

## Sections

Dashboard · Trading · Positions · Risk · ML/70D · Intelligence · Audit.

## Development

```bash
cd frontend
npm install
NSE_API_ORIGIN=http://127.0.0.1:8080 npm run dev   # proxies /api,/health,/ws to the backend
```

Vite dev server: `http://127.0.0.1:5173` (proxies API calls, so no CORS and
same-origin WebSockets).

### Auth (WEB-AUTH-P0)

The backend enforces token auth. Open the UI as
`http://127.0.0.1:5173/?token=<NSE_WEB_AUTH_TOKEN>`; the token is kept in
`sessionStorage` for the tab session only (never localStorage, never committed).
For local-only testing the backend documents `NSE_WEB_AUTH_DISABLE=1` — never
combine with LIVE mode or a routable host.

## Production build

```bash
npm run build    # -> dist/
```

`served.md`-style integration: point the engine's static serving at `dist/`
(see `docs/` notes added with this app). The bundle uses the **relative base
`/alt/`** so it can be mounted at `/alt/` on the FastAPI app without
colliding with the existing `Web/` dashboard at `/`.

## API surface used (all existing backend routes — nothing invented)

| Area | Endpoints |
|---|---|
| Canonical snapshot | `GET /api/status`, WebSocket `/ws` (`get_system_state()`) |
| Engine control | `POST /api/engine/toggle`, `POST /api/engine/mode` |
| MT5 | `GET /api/mt5/status` |
| Positions | `GET /api/v1/positions`, `GET /api/mt5/status` (orders), `GET /api/account/trades` |
| Trading actions | `POST /api/positions/close`, `POST /api/positions/modify` |
| Risk | `GET /api/v1/risk/status`, `GET /api/v1/risk/summary`, `GET /api/debug/state` |
| ML / 70D | `GET /api/models/integrity`, `GET /api/models/shadow70/summary`, `GET /api/v1/model/status`, `GET /api/v1/model/identity`, `GET /api/v1/features/status` |
| News/intel | `GET /api/news/state`, `GET /api/news/health`, `GET /api/news/latest`, `GET /api/intelligence/summary`, `GET /api/intelligence/autopsies` |
| Audit | `GET /api/v1/audit/events`, `GET /api/v1/observability/events`, `GET /api/v1/incidents`, `GET /api/v1/database/status`, `GET /api/v1/database/integrity` |

Deliberately NOT implemented (no backend route): manual order placement,
pending-order cancel, any client-side PnL/mode computation.
