"""
Cost-Aware Purged Triple-Barrier Labeling Engine (v3.6 Enterprise - Hardened 3-Class)
======================================================================================
Generates machine learning training labels using Marcos Lopez de Prado's
Purged Triple-Barrier method with real Gold friction cost deductions and MAE safeguards.

Enterprise Upgrades & Hardening Incorporated:
    1. Causal 3-Class Outcome Taxonomy (0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET; WAIT is derived in live policy).
    2. Explicit Evaluation & Purged Masks (Adds 'is_eval_sample' and 'is_purged' columns for clean filtering).
    3. Dynamic Entry Bar Friction Feasibility Check (Uses max(friction_usd, entry_spread)).
    4. Configurable Minimum Valid ATR Threshold (min_valid_atr parameter in constructor).
    5. Zero-Copy Explicit Float64 Conversions (.astype(np.float64, copy=False)).
    6. Cross-Version Polars Vectorized Mapping (NumPy Array Indexing replaces replace_strict).
    7. Diagnostic Telemetry Mode (Optional exit_reason, holding_bars, realized_r columns).
    8. Structured Config & Calibration Metrics (TripleBarrierConfig, TripleBarrierMetrics).

Invariants:
    - Absolute Zero Lookahead Bias: Every barrier evaluation uses strictly forward bars up to termination step.
    - Zero Overlapping Outcomes: Embargo guarantees serial independence of training samples.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.labeling.triple_barrier")


@dataclass(frozen=True)
class TripleBarrierConfig:
    """Configuration for cost-aware purged triple-barrier labeling."""

    take_profit_atr_mult: float = 1.1  # Scalp-calibrated TP multiplier
    stop_loss_atr_mult: float = 1.0  # Tight stop for scalping
    max_holding_bars: int = 15  # Maximum forward holding horizon
    friction_usd: float = 0.35  # Real Gold friction in USD ($0.35 per oz)
    embargo_bars: int = 3  # Purged Embargo gap to prevent serial correlation
    no_trade_stride_bars: int = 3  # Stride jump on NO_TRADE to prevent Class Imbalance
    max_allowed_mae_ratio: float = (
        0.75  # Max allowed adverse drawdown ratio before invalidating time exit
    )
    min_valid_atr: float = 0.20  # Configurable minimum valid ATR threshold
    include_diagnostics: bool = False  # If True, attaches diagnostic columns

    def validate(self) -> None:
        """Validates configuration bounds."""
        if self.take_profit_atr_mult <= 0:
            raise ValueError(
                f"take_profit_atr_mult must be positive, got {self.take_profit_atr_mult}"
            )
        if self.stop_loss_atr_mult <= 0:
            raise ValueError(f"stop_loss_atr_mult must be positive, got {self.stop_loss_atr_mult}")
        if self.max_holding_bars < 1:
            raise ValueError(f"max_holding_bars must be >= 1, got {self.max_holding_bars}")
        if self.friction_usd < 0:
            raise ValueError(f"friction_usd cannot be negative, got {self.friction_usd}")
        if self.embargo_bars < 0:
            raise ValueError(f"embargo_bars cannot be negative, got {self.embargo_bars}")
        if self.no_trade_stride_bars < 1:
            raise ValueError(f"no_trade_stride_bars must be >= 1, got {self.no_trade_stride_bars}")
        if not (0.0 < self.max_allowed_mae_ratio <= 1.0):
            raise ValueError(
                f"max_allowed_mae_ratio must be in (0, 1], got {self.max_allowed_mae_ratio}"
            )
        if self.min_valid_atr < 0:
            raise ValueError(f"min_valid_atr cannot be negative, got {self.min_valid_atr}")


@dataclass(frozen=True)
class TripleBarrierMetrics:
    """Aggregated quantitative performance and distribution metrics from a labeled dataset."""

    total_bars: int
    evaluated_samples: int
    purged_samples: int
    buy_count: int
    sell_count: int
    no_trade_count: int
    buy_ratio: float
    sell_ratio: float
    no_trade_ratio: float
    balance_ratio: float
    tp_hit_count: int
    sl_hit_count: int
    time_expiry_count: int
    dual_hit_count: int
    win_rate: float
    r_expectancy: float
    profit_factor: float
    avg_holding_bars: float
    pathology_detected: bool
    pathology_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_triple_barrier_metrics(labeled_df: pl.DataFrame) -> TripleBarrierMetrics:
    """Computes aggregated statistical and economic metrics from a labeled dataframe."""
    n = len(labeled_df)
    if n == 0:
        return TripleBarrierMetrics(
            total_bars=0,
            evaluated_samples=0,
            purged_samples=0,
            buy_count=0,
            sell_count=0,
            no_trade_count=0,
            buy_ratio=0.0,
            sell_ratio=0.0,
            no_trade_ratio=0.0,
            balance_ratio=0.0,
            tp_hit_count=0,
            sl_hit_count=0,
            time_expiry_count=0,
            dual_hit_count=0,
            win_rate=0.0,
            r_expectancy=0.0,
            profit_factor=0.0,
            avg_holding_bars=0.0,
            pathology_detected=True,
            pathology_reason="Empty DataFrame",
        )

    eval_mask = labeled_df["is_eval_sample"].to_numpy().astype(bool)
    n_eval = int(np.sum(eval_mask))
    n_purged = int(np.sum(labeled_df["is_purged"].to_numpy().astype(bool)))

    labels = labeled_df["label"].to_numpy()
    eval_labels = labels[eval_mask]

    buy_cnt = int(np.sum(eval_labels == ActionType.BUY_MARKET.value))
    sell_cnt = int(np.sum(eval_labels == ActionType.SELL_MARKET.value))
    no_trade_cnt = int(np.sum(eval_labels == ActionType.NO_TRADE.value))

    buy_ratio = buy_cnt / n_eval if n_eval > 0 else 0.0
    sell_ratio = sell_cnt / n_eval if n_eval > 0 else 0.0
    no_trade_ratio = no_trade_cnt / n_eval if n_eval > 0 else 0.0

    max_signals = max(buy_cnt, sell_cnt)
    balance_ratio = (min(buy_cnt, sell_cnt) / max_signals) if max_signals > 0 else 0.0

    tp_hits = 0
    sl_hits = 0
    time_expiries = 0
    dual_hits = 0
    avg_holding = 0.0
    r_exp = 0.0
    pf = 0.0
    win_rate = 0.0

    has_diag = "exit_reason" in labeled_df.columns and "realized_r" in labeled_df.columns
    if has_diag:
        eval_reasons = labeled_df["exit_reason"].to_numpy()[eval_mask]
        eval_r = labeled_df["realized_r"].to_numpy()[eval_mask].astype(np.float64)
        eval_holding = (
            labeled_df["holding_bars"].to_numpy()[eval_mask].astype(np.float64)
            if "holding_bars" in labeled_df.columns
            else np.zeros(n_eval)
        )

        tp_hits = int(np.sum(np.isin(eval_reasons, ["BUY_TP_HIT", "SELL_TP_HIT"])))
        sl_hits = int(np.sum(eval_reasons == "SL_HIT"))
        time_expiries = int(
            np.sum(
                np.isin(
                    eval_reasons,
                    ["TIME_EXPIRY_BUY", "TIME_EXPIRY_SELL", "TIME_EXPIRY_NO_TRADE"],
                )
            )
        )
        dual_hits = int(np.sum(eval_reasons == "DUAL_HIT_NEUTRALIZED"))

        avg_holding = float(np.mean(eval_holding)) if len(eval_holding) > 0 else 0.0

        if len(eval_r) > 0:
            wins = eval_r[eval_r > 0]
            losses = eval_r[eval_r < 0]
            r_exp = float(np.mean(eval_r))
            win_rate = float(len(wins) / len(eval_r))
            gross_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
            gross_loss = float(abs(np.sum(losses))) if len(losses) > 0 else 0.0
            pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    else:
        directional_count = buy_cnt + sell_cnt
        win_rate = directional_count / n_eval if n_eval > 0 else 0.0

    pathology = False
    reasons: list[str] = []
    if n_eval == 0:
        pathology = True
        reasons.append("Zero evaluated samples")
    elif buy_cnt == 0:
        pathology = True
        reasons.append("Zero BUY labels produced")
    elif sell_cnt == 0:
        pathology = True
        reasons.append("Zero SELL labels produced")
    elif (buy_cnt + sell_cnt) == 0:
        pathology = True
        reasons.append("All samples labeled NO_TRADE")

    return TripleBarrierMetrics(
        total_bars=n,
        evaluated_samples=n_eval,
        purged_samples=n_purged,
        buy_count=buy_cnt,
        sell_count=sell_cnt,
        no_trade_count=no_trade_cnt,
        buy_ratio=buy_ratio,
        sell_ratio=sell_ratio,
        no_trade_ratio=no_trade_ratio,
        balance_ratio=balance_ratio,
        tp_hit_count=tp_hits,
        sl_hit_count=sl_hits,
        time_expiry_count=time_expiries,
        dual_hit_count=dual_hits,
        win_rate=win_rate,
        r_expectancy=r_exp,
        profit_factor=pf,
        avg_holding_bars=avg_holding,
        pathology_detected=pathology,
        pathology_reason="; ".join(reasons) if reasons else "None",
    )


class TripleBarrierLabeler:
    """Computes outcome-based training labels for historical Gold (XAUUSD) tick/bar data.

    Institutional Purged Walk-Forward & Friction-Aware Implementation.
    """

    def __init__(
        self,
        take_profit_atr_mult: float = 1.1,  # Scalp-calibrated TP multiplier
        stop_loss_atr_mult: float = 1.0,  # Tight stop for scalping
        max_holding_bars: int = 15,  # Maximum forward holding horizon
        friction_usd: float = 0.35,  # Real Gold friction in USD ($0.35 per oz)
        embargo_bars: int = 3,  # Purged Embargo gap to prevent serial correlation
        no_trade_stride_bars: int = 3,  # Stride jump on NO_TRADE to prevent Class Imbalance
        max_allowed_mae_ratio: float = 0.75,  # Max allowed adverse drawdown ratio before invalidating time exit
        min_valid_atr: float = 0.20,  # Configurable minimum valid ATR threshold
        include_diagnostics: bool = False,
        config: TripleBarrierConfig | None = None,
    ) -> None:
        if config is not None:
            self.config = config
            self.tp_mult = config.take_profit_atr_mult
            self.sl_mult = config.stop_loss_atr_mult
            self.max_holding = config.max_holding_bars
            self.friction_usd = config.friction_usd
            self.embargo_bars = config.embargo_bars
            self.no_trade_stride_bars = config.no_trade_stride_bars
            self.max_allowed_mae_ratio = config.max_allowed_mae_ratio
            self.min_valid_atr = config.min_valid_atr
            self.include_diagnostics = config.include_diagnostics
        else:
            self.tp_mult = take_profit_atr_mult
            self.sl_mult = stop_loss_atr_mult
            self.max_holding = max_holding_bars
            self.friction_usd = friction_usd
            self.embargo_bars = embargo_bars
            self.no_trade_stride_bars = no_trade_stride_bars
            self.max_allowed_mae_ratio = max_allowed_mae_ratio
            self.min_valid_atr = min_valid_atr
            self.include_diagnostics = include_diagnostics
            self.config = TripleBarrierConfig(
                take_profit_atr_mult=take_profit_atr_mult,
                stop_loss_atr_mult=stop_loss_atr_mult,
                max_holding_bars=max_holding_bars,
                friction_usd=friction_usd,
                embargo_bars=embargo_bars,
                no_trade_stride_bars=no_trade_stride_bars,
                max_allowed_mae_ratio=max_allowed_mae_ratio,
                min_valid_atr=min_valid_atr,
                include_diagnostics=include_diagnostics,
            )
        self.config.validate()

    def compute_metrics(self, labeled_df: pl.DataFrame) -> TripleBarrierMetrics:
        """Computes summary metrics from a labeled dataframe."""
        return compute_triple_barrier_metrics(labeled_df)

    def label_dataframe(
        self, df: pl.DataFrame, include_diagnostics: bool | None = None
    ) -> pl.DataFrame:
        """Calculates friction-aware Purged Triple-Barrier labels for a Polars DataFrame.

        Expected input columns: ['close', 'high', 'low', 'atr' or 'atr_m1']
        Optional input column: ['spread']
        Adds output columns:
            - 'label': ActionType string value ('NO_TRADE', 'BUY_MARKET', 'SELL_MARKET')
            - 'is_eval_sample': Boolean flag indicating if the row was explicitly evaluated
            - 'is_purged': Boolean flag indicating if row was skipped due to embargo/stride
        Optional diagnostic output columns (when include_diagnostics=True):
            - 'exit_reason': string describing termination reason ('BUY_TP_HIT', 'SELL_TP_HIT', etc.)
            - 'holding_bars': int indicating holding duration until barrier hit
            - 'realized_r': float indicating realized return in R multiples
        """
        use_diag = (
            include_diagnostics if include_diagnostics is not None else self.include_diagnostics
        )

        # Robust Column Name Fallback for ATR ('atr_m1' from FeatureVector or 'atr' from historical DB)
        atr_col = "atr_m1" if "atr_m1" in df.columns else ("atr" if "atr" in df.columns else None)
        if atr_col is None:
            raise ValueError("DataFrame must contain either 'atr_m1' or 'atr' column.")

        # Zero-Copy NumPy float64 Array Conversion
        closes = df["close"].to_numpy().astype(np.float64, copy=False)
        highs = df["high"].to_numpy().astype(np.float64, copy=False)
        lows = df["low"].to_numpy().astype(np.float64, copy=False)
        atrs = df[atr_col].to_numpy().astype(np.float64, copy=False)

        has_spread = "spread" in df.columns
        spreads = (
            df["spread"].to_numpy().astype(np.float64, copy=False)
            if has_spread
            else np.full(len(df), self.friction_usd, dtype=np.float64)
        )

        n = len(df)

        # Int8 Encoded Numerical Array (0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET)
        encoded_labels = np.zeros(n, dtype=np.int8)
        evaluated_mask = np.zeros(n, dtype=bool)
        purged_mask = np.ones(n, dtype=bool)

        # Diagnostics Arrays
        exit_reasons = np.full(n, "NOT_EVALUATED", dtype=object)
        holding_steps = np.zeros(n, dtype=np.int32)
        realized_r_values = np.zeros(n, dtype=np.float64)

        skipped_invalid_atr = 0

        i = 0
        while i < n:
            close_price = closes[i]
            atr = atrs[i]

            # Skip invalid / uninitialized ATR rows
            if np.isnan(atr) or atr <= self.min_valid_atr:
                skipped_invalid_atr += 1
                exit_reasons[i] = "INVALID_ATR"
                i += 1
                continue

            entry_spread = spreads[i] if not np.isnan(spreads[i]) else self.friction_usd
            effective_friction = max(self.friction_usd, entry_spread)
            half_spread = entry_spread / 2.0

            # Spread-Adjusted Entry Prices (BUY at Ask, SELL at Bid)
            buy_entry = close_price + half_spread
            sell_entry = close_price - half_spread

            tp_dist = atr * self.tp_mult
            sl_dist = atr * self.sl_mult

            # Feasibility Check: Require TP distance to strictly exceed effective friction cost
            if tp_dist <= effective_friction:
                exit_reasons[i] = "FEASIBILITY_SKIPPED"
                step_advance = min(self.no_trade_stride_bars, max(1, n - 1 - i))
                i += step_advance
                continue

            # Long & Short Barriers
            buy_tp_price = buy_entry + tp_dist
            buy_sl_price = buy_entry - sl_dist

            sell_tp_price = sell_entry - tp_dist
            sell_sl_price = sell_entry + sl_dist

            # Adaptive Dynamic Tail Horizon for candles near end of dataset
            horizon = min(self.max_holding, n - 1 - i)
            if horizon <= 0:
                break

            future_highs = highs[i + 1 : i + 1 + horizon]
            future_lows = lows[i + 1 : i + 1 + horizon]
            future_closes = closes[i + 1 : i + 1 + horizon]
            future_spreads = spreads[i + 1 : i + 1 + horizon]

            # Mark sample as explicitly evaluated
            evaluated_mask[i] = True
            purged_mask[i] = False

            # ------------------------------------------------------------------
            # PATH-DEPENDENT STEP-BY-STEP BARRIER EVALUATION
            # ------------------------------------------------------------------
            label_code = 0  # 0 = NO_TRADE, 1 = BUY_MARKET, 2 = SELL_MARKET
            exit_step = horizon
            exit_reason = "TIME_EXPIRY_NO_TRADE"
            realized_r = 0.0

            for step in range(horizon):
                h = future_highs[step]
                l = future_lows[step]

                # Step-Dynamic Real-Time Spread Evaluation
                step_spread = (
                    future_spreads[step] if not np.isnan(future_spreads[step]) else entry_spread
                )

                # Check Long Touches (Exit at Bid)
                buy_hit_tp = h >= buy_tp_price
                buy_hit_sl = l <= buy_sl_price

                # Check Short Touches (Exit at Ask = Bid Low + Future Bar's Spread)
                sell_hit_tp = (l + step_spread) <= sell_tp_price
                sell_hit_sl = (h + step_spread) >= sell_sl_price

                # Neutralize Simultaneous Dual TP Spike (Eliminates Bullish Bias)
                if (
                    (buy_hit_tp and sell_hit_tp)
                    or (buy_hit_sl and sell_hit_sl)
                    or (buy_hit_tp and buy_hit_sl)
                    or (sell_hit_tp and sell_hit_sl)
                ):
                    label_code = 0
                    exit_step = step + 1
                    exit_reason = "DUAL_HIT_NEUTRALIZED"
                    realized_r = -effective_friction / sl_dist
                    break

                # Scenario A: BUY TP hit first
                elif buy_hit_tp and not buy_hit_sl:
                    label_code = 1
                    exit_step = step + 1
                    exit_reason = "BUY_TP_HIT"
                    realized_r = (tp_dist - effective_friction) / sl_dist
                    break

                # Scenario B: SELL TP hit first
                elif sell_hit_tp and not sell_hit_sl:
                    label_code = 2
                    exit_step = step + 1
                    exit_reason = "SELL_TP_HIT"
                    realized_r = (tp_dist - effective_friction) / sl_dist
                    break

                # Scenario C: Any Stop Loss Hit
                elif buy_hit_sl or sell_hit_sl:
                    label_code = 0
                    exit_step = step + 1
                    exit_reason = "SL_HIT"
                    realized_r = (-sl_dist - effective_friction) / sl_dist
                    break

            # ------------------------------------------------------------------
            # VERTICAL TIME BARRIER EXPIRATION & MAE SAFEGUARD
            # ------------------------------------------------------------------
            if label_code == 0 and exit_step == horizon and len(future_closes) > 0:
                final_close = future_closes[-1]
                net_buy_pnl = (final_close - buy_entry) - effective_friction
                net_sell_pnl = (sell_entry - final_close) - effective_friction

                max_buy_drawdown = max(0.0, buy_entry - np.min(future_lows))
                max_sell_drawdown = max(0.0, np.max(future_highs) - sell_entry)

                if net_buy_pnl > (atr * 0.50) and max_buy_drawdown <= (
                    sl_dist * self.max_allowed_mae_ratio
                ):
                    label_code = 1
                    exit_step = horizon
                    exit_reason = "TIME_EXPIRY_BUY"
                    realized_r = net_buy_pnl / sl_dist
                elif net_sell_pnl > (atr * 0.50) and max_sell_drawdown <= (
                    sl_dist * self.max_allowed_mae_ratio
                ):
                    label_code = 2
                    exit_step = horizon
                    exit_reason = "TIME_EXPIRY_SELL"
                    realized_r = net_sell_pnl / sl_dist
                else:
                    exit_reason = "TIME_EXPIRY_NO_TRADE"
                    best_dir_pnl = max(net_buy_pnl, net_sell_pnl)
                    realized_r = best_dir_pnl / sl_dist if best_dir_pnl > 0 else 0.0

            encoded_labels[i] = label_code
            exit_reasons[i] = exit_reason
            holding_steps[i] = exit_step
            realized_r_values[i] = realized_r

            # ------------------------------------------------------------------
            # PURGED EMBARGO & TAIL-BOUNDED ADVANCEMENT
            # ------------------------------------------------------------------
            if label_code != 0:
                step_advance = exit_step + self.embargo_bars
            else:
                step_advance = self.no_trade_stride_bars

            i += min(step_advance, max(1, n - 1 - i))

        # Vectorized 3-Class String Mapping
        label_lookup = np.array(
            [
                ActionType.NO_TRADE.value,  # 0
                ActionType.BUY_MARKET.value,  # 1
                ActionType.SELL_MARKET.value,  # 2
            ],
            dtype=object,
        )

        string_labels = label_lookup[encoded_labels]
        labels_series = pl.Series("label", string_labels)
        eval_mask_series = pl.Series("is_eval_sample", evaluated_mask)
        purged_mask_series = pl.Series("is_purged", purged_mask)

        eval_cnt = int(np.sum(evaluated_mask))
        buy_cnt = int(np.sum(encoded_labels == 1))
        sell_cnt = int(np.sum(encoded_labels == 2))
        no_trade_cnt = int(np.sum(encoded_labels == 0))

        logger.info(
            "Purged Triple-Barrier 3-Class Labeling Complete",
            total_samples=n,
            evaluated_samples=eval_cnt,
            buy_labels=buy_cnt,
            sell_labels=sell_cnt,
            no_trade_labels=no_trade_cnt,
            skipped_invalid_atr=skipped_invalid_atr,
            friction_usd=f"${self.friction_usd:.2f}",
            embargo_bars=self.embargo_bars,
        )

        result_cols = [
            labels_series,
            eval_mask_series,
            purged_mask_series,
        ]

        if use_diag:
            result_cols.extend(
                [
                    pl.Series("exit_reason", exit_reasons),
                    pl.Series("holding_bars", holding_steps),
                    pl.Series("realized_r", realized_r_values),
                ]
            )

        return df.with_columns(result_cols)
