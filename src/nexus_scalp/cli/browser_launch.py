"""Browser auto-launch seam — the Control Center opens itself after readiness.

WHERE/WHY: the end user who double-clicks the release EXE must never run
npm/build/dev-server or navigate to ``/alt`` (END-USER-RUNTIME-UI-INTEGRATION
mission). ``nexus start`` opens the OS default browser exactly ONCE, only after
the runtime readiness gate passes, at the ACTUAL bound port. This module owns
that single decision, the readiness probe that decides WHEN to make it, and
that single window.

BOUNDARY: enablement decision + the readiness gate + issuing one browser open.
No server code, NO config keys (``--no-browser`` is a CLI flag;
``NSE_NO_BROWSER`` is the documented env knob). Readiness probing lives HERE
(not in engine_boot) because BOTH start paths — ``nexus start`` and the legacy
``NexusTradingForexBot.py`` launcher — must run the identical CONTRACT #10
sequence and neither may import the Typer command tree to get it.
``engine_boot._open_control_center_when_ready`` is the thin wiring wrapper the
start command calls.

CONTRACT (frozen decision #5 / #10 — implement exactly):
  ``open_control_center(url, *, force=False) -> bool``
    * resolves the OS default browser (Windows: ``webbrowser.open``, falling
      back to ``start``; posix: ``webbrowser.open``)
    * NEVER raises — every failure path returns False and logs a SAFE reason at
      warning level (no credentials, no stack traces reaching user output)
    * at most ONE window per normal start: module-level ``_launched`` guard,
      re-checked under a lock; ``force=True`` bypasses for explicit user action
    * returns True only after the URL open was actually issued
  ``maybe_open_browser(url) -> bool`` — enablement precedence:
    ``--no-browser`` flag  >  env ``NSE_NO_BROWSER`` in (1, true, yes)
    >  ``stdout.isatty()``  → open.

USED BY: ``cli/engine_boot`` (``_open_control_center_when_ready`` wrapper),
``NexusTradingForexBot.py`` (legacy launcher worker), ``cli/doctor``
(read-only status row), ``cli/main`` (facade re-export seam),
``tests/unit/test_browser_launch.py`` + ``tests/unit/test_browser_launch_eur.py``.

DO-NOT-PUT-HERE: server routes, config schema, engine lifecycle.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urljoin

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.cli.browser_launch")

#: Env values that switch auto-open OFF (CONTRACT #10: ``NSE_NO_BROWSER`` in
#: (1, true, yes); compared case-insensitively after stripping).
ENV_DISABLED_VALUES = frozenset({"1", "true", "yes"})

#: Environment kill-switch name (CONTRACT #10); also read by ``nexus doctor``.
NO_BROWSER_ENV = "NSE_NO_BROWSER"

#: FROZEN readiness budget (CONTRACT #10 "<=30s"): retry ``/health`` and ``/``
#: for at most this long before reporting the phase diagnostic instead.
READY_TIMEOUT_S = 30.0
#: Poll cadence while waiting for readiness.
READY_POLL_INTERVAL_S = 0.25
#: Per-request budget for a readiness probe (a hung probe must not eat the
#: whole 30s window).
READY_REQUEST_TIMEOUT_S = 2.0
#: Loopback host of the CANONICAL root URL (CONTRACT #10 literal:
#: ``http://127.0.0.1:<ACTUAL port>/``).
CANONICAL_HOST = "127.0.0.1"

#: Platform resolved at import so tests can substitute it without touching the
#: shared ``sys`` module (Windows gets the ``start`` fallback, posix does not).
IS_WINDOWS = sys.platform == "win32"

# One window per normal start. The guard is read AND written under the lock so
# two concurrent callers can never both issue an open.
_launch_lock = threading.Lock()
_launched = False

# Transport for the ``--no-browser`` start flag: ``start_cmd`` cannot pass a new
# keyword through the ``_run_engine`` facade seam (its signature is pinned by
# tests/unit/test_cli_end_to_end.py), so the flag records itself here for this
# process and appends ``--no-browser`` to the daemon child command line.
_no_browser_flag = False


def request_no_browser() -> None:
    """Record that the operator passed ``--no-browser`` for THIS process."""
    global _no_browser_flag  # noqa: PLW0603 - process-scoped start flag
    try:
        _no_browser_flag = True
    except Exception:  # pragma: no cover - never block a start
        _safe_log("warning", "could not record --no-browser")


def _safe_log(level: str, message: str) -> None:
    """Log without ever letting logging itself break the never-raise contract."""
    try:
        getattr(logger, level, logger.info)(message)
    except Exception:  # pragma: no cover - logging must never raise here
        pass


def _redact(text: str) -> str:
    """Strip credentials/query material before anything is logged or printed."""
    if not text:
        return ""
    cleaned = text
    at = cleaned.find("//")
    if at != -1:
        authority = cleaned[at + 2 :]
        slash = authority.find("/")
        hostpart = authority[:slash] if slash != -1 else authority
        if "@" in hostpart:
            # Keep scheme + host, drop the userinfo, KEEP path/query/etc.
            remainder = authority[slash:] if slash != -1 else ""
            hidden = hostpart.rsplit("@", 1)[1]
            cleaned = cleaned[: at + 2] + "***@" + hidden + remainder
    question = cleaned.find("?")
    if question != -1:
        cleaned = cleaned[:question] + "?<redacted>"
    return cleaned


def _safe_reason(exc: BaseException) -> str:
    """Type name + redacted, length-capped message — never a stack trace."""
    try:
        detail = _redact(str(exc)).replace("\n", " ").strip()[:120]
    except Exception:  # pragma: no cover - defensive
        detail = ""
    name = type(exc).__name__
    return f"{name}: {detail}" if detail else name


def _stdout_isatty() -> bool:
    """True when a human terminal is attached (guarded: never raises)."""
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # pragma: no cover - closed/redirected stdout
        return False


def canonical_root_url(port: int) -> str:
    """The ONE canonical Control Center URL: ``http://127.0.0.1:<port>/``.

    CONTRACT #10: always loopback, always the caller-supplied ACTUAL bind
    port, always the ``/`` root entrypoint, always exactly one trailing
    slash. Callers pass the port uvicorn ACTUALLY bound (BUG-147/BUG-267
    actual-port), never a remembered default and never a hardcoded 8080/8081.
    """
    return f"http://{CANONICAL_HOST}:{int(port)}/"


def _windows_start(url: str) -> bool:
    """Windows fallback: ``start`` hands the URL to the registered handler.

    Isolated in its own function so tests can stub it instead of ever letting a
    real browser window appear.
    """
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", url],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception:
        return False
    return True


def _open_once(url: str) -> tuple[bool, str]:
    """Issue the open; returns (issued, safe_failure_reason)."""
    try:
        if webbrowser.open(url):
            return True, ""
    except Exception as exc:
        # Windows falls through to `start`; posix has no second chance.
        if not IS_WINDOWS:
            return False, _safe_reason(exc)
        _safe_log("debug", f"webbrowser.open failed, trying start: {_safe_reason(exc)}")
    if IS_WINDOWS and _windows_start(url):
        return True, ""
    return False, "no OS browser handler accepted the URL"


def open_control_center(url: str, *, force: bool = False) -> bool:
    """Open ``url`` in the OS default browser — at most one window per start.

    Contract: NEVER raises; every failure path returns False with a warning-level
    reason that carries no credentials and no stack trace. Returns True only
    after the open was actually issued. ``force=True`` bypasses the one-window
    guard for an explicit user action.
    """
    global _launched  # noqa: PLW0603 - one-window guard, always under the lock

    if not isinstance(url, str) or not url.strip():
        _safe_log("warning", "Control Center not opened: empty URL")
        return False
    target = url.strip()

    with _launch_lock:
        if _launched and not force:
            _safe_log("debug", "Control Center already opened once; not opening again")
            return False
        issued, reason = _open_once(target)
        if not issued:
            _safe_log(
                "warning",
                f"Control Center not opened ({reason or 'unknown reason'}); "
                f"open manually: {_redact(target)}",
            )
            return False
        _launched = True
        return True


def auto_open_state(*, no_browser: bool = False) -> dict[str, Any]:
    """Enablement decision + human reason (read-only; never raises)."""
    if no_browser or _no_browser_flag:
        return {"enabled": False, "reason": "--no-browser"}
    try:
        raw = os.getenv("NSE_NO_BROWSER", "")
    except Exception:  # pragma: no cover - defensive
        raw = ""
    if raw.strip().lower() in ENV_DISABLED_VALUES:
        return {"enabled": False, "reason": f"NSE_NO_BROWSER={raw.strip()}"}
    if not _stdout_isatty():
        return {"enabled": False, "reason": "stdout is not a terminal"}
    return {"enabled": True, "reason": "auto-open enabled"}


def browser_auto_open_enabled(*, no_browser: bool = False) -> bool:
    """CONTRACT #10 precedence: flag > NSE_NO_BROWSER > stdout.isatty()."""
    try:
        return bool(auto_open_state(no_browser=no_browser)["enabled"])
    except Exception:  # pragma: no cover - never raise from an enablement check
        return False


