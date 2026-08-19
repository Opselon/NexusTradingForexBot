# TASK-120-FORENSIC: Forensic Incident Center UI Fix (BUG-120)

Date: 2026-08-19
Agent: Hermes-Forensic-04
Branch: main (HEAD moved during session by parallel agents)

## Summary
Forensic Incident Center tab rendered as an invisible 0x0 panel. Root cause
was a SECTION-level nesting bug in Web/index.html, not a data/API failure.

## Evidence chain (every link verified)
1. UI: nav button exists, switchTab('tab-incidents') wired, loadIncidents()
   defined, all DOM ids present. NO console/page errors, NO failed requests.
2. API: /api/diagnostics/health + /incidents?limit=50 -> 200 with correct
   schema. Detail/search/forensics/lineage endpoints all 200.
3. DB: artifacts/audit.db has incidents/incident_events/
   incident_value_traces/incident_quarantine (5 incidents: 1 CRITICAL,
   1 HIGH, 3 MEDIUM; statuses OPEN/ROOT_CAUSE_IDENTIFIED).
4. Store: IncidentStore.list_incidents/count/get all work; as_dict()
   JSON-serializable.
5. RENDERING (the broken link): tab-incidents was nested INSIDE
   tab-liquidity (missing </section> since commit 111f16e6). switchTab()
   unhid the child; the hidden parent (display:none) kept it at 0x0.
   div_balance_check.py PASSED because the imbalance was in <section>
   tags, not <div>s.

## Root cause
`<section id="tab-liquidity">` (opened line 2229) never closed before
`<section id="tab-incidents">` (line 2319). Incident panel was a child of
the liquidity panel.

## Fix (smallest correct)
- Insert `</section>` after the liquidity panel's closing `</div>` (before
  the TAB 13 comment).
- Remove the orphaned trailing `</section>` (previously closed the
  mis-nested liquidity section at file end).
- Markup-only: no JS/API/DB changes.

## Regression protection
tests/unit/test_frontend_assets_phase14.py -> TestTabSectionNesting (3 tests):
- test_tab_sections_are_siblings: every .tab-content section must not
  contain another .tab-content section (walks section depth).
- test_incident_tab_section_has_expected_panels: incident DOM ids present.
- test_every_nav_button_target_has_a_sibling_section: nav switchTab targets
  all exist as sections.

## Verification (real browser, live paper session)
Playwright headless chromium -> http://127.0.0.1:8099 (nexus start --mode
paper --port 8099):
- BEFORE: INCIDENT_PANEL_VISIBLE=false, rect {x:0,y:0,w:0,h:0}
- AFTER:  INCIDENT_PANEL_VISIBLE=true, rect {x:280,y:162,w:1296,h:1009.5}
- 5 incident cards render; detail view opens; search finds INC-2026-D5659C10;
  Accounting probe (50 checked / 11 RECOVERABLE_FROM_BROKER); Timebase probe.
- 0 console errors, 0 page errors, 0 failed requests.
- Liquidity + Monitoring tabs still work (no collateral damage).

## Pre-existing note (not a regression)
The settings DB application_settings had execution.mode=LIVE persisted
(version 25). For the safe paper test session it was set to PAPER via
SettingsDatabase.set() (the canonical service API, version 26). The engine
truthfully reports runtime mode from connection state; with no MT5
connected it never trades. User should double-check the desired persisted
mode before the next LIVE session.

## Surviving artifacts
- Release bundle release/v9.0.0/.../index.html still contains the OLD
  broken nesting (built before the fix). The next release build regenerates
  it from Web/index.html; no manual edit needed.

## Commits
- Fix absorbed into 2d7a295 (Hermes-CI-Reporting) — index.html 5-line diff.
- 4f45a26 Hermes-Forensic-04: BUG-120 ledger entry + TestTabSectionNesting.

## EXACT NEXT-AGENT INSTRUCTIONS
- Nothing pending: incident center works. If you touch Web/index.html again,
  re-run tests/unit/test_frontend_assets_phase14.py::TestTabSectionNesting
  AND the div_balance_check.py script (they catch different bug classes).