# Agent Handoff — Debug 70D Forensic Console Upgrade (Hermes-Forensic-70D-UI)

**Date:** 2026-08-19
**Branch:** main

## Agent / Role
- **Agent:** Hermes-Forensic-70D-UI
- **Role:** Debug UI / Runtime Observability Engineer

## Task
Upgrade the Debug tab into a full 70D runtime intelligence console (brief: "UPGRADE DEBUG TAB INTO FULL 70D RUNTIME INTELLIGENCE CONSOLE").

## Starting / Ending HEAD
- Starting: `b41d76c` (before my first commit)
- Ending: `a369345` (my final commit; HEAD == origin/main verified)

## Commits
| SHA | Step | Scope |
|---|---|---|
| `3f3f3d9` | STEP-01/02 | backend canonical snapshot + 70D matrix + contract validation |
| `987c550` | STEP-07 | Debug tab UI upgrade |
| `a369345` | STEP-08 | TEST-DEBUG-01..32 suite + regression fixtures |

## Files
- `src/nexus_scalp/web/debug_snapshot.py` (new, ~1900 lines) — 18-section canonical snapshot builder, registry-driven 70D matrix, contract validation, DebugSnapshotStore ring, diff_snapshots.
- `src/nexus_scalp/web/server.py` — `/api/debug/state`, `/api/debug/snapshots`, `/api/debug/snapshots/{id}`, `/api/debug/compare`; `app.state.debug_snapshot_store` + `app.state.sse_diag`; SSE generator records connection/events/serialization errors; fixed latent `state_version` NameError in the SSE error path.
- `src/nexus_scalp/application/live_engine.py` — `_last_model_input_tensor` stash (observability only, INV-018).
- `Web/index.html` — Debug tab rebuilt: RUNTIME STATUS, 70D CONTRACT VALIDATION banner, 70D FEATURE MATRIX (10 columns + health badges + filters), FEATURE DETAIL, MODEL INPUT/OUTPUT, CONFIDENCE PIPELINE, POLICY DECISION TRACE, RISK ENGINE, EXPOSURE, EXECUTION, POSITIONS, EXIT FORENSICS, LIQUIDITY INTELLIGENCE + pools, NEWS, WORKERS, DATABASE, CACHES, CHART+SSE, DEBUG SNAPSHOT (copy/download/compare/JSON tree), NO HIDDEN ERRORS.
- `Web/app.js` — 7 chunks of renderers all consuming `/api/debug/state` + snapshot compare/history/copy/download/JSON tree. `node --check` PASS.
- `tests/unit/test_debug_snapshot_phase20.py` (new) — 36 tests (TEST-DEBUG-01..32 + API variants + regressions).
- `docs/DEBUG_70D_FORENSIC_UPGRADE_FINAL.md` — final report.

## Functions (new/changed)
- `build_debug_snapshot(engine, app_state)` — the canonical payload.
- `_feature_registry()` / `_features_section()` — registry-driven 70D matrix.
- `_contract_section()` — 70D + model contract validation.
- `_model_section()` / `_confidence_section()` / `_policy_section()` / `_risk_section()` / `_exposure_section()` / `_execution_section()` / `_positions_section()` / `_exit_section()` / `_liquidity_section()` / `_news_section()` / `_workers_section()` / `_database_section()` / `_cache_section()` / `_chart_section()` / `_sse_state_section()` / `_errors_section()`.
- `DebugSnapshotStore` / `diff_snapshots()`.
- `LiveEngine._infer_probabilities` — stashes `_last_model_input_tensor`.

## Shared / Architecture
- **SHARED API CHANGED:** `server.py` SSE generator now mutates `app.state.sse_diag` (additive, no protocol change).
- **ARCHITECTURE CHANGE:** Debug tab now reads ONE canonical endpoint (`/api/debug/state`) instead of 4 separate debug endpoints. Old endpoints (`/api/debug/features`, `/api/debug/health`, `/api/debug/model-test`, `/api/debug/ipc-telemetry`) remain functional for backward compatibility; the UI no longer calls `/api/debug/features` on refresh.
- The frontend is a pure renderer of backend state — no trading intelligence computed in JS.

## New Invariants
- INV (Debug): `/api/debug/state` must stay read-only, bounded (in-memory only, no DB scans / recompute / model reload), and every section must fail visibly with its own correlation_id.
- RAW/NORMALIZED/CLIPPED feature stages: `NOT_EXPOSED` when the runtime does not provide them — never a fake 0.

## Tests
- `tests/unit/test_debug_snapshot_phase20.py`: 36/36 pass.
- Focused regression trio (debug + web_security + web_chart_forming_bar_bug082): 47 passed.
- `node --check Web/app.js`: PASS. Div-balance checker on index.html: PASS.
- Full gate `beforePush.ps1` run: see quality-gate result (ruff/mypy/pytest full suite).

## Runtime Verification
- TestClient smoke (engine off + fake engine): `/api/debug/state` 200, 70 rows, contract validation, snapshots list/get/compare, SSE diag counters, canonical_json round-trip.
- GitHub: all 3 commits pushed, `HEAD == origin/main` after fetch.

## GitHub status
- 3 commits on `origin/main` (3f3f3d9, 987c550, a369345). No PR opened (direct push workflow per repo contract).

## Bugs
- Fixed: latent `state_version` NameError in SSE serialization-error handler (server.py) — would crash the SSE loop exactly on the path meant to report errors.
- Hardening: hasattr guards for `_entry_confidences` / `_peak_drawdown_usd`; news-worker `state` key normalization.
- No new open bugs.

## Known Risks
- Parallel-agent hazard: my STEP-08 commit absorbed another agent's staged registry rows (agents/bugs.md BUG-110, repository_state.md, taskboard.md, docs/TASK-09-*-FINAL) — additive registry rows, no conflict; their owner should verify their rows are complete.
- The working tree contains OTHER agents' uncommitted WIP (TASK-13 live_engine incident worker, TASK-9 governance Web edits, liquidity_runtime edits). I did NOT touch or commit them. Before pushing, verify `git status` ownership.
- `debug_snapshot.py` was written LF and git warns "LF will be replaced by CRLF" — harmless (repo-wide behavior).

## Unfinished
- Live-runtime visual validation (needs a running engine with MT5).
- Browser E2E for the new Debug tab (Playwright).
- Optional: surface `Shadow70Runtime.summary()` in the debug model section when a 70D candidate is attached.

## EXACT NEXT-AGENT INSTRUCTIONS
1. Read `docs/DEBUG_70D_FORENSIC_UPGRADE_FINAL.md` and this handoff.
2. Run the engine (LIVE or PAPER), open the Debug tab, visually verify: contract banner, 70D matrix values, liquidity pools, model input tensor, policy gates, snapshot capture/download/compare.
3. Run `tests/integration/test_playwright_e2e.py` (may need `--ignore` if playwright missing locally; CI installs it).
4. If the runtime exposes raw logits, extend `_model_section()` and TEST-DEBUG-33.
5. Attach any 70D shadow runtime summary into the debug `model` section once `_shadow70_runtime` has a contract (step 4 of the final report).