def maybe_open_browser(url: str, *, no_browser: bool = False) -> bool:
    """CONTRACT #10 seam: open the Control Center only when enabled.

    Returns False (silently, at debug level) when auto-open is disabled — a
    disabled browser is an operator choice, not a failure.
    """
    try:
        if not browser_auto_open_enabled(no_browser=no_browser):
            _safe_log(
                "debug",
                f"browser auto-open disabled ({auto_open_state(no_browser=no_browser)['reason']})",
            )
            return False
        return open_control_center(url)
    except Exception as exc:  # pragma: no cover - belt and braces
        _safe_log("warning", f"browser auto-open failed: {_safe_reason(exc)}")
        return False


# ---------------------------------------------------------------------------
# CONTRACT #10 readiness gate: /health (200) AND / (200 html), retry <= 30s
# ---------------------------------------------------------------------------
#: ``probe_server`` result keys; kept as a module constant so tests and the
#: start-path reporters cannot drift apart on the field names.
READY_FIELDS = ("health", "root")

Reporter = Callable[[dict[str, Any]], None]
Opener = Callable[[str], Any]


def _default_opener(url: str) -> Any:
    """One bounded readiness GET; transport failure returns ``None``."""
    try:
        return urllib.request.urlopen(url, timeout=READY_REQUEST_TIMEOUT_S)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    except Exception as probe_err:  # pragma: no cover - defensive
        _safe_log("debug", f"readiness probe error for {url}: {_safe_reason(probe_err)}")
        return None


