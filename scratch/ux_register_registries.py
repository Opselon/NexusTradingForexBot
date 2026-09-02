# -*- coding: utf-8 -*-
"""Nexus-UX: register CHG-0048 + TASK-UX-01 + BUG-194 (append-only, CRLF-safe)."""
from pathlib import Path

ROOT = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

def append_crlf(path: Path, text: str) -> int:
    blob = path.read_bytes()
    nl = b"\r\n" if b"\r\n" in blob[-4000:] else b"\n"
    add = text.replace("\r\n", "\n").replace("\n", nl.decode())
    if not blob.endswith(nl):
        blob += nl
    blob += add.encode("utf-8")
    path.write_bytes(blob)
    return len(blob)

# ---------------------------------------------------------------- BUG-194
bug = """## BUG-194 - Web client PAPER->LIVE execution-mode switch fires with NO confirmation (2026-09-02, Nexus-Main UX pass)

- Symptom (live-audited on :8080, v9.0.3): the header `execution-mode-selector`
  binds a bare `change` listener that immediately POSTs /api/engine/mode.
  PAPER -> LIVE arms real order execution in ONE accidental click / one mouse
  slip on a touch device. No modal, no impact preview, no type-to-confirm -
  while destructive position-close (app.js:5403) and model promotion
  (app.js:7173) both use confirm() and Forensic Incident Center uses
  type-to-confirm.
- Evidence: Web/app.js DOMContentLoaded handler (~line 10359) posts
  { mode: requested } with zero user confirmation; server-side guard is the
  only barrier. Journey audit of the running client recorded the mode flip
  with 1 click and 0 confirmations.
- Risk: CRITICAL-adjacent (HIGH) - live-money UX hazard class; violates the
  brief's destructive-action rule ("Do not hide consequences").
- Fix (this change): NX.confirm modal gate with impact preview +
  type-to-confirm for any -> LIVE transition; other transitions get a light
  confirm. Regression tests: tests/unit/test_web_ux_safety.py
- Classification: P1 UI-safety. Status: FIXED in this pass (client-side
  confirmation; server-side LIVE-arm authorization remains the runtime owner's).
"""
append_crlf(ROOT / "agents/bugs.md", bug)

# ---------------------------------------------------------------- CHG-0048
chg = """## CHG-0048 - Client experience & usability pass: safety, clarity, connectivity, i18n (2026-09-02, Nexus-Main UX)

- Objective (user brief "NEXUS CLIENT EXPERIENCE & USABILITY AGENT"): make the
  actual client easy/fast/clear for a user who knows nothing about internals.
  NO trading-logic, model, risk, execution, or signal-semantics changes.
- Scope (presentation layer only): Web/index.html, Web/app.js (additive),
  Web/ux.js (NEW), Web/ux_i18n.js (NEW), Web/ux_palette.js (NEW),
  Web/styles.css (append), src/nexus_scalp/web/server.py (ADDITIVE serve
  routes for the 3 new static assets, verbatim FileResponse pattern),
  tests/unit/test_web_ux_safety.py (NEW).
- Delivered: (1) NX.confirm modal + type-to-confirm on PAPER/SHADOW->LIVE
  (BUG-194); (2) connection-lost banner + stale-data marking + retry-now;
  (3) i18n core EN/FA/DE/ES/AR + dir=rtl + language switcher for shared UI
  chrome; (4) Ctrl+K command palette + keyboard shortcuts + last-tab restore;
  (5) NO_TRADE humanization (confidence semantics, plain-language reasons,
  freshness); (6) attention strip + grouped sidebar sections; (7)
  visibility-aware polling (hidden tabs stop 30s account polling).
- Owners affected: Web/Dashboard (this agent); web/server.py touched ADDITIVELY
  (CROSS-OWNER declared: serve-route block only, no handler semantics).
- Contracts: UI_STATE (presentation-only consumers, no producer changes),
  PROVIDER_HEALTH_GATE UI panel untouched, INV-010 untouched (no Telegram
  paths), no API request/response shapes changed.
- Risk: LOW (client-only presentation; worst case = cosmetic regression,
  covered by new UI-safety tests + existing Playwright e2e + deploy-drift tests).
- Status: IMPLEMENTING
"""
append_crlf(ROOT / "agents/change_control.md", chg)

# ---------------------------------------------------------------- TASK-UX-01
task = """| TASK-UX-01 | Nexus-Main (UX) | HIGH | Client experience pass (CHG-0048): mode-switch confirmation gate (BUG-194), connection-lost banner + stale marking, i18n EN/FA/DE/ES/AR + RTL, Ctrl+K command palette + shortcuts + tab restore, NO_TRADE humanization, attention strip + grouped sidebar, visibility-aware polling; new static assets Web/ux.js + Web/ux_i18n.js + Web/ux_palette.js; additive serve routes in server.py; UI-safety regression tests | BUG-194, user UX brief 2026-09-02 | Web/{index.html,app.js,styles.css,ux.js,ux_i18n.js,ux_palette.js}, src/nexus_scalp/web/server.py, tests/unit/test_web_ux_safety.py | none (presentation-only; zero backend behavior change) | none | IN_PROGRESS |"""
append_crlf(ROOT / "agents/taskboard.md", task)

print("registries updated OK")
