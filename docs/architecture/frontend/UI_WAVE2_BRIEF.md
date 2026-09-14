# UI WAVE 2 — Modern React Parity Console ("deep work" brief)

Worktree: `C:/Users/Capsizer/source/repos/nse_ui_wave2` · branch `ui/wave2-modern-react` (base b7274225).
All frontend work happens under `frontend/` ONLY. Never touch `Web/`, `src/nexus_scalp/`, or the
main checkout.

## Mission
Port everything the legacy `Web/` dashboard does into the clean-architecture React console at
`/alt`, and make it prettier, more modern, and better rendered — without breaking a single invariant.

## Architecture (already at HEAD — follow it, do not invent a new one)
- `src/app/featureModule.ts` — `defineFeature({meta, lazy})`; every feature folder
  `src/features/<name>/` has `api.ts` (transport calls), `model.ts` (DTO types/normalizers),
  `useCases.ts` (react-query keys/fns), `ui/<Name>Page.tsx`, `index.ts` (registration).
- Register new features in `src/app/featureRegistry.ts` (one line + import). Sidebar order = file order.
- Transport: `@/core/transport` — `getV1` (all `/api/v1/*` envelope), `getLegacy` (`/api/*`),
  `send`, `sendV1`, `drop`, `toQuery`. Auth is handled by core/middleware — never add headers yourself.
- Realtime: SSE ONLY (`/api/ticks/stream`) via `src/hooks/useRealtime.ts`
  (`useRealtimeSnapshot`, `useRealtimeStatus`, `useRealtimeVersion`, `useCoreEvent`) over
  `src/websocket/realtimeSocket.ts` (state_version out-of-order guard). WS is NOT available (ws="none").
- Primitives: `src/components/primitives.tsx` (Panel, MetricCard, DataTable, StatusBadge, ProbBar,
  Segmented, Skeleton, EmptyState, ErrorState, ConfirmModal, ToastHost). Viz: `src/components/viz/`
  (Gauge, Sparkline, HeatBar, DrawdownChart, EquityCurveChart, PnlWaterfall, geometry.ts — SVG/canvas
  helpers, no external chart libs).
- Theme: `src/styles/theme.css` CSS custom properties (--bg, --panel, --accent #41a6f2, --green,
  --red, --amber, --violet, mono/sans stacks). Density via `body.dense`. New CSS: one scoped file per
  feature (`ui/<feature>.css`) using the tokens; light glassmorphism, depth shadows via
  --elevate-hi/--elevate-lo, CSS 3D transforms (perspective/transform-style: preserve-3d) for card
  lift are welcome. No CSS framework, no CDN.
- Deps: DO NOT add npm packages (bundle stays small, offline vendor rule). Canvas 2D + SVG + CSS 3D
  is the rendering toolkit (legacy `command_center_spatial.js` is itself Canvas2D 2.5D — match it).

## Hard invariants (non-negotiable, repo law)
1. Backend-authoritative: render what the API sends. NEVER fabricate, interpolate, or demo-fill values.
   null/missing → "—" or honest empty/waiting state (see legacy "NEVER fake numbers").
2. Stale/error states are display state, never invented data; keep lastGood on stale (widget pattern).
3. No client-side indicator/feature math (audit lane-09 forbids it). Display transforms of backend
   counts (bar widths, gauge needle angle from backend sell/neutral/buy counts) ARE allowed.
4. Money paths fail closed; typed-confirm "LIVE" escalation semantics must survive porting.
5. Legacy files are REFERENCE ONLY (some are huge — `Web/app.js` is 13k lines: always grep + read a
   range, never whole-file). The React tree must not import anything from `Web/`.

## Known defects to fix in this wave (from docs/audit/wave_20260914/09_indicators_ui.md)
- D5: pivot `rows` is a dict keyed by level `Record<level, Record<column, number|null>>` —
  `types/features.ts:34-38` and `ai-analysis/model.ts:121` declare it wrong; `AiAnalysisPage.tsx:345-357`
  crashes on it. Fix DTOs, render a real pivot MATRIX (levels × 7 families: Classic/Fibonacci/Camarilla/
  Woodie/Demas/DeMark/Fibonacci variants as backend sends), delete phantom fields signal/kind/label/params.
- No ErrorBoundary exists anywhere → add `src/components/ErrorBoundary.tsx` and wrap routes in AppShell.
- D3 note: both UIs dodge the 1D/1W/1H 500 by curated TF lists — keep curated lists; do NOT add those
  aliases client-side until backend T-09.1 lands.
- Legacy `radar` object (SSE snapshot field, typed in `types/domain.ts:243`) is rendered by legacy
  Market Radar (app.js:9995+) but by NO React page — port it verbatim (state, best setup type/direction/
  quality, compatible strategies, news blackout, decision, updated) with a modern look.
- indicatorsApi.ts is unused — the new indicator console should use it.

## Acceptance gates (each lane runs its own before committing)
- `cd frontend && npx tsc -b --noEmit` → exit 0 (fix ALL type errors you introduce).
- If you touched shared files, run `npm run build` too.
- Commit: scoped conventional commit, `git add <only-your-files>` then `git diff --cached --name-only`
  must list exactly your scope, then commit. NEVER `git add -A`.

## Lane ownership (stay inside; anything outside = integration owner only)
- Lane A market-console: `src/pages/Dashboard/**` (incl. PriceChart, ReplayPanel)
- Lane I indicators: `src/features/ai-analysis/**`, `src/types/features.ts`, `src/components/ErrorBoundary.tsx`, `src/app/AppShell.tsx` (boundary wrap only)
- Lane N news: `src/features/news/**`
- Lane P ux-3d: `src/features/command-center/**`, `src/components/CommandPalette.tsx`, `src/components/AttentionStrip.tsx`, `src/components/ModeIndicator.tsx`, `src/hooks/useMutationFeedback.ts`, `src/styles/theme.css` (tokens only, additive)
- Lane X platform-pages: `src/features/database/**`, `src/features/debug/**`, `src/features/governance/**`, `src/features/incidents/**`, `src/features/rules/**`, `src/features/factory/**`, `src/features/health/**`, `src/features/liquidity/**`, `src/features/account/**`, `src/features/marketplace/**`, `src/features/research/**`, `src/features/config/**`, `src/features/control-center/**`, `src/pages/**` (except Dashboard)
