"""Canonical end-user product state (ENDUSER-OPERABILITY-HARDENING, EU-09).

WHY THIS EXISTS
---------------
The product previously had no single answer to the question every non-developer
asks: *"is my program running, is it ready, is it trading, and is it safe?"*

The facts existed but only as unrelated booleans spread over surfaces:

    /api/status            engine_running: bool, execution_mode, runtime_mode,
                           data_source, mode_source_mismatch
    frontend AppShell      "ENGINE RUNNING / STOPPED" (one bit)
    nexus status           health checks + environment + version (no process
                           state at all — it never says whether an engine is up)
    nexus health           install/config readiness, not runtime state
    risk / kill switch     a separate surface

So a user could see "ENGINE RUNNING" while the engine was in a LIVE/MT5
mismatch, and `nexus status` would still print HEALTHY, and nothing on any
surface would say whether LIVE trading was actually enabled. Those states were
conflated; this module un-conflates them by deriving FOUR independent axes from
facts that are already observed (never invented):

    application   is the program up, and is it serving?
    engine        is the trading loop running?
    execution     which mode did the operator ask for, and which source is
                  really feeding the engine (BUG-232 guard)?
    trading       is LIVE order placement possible right now?

CONTRACT (same rules as release/state_taxonomy.py — read, never fork):
  * every axis value is a member of that axis' STATES tuple;
  * UNKNOWN is a first-class value: if a fact was not observed we say so
    instead of guessing (no fake READY, no fake safety);
  * the axes are independent — one axis NEVER implies another. In particular
    APPLICATION READY != TRADING ARMED != LIVE TRADING ENABLED;
  * this module derives state from an already-captured ``/api/status``
    payload (or any dict with the same keys). It performs no I/O, starts no
    subsystem and cannot mutate anything: it is safe to call from the CLI,
    the API layer, tests and support tooling alike.
"""

from __future__ import annotations

from typing import Any, Final

# --- Axis 1: the application (program + web dashboard) ----------------------
APP_STOPPED: Final = "STOPPED"
APP_STARTING: Final = "STARTING"
APP_READY: Final = "READY"
APP_DEGRADED: Final = "DEGRADED"
APP_UNKNOWN: Final = "UNKNOWN"

APPLICATION_STATES: Final = (
    APP_STOPPED,
    APP_STARTING,
    APP_READY,
    APP_DEGRADED,
    APP_UNKNOWN,
)

# --- Axis 2: the trading engine loop ---------------------------------------
ENGINE_STOPPED: Final = "STOPPED"
ENGINE_RUNNING: Final = "RUNNING"
ENGINE_UNKNOWN: Final = "UNKNOWN"

ENGINE_STATES: Final = (ENGINE_STOPPED, ENGINE_RUNNING, ENGINE_UNKNOWN)

# --- Axis 3: execution mode (operator intent + observed source) -------------
MODE_PAPER: Final = "PAPER"
MODE_SHADOW: Final = "SHADOW"
MODE_LIVE: Final = "LIVE"
MODE_REPLAY: Final = "REPLAY"
MODE_UNKNOWN: Final = "UNKNOWN"

EXECUTION_MODES: Final = (MODE_PAPER, MODE_SHADOW, MODE_LIVE, MODE_REPLAY, MODE_UNKNOWN)

# --- Axis 4: is LIVE order placement possible right now? --------------------
TRADING_NOT_ARMED: Final = "NOT_ARMED"  # engine stopped / no intent
TRADING_PAPER: Final = "PAPER_ONLY"  # simulated fills only
TRADING_SHADOW: Final = "SHADOW_ONLY"  # observations only
TRADING_ARMED_LIVE: Final = "ARMED_LIVE"  # live orders possible
TRADING_BLOCKED: Final = "BLOCKED"  # armed but provably unsafe
TRADING_UNKNOWN: Final = "UNKNOWN"

TRADING_STATES: Final = (
    TRADING_NOT_ARMED,
    TRADING_PAPER,
    TRADING_SHADOW,
    TRADING_ARMED_LIVE,
    TRADING_BLOCKED,
    TRADING_UNKNOWN,
)

#: Canonical axis -> allowed values map (one owner for the vocabulary).
PRODUCT_STATE_AXES: Final[dict[str, tuple[str, ...]]] = {
    "application": APPLICATION_STATES,
    "engine": ENGINE_STATES,
    "execution_mode": EXECUTION_MODES,
    "trading": TRADING_STATES,
}


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _pick_mode(*candidates: Any) -> str:
    """First recognizable execution mode across runtime/config spellings."""
    for raw in candidates:
        token = _norm(raw)
        for known in (MODE_LIVE, MODE_PAPER, MODE_SHADOW, MODE_REPLAY):
            # runtime_mode may be richer than the config enum ("LIVE_READY",
            # "LIVE_CONFIGURED", "PAPER_SIMULATION"): match the family prefix.
            if token.startswith(known):
                return known
    return MODE_UNKNOWN


