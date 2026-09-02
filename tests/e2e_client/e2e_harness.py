"""NSE Client E2E harness — black-box acceptance via Playwright.

Real-browser journeys against http://127.0.0.1:8080 (PAPER engine).
Evidence: JSON results + screenshots in ./evidence/.
NO real trading: PAPER mode only, never clicks LIVE-confirm, never order UI.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

BASE = os.environ.get("NSE_E2E_BASE", "http://127.0.0.1:8080")
EVDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence")
os.makedirs(EVDIR, exist_ok=True)

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
RESULTS = {"started": STAMP, "base": BASE, "journeys": {}}
CONSOLE_ERRORS = []


def shot(page, name):
    path = os.path.join(EVDIR, f"{STAMP}_{name}.png")
    try:
        page.screenshot(path=path, full_page=False)
        return path
    except Exception as e:
        return f"SHOT_FAIL:{e}"


def attach_console(page):
    page.on(
        "console",
        lambda m: (
            CONSOLE_ERRORS.append({"type": m.type, "text": m.text[:300]})
            if m.type in ("error",)
            else None
        ),
    )
    page.on(
        "pageerror", lambda e: CONSOLE_ERRORS.append({"type": "pageerror", "text": str(e)[:300]})
    )


def record(journey, key, value, ok=None):
    j = RESULTS["journeys"].setdefault(journey, {})
    j[key] = value
    if ok is not None:
        checks = j.setdefault("checks", [])
        checks.append({"key": key, "ok": bool(ok)})


def save_results():
    path = os.path.join(EVDIR, f"results_{STAMP}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2, ensure_ascii=False)
    return path


def new_page(ctx):
    page = ctx.new_page()
    attach_console(page)
    return page


def timed_load(page, url, wait_hint=None, timeout=30000):
    t0 = time.time()
    page.goto(url, timeout=timeout, wait_until="domcontentloaded")
    dt_dom = time.time() - t0
    try:
        page.wait_for_load_state("load", timeout=timeout)
    except Exception:
        pass
    dt_load = time.time() - t0
    if wait_hint:
        try:
            page.wait_for_selector(wait_hint, timeout=15000)
        except Exception:
            pass
    return dt_dom, dt_load


def btn_by_text(page, text, exact=False):
    """Find a visible button/element by its text content (user-like discovery)."""
    norm = text.strip().lower()
    for sel in ("button", "a", "[role=button]", "[onclick]"):
        els = page.locator(sel)
        n = els.count()
        for i in range(min(n, 400)):
            el = els.nth(i)
            try:
                if not el.is_visible():
                    continue
                t = (el.inner_text() or "").strip().lower()
            except Exception:
                continue
            if (exact and t == norm) or (not exact and norm in t):
                return el
    return None


def section_visible(page, tab_id):
    return page.evaluate(
        f"(() => {{ const s = document.getElementById('{tab_id}');"
        f" return s ? !s.classList.contains('hidden') : false; }})()"
    )


def js_summary(page):
    """Everything a user can currently see in the header (status panel)."""
    return page.evaluate(
        """(() => ({
            badge: document.querySelector('#system-status-badge')?.innerText?.trim(),
            runtimeMode: document.querySelector('#runtime-mode-badge')?.innerText?.trim(),
            modeSelector: document.querySelector('#execution-mode-selector')?.value,
            obs: document.querySelector('#obs-strip')?.innerText?.trim(),
            health: document.querySelector('#header-health-badge')?.innerText?.trim(),
            healthDetail: document.querySelector('#header-health-detail')?.innerText?.trim(),
            symbol: document.querySelector('#quick-symbol')?.innerText?.trim(),
            bid: document.querySelector('#quick-bid')?.innerText?.trim(),
            ask: document.querySelector('#quick-ask')?.innerText?.trim(),
            regime: document.querySelector('#quick-regime')?.innerText?.trim(),
            lastUpdate: document.querySelector('#header-last-update')?.innerText?.trim(),
            activeTab: document.querySelector('.tab-btn.active')?.innerText?.trim()?.slice(0, 40),
        }))()"""
    )


def wait_for_live_data(page, key="bid", timeout_s=25):
    """Wait until a header market value looks real (numeric, not dash)."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        s = js_summary(page)
        last = s
        v = s.get(key) or ""
        if re.match(r"^\d", v):
            return {"ok": True, "seconds": round(time.time() - t0, 2), "summary": s}
        time.sleep(0.5)
    return {"ok": False, "seconds": round(time.time() - t0, 2), "summary": last}


def api(page, path, method="GET", body=None):
    """Call backend API from inside the page (same-origin, like the client itself)."""
    return page.evaluate(
        """async ([path, method, body]) => {
            try {
                const r = await fetch(path, {method, headers: {'Content-Type': 'application/json'},
                                             body: body ? JSON.stringify(body) : undefined});
                let data = null;
                try { data = await r.json(); } catch { data = null; }
                return {status: r.status, data};
            } catch (e) { return {status: 0, error: String(e)}; }
        }""",
        [path, method, body],
    )
