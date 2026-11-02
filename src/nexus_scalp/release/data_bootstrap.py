"""First-run historical-bars bootstrap — the canonical data auto-provision seam.

WHERE/WHY: the DATA health gate (``release.health.check_data``) hard-FAILs when
``data/raw/<SYM>_<TF>.parquet`` is absent, and a FAIL in any category makes the
whole ``/health`` verdict NOT READY — which blocks the Control Center launch
gate (``cli.engine_boot._open_control_center_when_ready``). On a brand-new
install the parquet NEVER exists (the tick-data lake is gitignored), so every
line-0 user was told "SERVER NOT READY ... Control Center not opened" even
though the engine itself booted fine on its live/synthetic feed.

This module owns the ONE recipe that mirrors the model starter mint
(``model_provisioning.FirstRunCoordinator.ensure_serving_model``): at boot,
before the health gate can ever be consulted, make sure the canonical bars slot
has real history, acquired in this precedence order:

  1. LIVE MT5 terminal (the operator's own broker history — the best source;
     never required, silently skipped when no terminal is available).
  2. The SHIPPED seed file baked into the bundle (``--add-data
     data/raw/XAUUSD_M1.csv`` -> ``_internal/data-seed/``): a real captured
     M1 export, normalized through the same cleaner ``nexus data-restore``
     uses. Honest provenance: it is REAL broker history, just not live-fresh.
  3. Nothing else — if neither source exists, report honestly and let the
     health gate say what is missing (never fabricate candles; the training
     pipeline must never be fed synthetic history pretending to be real).

BOUNDARY: filesystem + broker adapter only; never loads bars into an engine,
never mutates the registry, never logs secrets. Import-light at module scope
(torch/polars/MetaTrader5 stay function-local — the slim onefile CLI imports
this via the repair/boot path only).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.release import paths as rpaths

logger = get_logger("nexus_scalp.release.data_bootstrap")

#: Canonical symbol/timeframe of the serving-history slot the DATA gate reads.
DEFAULT_SYMBOL = "XAUUSD"
DEFAULT_TIMEFRAME = "M1"

#: Minimum rows a seed must carry to be worth canonicalizing (walk-forward
#: needs >= 10k per check_data; anything smaller cannot unblock the gate).
MIN_SEED_ROWS = 10_000


def _seed_candidates() -> list[Path]:
    """Read-only seed sources shipped with the bundle or left in the tree.

    Precedence: the packaged ``_internal/data-seed`` directory (PyInstaller
    --add-data target), then the tracked ``data_seed/`` sibling for source
    checkouts (``data/raw`` is gitignored, so the shipped capture lives in
    ``data_seed/`` and is baked into the bundle at build time). Directories
    are probed for both .parquet and .csv.
    """
    roots: list[Path] = []
    if rpaths.is_frozen():
        exe = rpaths.exe_dir()
        roots.append(exe / "_internal" / "data-seed")
        roots.append(exe / "data-seed")
    else:
        roots.append(rpaths.exe_dir() / "data_seed")
    candidates: list[Path] = []
    name_stems = (f"{DEFAULT_SYMBOL}_{DEFAULT_TIMEFRAME}",)
    for root in roots:
        if not root.exists():
            continue
        for stem in name_stems:
            for suffix in (".parquet", ".csv"):
                p = root / f"{stem}{suffix}"
                if p.is_file():
                    candidates.append(p)
    return candidates


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
    can fall through to the next source instead of writing a useless file.
    """
    import polars as pl

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


def fetch_live_mt5_bars(
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    *,
    count: int = 100_000,
) -> dict[str, Any]:
    """Acquire history from a connected MT5 terminal (best source, optional).

    Never raises for availability reasons: no terminal / connect failure /
    zero bars all return ``{"ok": False, "reason": ...}`` so the caller falls
    through to the shipped seed. A real terminal wins because its history is
    live-fresh; the seed is a fallback, not a competitor.
    """
    try:
        from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter
    except Exception as exc:
        return {"ok": False, "reason": f"adapter unavailable ({type(exc).__name__})"}
    if not HAS_NATIVE_MT5:
        return {"ok": False, "reason": "no native MetaTrader5 package"}
    adapter = DirectMT5Adapter()
    try:
        adapter.connect()
    except Exception as exc:
        return {"ok": False, "reason": f"MT5 connect failed ({type(exc).__name__})"}
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


def install_seed_bars(
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> dict[str, Any]:
    """Canonicalize the shipped seed into the writable data root.

    Reads the first usable seed candidate (.parquet preferred over .csv),
    normalizes it through the shared cleaner, and writes the canonical
    parquet. Never fabricates bars: a seed that cannot meet the row floor is
    reported, not padded.
    """
    import polars as pl

    out_path = _canonical_bars_path(symbol, timeframe)
    errors: list[str] = []
    for src in _seed_candidates():
        try:
            if src.suffix.lower() == ".csv":
                frame_in = pl.read_csv(src, try_parse_dates=True)
            else:
                frame_in = pl.read_parquet(src)
        except Exception as exc:
            errors.append(f"{src.name}: unreadable ({type(exc).__name__})")
            continue
        if frame_in.is_empty():
            errors.append(f"{src.name}: zero rows")
            continue
        try:
            written = _normalize_and_write(frame_in, out_path)
        except Exception as exc:
            errors.append(f"{src.name}: {exc}")
            continue
        return {"ok": True, "source": "shipped-seed", "seed": str(src), **written}
    return {
        "ok": False,
        "reason": "; ".join(errors) if errors else "no seed file present in the bundle",
    }


def ensure_canonical_bars(
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    *,
    allow_live_fetch: bool = True,
) -> dict[str, Any]:
    """Boot seam: guarantee the canonical bars slot before the health gate runs.

    Idempotent and cheap when the slot already exists (returns immediately).
    Otherwise tries LIVE MT5 first (when allowed and available), then the
    shipped seed. Honesty contract: when nothing can provide real history the
    result says so — the DATA gate keeps failing LOUDLY rather than being
    papered over with fabricated candles.
    """
    if bars_slot_exists(symbol, timeframe):
        return {"action": "keep", "path": str(_canonical_bars_path(symbol, timeframe))}
    if allow_live_fetch:
        live = fetch_live_mt5_bars(symbol, timeframe)
        if live.get("ok"):
            logger.info(
                "[DATA-BOOTSTRAP] event=ACQUIRED source=mt5-live rows=%s path=%s",
                live.get("rows"),
                live.get("path"),
            )
            return {"action": "fetched-live", **live}
        logger.debug(
            "[DATA-BOOTSTRAP] event=LIVE_UNAVAILABLE reason=%s (falling back to shipped seed)",
            live.get("reason", "unknown"),
        )
    seeded = install_seed_bars(symbol, timeframe)
    if seeded.get("ok"):
        logger.info(
            "[DATA-BOOTSTRAP] event=ACQUIRED source=shipped-seed rows=%s seed=%s path=%s",
            seeded.get("rows"),
            seeded.get("seed"),
            seeded.get("path"),
        )
        return {"action": "seeded", **seeded}
    logger.warning(
        "[DATA-BOOTSTRAP] event=UNPROVISIONED reason=%s (the DATA gate will fail loudly)",
        seeded.get("reason", "unknown"),
    )
    return {"action": "none", "reason": seeded.get("reason", "no bars source available")}


__all__ = [
    "DEFAULT_SYMBOL",
    "DEFAULT_TIMEFRAME",
    "MIN_SEED_ROWS",
    "bars_slot_exists",
    "ensure_canonical_bars",
    "fetch_live_mt5_bars",
    "install_seed_bars",
]
