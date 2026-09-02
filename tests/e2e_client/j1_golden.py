"""J1 — GOLDEN JOURNEY: fresh start → understand → navigate → verify live data.

Gates: 01 FIRST RUN, 02 STARTUP, 03 DAILY USE (initial), 26 REAL-TIME DATA.
"""

import json
import time

from e2e_harness import (
    BASE,
    CONSOLE_ERRORS,
    RESULTS,
    btn_by_text,
    js_summary,
    new_page,
    record,
    save_results,
    shot,
    timed_load,
    wait_for_live_data,
)
from playwright.sync_api import sync_playwright

TABS = [
    "tab-monitoring",
    "tab-health",
    "tab-account",
    "tab-ai-analysis",
    "tab-research",
    "tab-factory",
    "tab-news",
    "tab-rules",
    "tab-config",
    "tab-debug",
    "tab-governance",
    "tab-liquidity",
    "tab-incidents",
    "tab-command-center",
    "tab-database",
]


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = new_page(ctx)

        # -- 1. FIRST LOAD
        dt_dom, dt_load = timed_load(page, BASE + "/", "header")
        record("J1_golden", "load_dom_seconds", round(dt_dom, 2))
        record("J1_golden", "load_full_seconds", round(dt_load, 2), ok=dt_load < 15)
        record("J1_golden", "header_renders", True, ok=True)

        summ = js_summary(page)
        record("J1_golden", "header_initial", summ)
        # The user must be able to tell mode immediately
        mode_understandable = bool(summ.get("modeSelector")) and bool(summ.get("runtimeMode"))
        record("J1_golden", "mode_visible_without_clicks", summ, ok=mode_understandable)

        shot(page, "j1_first_load")

        # -- 2. Header status badge vs API truth (UI/API coherence)
        health = page.evaluate("async () => (await (await fetch('/health')).json())")
        verdict = health.get("verdict")
        badge = (summ.get("badge") or "").upper()
        record("J1_golden", "api_health_verdict", verdict)
        record("J1_golden", "badge_text", badge)

        # -- 3. Sidebar navigation: count + switch through every tab
        nav_buttons = page.locator("aside button.tab-btn")
        n_nav = nav_buttons.count()
        record("J1_golden", "nav_buttons_count", n_nav, ok=n_nav >= 10)

        tab_results = {}
        for tab in TABS:
            ok_switch = page.evaluate(
                f"(() => {{ const b=[...document.querySelectorAll('aside button.tab-btn')]"
                f".find(x => (x.getAttribute('onclick')||'').includes('{tab}'));"
                f" if(!b) return false; b.click(); return true; }})()"
            )
            time.sleep(0.7)  # give the tab's loader a moment (user-paced)
            visible = page.evaluate(
                f"(() => {{ const s=document.getElementById('{tab}');"
                f" return s ? !s.classList.contains('hidden') : false; }})()"
            )
            content_len = page.evaluate(
                f"(() => {{ const s=document.getElementById('{tab}');"
                f" return s ? s.innerText.replace(/\\s+/g,' ').trim().length : -1; }})()"
            )
            tab_results[tab] = {
                "switch": ok_switch,
                "visible": visible,
                "content_chars": content_len,
            }
        record("J1_golden", "tab_matrix", tab_results)
        blanks = [t for t, r in tab_results.items() if r["visible"] and r["content_chars"] < 40]
        record("J1_golden", "blank_tabs", blanks, ok=len(blanks) == 0)
        shot(page, "j1_tab_monitoring")

        # -- 4. LIVE DATA: wait for a real bid price in the header
        live = wait_for_live_data(page, "bid", 25)
        record(
            "J1_golden",
            "live_bid_appears",
            {"ok": live["ok"], "seconds": live["seconds"]},
            ok=live["ok"],
        )
        record("J1_golden", "header_after_wait", live.get("summary"))
        shot(page, "j1_live_data")

        # -- 5. obs strip: SSE/rest connectivity visible?
        time.sleep(2)
        obs = js_summary(page).get("obs")
        record("J1_golden", "obs_strip_after_wait", obs)

        # -- 6. Console health after first pass
        errs = [e for e in CONSOLE_ERRORS if "favicon" not in e["text"].lower()]
        record("J1_golden", "console_errors", errs[:12], ok=len(errs) == 0)

        browser.close()

    path = save_results()
    print("SAVED", path)
    print(json.dumps(RESULTS["journeys"]["J1_golden"], indent=2, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    run()