def derive_product_state(
    status: dict[str, Any] | None,
    *,
    reachable: bool,
) -> dict[str, Any]:
    """Derive the four canonical axes from one observed ``/api/status`` payload.

    Parameters
    ----------
    status:
        The captured payload (``None`` when the endpoint could not be read).
    reachable:
        Whether the web server answered at all. This is observed separately
        because "no HTTP" and "HTTP says engine stopped" are different facts
        with different user actions.

    Returns
    -------
    dict with the four axes plus ``reasons`` (why each axis has its value) and
    ``summary`` (one line a human can read). Never raises: an unrecognised or
    missing fact degrades to ``UNKNOWN`` rather than to a wrong answer.
    """
    status = status or {}

    # --- Axis 1: application ---------------------------------------------
    if not reachable:
        application = APP_STOPPED
        app_reason = "no response from the local dashboard endpoint"
    else:
        engine_running = status.get("engine_running")
        if engine_running is None:
            application = APP_STARTING
            app_reason = "dashboard is serving but has not reported engine state yet"
        elif engine_running:
            # DEGRADED only from observed runtime facts, never from absence.
            if status.get("mode_source_mismatch"):
                application = APP_DEGRADED
                app_reason = (
                    "engine runs but the configured mode disagrees with the live data source"
                )
            elif _norm(status.get("runtime_mode")) == "DEGRADED":
                application = APP_DEGRADED
                app_reason = "engine reports its own DEGRADED runtime state"
            else:
                application = APP_READY
                app_reason = "dashboard serving and trading engine running"
        else:
            application = APP_STOPPED
            app_reason = "dashboard is serving but the trading engine is stopped"

    # --- Axis 2: engine ---------------------------------------------------
    if not reachable or status.get("engine_running") is None:
        engine = ENGINE_UNKNOWN if reachable else ENGINE_STOPPED
    else:
        engine = ENGINE_RUNNING if status.get("engine_running") else ENGINE_STOPPED

    # --- Axis 3: execution mode ------------------------------------------
    execution_mode = _pick_mode(
        status.get("runtime_mode"),
        status.get("execution_mode"),
    )

    # --- Axis 4: trading safety ------------------------------------------
    data_source = _norm(status.get("data_source"))
    mismatch = bool(status.get("mode_source_mismatch"))
    halted = _norm(status.get("risk_state")) in {"HALT", "HALTED", "BLOCKED"}

    if engine != ENGINE_RUNNING:
        trading = TRADING_NOT_ARMED if engine == ENGINE_STOPPED else TRADING_UNKNOWN
        trade_reason = "trading engine is not running, so no orders can be placed"
    elif halted:
        trading = TRADING_BLOCKED
        trade_reason = "a risk halt is active: new entries are blocked"
    elif execution_mode == MODE_LIVE:
        if mismatch or data_source in {"", "UNAVAILABLE", "PAPER_SIMULATION"}:
            trading = TRADING_BLOCKED
            trade_reason = (
                "LIVE is configured but the engine is not fed by a real live "
                "broker source, so live order placement is blocked"
            )
        else:
            trading = TRADING_ARMED_LIVE
            trade_reason = "LIVE mode with a live broker source: real orders are possible"
    elif execution_mode == MODE_PAPER:
        trading = TRADING_PAPER
        trade_reason = "PAPER mode: fills are simulated, no real orders are sent"
    elif execution_mode == MODE_SHADOW:
        trading = TRADING_SHADOW
        trade_reason = "SHADOW mode: decisions are observed only, no orders are sent"
    elif execution_mode == MODE_REPLAY:
        trading = TRADING_PAPER
        trade_reason = "REPLAY mode: historical simulation, no orders are sent"
    else:
        trading = TRADING_UNKNOWN
        trade_reason = "execution mode was not reported, so order safety is unknown"

    summary = (
        f"application {application} · engine {engine} · mode {execution_mode} · trading {trading}"
    )

    return {
        "application": application,
        "engine": engine,
        "execution_mode": execution_mode,
        "trading": trading,
        "reasons": {
            "application": app_reason,
            "trading": trade_reason,
        },
        "summary": summary,
        "axes": {name: list(values) for name, values in PRODUCT_STATE_AXES.items()},
    }
