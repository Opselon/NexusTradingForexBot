"""First-run historical-bars bootstrap — LIVE broker data, operator-guided.

WHERE/WHY: the DATA health gate (``release.health.check_data``) hard-FAILs when
``data/raw/<SYM>_<TF>.parquet`` is absent, and a FAIL in any category makes the
whole ``/health`` verdict NOT READY — which blocks the Control Center launch
gate (``cli.engine_boot._open_control_center_when_ready``). On a brand-new
install the parquet NEVER exists (the tick-data lake is gitignored), so every
line-0 user was told "SERVER NOT READY ... Control Center not opened" even
though the engine itself booted fine.

PRODUCT DECISION (operator direction, 2026-11): the fix is NEVER a static
shipped dataset. Market data is per-broker and time-sensitive — a baked CSV
would silently train/serve the operator on history that is not theirs and is
stale the second it is cut. Instead this module drives an interactive
first-run flow: the console asks the operator to open MetaTrader 5 and log in
to their broker account, then our automation fetches fresh M1 bars straight
from that terminal. While the operator is getting the terminal ready we poll
patiently; on success the canonical parquet is written from the live rates.
If the operator declines (or the timeout elapses) the DATA gate stays honestly
FAILED — no fabricated candles, ever, and no third-party data.

BOUNDARY: console (rich) + filesystem + broker adapter only; never loads bars
into an engine, never mutates the registry, never logs secrets. Import-light
at module scope (torch/polars/MetaTrader5 stay function-local — the slim
onefile CLI imports this via the repair/boot path only).
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.release import paths as rpaths

logger = get_logger("nexus_scalp.release.data_bootstrap")

#: Canonical symbol/timeframe of the serving-history slot the DATA gate reads.
DEFAULT_SYMBOL = "XAUUSD"
DEFAULT_TIMEFRAME = "M1"

#: Minimum rows a fetch must return to be worth canonicalizing (walk-forward
#: needs >= 10k per check_data; anything smaller cannot unblock the gate).
MIN_SEED_ROWS = 10_000

#: How long the interactive flow waits for the operator to bring a terminal
#: up (seconds). Generous on purpose: MT5 cold-start + manual login is slow.
DEFAULT_WAIT_SECONDS = 300

#: Poll interval between terminal-availability probes while waiting.
POLL_INTERVAL_SECONDS = 5.0


def _canonical_bars_path(symbol: str = DEFAULT_SYMBOL, timeframe: str = DEFAULT_TIMEFRAME) -> Path:
    """The writable canonical slot the DATA gate and trainers read."""
    return (
        rpaths.get_runtime_workspace()
        / "data"
        / "raw"
        / f"{symbol.upper()}_{timeframe.upper()}.parquet"
    )


def bars_slot_exists(symbol: str = DEFAULT_SYMBOL, timeframe: str = DEFAULT_TIMEFRAME) -> bool:
    return _canonical_bars_path(symbol, timeframe).exists()


def _normalize_and_write(frame_in: Any, out_path: Path) -> dict[str, Any]:
    """Clean + write the bars frame exactly like ``nexus data-restore`` does.

    Returns the restore-style summary. Raises ValueError when the frame cannot
    satisfy the canonical contract (too few rows after cleaning) so callers
    report the shortfall instead of writing a useless file.
    """
    from nexus_scalp.model_generation.bars_normalize import normalize_bars_frame

    frame, stats = normalize_bars_frame(frame_in)
    frame = frame.sort("time")
    if frame.height < MIN_SEED_ROWS:
        raise ValueError(
            f"source yields {frame.height} clean rows (< {MIN_SEED_ROWS}); "
            "cannot unblock the DATA gate"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_path)
    return {
        "rows": frame.height,
        "stats": stats,
        "path": str(out_path),
    }


def _mt5_adapter() -> Any | None:
    """Import-safe construction of the direct MT5 adapter, or None."""
    try:
        from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter
    except Exception as exc:
        logger.debug("[DATA-BOOTSTRAP] event=ADAPTER_IMPORT_FAILED error=%s", exc)
        return None
    if not HAS_NATIVE_MT5:
        return None
    try:
        return DirectMT5Adapter()
    except Exception as exc:
        logger.debug("[DATA-BOOTSTRAP] event=ADAPTER_CONSTRUCT_FAILED error=%s", exc)
        return None


def fetch_live_mt5_bars(
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    *,
    count: int = 100_000,
) -> dict[str, Any]:
    """Acquire history from a connected MT5 terminal (the only source).

    Never raises for availability reasons: no terminal / connect failure /
    zero bars all return ``{"ok": False, "reason": ...}`` so the caller can
    surface the problem and (in interactive contexts) retry after the
    operator has opened the terminal and logged in.
    """
    adapter = _mt5_adapter()
    if adapter is None:
        return {"ok": False, "reason": "MetaTrader5 terminal not available on this machine"}
    try:
        adapter.connect()
    except Exception as exc:
        return {"ok": False, "reason": f"MT5 connect failed ({type(exc).__name__})"}
    if not getattr(adapter, "_connected", False):
        last = getattr(adapter, "_conn_state", None)
        detail = getattr(last, "detail", "") or ""
        return {"ok": False, "reason": f"terminal not connected ({detail})"}
    try:
        bars = adapter.get_rate_history(symbol, timeframe=timeframe, count=count)
    except Exception as exc:
        return {"ok": False, "reason": f"copy_rates failed ({type(exc).__name__})"}
    finally:
        with contextlib.suppress(Exception):
            adapter.disconnect()
    if not bars:
        return {"ok": False, "reason": "terminal returned 0 bars"}
    import polars as pl

    frame = pl.DataFrame(
        {
            "time": [int(b.time or 0) for b in bars],
            "open": [float(b.open or 0.0) for b in bars],
            "high": [float(b.high or 0.0) for b in bars],
            "low": [float(b.low or 0.0) for b in bars],
            "close": [float(b.close or 0.0) for b in bars],
            "tick_volume": [int(b.tick_volume or 0) for b in bars],
            "spread": [int(b.spread or 0) for b in bars],
            "real_volume": [int(b.real_volume or 0) for b in bars],
            "time_utc": [b.time_utc for b in bars],
        }
    )
    out_path = _canonical_bars_path(symbol, timeframe)
    try:
        written = _normalize_and_write(frame, out_path)
    except Exception as exc:
        return {"ok": False, "reason": f"live bars failed normalization ({exc})"}
    return {"ok": True, "source": "mt5-live", **written}


def _print(console: Any, *args: Any, **kwargs: Any) -> None:
    """Console print that tolerates a missing/None console (json mode)."""
    if console is None:
        return
    with contextlib.suppress(Exception):
        console.print(*args, **kwargs)


def _prompt_retry(console: Any, deadline: float) -> bool:
    """Ask a waiting operator whether to keep waiting; non-tty => keep waiting.

    Returns True while there is time left and the operator has not declined.
    """
    import sys

    if not sys.stdin.isatty():
        # Non-interactive context (service/CI): never block on a human, just
        # keep polling until the deadline.
        return time.monotonic() < deadline
    try:
        from rich.prompt import Confirm

        return bool(
            Confirm.ask(
                "Keep waiting for MetaTrader 5?",
                default=True,
                console=console,
                show_default=True,
            )
        )
    except Exception:
        return time.monotonic() < deadline


def ensure_canonical_bars(
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    *,
    console: Any = None,
    interactive: bool = True,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Boot seam: guarantee the canonical bars slot before the health gate runs.

    Operator-guided LIVE flow:

    1. If the slot already exists, keep it (idempotent, instant).
    2. Otherwise try the live MT5 terminal once. When it is not there yet AND
       we are interactive, show the console prompt: open MetaTrader 5 and
       log in to your broker account — then poll the terminal and fetch
       fresh M1 bars as soon as it is reachable.
    3. Never fabricate history: an operator who declines, or a timeout,
       leaves the DATA gate honestly FAILED with the reason surfaced.

    ``interactive=False`` (service/CI contexts) skips every human prompt and
    just attempts the fetch once — the caller reports the outcome.
    """
    if bars_slot_exists(symbol, timeframe):
        return {"action": "keep", "path": str(_canonical_bars_path(symbol, timeframe))}

    # Fast path: terminal already up (common: operator started MT5 first).
    live = fetch_live_mt5_bars(symbol, timeframe)
    if live.get("ok"):
        logger.info(
            "[DATA-BOOTSTRAP] event=ACQUIRED source=mt5-live rows=%s path=%s",
            live.get("rows"),
            live.get("path"),
        )
        return {"action": "fetched-live", **live}

    if not interactive:
        logger.warning(
            "[DATA-BOOTSTRAP] event=UNPROVISIONED reason=%s (the DATA gate will fail loudly)",
            live.get("reason", "unknown"),
        )
        return {"action": "none", "reason": live.get("reason", "MetaTrader5 terminal unavailable")}

    # Interactive: bring the operator in. Every prompt is skip-safe so a
    # non-tty or --json run never dead-locks on a human.
    from rich.panel import Panel

    deadline = time.monotonic() + max(0.0, wait_seconds)
    _print(
        console,
        Panel(
            "[bold cyan]Fresh market data required[/bold cyan]\n"
            "Nexus needs [bold]live M1 bars from your broker[/bold] through your "
            "MetaTrader 5 terminal.\n\n"
            "1. Open [bold]MetaTrader 5[/bold] on this machine.\n"
            "2. Log in to your broker account (demo or real).\n"
            "3. Make sure the [bold]" + symbol + "[/bold] chart is available.\n\n"
            "Nexus fetches the history automatically as soon as the terminal is "
            "ready — nothing is ever downloaded from a third party or shipped "
            "as a static file.",
            title="[bold]First-run data setup[/bold]",
            border_style="cyan",
        )
    )

    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        _print(
            console,
            f"[dim]Waiting for MetaTrader 5... (attempt {attempt}, "
            f"{int(deadline - time.monotonic())}s left)[/dim]",
        )
        live = fetch_live_mt5_bars(symbol, timeframe)
        if live.get("ok"):
            _print(
                console,
                Panel(
                    "[bold green]Live history acquired[/bold green]\n"
                    f"{live.get('rows', 0)} fresh {symbol} {timeframe} bars fetched from "
                    "your MetaTrader 5 terminal.",
                    border_style="green",
                ),
            )
            logger.info(
                "[DATA-BOOTSTRAP] event=ACQUIRED source=mt5-live rows=%s path=%s "
                "after_operator_wait_s=%.0f",
                live.get("rows"),
                live.get("path"),
                wait_seconds - max(0.0, deadline - time.monotonic()),
            )
            return {"action": "fetched-live", **live}

        if not _prompt_retry(console, deadline):
            break
        time.sleep(max(0.0, poll_interval))

    reason = live.get("reason", "MetaTrader5 terminal not available before timeout")
    logger.warning(
        "[DATA-BOOTSTRAP] event=UNPROVISIONED reason=%s (the DATA gate will fail loudly)",
        reason,
    )
    _print(
        console,
        Panel(
            "[bold yellow]Live data not available yet[/bold yellow]\n"
            f"{reason}\n\n"
            "The DATA health check stays [bold]FAILED[/bold] until fresh history "
            "is fetched. Open MetaTrader 5, log in to your broker account, and "
            "run [bold]nexus start[/bold] again — or use [bold]nexus data-fetch[/bold] "
            "once the terminal is up.",
            border_style="yellow",
        ),
    )
    return {"action": "none", "reason": reason}


__all__ = [
    "DEFAULT_SYMBOL",
    "DEFAULT_TIMEFRAME",
    "DEFAULT_WAIT_SECONDS",
    "MIN_SEED_ROWS",
    "POLL_INTERVAL_SECONDS",
    "bars_slot_exists",
    "ensure_canonical_bars",
    "fetch_live_mt5_bars",
]
