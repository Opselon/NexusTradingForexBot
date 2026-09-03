"""DATA AVAILABILITY + QUALITY capture — READ-ONLY MT5 broker history (Phase: Data Gate).

Captures REAL raw history from the MetaTrader5 terminal:
  * available symbols list (symbols_get)
  * per-symbol/timeframe availability (copy_rates_from_pos sizes)
  * full historical walk for chosen symbol+timeframes (iterative paging via
    copy_rates_range from earliest known server data)
  * writes RAW bars to data/raw/<symbol>_<TF>.parquet (+ .csv sidecar)
  * writes a JSON quality/availability report to data/raw/capture_report.json

READ-ONLY: never places orders. Never touches artifacts/models/.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import MetaTrader5 as mt5
import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parent
RAW_DIR = REPO / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Try to honor the broker's max bars per request. Verified on this terminal:
# copy_rates_from_pos count=100_000 -> Invalid params (-2); count=50_000 works.
PAGE = 50_000
MIN_PAGE_GROWTH = 50  # if a page returns fewer than this many new bars, stop

PRIMARY = "XAUUSD"
TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1"]

TF_MT5 = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


def mt5_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "time": int(row["time"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "tick_volume": int(row["tick_volume"]),
        "spread": int(row["spread"]),
        "real_volume": int(row["real_volume"]),
    }


def fetch_symbols() -> list[dict[str, Any]]:
    """symbols_get: all symbols, filtered to tradeable FX/metals by default."""
    raw = mt5.symbols_get()
    if raw is None:
        return []
    out = []
    for s in raw:
        try:
            out.append(
                {
                    "name": s.name,
                    "path": s.path,
                    "currency_base": s.currency_base,
                    "currency_profit": s.currency_profit,
                    "trade_mode": int(s.trade_mode),
                    "volume_min": float(s.volume_min),
                    "volume_max": float(s.volume_max),
                    "trade_tick_size": float(s.trade_tick_size),
                    "visible": bool(s.visible),
                }
            )
        except Exception:
            continue
    return out


def first_available_time(symbol: str, tf: str, probe_pages: int = 3) -> int | None:
    """Probe how far back the broker can serve history for symbol+tf.

    Strategy: request a huge page from position 0; copy_rates_from_pos returns
    the MOST RECENT bars. To find the EARLIEST available bar we instead walk
    copy_rates_range backwards: (now - K*days, now) then shrink the window.
    For the availability report a coarse probe is enough.
    """
    mt5_tf = TF_MT5[tf]
    # First: how many bars does the server hold? Try a very old from-date.
    start = datetime(2020, 1, 1, tzinfo=UTC)
    for _ in range(probe_pages):
        raw = mt5.copy_rates_range(symbol, mt5_tf, start, datetime.now(UTC))
        if raw is not None and len(raw) > 0:
            return int(raw[0]["time"])
        # no data from 2020; step forward
        start = start + timedelta(days=365)
    # fallback: from_pos to get earliest available of last PAGE bars
    raw = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, PAGE)
    if raw is not None and len(raw) > 0:
        return int(raw[0]["time"])
    return None


def fetch_full_history(symbol: str, tf: str) -> pl.DataFrame | None:
    """Walk the FULL terminal-cache history for (symbol, tf) via from_pos crawl.

    copy_rates_from_pos(pos, count) returns up to `count` bars starting at
    position `pos` (0 = most recent, 1 = one bar older, ...). Each call returns
    the MOST RECENT bars at that offset, so we monotonically advance pos and
    stop when the server/cache returns fewer than the requested page or empty.
    This exhaustively captures whatever the terminal has cached locally —
    the hard limit for M1/M5/M15 is the terminal's own cache depth.
    """
    rows: list[dict[str, Any]] = []
    pos = 0
    while pos < 5_000_000:
        raw = mt5.copy_rates_from_pos(symbol, TF_MT5[tf], pos, PAGE)
        if raw is None or len(raw) == 0:
            break
        chunk = [mt5_row_to_dict(r) for r in raw]
        rows.extend(chunk)
        pos += len(chunk)
        print(f"    ...from_pos {tf}: total={len(rows)} (pos={pos}, chunk_n={len(chunk)})")
        if len(chunk) < PAGE:
            break  # cache exhausted

    if not rows:
        return None
    df = pl.DataFrame(rows)
    df = df.unique(subset=["time"], keep="first").sort("time")
    # naive-UTC datetime column (matches SampleFactory._parse_ts convention:
    # naive timestamps are interpreted as UTC)
    df = df.with_columns(pl.from_epoch(pl.col("time"), time_unit="s").alias("time_utc"))
    return df


def bars_per_day_stats(df: pl.DataFrame) -> dict[str, Any]:
    day = df.with_columns(pl.col("time_utc").dt.date().alias("day"))
    per_day = day.group_by("day").len().sort("day")
    return {
        "days": int(per_day.height),
        "min_per_day": int(per_day["len"].min()) if per_day.height else 0,
        "max_per_day": int(per_day["len"].max()) if per_day.height else 0,
        "mean_per_day": round(float(per_day["len"].mean()), 2) if per_day.height else 0.0,
    }


def gap_report(df: pl.DataFrame, tf_minutes: int) -> dict[str, Any]:
    """Expected spacing vs observed; weeks/weekends naturally produce gaps."""
    ts = df["time"].to_numpy()
    if len(ts) < 2:
        return {"gaps_total": 0, "largest_gap_min": 0, "gap_count_by_size": {}}
    deltas = np.diff(ts)
    expected = tf_minutes * 60
    gaps = deltas[deltas > expected]
    # classify: 1.5x-3x (small hiccup), 3x-24h (intraday missing), >24h (weekend/holiday)
    bucket = {
        "1.5x-3x": int(((gaps >= 1.5 * expected) & (gaps < 3 * expected)).sum()),
        "3x-24h": int(((gaps >= 3 * expected) & (gaps < 86400)).sum()),
        "24h+": int((gaps >= 86400).sum()),
    }
    return {
        "timeframe_min": tf_minutes,
        "expected_spacing_s": expected,
        "total_intervals": len(deltas),
        "gaps_total": len(gaps),
        "gap_pct": round(100.0 * len(gaps) / len(deltas), 3),
        "largest_gap_s": int(gaps.max()) if len(gaps) else 0,
        "gap_count_by_size": bucket,
    }


def quality_report(symbol: str, tf: str, df: pl.DataFrame) -> dict[str, Any]:
    n = df.height
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    tv = df["tick_volume"].to_numpy()

    invalid_ohlc = int(
        (
            (h < l)
            | (h < o)
            | (l > o)
            | (c < l)
            | (c > h)
            | ~np.isfinite(o)
            | ~np.isfinite(h)
            | ~np.isfinite(l)
            | ~np.isfinite(c)
        ).sum()
    )
    zero_neg_vol = int((tv <= 0).sum())
    int(df["time"].is_duplicated().sum())

    tf_min = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}.get(tf, 1)
    gaps = gap_report(df, tf_min)

    return {
        "symbol": symbol,
        "timeframe": tf,
        "total_bars": n,
        "earliest_time": str(df["time_utc"].min()),
        "latest_time": str(df["time_utc"].max()),
        "earliest_epoch": int(df["time"].min()),
        "latest_epoch": int(df["time"].max()),
        "per_day": bars_per_day_stats(df),
        "duplicate_timestamps": int(df["time"].is_duplicated().sum()),
        "invalid_ohlc_rows": invalid_ohlc,
        "zero_neg_volume_rows": zero_neg_vol,
        "timezone_convention": "UTC (MT5 copy_rates epoch seconds -> UTC)",
        "gaps": gaps,
        "files": {
            "parquet": str(RAW_DIR / f"{symbol}_{tf}.parquet"),
            "csv": str(RAW_DIR / f"{symbol}_{tf}.csv"),
        },
    }


def main() -> int:
    print("=== MT5 CONNECT (read-only) ===")
    if not mt5.initialize():
        print("FATAL: mt5.initialize() failed:", mt5.last_error())
        return 1
    print("MT5 version:", mt5.version())

    report: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "mt5_version": str(mt5.version()),
        "terminal": None,
    }
    term = mt5.terminal_info()
    if term is not None:
        report["terminal"] = {
            "name": term.name,
            "company": term.company,
            "path": term.path,
            "connected": bool(getattr(term, "connected", False)),
        }
    acc = mt5.account_info()
    if acc is not None:
        report["account"] = {
            "login": int(acc.login),
            "server": acc.server,
            "company": acc.company,
            "currency": acc.currency,
        }

    # 1. available symbols
    syms = fetch_symbols()
    report["symbols_total"] = len(syms)
    report["symbols_sample"] = syms[:60]
    metals = [s["name"] for s in syms if "XAU" in s["name"] or "GOLD" in s["name"]]
    report["gold_symbols"] = metals
    print(f"Symbols total: {len(syms)}; gold: {metals}")

    # 2. availability per timeframe for the primary symbol
    availability: dict[str, Any] = {}
    for tf in TIMEFRAMES:
        earliest = first_available_time(PRIMARY, tf)
        n = 0
        raw = mt5.copy_rates_from_pos(PRIMARY, TF_MT5[tf], 0, 5)
        if raw is not None:
            n = len(raw)
        availability[tf] = {
            "available": earliest is not None,
            "earliest_epoch": earliest,
            "earliest_utc": (
                datetime.fromtimestamp(earliest, tz=UTC).isoformat() if earliest else None
            ),
            "probe_rows": n,
        }
        print(f"  {PRIMARY} {tf}: earliest={availability[tf]['earliest_utc']}")
    report["availability"] = availability

    # 3. full history capture for all timeframes the cache serves
    captures: dict[str, Any] = {}
    for tf in TIMEFRAMES:
        print(f"=== Capturing {PRIMARY} {tf} ===")
        t0 = time.perf_counter()
        df = fetch_full_history(PRIMARY, tf)
        elapsed = time.perf_counter() - t0
        if df is None or df.height == 0:
            captures[tf] = {"captured": False}
            print(f"  {PRIMARY} {tf}: NOTHING captured")
            continue
        # write parquet + csv
        pq = RAW_DIR / f"{PRIMARY}_{tf}.parquet"
        csv = RAW_DIR / f"{PRIMARY}_{tf}.csv"
        df.write_parquet(pq)
        df.write_csv(csv)
        q = quality_report(PRIMARY, tf, df)
        q["capture_elapsed_s"] = round(elapsed, 2)
        q["file_size_bytes"] = pq.stat().st_size
        captures[tf] = q
        print(
            f"  {PRIMARY} {tf}: {df.height} bars | "
            f"{df['time_utc'].min()} -> {df['time_utc'].max()} | "
            f"gaps={q['gaps']['gaps_total']} | invalid_ohlc={q['invalid_ohlc_rows']} | "
            f"dups={q['duplicate_timestamps']} | zero_vol={q['zero_neg_volume_rows']}"
        )
    report["captures"] = captures

    # 4. write report
    rp = RAW_DIR / "capture_report.json"
    rp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n=== REPORT: {rp} ===")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    t0 = time.perf_counter()
    rc = main()
    print(f"elapsed={time.perf_counter() - t0:.1f}s")
    sys.exit(rc)