def _close(resp: Any) -> None:
    try:
        resp.close()
    except Exception:  # pragma: no cover
        pass


def _status_of(resp: Any) -> int:
    try:
        return int(getattr(resp, "status", 0) or resp.getcode())
    except Exception:  # pragma: no cover - malformed response
        return 0


def probe_server(url_root: str, *, opener: Opener | None = None) -> dict[str, bool]:
    """One readiness round: ``{"health": /health 200, "root": / 200 html}``.

    CONTRACT #10 requires BOTH facts before a browser may open. A 200 that is
    not HTML (JSON/text) is NOT a Control Center shell and must not unlock the
    browser. ``opener`` is duck-typed so unit tests inject a fake transport
    instead of binding a shared port; the default talks to loopback.
    """
    fetch = _default_opener if opener is None else opener
    root = url_root if str(url_root).endswith("/") else f"{url_root}/"

    def _get(url: str) -> Any:
        # Total transport call: a custom opener that raises must degrade to
        # "not ready", never to an exception escaping the gate.
        try:
            return fetch(url)
        except Exception as fetch_err:  # pragma: no cover - defensive
            _safe_log("debug", f"readiness fetch failed for {url}: {_safe_reason(fetch_err)}")
            return None

    health = False
    health_resp = _get(urljoin(root, "health"))
    if health_resp is not None:
        health = _status_of(health_resp) == 200
        _close(health_resp)

    root_ok = False
    root_resp = _get(root)
    if root_resp is not None:
        try:
            headers = getattr(root_resp, "headers", None)
            ctype = ""
            if headers is not None:
                ctype = str(headers.get("Content-Type") or headers.get("content-type") or "")
            head = b""
            try:
                head = bytes(root_resp.read(2048)).lstrip().lower()
            except Exception:  # pragma: no cover - body is a bonus, not the gate
                head = b""
            looks_html = head.startswith(b"<!doctype html") or head.startswith(b"<html")
            root_ok = _status_of(root_resp) == 200 and ("html" in ctype.lower() or looks_html)
        except Exception:  # pragma: no cover - defensive
            root_ok = False
        _close(root_resp)

    return {"health": health, "root": root_ok}


