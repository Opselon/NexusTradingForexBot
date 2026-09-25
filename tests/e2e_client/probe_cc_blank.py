"""BUG-206 probe — Control Center tab renders BLANK.

Repro: switchTab('tab-control-center') -> app.js hook calls
window.NX.cc.views.showTab('cc-overview') -> control_center.js render():
document.getElementById('cc-overview') -> NOT FOUND (index.html only ships
<section id=tab-control-center><div id=cc-root></div></section> — no
per-view cc-* element ids) -> early return -> #cc-root stays empty => void panel.

This script asserts the DOM contract the CC frontend expects.
"""

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    pg.evaluate(
        "(() => { const b=[...document.querySelectorAll('aside button.tab-btn')]"
        ".find(x => (x.getAttribute('onclick')||'').includes('tab-control-center')); b && b.click(); })()"
    )
    pg.wait_for_timeout(2500)
    state = pg.evaluate(
        """(() => ({
            root: !!document.getElementById('cc-root'),
            rootChildren: document.getElementById('cc-root')?.childElementCount ?? -1,
            ccOverviewEl: !!document.getElementById('cc-overview'),
            ccDecisionsEl: !!document.getElementById('cc-decisions'),
            nxcc: !!(window.NX && window.NX.cc && window.NX.cc.views),
            bootRan: !!(window.NX && window.NX.cc && window.NX.cc.state &&
                        window.NX.cc.state.snapshot('cc-summary') !== undefined),
            snapState: window.NX?.cc?.state?.snapshot?.('cc-summary')?.state ?? 'N/A',
        }))()"""
    )
    print("CC_DOM:", state)
    b.close()
