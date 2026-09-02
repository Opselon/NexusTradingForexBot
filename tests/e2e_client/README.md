# Client E2E Golden Journeys (black-box acceptance)

Real-browser (Playwright) operator journeys against a RUNNING engine.
Safety: PAPER/SHADOW only — the suite never POSTS toward LIVE, never places
orders, never mutates model/risk state. Journey J2 flips LIVE->PAPER when
starting in LIVE (safe direction only).

Run (engine must be reachable):
    NSE_E2E_BASE=http://127.0.0.1:8081 python j1_golden.py
Evidence (screenshots + JSON results) lands in ./evidence/.

Journeys map to acceptance gates:
  j1 -> GATE 01/02 (first run, startup) + 03 + 26 (real-time data)
  j2 -> GATE 21 (LIVE/PAPER safety) + 28 (UI/API semantics) + 14
  j3 -> GATE 04/05 (signal, NO_TRADE) + 06 + 08 + 18
  j4 -> GATE 10/11/12/13 (disconnect/reconnect/refresh/session) + 14
  j6 -> GATE 18/19 (responsive, a11y)
  j7 -> GATE 16 (localization presence; current status = UX GAP, EN-only)
