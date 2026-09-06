"""Deterministic market-stress tick/bar generator for PAPER simulation.

No torch / numpy dependency — uses stdlib ``random.Random`` seeded per run
so two calls with the same seed are bit-identical.

Public contract required by the task:
    - Regime            (Enum)
    - PaperStressScenario (dataclass)
    - generate_ticks(n, regime_sequence, seed)
    - generate_bars(n, timeframe, seed)

Seeds make runs bit-identical.  Also exposes PaperStressSpread hook so
stress suites can widen spreads intentionally while the baseline PAPER
spread stays tight enough to trade (8-18c for gold, not 25-45c).
"""

from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum  # py 3.11
from typing import Any

# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------


class Regime(StrEnum):
    """Market regime for deterministic stress generation."""

    RANGE = "RANGE"
    CALM = "CALM"  # alias for RANGE
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    VOLATILE = "VOLATILE"
    SPIKE_UP = "SPIKE_UP"
    SPIKE_DOWN = "SPIKE_DOWN"
    FLASH_CRASH = "FLASH_CRASH"


# ---------------------------------------------------------------------------
# Helpers — deterministic price walk
# ---------------------------------------------------------------------------

_DEFAULT_START_PRICE: dict[str, float] = {
    "XAUUSD": 4400.0,
    "XAGUSD": 28.0,
    "EURUSD": 1.08500,
    "GBPUSD": 1.27000,
    "USDJPY": 150.0,
}

_TIMEFRAME_MINUTES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


def _is_metal(symbol: str) -> bool:
    u = (symbol or "").upper()
    return u.startswith(("XAU", "XAG", "GOLD", "SILVER"))


def _seed_price(symbol: str, fallback: float | None = None) -> float:
    """Plausible seed price, env-overridable NEXUS_PAPER_SEED_<SYM>."""
    u = (symbol or "").upper()
    env_key = f"NEXUS_PAPER_SEED_{u}"
    raw = os.environ.get(env_key, "").strip()
    if raw:
        try:
            return float(raw)
        except Exception:
            pass
    if u in _DEFAULT_START_PRICE:
        return _DEFAULT_START_PRICE[u]
    if fallback is not None:
        return float(fallback)
    return 4400.0 if _is_metal(symbol) else 1.08


def _digits(symbol: str) -> int:
    return 2 if _is_metal(symbol) else 5


def _regime_step(rng: random.Random, regime: Regime, symbol: str) -> float:
    """Deterministic signed price step for one tick under regime."""
    is_metal = _is_metal(symbol)
    if regime in (Regime.RANGE, Regime.CALM):
        # tight, mean-reverting
        return rng.uniform(-0.06, 0.06) if is_metal else rng.uniform(-0.00006, 0.00006)
    if regime == Regime.TREND_UP:
        # persistent upward drift + small noise
        base = 0.10 if is_metal else 0.00010
        return base + rng.uniform(-0.04, 0.05)
    if regime == Regime.TREND_DOWN:
        base = -0.10 if is_metal else -0.00010
        return base + rng.uniform(-0.05, 0.04)
    if regime == Regime.VOLATILE:
        return rng.uniform(-0.45, 0.45) if is_metal else rng.uniform(-0.00040, 0.00040)
    if regime == Regime.SPIKE_UP:
        # rare aggressive burst
        return (
            rng.choice([0.35, 0.55, 0.80]) if is_metal else rng.choice([0.00035, 0.00055, 0.00080])
        )
    if regime == Regime.SPIKE_DOWN:
        return (
            rng.choice([-0.35, -0.55, -0.80])
            if is_metal
            else rng.choice([-0.00035, -0.00055, -0.00080])
        )
    if regime == Regime.FLASH_CRASH:
        # strong negative, then partial rebound handled by caller walk
        return rng.uniform(-1.2, -0.6) if is_metal else rng.uniform(-0.0012, -0.0006)
    # fallback
    return rng.uniform(-0.06, 0.06) if is_metal else rng.uniform(-0.00006, 0.00006)


