# Control Center UI Architecture (CHG-0043, TASK-CONTROL-CENTER)

> Operator-facing architecture of the NEXUS Control Center. UI-owned doc;
> backend architecture lives in `docs/architecture/` elsewhere. Status
> semantics here are the CONTRACT for every future CC view.

## Purpose

One coherent surface where an operator answers, without reading logs:

WHAT is happening → WHY → WHAT changed → WHAT is blocked → WHAT is healthy →
WHAT is serving → WHY did/didn't a trade happen → WHAT is safe to do next.

Observation model: OBSERVE → UNDERSTAND → INVESTIGATE → ACT → VERIFY.

## Navigation & Screens

| Tab (nav id) | Screen | Data sources (all read-only) |
| --- | --- | --- |
| Control Center (`tab-control-center`) | Overview: mode banner, 6-tile status strip (Runtime/Data/Model/Inference/Database/MT5), runtime truth, market snapshot, latest decision, active warnings | `/api/operator/summary`, `/api/operator/decisions?limit=1`, `liveUiSnapshot` (SSE, owned by app.js) |
| ↳ Decision Observatory | history (filters: hours/action/gate/search), terminal-stage funnel + blocking gates, NO_TRADE forensics (gates/regimes/reasons/hourly trend/recent), decision inspector drilldown | `/api/operator/decisions`, `/api/operator/funnel`, `/api/operator/no-trade`, `/api/operator/decisions/{id}` |
| ↳ Model & Feature Contract | serving identity (id/version/artifact/schema/dim/scaler), probability distribution, grouped feature contract (Base 0..49 · News 50..59 · Liquidity 60..69) with search + anomaly highlight | `liveUiSnapshot.model`, `.features` |
| ↳ Risk & Execution | risk state + limits, open positions, recent dispatches + latency percentiles | `liveUiSnapshot.risk/.positions`, `/api/operator/orders` |
| ↳ Diagnostics | engine self-check grid (per-category verdict + suggestion), incident health, sanitized copyable report | `/health`, `/api/diagnostics/health` |
| System Health (`tab-health`) | pre-existing subsystem panel (ORPHAN FIX: nav button added 2026-09-02 — the section existed but was unreachable since inception) | pre-existing loaders |

## Module layout

```
Web/
  cc_components.js   NX.cc.design — component library (badges, cards, states,
                     confirm dialog, tables, bars, copy, toast)
  cc_state.js        NX.cc.state  — finite UI state machine + bounded
                     visibility-aware polling engine
  control_center.js  NX.cc.views  — the five CC views + boot/tabs wiring
  cc_styles.css      .ccb-*/.cc-* component layer (dark technical theme)
  api_client.js      NX.api (shared; +AbortController signal passthrough)
  app.js             switchTab/DOMContentLoaded hooks; operator safety guards
src/nexus_scalp/web/operator_routes.py  /api/operator/* read-only evidence
src/nexus_scalp/web/server.py           registration + static asset routes
```

## State semantics (binding)

### Component states (`NX.cc.design.STATES`)

`HEALTHY · READY · PASS · AVAILABLE · FRESH · ACTIVE · ENABLED · VALID` (ok) —
`DEGRADED · WARNING · STALE · RECOVERING` (warn) —
`BLOCKED · FAIL · ERROR · UNAVAILABLE` (bad) —
`DISABLED · NOT_CONFIGURED · NOT_INITIALIZED · NOT_APPLICABLE · NOT_RECORDED ·
UNKNOWN` (muted).

Rules: glyph + text always accompany color (never color alone); unknown
values render as `NOT RECORDED` / `EVIDENCE NOT RECORDED`, never as zeros;
mode badges (LIVE/PAPER/SHADOW/REPLAY/RESEARCH) are visually distinct classes.

### UI data states (`NX.cc.state`)

One enum per resource: `LOADING → READY | STALE | ERROR | EMPTY`
(impossible combinations unrepresentable). Polling: bounded interval
(default 15s, never 1s-everything), fetch timeout 8s, exponential error
backoff capped 60s, STALE at 2.5× interval without success, paused while
`document.hidden`, leak-free `untrack` (verified: pending timers cleared,
mid-flight fetches never reschedule).

## Dangerous actions (operator safety)

Structured confirmation (`NX.cc.design.confirmDialog`) — rows ACTION /
CURRENT STATE / IMPACT / RECOVERY + verb-specific CONFIRM button; ESC
cancels, Enter confirms, focus lands on CANCEL. Guards wired:

- **Stop/Start Bot** (`app.js toggleEngineRunning` → `doEngineToggle`)
- **Switch to LIVE** (`app.js` mode selector → `performEngineModeSet`;
  cancel reverts the selector to the authoritative server mode)

Promotion/freeze actions in Model Governance already had confirmations
(pre-existing); CC adds no new mutation surface — the /api/operator/*
module is structurally read-only (`file:...?mode=ro`).

## Data truth rules

1. Every number reconciles summary ↔ detail (funnel/NO_TRADE totals vs
   filtered rows; enforced by tests).
2. The ledger records the FINAL blocking stage per decision — the funnel is
   labeled a TERMINAL distribution; a fabricated per-stage pass-through
   funnel is forbidden.
3. Rows with a model_action lacking direction (GUARDIAN abstentions) are
   counted and surfaced as `model_direction_unresolved` — counterfactual
   direction is NOT reconstructable (TICK_COUNTERFACTUAL v1 honesty).
4. Malformed payload rows stay visible with `payload_ok: false`; they are
   never silently dropped.
5. Order correlation method is disclosed in every detail response
   (`order_id == request_id`, fallback `execution_id`).

## Tests

- `tests/unit/test_operator_routes.py` — 19 pytest cases (route parity,
  read-only enforcement, filters/clamps, envelope stability, reconciliation,
  sanitization).
- `tests/js/cc_design.test.js` — 9 node --test cases (vocabulary, badges,
  freshness, NOT-RECORDED honesty, structured confirm, bar math).
- `tests/js/cc_state.test.js` — 6 node --test cases (enum states, EMPTY
  contract, error backoff, untrack/reset hygiene incl. no leaked timers).
- `tests/unit/test_frontend_assets_phase14.py` — guards every index.html
  local ref resolves 200 (the CC asset routes are covered by it).

## Deliberate non-goals

- Replay/Shadow CC tabs are NOT built here: the replay session engine is
  another owner's active CHG-0043 workstream; surfacing half-wired modes
  would violate mode-separation honesty. Entry points remain the existing
  panels until that lands.
- `/api/v1` (TASK-API-PLATFORM) is the developer surface; `/api/operator/*`
  serves the dashboard. Overlap reconciliation belongs to the API-platform
  owner (documented in commit 1c4d293 handoff).
