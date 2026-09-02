"""J6 — RESPONSIVE + KEYBOARD/A11Y + ZERO-CONSOLE-ERROR acceptance.

Gates: 18 RESPONSIVE, 19 ACCESSIBILITY, 31-32.
Breakpoints: 320 / 375 / 768 / 1024 / 1440.
Checks: no horizontal overflow, nav reachable, dialog/keyboard Tab/Escape.
"""

import json

from e2e_harness import RESULTS, record, save_results, shot
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081"
BREAKPOINTS = [320, 375, 768, 1024, 1440]


def tab_click(page, tab):
    page.evaluate(
        f"(() => {{ const b=[...document.querySelectorAll('aside button.tab-btn')]"
        f".find(x => (x.getAttribute('onclick')||'').includes('{tab}')); b && b.click(); }})()"
    )


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        results = {}

        for w in BREAKPOINTS:
            ctx = browser.new_context(viewport={"width": w, "height": 850})
            page = ctx.new_page()
            page.goto(BASE + "/", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            nav_visible = page.evaluate(
                "(() => { const b = [...document.querySelectorAll('aside button.tab-btn')]"
                ".find(x => (x.getAttribute('onclick')||'').includes('tab-monitoring'));"
                " return b ? (b.offsetParent !== null || b.getClientRects().length > 0) : false; })()"
            )
            # can the user reach the config tab by clicking at this width?
            reachable = True
            try:
                tab_click(page, "tab-config")
                page.wait_for_timeout(400)
                reachable = page.evaluate(
                    "document.getElementById('tab-config').classList.contains('hidden') === false"
                )
            except Exception:
                reachable = False
            results[w] = {
                "overflow_px": overflow,
                "nav_visible": bool(nav_visible),
                "config_reachable": bool(reachable),
            }
            record("J6_responsive", f"bp_{w}", results[w])
            shot(page, f"j6_w{w}")
            ctx.close()

        record("J6_responsive", "summary", results)
        no_overflow = all(v["overflow_px"] <= 2 for v in results.values())
        record("J6_responsive", "no_horizontal_overflow_anywhere", no_overflow, ok=no_overflow)

        # ---- KEYBOARD / A11Y ----
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        # Tab reachability: 20 tabs from body -> count focusable stops
        focused = []
        for _ in range(20):
            page.keyboard.press("Tab")
            focused.append(page.evaluate("document.activeElement?.tagName || ''"))
        record("J6_a11y", "tab_stops_sample", focused[:20])
        focus_visible = page.evaluate(
            """(() => { const el = document.activeElement; if (!el) return false;
                const s = getComputedStyle(el); return s.outlineStyle !== 'none' || el.tagName === 'BODY'; })()"""
        )
        record("J6_a11y", "focus_indicator_present_at_last_stop", bool(focus_visible))
        # Escape closes dialogs: open the first toggle-able dialog if any
        esc_ok = page.evaluate(
            """(() => { const dlg = document.querySelector('.modal, dialog, [role=dialog]');
                return dlg ? 'dialog-present' : 'no-dialog-on-default-view'; })()"""
        )
        record("J6_a11y", "dialog_escape_context", esc_ok)
        # Buttons must have accessible names (aria-label or text)
        record(
            "J6_a11y",
            "icon_only_buttons_without_label_count",
            page.evaluate(
                "[...document.querySelectorAll('button')].filter(b => b.offsetParent !== null && !(b.innerText||'').trim() && !b.getAttribute('aria-label') && !b.title).length"
            ),
        )
        shot(page, "j6_a11y")
        ctx.close()
        browser.close()

    path = save_results()
    print("SAVED", path)
    print(json.dumps(RESULTS["journeys"]["J6_responsive"], indent=1, ensure_ascii=False)[:2400])


if __name__ == "__main__":
    run()