def wait_for_server_ready(
    url_root: str,
    *,
    timeout_s: float = READY_TIMEOUT_S,
    interval_s: float = READY_POLL_INTERVAL_S,
    opener: Opener | None = None,
) -> dict[str, Any]:
    """Poll until ``/health`` is 200 AND ``/`` is 200 html, bounded by timeout.

    Never raises. Returns the phase diagnostic::

        {"url", "ready", "phase", "health", "root", "attempts", "elapsed_s"}

    ``phase`` is ``READY``, ``TIMEOUT_HEALTH`` (backend never answered
    ``/health``), ``TIMEOUT_ROOT`` (health ok, shell never 200 html) or
    ``TIMEOUT_RACY`` (both seen, never simultaneously up).
    """
    root = url_root if str(url_root).endswith("/") else f"{url_root}/"
    started = time.monotonic()
    deadline = started + max(0.0, float(timeout_s))
    attempts = 0
    health_seen = False
    root_seen = False

    while True:
        attempts += 1
        try:
            flags = probe_server(root, opener=opener)
        except Exception as probe_err:  # pragma: no cover - probe never raises
            _safe_log("warning", f"readiness probe raised for {root}: {_safe_reason(probe_err)}")
            flags = {"health": False, "root": False}
        health_seen = health_seen or bool(flags.get("health"))
        root_seen = root_seen or bool(flags.get("root"))
        if flags.get("health") and flags.get("root"):
            return {
                "url": root,
                "ready": True,
                "phase": "READY",
                "health": True,
                "root": True,
                "attempts": attempts,
                "elapsed_s": round(time.monotonic() - started, 3),
            }
        now = time.monotonic()
        if now >= deadline:
            break
        time.sleep(min(max(0.01, float(interval_s)), max(0.0, deadline - now)))

    phase = (
        "TIMEOUT_HEALTH"
        if not health_seen
        else ("TIMEOUT_ROOT" if not root_seen else "TIMEOUT_RACY")
    )
    return {
        "url": root,
        "ready": False,
        "phase": phase,
        "health": health_seen,
        "root": root_seen,
        "attempts": attempts,
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def launch_when_ready(
    url_root: str,
    *,
    no_browser: bool = False,
    timeout_s: float = READY_TIMEOUT_S,
    interval_s: float = READY_POLL_INTERVAL_S,
    opener: Opener | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """CONTRACT #10 launch sequence: wait -> open EXACTLY ONCE -> report.

    Never raises. The returned diagnostic carries:

    ``opened``  the browser was handed the URL (at most one attempt, and the
                one-window guard in ``open_control_center`` makes it at most
                one per start)
    ``reason``  why it was not, whenever ``opened`` is False
    ``phase``   readiness phase (``READY`` / ``TIMEOUT_*``)

    A readiness timeout means the backend never proved itself: per section 57
    the browser is NOT opened and the caller prints ``SERVER READY`` + the
    actual URL + this phase diagnostic instead.
    """
    diag: dict[str, Any]
    try:
        diag = wait_for_server_ready(
            url_root, timeout_s=timeout_s, interval_s=interval_s, opener=opener
        )
    except Exception as wait_err:  # pragma: no cover - never raises
        return {
            "url": url_root,
            "ready": False,
            "phase": "WAIT_FAILED",
            "health": False,
            "root": False,
            "attempts": 0,
            "elapsed_s": 0.0,
            "opened": False,
            "reason": f"readiness wait failed: {_safe_reason(wait_err)}",
        }

    diag["opened"] = False
    if not diag["ready"]:
        # Backend failure NEVER opens a browser (section 57).
        diag["reason"] = f"readiness {diag['phase']}"
        return diag

    try:
        if environ is not None:
            opened = _maybe_open_with_environ(diag["url"], no_browser=no_browser, environ=environ)
        else:
            opened = maybe_open_browser(diag["url"], no_browser=no_browser)
    except Exception as open_err:  # pragma: no cover - never raises
        opened = False
        _safe_log("warning", f"browser auto-open failed: {_safe_reason(open_err)}")

    diag["opened"] = bool(opened)
    if diag["opened"]:
        diag["reason"] = ""
    else:
        diag["reason"] = (
            auto_open_state(no_browser=no_browser)["reason"] or "browser handoff failed"
        )
    return diag


def _maybe_open_with_environ(url: str, *, no_browser: bool, environ: Mapping[str, str]) -> bool:
    """``maybe_open_browser`` against an injected environ (unit-test seam)."""
    if no_browser or _no_browser_flag:
        return False
    raw = str(environ.get(NO_BROWSER_ENV, "")).strip().lower()
    if raw in ENV_DISABLED_VALUES:
        return False
    if not _stdout_isatty():
        return False
    return open_control_center(url)


def report_ready_or_opened(diag: dict[str, Any], *, style: str = "panel") -> str:
    """Plain-text render of the CONTRACT #10 outcome line (never raises).

    The start paths print this so the operator always learns the ACTUAL URL:

    * browser opened -> ``CONTROL CENTER OPENED <url>``
    * otherwise      -> ``SERVER READY`` + actual URL + phase diagnostic

    Returned as text (not printed) so both the rich console and the legacy
    launcher can render it, and so tests assert on it without a TTY.
    """
    try:
        url = str(diag.get("url") or "")
        if diag.get("opened"):
            return f"CONTROL CENTER OPENED {url}"
        reason = str(diag.get("reason") or "")
        return (
            f"SERVER READY {url} "
            f"[phase={diag.get('phase')} health={diag.get('health')} "
            f"root={diag.get('root')} attempts={diag.get('attempts')} "
            f"elapsed={diag.get('elapsed_s')}s browser={reason or 'not opened'}]"
        )
    except Exception as report_err:  # pragma: no cover - never raises
        return f"SERVER READY [phase=REPORT_FAILED {_safe_reason(report_err)}]"


def start_browser_worker(
    url_root: str,
    *,
    no_browser: bool = False,
    reporter: Reporter | None = None,
    timeout_s: float = READY_TIMEOUT_S,
    interval_s: float = READY_POLL_INTERVAL_S,
    daemon: bool = True,
) -> threading.Thread | None:
    """Run :func:`launch_when_ready` on a daemon thread; never blocks the boot.

    The engine must not stall for up to 30s waiting on readiness, and a
    browser fault must never reach the engine (section 27), so the whole
    sequence runs isolated with a last-resort catch. Returns the thread, or
    ``None`` if it cannot even be created (also non-fatal).
    """

    def _run() -> None:
        diag: dict[str, Any] = {}
        try:
            diag = launch_when_ready(
                url_root, no_browser=no_browser, timeout_s=timeout_s, interval_s=interval_s
            )
        except Exception as worker_err:  # pragma: no cover - belt and braces
            _safe_log(
                "warning", f"launch sequence failed for {url_root}: {_safe_reason(worker_err)}"
            )
            return
        if reporter is not None:
            try:
                reporter(diag)
            except Exception as report_err:  # pragma: no cover - reporting is cosmetic
                _safe_log("warning", f"readiness reporter failed: {_safe_reason(report_err)}")

    try:
        thread = threading.Thread(target=_run, name="nse-browser-launch", daemon=daemon)
        thread.start()
        return thread
    except Exception as thread_err:  # pragma: no cover - non-fatal
        _safe_log("warning", f"could not start launch thread: {_safe_reason(thread_err)}")
        return None
