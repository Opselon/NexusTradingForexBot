"""J3 — Signal / NO_TRADE drilldown + decision inspector + positions + diagnostics.

Gates: 04 SIGNAL, 05 NO_TRADE, 06 POSITIONS, 08 DIAGNOSTICS, 18 DECISION DRILLDOWN.
Black-box user questions:
  - "What is the current signal and WHY?" (human reason + technical evidence)
  - "Show me the decision list and let me drill in and come back."
  - "Show my positions / empty state."
  - "System health and diagnostics without a terminal."
"""

import json
import time

from e2e_harness import RESULTS, js_summary, record, save_results, shot
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081"


def tab_click(page, tab):
    page.evaluate(
        f"(() => {{ const b=[...document.querySelectorAll('aside button.tab-btn')]"
        f".find(x => (x.getAttribute('onclick')||'').includes('{tab}')); b && b.click(); }})()"
    )


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(4500)

        # ---- SIGNAL EXPERIENCE (monitoring tab = default) ----
        decision = page.evaluate(
            """(() => ({
                badge: document.getElementById('ai-decision-badge')?.innerText?.trim(),
                conf: document.getElementById('ai-confidence')?.innerText?.trim(),
                reason: document.getElementById('ai-reason-text')?.innerText?.trim()?.slice(0, 220),
            }))()"""
        )
        record("J3_signal", "decision_badge", decision)
        st = page.evaluate("async () => (await (await fetch('/api/live/state')).json())")
        strat = st.get("strategy") or {}
        record(
            "J3_signal",
            "api_strategy_decision",
            {"decision": strat.get("decision"), "reason": (strat.get("reason") or "")[:200]},
        )
        # NO_TRADE must be explainable: a reason string must exist whenever decision is NO_TRADE-ish
        no_trade = str(strat.get("decision", "")).upper() in ("NO_TRADE", "WAIT", "")
        record("J3_signal", "no_trade_present", no_trade)
        record(
            "J3_signal",
            "reason_available_for_no_trade",
            bool(strat.get("reason")),
            ok=bool(strat.get("reason")),
        )
        # Technical evidence: probabilities + gate info reachable in UI?
        monitor_text = page.evaluate(
            "document.getElementById('tab-monitoring')?.innerText.replace(/\\s+/g,' ').slice(0, 1500)"
        )
        has_tech = ("Conf" in (monitor_text or "")) or ("prob" in monitor_text.lower())
        record("J3_signal", "technical_evidence_visible", has_tech, ok=has_tech)
        shot(page, "j3_signal_monitoring")

        # ---- DECISION LIST DRILLDOWN (ai-analysis tab) ----
        tab_click(page, "tab-ai-analysis")
        page.wait_for_timeout(2500)
        ai_rows = page.evaluate(
            """(() => {
                const t = document.querySelector('#tab-ai-analysis table');
                if (!t) return {table: false};
                const rows = [...t.querySelectorAll('tbody tr')];
                return {table: true, rowCount: rows.length,
                        firstRow: rows[0]?.innerText?.replace(/\\s+/g,' ').slice(0,160)};
            })()"""
        )
        record("J3_decision_drilldown", "ai_analysis_table", ai_rows)
        # Click first row (drill in), then verify we can come back without losing the list
        clicked = False
        try:
            first_row = page.locator("#tab-ai-analysis table tbody tr").first
            if first_row.count() > 0:
                first_row.click()
                page.wait_for_timeout(1800)
                clicked = True
        except Exception:
            pass
        record("J3_decision_drilldown", "row_clickable", clicked, ok=clicked)
        shot(page, "j3_ai_drilldown")
        tab_click(page, "tab-monitoring")
        page.wait_for_timeout(1200)
        tab_click(page, "tab-ai-analysis")
        page.wait_for_timeout(1500)
        rows_after = page.evaluate(
            "document.querySelectorAll('#tab-ai-analysis table tbody tr').length"
        )
        record("J3_decision_drilldown", "list_survives_roundtrip", rows_after)

        # ---- POSITIONS ----
        tab_click(page, "tab-account")
        page.wait_for_timeout(2500)
        acct = page.evaluate(
            """(() => ({
                balance: document.querySelector('#tab-account')?.innerText?.match(/Balance[\\s\\S]{0,40}?([-\\d.,]+)/i)?.[1] || null,
                positionsSection: !!document.querySelector('#tab-account .positions, #tab-account [id*=position]'),
                textSample: document.getElementById('tab-account')?.innerText.replace(/\\s+/g,' ').slice(0, 400),
            }))()"""
        )
        record("J3_positions", "account_tab", acct)
        # API truth for positions
        api_pos = page.evaluate(
            "async () => { const d = await (await fetch('/api/live/state')).json(); return (d.positions || []).length; }"
        )
        record("J3_positions", "api_positions_count", api_pos)
        shot(page, "j3_positions")

        # ---- DIAGNOSTICS (health tab, incidents tab) ----
        tab_click(page, "tab-health")
        page.wait_for_timeout(2500)
        health_txt = page.evaluate(
            "document.getElementById('tab-health')?.innerText.replace(/\\s+/g,' ').slice(0, 700)"
        )
        record("J3_diagnostics", "health_tab_text", health_txt)
        shot(page, "j3_health")
        tab_click(page, "tab-incidents")
        page.wait_for_timeout(2200)
        inc = page.evaluate(
            "document.getElementById('tab-incidents')?.innerText.replace(/\\s+/g,' ').slice(0, 400)"
        )
        record("J3_diagnostics", "incidents_tab_text", inc)
        browser.close()

    path = save_results()
    print("SAVED", path)
    print(json.dumps(RESULTS["journeys"], indent=1, ensure_ascii=False)[:3200])


if __name__ == "__main__":
    run()