def _regime_spread(rng: random.Random, regime: Regime, symbol: str, scale: float = 1.0) -> float:
    """Deterministic spread for a tick. Baseline gold 0.08-0.18, scaled."""
    is_metal = _is_metal(symbol)
    if is_metal:
        if regime == Regime.VOLATILE:
            lo, hi = 0.12, 0.28
        elif regime in (Regime.SPIKE_UP, Regime.SPIKE_DOWN, Regime.FLASH_CRASH):
            lo, hi = 0.18, 0.40
        else:
            lo, hi = 0.08, 0.18
        base = rng.uniform(lo, hi)
        return round(base * float(scale), 2)
    # FX
    base_fx = rng.uniform(0.00008, 0.00016)
    return round(base_fx * float(scale), 5)


# ---------------------------------------------------------------------------
# PaperStressSpread hook — lets stress suites widen spread intentionally
# ---------------------------------------------------------------------------


@dataclass
class PaperStressSpread:
    """Spread override installed onto a Paper adapter for a stress run.

    Baseline PAPER stays at 8-18c (scale 1.0) so signals can actually fire.
    Stress tests set ``scale`` >> 1 (e.g. 2.5) to simulate illiquidity.
    """

    scale: float = 1.0
    min_spread: float = 0.08
    max_spread: float = 0.18

    def install(self, adapter: Any) -> None:
        """Attach this spread profile to a PaperMT5Adapter instance."""
        try:
            adapter.set_stress_spread(self.scale)  # type: ignore[attr-defined]
        except AttributeError:
            # fallback: set the private override directly
            adapter._stress_spread_scale = float(self.scale)
            adapter._stress_spread_profile = self

    @staticmethod
    def uninstall(adapter: Any) -> None:
        try:
            adapter.clear_stress_spread()  # type: ignore[attr-defined]
        except AttributeError:
            adapter._stress_spread_scale = None
            adapter._stress_spread_profile = None


def _effective_spread_scale(adapter: Any | None = None) -> float:
    """Resolve spread scale: adapter override > env NEXUS_PAPER_SPREAD_SCALE > 1.0."""
    if adapter is not None:
        v = getattr(adapter, "_stress_spread_scale", None)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    raw = os.environ.get("NEXUS_PAPER_SPREAD_SCALE", "").strip()
    if raw:
        try:
            s = float(raw)
            # clamp to avoid pathological values
            return max(0.1, min(s, 10.0))
        except Exception:
            pass
    return 1.0


# ---------------------------------------------------------------------------
# Core dataclass
# ---------------------------------------------------------------------------


@dataclass
class PaperStressScenario:
    """Deterministic stress scenario.

    Attributes:
        symbol:            e.g. XAUUSD
        regime_sequence:   ordered regimes to cycle through (one per tick/bar)
        start_price:       seed mid price; ``None`` -> plausible default / env
        seed:              default RNG seed if not passed to generators
        spread_scale:      1.0 = baseline 8-18c gold; >1 widens for stress
        baseline_volatility: multiplier on step sizes
    """

    symbol: str = "XAUUSD"
    regime_sequence: list[Regime] = field(default_factory=lambda: [Regime.RANGE])
    start_price: float | None = None
    seed: int | None = 42
    spread_scale: float = 1.0
    baseline_volatility: float = 1.0
    start_time: datetime | None = None

    def __post_init__(self) -> None:
        # normalize regime entries that may come in as strings
        norm: list[Regime] = []
        for r in self.regime_sequence or [Regime.RANGE]:
            if isinstance(r, Regime):
                norm.append(r)
            elif isinstance(r, str):
                try:
                    norm.append(Regime(r))
                except ValueError:
                    # allow case-insensitive
                    norm.append(Regime(r.upper()))
            else:
                norm.append(Regime.RANGE)
        self.regime_sequence = norm or [Regime.RANGE]
        if self.start_price is None:
            self.start_price = _seed_price(self.symbol)
        if self.start_time is None:
            self.start_time = datetime.now(UTC).replace(second=0, microsecond=0)

    # Convenience wrappers so callers can use scenario.generate_ticks(n, seed=...)
    def generate_ticks(  # type: ignore[no-redef]
        self,
        n: int,
        regime_sequence: list[Regime] | None = None,
        seed: int | None = None,
    ) -> list[Any]:
        return generate_ticks(
            n,
            regime_sequence=regime_sequence
            if regime_sequence is not None
            else self.regime_sequence,
            seed=seed if seed is not None else self.seed,
            symbol=self.symbol,
            start_price=self.start_price,
            spread_scale=self.spread_scale,
            start_time=self.start_time,
        )

    def generate_bars(
        self,
        n: int,
        timeframe: str = "M1",
        seed: int | None = None,
    ) -> list[Any]:
        return generate_bars(
            n,
            timeframe=timeframe,
            seed=seed if seed is not None else self.seed,
            symbol=self.symbol,
            start_price=self.start_price,
            regime_sequence=self.regime_sequence,
            spread_scale=self.spread_scale,
            start_time=self.start_time,
        )


