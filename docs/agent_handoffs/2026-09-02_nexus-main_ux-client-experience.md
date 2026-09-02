# AGENT HANDOFF — 2026-09-02 — Nexus-Main (UX) — CHG-0048 Client Experience Pass

## Agent / Role / Task
- **Agent:** Nexus-Main (UX)
- **Role:** Client Experience & Usability Engineer
- **Task:** TASK-UX-01 — Transform the actual client (Web/) into an easy, fast, clear, professional product. Presentation-layer only; ZERO trading-logic/model/risk/execution changes.
- **CHANGE-ID:** CHG-0048 · **BUG:** BUG-194 (fixed) · **Branch:** main

## Git Range
- **Starting HEAD:** 67d7982 (post CHG-0042 docs registration; dirty tree from parallel agents preserved)
- **Commits (this task, in order):**
  1. `804fbf7` CHG-0048 registration + BUG-194 ledger entry
  2. `cb63e72` BUG-194 fix — typed confirmation gate on execution-mode switch (ux.js NEW, app.js handler)
  3. `9a0ac92` connection-lost banner + stale marking + retry-now (ux_conn.js NEW, index.html, app.js wiring)
  4. `ced7cf1` i18n framework EN/FA/DE/ES/AR + RTL + language switcher (ux_i18n.js NEW, styles.css RTL/reduced-motion)
  5. `cc1d9d0` Ctrl+K command palette + shortcuts + last-tab restore (ux_palette.js NEW)
  6. `c9dba06` decision humanization — two-layer signal card + honest confidence semantics (ux_signal.js NEW)
  7. `6d9b517` serve routes for the 6 UX assets (server.py ADDITIVE, cross-owner declared)
  8. `924a075` attention strip + hidden-tab polling suppression (ux_attention.js NEW)
  9. `19bf95b` ruff lint fixes in UX test suite
  10. `ae8d688` UI-safety regression suite — 28 tests (tests/unit/test_web_ux_safety.py NEW)
  11. `0b3a866` UX suite wired into tests/critical_suite.txt
  (+ taskboard progress note `eb6a818`, taskboard closure commit from this agent)

## Files Changed (functions/classes level)
- **Web/ux.js** (NEW, ~330L): `NX.confirmTyped(spec)` (typed-confirmation modal, focus trap, Esc/Tab handling), `NX.confirmModeChange(from,to)` (LIVE requires typing "LIVE"), `NX.toast(msg,kind)` (4s dedupe non-spam), `NX.markStale(el,ageSec)`.
- **Web/ux_conn.js** (NEW, ~130L): `NXConn` UP/DEGRADED/DOWN controller; `setUp()` on every real live event (SSE tick/state/heartbeat, snapshot OK), `setDown()` on SSE error/fetch failure, `setDegraded()` on 30s stale-stream; honest "Last update HH:MM:SS (Ns ago)"; Retry-now button calls `fetchSystemSnapshot()+startSSE()`.
- **Web/ux_i18n.js** (NEW, ~290L): `NX_I18N` EN(source)/FA/DE/ES/AR dictionaries, `dir`/`lang` + `ux-rtl` on `<html>`, `nexus:lang-changed` event, localStorage `nexus.ui.lang` (UI-only), browser-language detection.
- **Web/ux_palette.js** (NEW, ~300L): `NXPalette` Ctrl/Cmd+K palette (14 tabs + 7 actions + help), Alt+1..4 tab jumps, R refresh (never while typing), last-tab restore `nexus.ui.tab`. **Safety: zero dangerous commands in palette.**
- **Web/ux_signal.js** (NEW, ~160L): `NXSignal.render(payload)` two-layer decision card. TRUTH RULE: `ai_confidence==0` with a non-gate reason renders "Signal not available" (model not consulted), never a fake 0.0%; unknown reason codes render verbatim.
- **Web/ux_attention.js** (NEW, ~130L): `NXAttention.render(snapshot)` CRITICAL/ACTION/calm strip; rows ONLY from payload fields (`health.subsystems`, `is_stale`, `runtime_mode`, NXConn state); no fetch of its own; string-key re-render dedupe.
- **Web/app.js** (5 surgical edits): mode handler → `await NX.confirmModeChange` + revert on cancel; `NXConn.setUp/setDown/setDegraded` at 10 call sites; account 30s polling gated on `document.hidden` + visibilitychange catch-up; decision card routes through `NXSignal.render` (legacy path preserved as fallback); language switcher binding.
- **Web/index.html**: `#conn-lost-banner` (role=alert, starts hidden) first in body; language select in sidebar preferences; 6 ux script tags ordered BEFORE app.js.
- **Web/styles.css** (appended): RTL mirroring (`.tab-btn.active` right border, aside border flip), `prefers-reduced-motion` kill-switch, `.ux-stale` dimming.
- **src/nexus_scalp/web/server.py** (+27L, CROSS-OWNER additive only): 6 FileResponse serve routes `/ux_*.js`, verbatim pattern, placed before `/app.js`. No existing route/handler touched.
- **tests/unit/test_web_ux_safety.py** (NEW, 28 tests): mode gate, palette safety, connectivity truth, humanization truth rules, i18n coverage, serve routes via `create_app` (skips if foreign WIP breaks it), index load order, attention payload-sourcing, polling suppression.
- **tests/critical_suite.txt**: +1 path with rationale comments (manifest verifier: 67 paths OK).

