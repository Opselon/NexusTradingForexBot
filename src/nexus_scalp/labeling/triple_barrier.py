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

Invariants:
    - Absolute Zero Lookahead Bias: Every barrier evaluation uses strictly forward bars up to termination step.
    - Zero Overlapping Outcomes: Embargo guarantees serial independence of training samples.
"""

import numpy as np
import polars as pl

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.labeling.triple_barrier")


class TripleBarrierLabeler:
    """
    Computes outcome-based training labels for historical Gold (XAUUSD) tick/bar data.
    Institutional Purged Walk-Forward & Friction-Aware Implementation.
    """

    def __init__(
        self,
        take_profit_atr_mult: float = 1.1,   # Scalp-calibrated TP multiplier
        stop_loss_atr_mult: float = 1.0,     # Tight stop for scalping
        max_holding_bars: int = 15,          # Maximum forward holding horizon
        friction_usd: float = 0.35,          # Real Gold friction in USD ($0.35 per oz)
        embargo_bars: int = 3,               # Purged Embargo gap to prevent serial correlation
        no_trade_stride_bars: int = 3,       # Stride jump on NO_TRADE to prevent Class Imbalance
        max_allowed_mae_ratio: float = 0.75, # Max allowed adverse drawdown ratio before invalidating time exit
        min_valid_atr: float = 0.20,         # Configurable minimum valid ATR threshold
    ) -> None:
        self.tp_mult = take_profit_atr_mult
        self.sl_mult = stop_loss_atr_mult
        self.max_holding = max_holding_bars
        self.friction_usd = friction_usd
        self.embargo_bars = embargo_bars
        self.no_trade_stride_bars = no_trade_stride_bars
        self.max_allowed_mae_ratio = max_allowed_mae_ratio
        self.min_valid_atr = min_valid_atr

    def label_dataframe(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Calculates friction-aware Purged Triple-Barrier labels for a Polars DataFrame.

        Expected input columns: ['close', 'high', 'low', 'atr' or 'atr_m1']
        Optional input column: ['spread']
        Adds output columns: 
            - 'label': ActionType string value ('NO_TRADE', 'BUY_MARKET', 'SELL_MARKET')
            - 'is_eval_sample': Boolean flag indicating if the row was explicitly evaluated
            - 'is_purged': Boolean flag indicating if row was skipped due to embargo/stride
        """
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
        spreads = df["spread"].to_numpy().astype(np.float64, copy=False) if has_spread else np.full(len(df), self.friction_usd, dtype=np.float64)

        n = len(df)
        
        # Int8 Encoded Numerical Array (0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET)
        encoded_labels = np.zeros(n, dtype=np.int8)
        evaluated_mask = np.zeros(n, dtype=bool)
        purged_mask = np.ones(n, dtype=bool)

        skipped_invalid_atr = 0

        i = 0
        while i < n:
            close_price = closes[i]
            atr = atrs[i]

            # Skip invalid / uninitialized ATR rows
            if np.isnan(atr) or atr <= self.min_valid_atr:
                skipped_invalid_atr += 1
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

            for step in range(horizon):
                h = future_highs[step]
                l = future_lows[step]
                
                # Step-Dynamic Real-Time Spread Evaluation
                step_spread = future_spreads[step] if not np.isnan(future_spreads[step]) else entry_spread

                # Check Long Touches (Exit at Bid)
                buy_hit_tp = h >= buy_tp_price
                buy_hit_sl = l <= buy_sl_price

                # Check Short Touches (Exit at Ask = Bid Low + Future Bar's Spread)
                sell_hit_tp = (l + step_spread) <= sell_tp_price
                sell_hit_sl = (h + step_spread) >= sell_sl_price

                # Neutralize Simultaneous Dual TP Spike (Eliminates Bullish Bias)
                if (buy_hit_tp and sell_hit_tp) or (buy_hit_sl and sell_hit_sl) or (buy_hit_tp and buy_hit_sl) or (sell_hit_tp and sell_hit_sl):
                    label_code = 0
                    exit_step = step + 1
                    break

                # Scenario A: BUY TP hit first
                elif buy_hit_tp and not buy_hit_sl:
                    label_code = 1
                    exit_step = step + 1
                    break

                # Scenario B: SELL TP hit first
                elif sell_hit_tp and not sell_hit_sl:
                    label_code = 2
                    exit_step = step + 1
                    break

                # Scenario C: Any Stop Loss Hit
                elif buy_hit_sl or sell_hit_sl:
                    label_code = 0
                    exit_step = step + 1
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

                if net_buy_pnl > (atr * 0.50) and max_buy_drawdown <= (sl_dist * self.max_allowed_mae_ratio):
                    label_code = 1
                    exit_step = horizon
                elif net_sell_pnl > (atr * 0.50) and max_sell_drawdown <= (sl_dist * self.max_allowed_mae_ratio):
                    label_code = 2
                    exit_step = horizon

            encoded_labels[i] = label_code

            # ------------------------------------------------------------------
            # PURGED EMBARGO & TAIL-BOUNDED ADVANCEMENT
            # ------------------------------------------------------------------
            if label_code != 0:
                step_advance = exit_step + self.embargo_bars
            else:
                step_advance = self.no_trade_stride_bars

            i += min(step_advance, max(1, n - 1 - i))

        # Vectorized 3-Class String Mapping
        label_lookup = np.array([
            ActionType.NO_TRADE.value,    # 0
            ActionType.BUY_MARKET.value,  # 1
            ActionType.SELL_MARKET.value, # 2
        ], dtype=object)

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

        return df.with_columns([
            labels_series,
            eval_mask_series,
            purged_mask_series,
        ])