# ---------------------------------------------------------------------------
# Module-level deterministic generators (required contract)
# ---------------------------------------------------------------------------


def generate_ticks(
    n: int,
    regime_sequence: list[Regime] | list[str] | None = None,
    seed: int | None = None,
    *,
    symbol: str = "XAUUSD",
    start_price: float | None = None,
    spread_scale: float | None = None,
    start_time: datetime | None = None,
) -> list[Any]:
    """Generate *n* deterministic ticks. Seed makes run bit-identical.

    Args:
        n:                number of ticks
        regime_sequence:  list of Regime (cycled one per tick); None -> [RANGE]
        seed:             RNG seed; None -> 42
        symbol:           instrument (kw-only, default XAUUSD)
        start_price:      seed mid price
        spread_scale:     spread multiplier (1.0 = 8-18c gold)
        start_time:       first tick timestamp

    Returns:
        list[TickData] (falls back to dicts if domain import unavailable)
    """
    if n <= 0:
        return []
    seq: list[Regime] = []
    raw_seq = regime_sequence if regime_sequence is not None else [Regime.RANGE]
    for r in raw_seq:
        if isinstance(r, Regime):
            seq.append(r)
        elif isinstance(r, str):
            try:
                seq.append(Regime(r))
            except ValueError:
                seq.append(Regime(r.upper()))  # type: ignore[arg-type]
        else:
            seq.append(Regime.RANGE)
    if not seq:
        seq = [Regime.RANGE]

    rng = random.Random(int(seed) if seed is not None else 42)
    digits = _digits(symbol)
    is_metal = _is_metal(symbol)
    price = float(start_price if start_price is not None else _seed_price(symbol))
    baseline = price
    scale = float(spread_scale if spread_scale is not None else 1.0)
    t0 = (
        start_time if start_time is not None else datetime.now(UTC).replace(second=0, microsecond=0)
    )

    # Try to build real TickData; fall back to dicts if import fails (no torch needed)
    try:
        from nexus_scalp.domain.models import TickData as _TickData  # local import

        use_model = True
    except Exception:
        _TickData = dict  # type: ignore
        use_model = False

    out: list[Any] = []
    for i in range(int(n)):
        regime = seq[i % len(seq)]
        step = _regime_step(rng, regime, symbol)  # already instrument-aware via symbol
        # baseline_volatility handled via caller scenario; raw step already deterministic
        candidate = price + step
        # mean-revert if drift > 2% from baseline
        if baseline > 0 and abs(candidate - baseline) > baseline * 0.02:
            candidate = baseline + (candidate - baseline) * 0.5
        price = round(candidate, digits)
        spread = _regime_spread(rng, regime, symbol, scale)
        bid = round(price, digits)
        ask = round(bid + spread, digits)
        # ensure bid <= ask even after rounding
        if ask < bid:
            ask = round(bid + (0.08 if is_metal else 0.00010), digits)
        ts = t0 + timedelta(milliseconds=i * 250)  # 4 ticks/sec
        vol = float(rng.randint(1, 15))
        if use_model:
            out.append(
                _TickData(  # type: ignore[operator]
                    symbol=symbol,
                    timestamp=ts,
                    bid=bid,
                    ask=ask,
                    last=bid,
                    volume=vol,
                    flags=6,
                )
            )
        else:
            out.append(
                {
                    "symbol": symbol,
                    "timestamp": ts.isoformat(),
                    "bid": bid,
                    "ask": ask,
                    "last": bid,
                    "volume": vol,
                    "flags": 6,
                    "regime": regime.value,
                    "spread": spread,
                }
            )
    return out