## Shared Functions / Contracts
- NO shared Python function changed. `performEngineModeSet` (JS) posts the SAME `/api/engine/mode` payload as before — the gate sits strictly in front of it.
- Contracts touched: NONE (presentation-only). UI_STATE consumers only. INV-010 untouched (no Telegram paths). Settings DB untouched.

## Runtime Verification (evidence-graded)
- TESTED: 28/28 UX-safety tests green; 51 green across UX+web-security+deploy-drift suites; ruff clean; `ruff format --check` clean; mypy server.py clean; `py_compile` server.py OK; `node --check` on all 6 JS modules OK; manifest verifier OK; TestClient 200 + exact byte counts for all 6 assets; standalone route probe.
- OBSERVED (live, pre-edit baseline): running server :8080 v9.0.3 PAPER/DEMO; /api/status payload fields the strip consumes exist (health.subsystems, is_stale, runtime_mode, live_freshness).
- NOT RUNTIME VERIFIED YET: the live :8080 process still serves the OLD bundle (404s for ux_*.js are EXPECTED until restart). Restart must follow the live-restart playbook (quarantine foreign WIP, taskkill worker, relaunch `NexusTradingForexBot.py`, verify /api/status + SSE + ux assets 200, restore WIP).

## Parallel-Agent Disclosures
- **Absorption:** `c9dba06` carries `tests/installer/test_lifecycle.py` (foreign, staged by parallel installer agent at commit time). Verified: compiles, 8/8 tests PASS offline-by-default suite. Owner should treat c9dba06 as the carrier; no content altered by me.
- **Foreign WIP untouched (never staged/committed by me):** api_v1_wiring.py + api_v1/ (CHG-0043 platform, features.py referenced-not-present breaks full `create_app` import at the moment — my TestClient test skips cleanly in that state), live_engine.py, order_manager, release/*, telegram_notifier, installer/install.ps1, docs/*, ~12 other modified files in tree.

## Bugs Fixed / Discovered
- **Fixed:** BUG-194 (PAPER→LIVE zero-confirmation; upgraded CHG-0043's mid-pass confirmDialog to typed gate + PAPER↔SHADOW coverage + removed the silent no-op fallback when NX.cc.design was absent).
- **Discovered, NOT fixed (documented):** 1) LIVE server restart required to serve new assets (process older than commits); 2) `create_app()` currently fails on foreign api_v1 WIP (features.py missing) — NOT mine to fix; 3) NO_TRADE confidence 0.0 root semantics live in policy layer (my fix is presentation-only per scope §55).

## Known Risks
- Panel-level strings beyond shared chrome remain EN-only until per-panel migration (framework is in place).
- Palette jump targets assume the current 14 tab ids (index-load-order test + palette test will catch tab removal).
- Reduced-motion CSS uses a broad transition-duration kill inside the media query (intentional, per brief §36).

## Unfinished / Next-Agent Instructions
1. **Runtime restart + live smoke** (owner: any agent with restart authorization; follow observability-and-live-restart-playbook): verify `/ux_*.js` 200, banner appears on SSE kill within ~5s, mode PAPER→LIVE shows typed dialog, Ctrl+K opens palette, `dir=rtl` flips on FA.
2. **Per-panel i18n migration** (owner: Hermes-UI successor): move domain-panel strings onto NX_I18N keys progressively.
3. **Setup wizard / HOME tab** (brief §4/§5/§9, deferred): the attention strip + palette + tab-restore are the groundwork; a dedicated HOME aggregation tab is the natural next step.
4. After foreign CHG-0043 api_v1 work lands, remove the `pytest.skip` path in TestServeRoutes if desired (it currently self-heals).

## Verification Commands
```
.venv/Scripts/python.exe -m pytest tests/unit/test_web_ux_safety.py -q        # 28 passed
.venv/Scripts/python.exe -m ruff check tests/unit/test_web_ux_safety.py       # clean
node --check Web/ux.js && node --check Web/ux_conn.js && node --check Web/ux_i18n.js \
  && node --check Web/ux_signal.js && node --check Web/ux_attention.js && node --check Web/ux_palette.js
.venv/Scripts/python.exe scripts/ci/verify_critical_suite_manifest.py         # 67 paths OK
```
