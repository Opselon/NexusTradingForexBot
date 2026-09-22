"""Feature builder for the Layer-2 Position Decision Adviser.

Contract (TASK-POSA-001): every input here must be computable at decision time
from live position state — never from the future. The companion generator
(``position_replay``) writes the same columns, so the adviser trains and serves
on one schema.

Causal / future separation is EXACT here (verified against the generator at
position_replay.py:820-975):
    * CAUSAL (this file consumes these): timestamp, bar_index, direction,
      entry_price, current_price, position_age, position_age_bars,
      unrealized_pnl_gross/net, unrealized_pnl_r, current_r_gross/net,
      current_return, distance_to_stop/target (+_r), atr, spread,
      estimated_slippage, model_signal/probability/confidence, signal_age,
      market_regime.
    * FUTURE (never read; labels only): future_return, future_r_net,
      best_future_r, worst_future_r, mfe_usd, mae_usd, time_to_mfe/mae,
      continuation_value, horizon_continuation_value, close_now_net_r,
      continuation_net_r, continuation_mfe_r, continuation_mae_r,
      optimal_action, split.
"""

from __future__ import annotations

from typing import Any

import numpy as np

#: Canonical feature order. Frozen: the saved scaler and the trained linear
#: head both key off this exact sequence, so reordering breaks both silently.
ADVISER_FEATURE_ORDER: tuple[str, ...] = (
    "unrealized_pnl_r",
    "current_r_net",
    "current_return",
    "distance_to_stop_r",
    "distance_to_target_r",
    "position_age_bars",
    "atr",
    "spread",
    "estimated_slippage",
    "model_probability",
    "model_confidence",
    "signal_age",
)

ADVISER_FEATURE_DIM = len(ADVISER_FEATURE_ORDER)

#: Non-numeric columns the generator writes which carry no per-bar numeric
#: signal for the linear head; they are used for grouping/labels only.
_NON_NUMERIC = frozenset(
    {
        "timestamp",
        "position_id",
        "entry_timestamp",
        "direction",
        "model_signal",
        "market_regime",
        "primary_model_id",
        "primary_model_version",
        "primary_model_hash",
        "schema_id",
        "schema_version",
        "schema_hash",
        "feature_order_hash",
        "scaler_hash",
        "optimal_action",
        "split",
        "entry_price",
        "current_price",
        "bar_index",
        "unrealized_pnl_gross",
        "unrealized_pnl_net",
        "distance_to_stop",
        "distance_to_target",
        "trade_id",
    }
)


class AdviserFeatureError(RuntimeError):
    """Raised when the feature vector cannot be built (fail loud, never fake)."""


def causal_columns() -> tuple[str, ...]:
    """The columns this adviser is permitted to read."""
    return ADVISER_FEATURE_ORDER


def is_causal_column(name: str) -> bool:
    return name in ADVISER_FEATURE_ORDER


def assert_no_label_leakage(feature_names: list[str]) -> list[str]:
    """Fail closed if a FUTURE/label column is present in the FEATURE VECTOR.

    Takes the actual ordered feature list (what will be fed to the model), NOT
    the whole dataset frame — the generator legitimately stores the future
    columns as the LABEL, so their presence in the dataset is correct and
    required; it is their presence in the INPUT VECTOR that is a leak.

    Returns the offending names (empty = clean). The trainer calls this with
    ADVISER_FEATURE_ORDER, so a schema drift that ever routes a label into the
    inputs fails here rather than leaking silently.
    """
    _FUTURE = frozenset(
        {
            "future_return",
            "future_r_net",
            "best_future_r",
            "worst_future_r",
            "mfe_usd",
            "mae_usd",
            "time_to_mfe",
            "time_to_mae",
            "continuation_value",
            "horizon_continuation_value",
            "close_now_net_r",
            "continuation_net_r",
            "continuation_mfe_r",
            "continuation_mae_r",
            "optimal_action",
            "split",
        }
    )
    return [c for c in feature_names if c in _FUTURE]


def _col(frame: Any, name: str) -> np.ndarray:
    """Read a required column as float64; fail loud on absence or NaN/Inf."""
    if name not in frame.columns:
        raise AdviserFeatureError(
            f"adviser feature column {name!r} is missing from the position dataset"
        )
    arr = np.asarray(frame[name].to_numpy(), dtype=np.float64)
    if not np.all(np.isfinite(arr)):
        bad = int(np.sum(~np.isfinite(arr)))
        raise AdviserFeatureError(
            f"adviser feature column {name!r} has {bad} non-finite value(s); "
            "refusing to build a feature vector from dirty rows"
        )
    return arr


def build_training_matrix(frame: Any) -> tuple[np.ndarray, list[str]]:
    """Build the (N, ADVISER_FEATURE_DIM) matrix from a position dataset frame.

    Reads ONLY ``ADVISER_FEATURE_ORDER`` columns, so the future/label columns
    the generator stores stay untouched. Fails closed on any missing column or
    non-finite value. (The leakage guard itself is applied by the caller over
    the FEATURE VECTOR via ``assert_no_label_leakage``.)
    """
    cols = [_col(frame, name) for name in ADVISER_FEATURE_ORDER]
    mat = np.stack(cols, axis=1).astype(np.float32)
    return mat, list(ADVISER_FEATURE_ORDER)


def build_live_vector(position_state: dict[str, Any]) -> tuple[np.ndarray, list[str]]:
    """Build ONE (ADVISER_FEATURE_DIM,) vector from live position state.

    ``position_state`` is the dict the decide system already computes per open
    ticket (R-multiple, age, ATR, spread, signal confidence). Every key is
    optional in the sense that a MISSING key fails loud rather than defaulting
    to a fabricated value — a live feature vector that silently substitutes 0
    for a real signal is worse than no adviser at all.
    """
    vals: list[float] = []
    for name in ADVISER_FEATURE_ORDER:
        if name not in position_state:
            raise AdviserFeatureError(
                f"live adviser input is missing required key {name!r}; refusing to fabricate it"
            )
        raw = position_state[name]
        try:
            v = float(raw)
        except (TypeError, ValueError) as exc:
            raise AdviserFeatureError(
                f"live adviser input {name!r} is not numeric: {raw!r}"
            ) from exc
        if not np.isfinite(v):
            raise AdviserFeatureError(f"live adviser input {name!r} is non-finite ({v}); refused")
        vals.append(v)
    vec = np.asarray(vals, dtype=np.float32)
    return vec, list(ADVISER_FEATURE_ORDER)


__all__ = [
    "ADVISER_FEATURE_DIM",
    "ADVISER_FEATURE_ORDER",
    "AdviserFeatureError",
    "assert_no_label_leakage",
    "build_live_vector",
    "build_training_matrix",
    "causal_columns",
    "is_causal_column",
]
