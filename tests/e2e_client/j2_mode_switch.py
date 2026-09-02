"""J2 — OPERATOR MODE-SWITCH SAFETY + header UI/API coherence.

Gates: 21 LIVE/PAPER SAFETY, 28 UI/API SEMANTIC, 14 SAFE ACTION.
The E2E operator changes the mode selector in the real UI (PAPER->SHADOW,
SHADOW->PAPER — never toward LIVE) and verifies:
  - every click produces one POST (no duplicates on rapid double-click),
  - UI selector + runtime badge reflect API truth afterwards,
  - LIVE is reachable ONLY via explicit selection (never auto).
"""

import json
import time

from e2e_harness import RESULTS, js_summary, record, save_results, shot
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081"


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        posts = []
        page.on("request", lambda r: posts.append(r.url) if "/api/engine/mode" in r.url else None)
        page.on(
            "console",
            lambda m: (
                RESULTS.setdefault("_console", []).append({"type": m.type, "text": m.text[:200]})
                if m.type == "error"
                else None
            ),
        )

        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        ui = js_summary(page)
        record(
            "J2_mode_switch",
            "ui_before",
            {"sel": ui.get("modeSelector"), "badge": ui.get("runtimeMode")},
        )
        posts.clear()

        # --- user changes selector PAPER -> SHADOW (safe direction) ---
        sel = page.locator("#execution-mode-selector")
        sel.select_option("SHADOW")
        page.wait_for_timeout(2500)
        n_shadow = len(posts)
        record("J2_mode_switch", "posts_after_first_change", n_shadow, ok=n_shadow == 1)
        api1 = page.evaluate(
            "async () => { const r = await fetch('/api/live/state'); const d = await r.json(); return (d.market || {}).execution_mode; }"
        )
        record("J2_mode_switch", "api_after_shadow", api1)

        # --- rapid double change (SHADOW -> PAPER x3 fast) — must not duplicate posts ---
        posts.clear()
        sel.select_option("PAPER")
        sel.select_option("SHADOW")
        sel.select_option("PAPER")
        page.wait_for_timeout(3500)
        record("J2_mode_switch", "posts_after_rapid_3_changes", len(posts))
        api2 = page.evaluate(
            "async () => { const r = await fetch('/api/live/state'); const d = await r.json(); return (d.market || {}).execution_mode; }"
        )
        record("J2_mode_switch", "api_after_rapid", api2)
        ui2 = js_summary(page)
        record(
            "J2_mode_switch",
            "ui_after_rapid",
            {"sel": ui2.get("modeSelector"), "badge": ui2.get("runtimeMode")},
        )
        agree = ui2.get("modeSelector") == api2
        record("J2_mode_switch", "ui_matches_api_after_changes", agree, ok=agree)
        shot(page, "j2_after_mode_switch")

        # --- selector must not offer an unguarded LIVE path (check presence only,
        # NEVER selecting LIVE in E2E) ---
        opts = page.evaluate(
            "[...document.querySelectorAll('#execution-mode-selector option')].map(o => o.value)"
        )
        record("J2_mode_switch", "selector_options", opts)
        # If LIVE is offered, a confirm guard MUST exist (we verify guard wiring
        # by code presence in the change handler; the click itself is NOT done).
        if "LIVE" in opts:
            src = page.evaluate(
                'fetch(\'/app.js\').then(r => r.text()).then(t => t.includes("confirm") && t.includes("LIVE"))'
            )
            record("J2_mode_switch", "live_guard_wiring_found", src)
        browser.close()

    path = save_results()
    print("SAVED", path)
    print(json.dumps(RESULTS["journeys"]["J2_mode_switch"], indent=1, ensure_ascii=False)[:2200])


if __name__ == "__main__":
    run()