def generate_bars(
    n: int,
    timeframe: str = "M1",
    seed: int | None = None,
    *,
    symbol: str = "XAUUSD",
    start_price: float | None = None,
    regime_sequence: list[Regime] | None = None,
    spread_scale: float | None = None,
    start_time: datetime | None = None,
) -> list[Any]:
    """Generate *n* deterministic OHLC bars. Seed makes run bit-identical.

    Bars are contiguous, ascending, completed (is_complete=True). Each bar's
    OHLC is derived from several deterministic ticks so high/low are not
    degenerate.
    """
    if n <= 0:
        return []
    tf = str(timeframe).upper()
    bar_min = _TIMEFRAME_MINUTES.get(tf, 1)
    rng = random.Random(int(seed) if seed is not None else 42)
    digits = _digits(symbol)
    price = float(start_price if start_price is not None else _seed_price(symbol))
    baseline = price
    seq = regime_sequence if regime_sequence is not None else [Regime.RANGE]
    # normalize seq
    norm_seq: list[Regime] = []
    for r in seq:
        if isinstance(r, Regime):
            norm_seq.append(r)
        elif isinstance(r, str):  # type: ignore[unreachable]
            try:
                norm_seq.append(Regime(r))  # type: ignore[arg-type]
            except ValueError:
                norm_seq.append(Regime(r.upper()))  # type: ignore[arg-type]
        else:
            norm_seq.append(Regime.RANGE)
    if not norm_seq:
        norm_seq = [Regime.RANGE]

    t0 = (
        start_time if start_time is not None else datetime.now(UTC).replace(second=0, microsecond=0)
    )
    # align to timeframe boundary
    minute = (t0.minute // bar_min) * bar_min
    t0 = t0.replace(minute=minute, second=0, microsecond=0)

    try:
        from nexus_scalp.market_data.bar_aggregator import BarData as _BarData

        use_model = True
    except Exception:
        _BarData = dict  # type: ignore
        use_model = False

    out: list[Any] = []
    for i in range(int(n)):
        regime = norm_seq[i % len(norm_seq)]
        # drift magnitude per regime
        if regime == Regime.VOLATILE:
            amp = 0.55 if _is_metal(symbol) else 0.00055
        elif regime in (Regime.SPIKE_UP, Regime.SPIKE_DOWN, Regime.FLASH_CRASH):
            amp = 0.90 if _is_metal(symbol) else 0.00090
        elif regime in (Regime.TREND_UP, Regime.TREND_DOWN):
            amp = 0.30 if _is_metal(symbol) else 0.00030
        else:
            amp = 0.18 if _is_metal(symbol) else 0.00018

        drift = rng.uniform(-amp, amp)
        # trend bias
        if regime == Regime.TREND_UP:
            drift = abs(drift) * 0.7 + rng.uniform(0.05, 0.15) * (1 if _is_metal(symbol) else 0.001)
        elif regime == Regime.TREND_DOWN:
            drift = -abs(drift) * 0.7 - rng.uniform(0.05, 0.15) * (
                1 if _is_metal(symbol) else 0.001
            )
        elif regime == Regime.SPIKE_UP:
            drift = abs(drift) + (0.5 if _is_metal(symbol) else 0.0005)
        elif regime == Regime.SPIKE_DOWN:
            drift = -abs(drift) - (0.5 if _is_metal(symbol) else 0.0005)
        elif regime == Regime.FLASH_CRASH:
            drift = -abs(drift) - (1.0 if _is_metal(symbol) else 0.001)

        # mean-revert guard
        candidate_close = price + drift
        if baseline > 0 and abs(candidate_close - baseline) > baseline * 0.03:
            candidate_close = baseline + (candidate_close - baseline) * 0.6

        open_p = round(price, digits)
        close_p = round(candidate_close, digits)
        # intrabar high/low from extra jitter
        jitter = abs(drift) * 0.6 + (
            rng.uniform(0.02, 0.10) if _is_metal(symbol) else rng.uniform(0.00002, 0.00010)
        )
        high_p = round(max(open_p, close_p) + jitter * 0.5, digits)
        low_p = round(min(open_p, close_p) - jitter * 0.5, digits)
        # clamp low <= open,close <= high
        low_p = min(low_p, open_p, close_p)
        high_p = max(high_p, open_p, close_p)

        ts = t0 + timedelta(minutes=bar_min * i)
        tick_vol = int(rng.randint(50, 250))
        if use_model:
            out.append(
                _BarData(  # type: ignore[operator]
                    symbol=symbol,
                    timeframe=tf,
                    timestamp=ts,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    tick_volume=tick_vol,
                    is_complete=True,
                )
            )
        else:
            out.append(
                {
                    "symbol": symbol,
                    "timeframe": tf,
                    "timestamp": ts.isoformat(),
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "tick_volume": tick_vol,
                    "is_complete": True,
                    "regime": regime.value,
                }
            )
        price = close_p
    return out


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


def _cli() -> None:
    p = argparse.ArgumentParser(description="Deterministic PAPER stress generator")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--ticks", type=int, default=0, help="number of ticks to emit")
    p.add_argument("--bars", type=int, default=0, help="number of bars to emit")
    p.add_argument("--timeframe", default="M1")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--regimes",
        default="RANGE,TREND_UP,VOLATILE,TREND_DOWN",
        help="comma-separated Regime names cycled per tick/bar",
    )
    p.add_argument("--spread-scale", type=float, default=1.0)
    p.add_argument("--json", action="store_true", help="emit JSON lines")
    args = p.parse_args()

    seq: list[Regime] = []
    for raw_name in args.regimes.split(","):
        clean = raw_name.strip().upper()
        if not clean:
            continue
        try:
            seq.append(Regime(clean))
        except ValueError as exc:
            raise SystemExit(
                f"unknown regime: {clean} (valid: {[r.value for r in Regime]})"
            ) from exc

    if args.ticks:
        ticks = generate_ticks(
            args.ticks, seq, seed=args.seed, symbol=args.symbol, spread_scale=args.spread_scale
        )
        if args.json:
            for t in ticks:
                if hasattr(t, "model_dump"):
                    print(json.dumps(t.model_dump(), default=str))
                else:
                    print(json.dumps(t, default=str))
        else:
            for t in ticks:
                print(t)

    if args.bars:
        bars = generate_bars(
            args.bars,
            timeframe=args.timeframe,
            seed=args.seed,
            symbol=args.symbol,
            regime_sequence=seq,
            spread_scale=args.spread_scale,
        )
        if args.json:
            for b in bars:
                if hasattr(b, "model_dump"):
                    print(json.dumps(b.model_dump(), default=str))
                else:
                    print(json.dumps(b, default=str))
        else:
            for b in bars:
                print(b)

    if not args.ticks and not args.bars:
        p.print_help()


if __name__ == "__main__":
    _cli()
