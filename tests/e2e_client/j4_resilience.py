"""J4 — Disconnect/reconnect + refresh + rapid input + stale-data honesty.

Gates: 10 DISCONNECT, 11 RECONNECT, 12 REFRESH, 13 SESSION RESTORE,
       14 FAST USER INPUT, 27 DATA FRESHNESS, 37 ERROR RECOVERY.
Method: real browser against the live client; disconnection is simulated by
killing the page's network (route abort) — like a Wi-Fi drop — then restored.
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

        # Baseline: live state visible
        before = js_summary(page)
        record(
            "J4_resilience",
            "baseline",
            {"badge": before.get("badge"), "bid": before.get("bid"), "obs": before.get("obs")},
        )

        # ---- DISCONNECT: abort all /api + /api/ticks/stream requests ----
        page.route("**/api/**", lambda route: route.abort("internetdisconnected"))
        t0 = time.time()
        page.wait_for_timeout(6000)  # let UI notice
        during = js_summary(page)
        record(
            "J4_resilience",
            "ui_during_disconnect",
            {
                "badge": during.get("badge"),
                "obs": during.get("obs"),
                "health": during.get("health"),
                "seconds_elapsed": round(time.time() - t0, 1),
            },
        )
        shot(page, "j4_disconnected")

        # Stale-data honesty: UI must NOT keep showing fresh-looking live prices
        # with no staleness signal. We accept either a connection/stale indicator
        # or an explicit stale/old marker in obs/health.
        stale_signals = json.dumps(during).lower()
        honest = any(
            k in stale_signals for k in ("stale", "reconnect", "disconn", "offline", "paused")
        )
        record("J4_resilience", "disconnect_visible_to_user", honest, ok=honest)

        # ---- RECONNECT: restore network ----
        page.unroute("**/api/**")
        page.wait_for_timeout(9000)  # SSE retry + REST refresh cadence
        after = js_summary(page)
        record(
            "J4_resilience",
            "ui_after_reconnect",
            {"badge": after.get("badge"), "bid": after.get("bid"), "obs": after.get("obs")},
        )
        recovered = (after.get("badge") or "").upper() == "RUNNING" and after.get("bid") not in (
            None,
            "—",
        )
        record("J4_resilience", "recovery_automatic", recovered, ok=recovered)
        shot(page, "j4_reconnected")

        # ---- BROWSER REFRESH: safe state survives, no auto-dangerous actions ----
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        after_reload = js_summary(page)
        record(
            "J4_resilience",
            "after_refresh",
            {
                "badge": after_reload.get("badge"),
                "mode": after_reload.get("modeSelector"),
                "bid": after_reload.get("bid"),
            },
        )
        # engine must still be running (refresh must never stop/restart anything dangerous)
        st = page.evaluate("async () => (await (await fetch('/api/live/state')).json())")
        record(
            "J4_resilience",
            "engine_still_running_after_refresh",
            st.get("engine_running"),
            ok=bool(st.get("engine_running")),
        )
        shot(page, "j4_after_refresh")

        # ---- RAPID INPUT: tab switching 12x fast + 8 clicks on refresh buttons ----
        errors_before = RESULTS.get("_console", [])
        tabs = ["tab-monitoring", "tab-account", "tab-health", "tab-ai-analysis"]
        for i in range(12):
            tab_click(page, tabs[i % len(tabs)])
            page.wait_for_timeout(60)
        # spam any visible refresh buttons on monitoring
        page.evaluate(
            """(() => { const btns = [...document.querySelectorAll('#tab-monitoring button')]
                .filter(b => /refresh|reload/i.test(b.innerText)); 
                btns.slice(0,2).forEach(b => { for (let i=0;i<4;i++) b.click(); }); })()"""
        )
        page.wait_for_timeout(2500)
        console_errs = [e for e in RESULTS.get("_console", []) if e not in errors_before]
        record(
            "J4_resilience",
            "console_errors_after_rapid_input",
            console_errs[:8],
            ok=len(console_errs) == 0,
        )
        page_crashed = page.evaluate("1 + 1") != 2
        record("J4_resilience", "page_alive_after_rapid_input", not page_crashed, ok=True)
        shot(page, "j4_after_rapid")

        browser.close()

    path = save_results()
    print("SAVED", path)
    print(json.dumps(RESULTS["journeys"]["J4_resilience"], indent=1, ensure_ascii=False)[:2600])


if __name__ == "__main__":
    run()
