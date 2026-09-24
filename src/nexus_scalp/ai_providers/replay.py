"""Replay / backtest engine over the MT5 MCP (ECOSYSTEM-001, Section 45).

What this is
------------
Replay historical bars through the SAME canonical pipeline a live decision uses:

    bars (MT5 MCP) -> simulated position + market state at t
                   -> canonical PositionDecisionRequest
                   -> internal ML / System One / OpenRouter
                   -> deterministic policy
                   -> risk gate
                   -> recorded outcome vs the real future bars

What it is NOT
--------------
Not a strategy tester. Not an EA. It never places, modifies or closes an order:
the recording step is observation-only, and the strategy tester's order tools
(trade_*, tester_run_backtest) are deliberately not wired here (Section 71:
real provider testing must never place real trades).

No future-data leakage (Section 9)
----------------------------------
At decision time ``t`` the engine only ever builds requests from bars
``[start .. t]``. The provider sees the same information horizon a live system
would. Future bars are read ONLY after the decision, to score it -- and never
included in any request payload. ``decision_horizon_bar_count`` exists to make
the boundary explicit and unit-testable.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.ai_providers.contract import PositionDecisionRequest
from nexus_scalp.ai_providers.errors import ProviderError, ProviderErrorCategory
from nexus_scalp.ai_providers.mt5_mcp import MT5MCPClient
from nexus_scalp.ai_providers.sample import SIMULATED_CONTEXT_VERSION

# The bars at [t+1 .. t+HORIZON] are used to score a decision only.
DECISION_HORIZON_BARS = 12


@dataclass(frozen=True)
class ReplayResult:
    """One replay step: decision at t, outcome measured afterwards."""

    step: int
    decision_time: str
    provider: str
    model: str | None
    action: str
    policy_winner: str
    confidence: float
    entry_price: float
    exit_price: float | None
    realized_r: float
    mae_r: float
    mfe_r: float
    bars_held: int
    fallback_used: bool
    failure_category: str | None
    latency_ms: float
    is_test_data: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "is_test_data": True,
            "recorded_as": "SIMULATED TEST DATA",
        }


@dataclass
class ReplaySummary:
    symbol: str
    period: str
    steps: int
    decided: int
    failed: int
    hold_count: int
    close_count: int
    other_count: int
    mean_realized_r: float
    mean_mae_r: float
    mean_mfe_r: float
    fallback_count: int
    mean_latency_ms: float
    failures_by_category: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


@dataclass
class BacktestConfig:
    endpoint: str = "http://127.0.0.1:22346/mcp"
    api_key: str | None = None
    symbol: str = "XAUUSD"
    period: str = "M15"
    datetime_from: str = ""
    datetime_to: str = ""
    provider: str = "internal_nse_ml"
    max_steps: int = 12
    bars_per_step: int = 200
    account_balance: float = 10000.0
    risk_per_trade: float = 0.02
    take_profit_r: float = 1.5
    stop_loss_r: float = 1.0


class ReplayEngine:
    """Replays historical bars through the provider ecosystem."""

    def __init__(self, orchestrator: Any, client: MT5MCPClient | None = None) -> None:
        self._orch = orchestrator
        self._client = client

    # -- entry points ----------------------------------------------------------

    def run(
        self,
        config: BacktestConfig,
        on_step: Any = None,
    ) -> tuple[list[ReplayResult], ReplaySummary]:
        bars = self._load_bars(config)
        if len(bars) < config.bars_per_step + DECISION_HORIZON_BARS + 5:
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "not enough bars for replay: got "
                f"{len(bars)}, need {config.bars_per_step + DECISION_HORIZON_BARS + 5}",
            )
        steps = self._step_indices(bars, config)
        results: list[ReplayResult] = []
        for idx, t in enumerate(steps):
            result = self._decide_and_record(idx, bars, t, config)
            results.append(result)
            if on_step:
                on_step(result)
        return results, self._summarise(config, results)

    def health(self, config: BacktestConfig) -> dict[str, Any]:
        client = self._client or MT5MCPClient(config.endpoint, config.api_key)
        return client.health()

    # -- core ------------------------------------------------------------------

    def _load_bars(self, config: BacktestConfig) -> list[dict[str, Any]]:
        client = self._client or MT5MCPClient(config.endpoint, config.api_key)
        bars = client.chart_history(config.symbol, config.period, config.datetime_from, config.datetime_to)
        bars = [b for b in bars if b.get("close") is not None]
        bars.sort(key=lambda b: str(b.get("time")))
        return bars

    def _step_indices(self, bars: list[dict[str, Any]], config: BacktestConfig) -> list[int]:
        """Decision points, spaced so the outcome window fits inside the data."""
        last_decidable = len(bars) - 1 - DECISION_HORIZON_BARS
        if last_decidable < config.bars_per_step:
            return []
        n = min(config.max_steps, last_decidable - config.bars_per_step + 1)
        if n <= 0:
            return []
        step = max(1, (last_decidable - config.bars_per_step) // max(1, n - 1)) if n > 1 else 1
        indices = [config.bars_per_step + i * step for i in range(n)]
        return sorted(set(i for i in indices if config.bars_per_step <= i <= last_decidable))

    def _decide_and_record(
        self,
        step: int,
        bars: list[dict[str, Any]],
        t: int,
        config: BacktestConfig,
    ) -> ReplayResult:
        # Only bars [start .. t] may influence the request (Section 9).
        visible = bars[max(0, t - config.bars_per_step + 1) : t + 1]
        request = self._build_request(step, visible, config)
        start = time.perf_counter()
        category: str | None = None
        fallback = False
        action = "NO_ACTION"
        winner = "NO_ACTION"
        confidence = 0.0
        provider = config.provider
        model: str | None = None
        try:
            outcome = self._orch.evaluate_position(request)
            action = outcome.final_action
            winner = outcome.policy.winner
            confidence = float(outcome.policy.scores.get(outcome.policy.winner, 0.0))
            provider = outcome.providers_used[0] if outcome.providers_used else config.provider
            fallback = bool(outcome.fallback_used)
            model = next(
                (
                    (outcome.evidence.get(p) or {}).get("model")
                    for p in outcome.providers_used
                    if (outcome.evidence.get(p) or {}).get("model")
                ),
                None,
            )
        except ProviderError as exc:
            category = exc.category.value
        except Exception as exc:  # noqa: BLE001 - replay must not die on one step
            category = f"UNEXPECTED:{type(exc).__name__}"

        entry = float(visible[-1]["close"])
        outcome_stats = self._measure_outcome(bars, t, config, action, entry)
        return ReplayResult(
            step=step,
            decision_time=str(bars[t].get("time")),
            provider=provider,
            model=model,
            action=action,
            policy_winner=winner,
            confidence=confidence,
            entry_price=entry,
            exit_price=outcome_stats["exit_price"],
            realized_r=outcome_stats["realized_r"],
            mae_r=outcome_stats["mae_r"],
            mfe_r=outcome_stats["mfe_r"],
            bars_held=outcome_stats["bars_held"],
            fallback_used=fallback,
            failure_category=category,
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    def _build_request(
        self,
        step: int,
        visible: list[dict[str, Any]],
        config: BacktestConfig,
    ) -> PositionDecisionRequest:
        """Canonical snapshot built ONLY from ``visible`` bars.

        The position is synthetic: replay has no live ticket, so the fields are
        clearly marked and the risk context is policy-derived, not invented.
        """
        latest = visible[-1]
        entry_idx = max(0, len(visible) // 2)
        entry = float(visible[entry_idx]["close"])
        close = float(latest["close"])
        atr = _atr(visible[-14:] if len(visible) >= 14 else visible)
        direction = 1 if close >= entry else -1
        # One shared definition of "1R" for request and outcome scoring, so the
        # decision's unrealized_r and the measured realized_r are on the same
        # scale (a mismatch would silently mis-grade every replay step).
        risk_distance = max(atr * config.stop_loss_r, 1e-9)
        stop_loss = entry - direction * risk_distance
        take_profit = entry + direction * risk_distance * config.take_profit_r / config.stop_loss_r

        return PositionDecisionRequest(
            schema_version="1.0.0",
            position_id=f"REPLAY-{config.symbol}-{step}",
            ticket=0,  # synthetic: replay has no live broker ticket
            symbol=config.symbol,
            side="BUY" if direction > 0 else "SELL",
            entry_price=entry,
            current_price=close,
            average_price=entry,
            volume=0.01,
            position_age_sec=float(len(visible) - entry_idx) * 15.0 * 60.0,
            unrealized_pnl=(close - entry) * direction,
            unrealized_r=_r_multiples(close - entry, risk_distance, direction),
            current_tp=take_profit,
            current_sl=stop_loss,
            bid=close - 0.02,
            ask=close + 0.02,
            spread=0.04,
            spread_relative=0.04 / close,
            volatility=atr,
            atr=atr,
            momentum=_momentum(visible[-10:] if len(visible) >= 10 else visible),
            trend="UP" if direction > 0 else "DOWN",
            market_structure="RANGE",
            regime="UNKNOWN",
            regime_confidence=0.0,
            session="UNKNOWN",
            liquidity_state="UNKNOWN",
            news_state="",
            balance=config.account_balance,
            equity=config.account_balance + (close - entry) * direction,
            margin=120.0,
            free_margin=config.account_balance - 120.0,
            margin_level=250.0,
            leverage=100,
            account_currency="USD",
            current_risk=config.risk_per_trade,
            max_allowed_risk=0.05,
            risk_budget=0.05 - config.risk_per_trade,
            distance_to_sl=abs(close - stop_loss),
            distance_to_tp=abs(take_profit - close),
            reward_to_risk=config.take_profit_r / config.stop_loss_r,
            exposure=config.account_balance * config.risk_per_trade,
            model_confidence=0.0,
            model_prediction="",
            broker="SIMULATED",
            server="REPLAY",
            tick_size=0.01,
            digits=2,
            volume_step=0.01,
            stops_level=0.0,
            freeze_level=0.0,
            timestamp=datetime.now(UTC),
            data_freshness_sec=0.0,
            provider_request_id=f"replay-{step}-{int(time.time())}",
            decision_context_version=SIMULATED_CONTEXT_VERSION,
        )

    def _measure_outcome(
        self,
        bars: list[dict[str, Any]],
        t: int,
        config: BacktestConfig,
        action: str,
        entry: float,
    ) -> dict[str, Any]:
        """Score the decision against bars [t+1 .. t+HORIZON].

        These bars are read AFTER the decision and never enter any request.
        For CLOSE/REDUCE the trade is resolved immediately at t+1; for HOLD it
        rides the horizon and resolves at the exit that actually occurs first:
        TP, then SL, else the last bar.
        """
        ahead = bars[t + 1 : t + 1 + DECISION_HORIZON_BARS]
        if not ahead:
            return {"exit_price": None, "realized_r": 0.0, "mae_r": 0.0, "mfe_r": 0.0, "bars_held": 0}

        visible = bars[max(0, t - config.bars_per_step + 1) : t + 1]
        entry_idx = max(0, len(visible) // 2)
        atr = _atr(visible[-14:] if len(visible) >= 14 else visible)
        risk_distance = max(atr * config.stop_loss_r, 1e-9)
        direction = 1 if entry <= ahead[0]["close"] else -1
        # The synthetic position's own SL/TP, recomputed identically to the request.
        stop_loss = entry - direction * risk_distance
        take_profit = entry + direction * risk_distance * config.take_profit_r / config.stop_loss_r

        if action in ("CLOSE", "REDUCE"):
            exit_price = float(ahead[0]["close"])
            return {
                "exit_price": exit_price,
                "realized_r": _r_multiples(exit_price - entry, risk_distance, direction),
                "mae_r": _worst_r(ahead, entry, risk_distance, direction),
                "mfe_r": _best_r(ahead, entry, risk_distance, direction),
                "bars_held": 1,
            }

        # HOLD / NO_ACTION / adjustments: walk forward to the first TP or SL hit.
        exit_price = float(ahead[-1]["close"])
        bars_held = len(ahead)
        for i, bar in enumerate(ahead):
            if direction > 0 and bar.get("high") is not None and float(bar["high"]) >= take_profit:
                exit_price = take_profit
                bars_held = i + 1
                break
            if direction > 0 and bar.get("low") is not None and float(bar["low"]) <= stop_loss:
                exit_price = stop_loss
                bars_held = i + 1
                break
            if direction < 0 and bar.get("low") is not None and float(bar["low"]) <= take_profit:
                exit_price = take_profit
                bars_held = i + 1
                break
            if direction < 0 and bar.get("high") is not None and float(bar["high"]) >= stop_loss:
                exit_price = stop_loss
                bars_held = i + 1
                break
        return {
            "exit_price": exit_price,
            "realized_r": _r_multiples(exit_price - entry, risk_distance, direction),
            "mae_r": _worst_r(ahead, entry, risk_distance, direction),
            "mfe_r": _best_r(ahead, entry, risk_distance, direction),
            "bars_held": bars_held,
        }

    # -- summary ---------------------------------------------------------------

    def _summarise(self, config: BacktestConfig, results: list[ReplayResult]) -> ReplaySummary:
        decided = [r for r in results if r.failure_category is None]
        failed = [r for r in results if r.failure_category is not None]
        by_cat: dict[str, int] = {}
        for r in failed:
            by_cat[r.failure_category] = by_cat.get(r.failure_category, 0) + 1
        rvals = [r.realized_r for r in decided if r.exit_price is not None]
        return ReplaySummary(
            symbol=config.symbol,
            period=config.period,
            steps=len(results),
            decided=len(decided),
            failed=len(failed),
            hold_count=sum(1 for r in decided if r.action == "HOLD"),
            close_count=sum(1 for r in decided if r.action in ("CLOSE", "REDUCE")),
            other_count=sum(1 for r in decided if r.action not in ("HOLD", "CLOSE", "REDUCE")),
            mean_realized_r=round(sum(rvals) / len(rvals), 4) if rvals else 0.0,
            mean_mae_r=round(sum(r.mae_r for r in decided) / max(1, len(decided)), 4),
            mean_mfe_r=round(sum(r.mfe_r for r in decided) / max(1, len(decided)), 4),
            fallback_count=sum(1 for r in results if r.fallback_used),
            mean_latency_ms=round(sum(r.latency_ms for r in decided) / max(1, len(decided)), 1),
            failures_by_category=by_cat,
        )


# -- indicators (minimal, dependency-free) ------------------------------------


def _atr(bars: list[dict[str, Any]], window: int = 14) -> float:
    """True-range average. Falls back to a mean absolute range when bars are
    too few -- replay must never crash mid-run on a short window."""
    if not bars:
        return 0.0
    trs: list[float] = []
    for i, bar in enumerate(bars):
        high = _as_float(bar.get("high"))
        low = _as_float(bar.get("low"))
        prev_close = _as_float(bars[i - 1].get("close")) if i > 0 else None
        if high is None or low is None:
            continue
        if prev_close is None:
            trs.append(high - low)
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        high = _as_float(bars[0].get("high")) or 0.0
        low = _as_float(bars[0].get("low")) or 0.0
        return max(high - low, 1e-6)
    window = min(window, len(trs))
    return max(sum(trs[-window:]) / window, 1e-6)


def _momentum(bars: list[dict[str, Any]]) -> float:
    if len(bars) < 2:
        return 0.0
    first = _as_float(bars[0].get("close"))
    last = _as_float(bars[-1].get("close"))
    if first is None or last is None or first == 0:
        return 0.0
    return (last - first) / first


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _r_multiples(move: float, risk_distance: float, direction: int) -> float:
    """Signed R of a price move in the position's direction."""
    if risk_distance is None or risk_distance <= 0:
        return 0.0
    return (move * direction) / risk_distance


def _worst_r(bars: list[dict[str, Any]], entry: float, risk_distance: float, direction: int) -> float:
    worst = 0.0
    for bar in bars:
        extreme = bar.get("low") if direction > 0 else bar.get("high")
        if extreme is None:
            continue
        worst = min(worst, _r_multiples(float(extreme) - entry, risk_distance, direction))
    return worst


def _best_r(bars: list[dict[str, Any]], entry: float, risk_distance: float, direction: int) -> float:
    best = 0.0
    for bar in bars:
        extreme = bar.get("high") if direction > 0 else bar.get("low")
        if extreme is None:
            continue
        best = max(best, _r_multiples(float(extreme) - entry, risk_distance, direction))
    return best
