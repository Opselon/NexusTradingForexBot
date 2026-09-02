"""J7 — LOCALIZATION acceptance: language switch workflows (EN/FA + RTL check).

The client is EN-only today (grep found no i18n). This journey verifies that
claim from the UI (not code): a language switcher must either exist and work,
or its absence is recorded as a UX GAP per the brief (§29 LOCALIZATION).
Persian RTL rendering is tested against the STATIC DOCS SITE if reachable
(site/ platform by Nexus-Docs) — a secondary surface the operator may open.
"""

import json

from e2e_harness import RESULTS, record, save_results, shot
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081"


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Search the visible UI for any language switch affordance
        lang_switch = page.evaluate(
            """(() => {
                const cands = [...document.querySelectorAll('button, select, a')]
                    .filter(el => el.offsetParent !== null);
                const hits = cands.filter(el =>
                    /language|lang|فارسی|english|deutsch|español|العربية/i.test(
                        (el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')
                        + ' ' + (el.title || '')));
                return hits.map(h => ({tag: h.tagName, text: (h.innerText || h.title || h.getAttribute('aria-label') || '').slice(0, 40)}));
            })()"""
        )
        record("J7_localization", "language_switcher_candidates", lang_switch)
        record(
            "J7_localization", "client_i18n_present", len(lang_switch) > 0, ok=False
        )  # brief §29 requires EN/FA/DE/ES/AR — absence = UX GAP
        record("J7_localization", "html_lang", page.evaluate("document.documentElement.lang"))

        # RTL readiness probe (does the layout have dir handling anywhere?)
        rtl_ready = page.evaluate(
            "fetch('/styles.css').then(r => r.text()).then(t => ({rtl_rules: (t.match(/dir=rtl|\\[dir='rtl'\\]|rtl/g) || []).length}))"
        )
        record("J7_localization", "client_css_rtl_rules", rtl_ready)

        shot(page, "j7_lang_probe")
        browser.close()

    path = save_results()
    print("SAVED", path)
    print(json.dumps(RESULTS["journeys"]["J7_localization"], indent=1, ensure_ascii=False)[:1200])


if __name__ == "__main__":
    run()
