"""Cross-platform default-browser opening.

Uses the standard-library ``webbrowser`` module, which dispatches to the
platform's registered browser APIs (os.startfile on Windows, open/xdg-open
through the OS registry elsewhere) — never a hand-built shell string.
Failures are reported, never silent, and the caller falls back to showing a
copyable link.
"""

from __future__ import annotations

import webbrowser


def open_default_browser(url: str) -> bool:
    """Best-effort open in the system default browser. True on success."""
    if not url.startswith(("https://", "http://")):
        return False
    try:
        return bool(webbrowser.open(url, new=1, autoraise=True))
    except Exception:  # any browser failure is just "no open"
        return False
