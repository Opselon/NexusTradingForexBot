"""Shadow Paired Outcome Resolver (CHG-0046 / SHADOW_EVIDENCE v2, D3).

Closes the outcome layer of the Shadow system WITHOUT inventing markets:

    paired decision record (champion action/geometry, shadow action/geometry)
        + certified historical ticks (research.mt5_tick_dataset surface)
        -> walk EACH side forward over the SAME market path
        -> champion_R / shadow_R / Delta_R (PAIRED — same timestamps, same
           costs, same execution assumptions, side-aware fills)
        -> written back as RESOLVED outcome fields; anything not computable
           stays NOT_RECORDED (never fabricated).

Reuses the certified semantics of research.counterfactual.walk_candidate
(TICK_COUNTERFACTUAL v1, CHG-0041): BUY fills at ASK and exits on BID, SELL
fills at BID and exits on ASK (spread paid), strictly chronological walk,
coverage-limited (never extrapolate past the tick data), R from the RECORDED
risk geometry only.

RESEARCH ONLY: no order authority, no live-path call. The resolver runs
offline / in background workers over persisted shadow decisions.

Side semantics (CRITICAL correctness rule):
    * a side with NO directional action (NO_TRADE/WAIT) has R = 0.0 —
      flat is flat, not the mirror of the other side's loss. The previous
      champion-R derivation (`-hypothetical_r` on any action mismatch)
      fabricated losses for flat champions and is retired.
    * both sides directional: each walked on its own geometry.
    * geometry unusable -> that side's R = None (NOT_RECORDED), never 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.compat import direction_of

logger = get_logger("nexus_scalp.shadow.outcomes")

#: Default bounded evaluation horizon (matches the certified counterfactual
#: default: 120 minutes covers the scalp holding profile many times over).
DEFAULT_HORIZON_MINUTES: int = 120

#: Outcome resolution status vocabulary (SHADOW_EVIDENCE v2).
STATUS_PENDING = "PENDING"
STATUS_RESOLVED = "RESOLVED"
STATUS_NOT_RECORDED = "NOT_RECORDED"

NOT_RECORDED = "NOT_RECORDED"


@dataclass(frozen=True, slots=True)
class SideGeometry:
    """One side's recorded decision + risk geometry (never invented)."""

    action: str  # canonical action (BUY/SELL/NO_TRADE/WAIT)
    entry: float
    sl: float
    tp: float


@dataclass(frozen=True, slots=True)
class PairedTick:
    """One market observation (bid/ask at a timestamp) from the tick store."""

    timestamp: datetime
    bid: float
    ask: float


@dataclass(frozen=True, slots=True)
class SideOutcome:
    """Outcome of ONE side over the shared market path."""

    direction: str  # BUY | SELL | NONE
    entry_price: float | None
    exit_price: float | None
    r: float | None  # None = NOT_RECORDED (flat sides resolve to 0.0)
    mfe_r: float | None
    mae_r: float | None
    pnl_usd: float | None
    holding_sec: float
    exit_reason: str  # TARGET_HIT / STOP_HIT / HORIZON / NO_TRADE / NOT_RECORDED
    ticks_seen: int


@dataclass(frozen=True, slots=True)
class PairedOutcome:
    """Paired Champion-vs-Shadow outcome on the SAME market path."""

    champion: SideOutcome
    shadow: SideOutcome
    delta_r: float | None  # shadow_R - champion_R (None when either side NR)


def _side_outcome(
    side: SideGeometry,
    ticks: Sequence[PairedTick],
    decision_ts: datetime,
    horizon_minutes: int,
    contract_size: float = 100.0,
) -> SideOutcome:
    """Walks ONE side over the shared tick path (certified semantics).

    ``r`` is the WALK-END mark R — the honest full-horizon result, exactly
    like TICK_COUNTERFACTUAL v1 (the walk never stops at a barrier; a
    barrier touch is RECORDED as time-to-target/stop and surfaced in
    ``exit_reason`` but does not truncate the evaluation window). MFE/MAE
    keep the excursion picture.
    """
    direction = direction_of(side.action)
    if direction == "NONE":
        # A flat side is flat: R = 0.0 by construction, no walk needed.
        return SideOutcome(
            direction="NONE",
            entry_price=None,
            exit_price=None,
            r=0.0,
            mfe_r=0.0,
            mae_r=0.0,
            pnl_usd=0.0,
            holding_sec=0.0,
            exit_reason="NO_TRADE",
            ticks_seen=0,
        )

    # Entry on the OPPOSITE side of the quote (spread paid), exit on the
    # own side — exactly the certified counterfactual fill semantics.
    entry_price: float | None = None
    entry_ts: datetime | None = None
    for t in ticks:
        if t.timestamp >= decision_ts:
            entry_price = t.ask if direction == "BUY" else t.bid
            entry_ts = t.timestamp
            break
    if entry_price is None or entry_ts is None:
        return SideOutcome(
            direction=direction,
            entry_price=None,
            exit_price=None,
            r=None,
            mfe_r=None,
            mae_r=None,
            pnl_usd=None,
            holding_sec=0.0,
            exit_reason=NOT_RECORDED,
            ticks_seen=0,
        )

    risk_distance: float | None = None
    if side.entry > 0 and side.sl > 0:
        rd = abs(side.entry - side.sl)
        if rd > 1e-9:
            risk_distance = rd

    end_limit = decision_ts + timedelta(minutes=horizon_minutes)
    mfe_usd = 0.0
    mae_usd = 0.0
    mark: float | None = None
    last_ts: datetime | None = None
    time_to_target: float | None = None
    time_to_stop: float | None = None
    ticks_seen = 0

    for t in ticks:
        if t.timestamp < entry_ts:
            continue
        if t.timestamp > end_limit:
            break
        ticks_seen += 1
        last_ts = t.timestamp
        exit_side = t.bid if direction == "BUY" else t.ask
        mark = exit_side
        delta = (exit_side - entry_price) if direction == "BUY" else (entry_price - exit_side)
        pnl = delta * contract_size
        mfe_usd = max(mfe_usd, pnl)
        mae_usd = min(mae_usd, pnl)
        if time_to_target is None and side.tp > 0:
            hit = t.bid >= side.tp if direction == "BUY" else t.ask <= side.tp
            if hit:
                time_to_target = (t.timestamp - decision_ts).total_seconds()
        if time_to_stop is None and side.sl > 0:
            hit = t.bid <= side.sl if direction == "BUY" else t.ask >= side.sl
            if hit:
                time_to_stop = (t.timestamp - decision_ts).total_seconds()

    if mark is None or ticks_seen == 0:
        return SideOutcome(
            direction=direction,
            entry_price=entry_price,
            exit_price=None,
            r=None,
            mfe_r=None,
            mae_r=None,
            pnl_usd=None,
            holding_sec=0.0,
            exit_reason=NOT_RECORDED,
            ticks_seen=0,
        )

    r: float | None
    if risk_distance is None:
        r = None  # unusable geometry — never fabricate R
    else:
        final_delta = (mark - entry_price) if direction == "BUY" else (entry_price - mark)
        r = final_delta / risk_distance

    pnl_total = ((mark - entry_price) if direction == "BUY" else (entry_price - mark)) * (
        contract_size
    )
    mfe_r_val = (mfe_usd / (risk_distance * contract_size)) if risk_distance else None
    mae_r_val = (mae_usd / (risk_distance * contract_size)) if risk_distance else None

    if time_to_target is not None and (time_to_stop is None or time_to_target <= time_to_stop):
        exit_reason = "TARGET_HIT"
    elif time_to_stop is not None:
        exit_reason = "STOP_HIT"
    else:
        exit_reason = "HORIZON"

    holding = (last_ts - decision_ts).total_seconds() if last_ts else 0.0
    return SideOutcome(
        direction=direction,
        entry_price=entry_price,
        exit_price=mark,
        r=r,
        mfe_r=mfe_r_val,
        mae_r=mae_r_val,
        pnl_usd=pnl_total,
        holding_sec=holding,
        exit_reason=exit_reason,
        ticks_seen=ticks_seen,
    )


def resolve_paired(
    *,
    champion_action: str,
    champion_entry: float,
    champion_sl: float,
    champion_tp: float,
    shadow_action: str,
    shadow_entry: float,
    shadow_sl: float,
    shadow_tp: float,
    ticks: Sequence[PairedTick],
    decision_ts: datetime,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
) -> PairedOutcome:
    """Resolves BOTH sides on the SAME market path (pure, deterministic)."""
    champ = _side_outcome(
        SideGeometry(champion_action, champion_entry, champion_sl, champion_tp),
        ticks,
        decision_ts,
        horizon_minutes,
    )
    shad = _side_outcome(
        SideGeometry(shadow_action, shadow_entry, shadow_sl, shadow_tp),
        ticks,
        decision_ts,
        horizon_minutes,
    )
    delta = (shad.r - champ.r) if (shad.r is not None and champ.r is not None) else None
    return PairedOutcome(champion=champ, shadow=shad, delta_r=delta)


def apply_to_record_fields(outcome: PairedOutcome) -> dict[str, Any]:
    """Flattens a PairedOutcome into ShadowDecisionRecord update fields.

    The champion side maps onto the record's hypothetical_* columns (the
    champion is the baseline); the shadow side maps onto shadow_* columns.
    status is RESOLVED only when BOTH sides carry a computable R; otherwise
    the untouched fields stay NOT_RECORDED.
    """
    c, s = outcome.champion, outcome.shadow
    both_resolved = c.r is not None and s.r is not None
    return {
        "hypothetical_entry": c.entry_price or 0.0,
        "hypothetical_exit": c.exit_price or 0.0,
        "hypothetical_pnl_usd": c.pnl_usd if c.pnl_usd is not None else 0.0,
        "hypothetical_r": c.r if c.r is not None else 0.0,
        "mfe_r": c.mfe_r if c.mfe_r is not None else 0.0,
        "mae_r": c.mae_r if c.mae_r is not None else 0.0,
        "holding_duration_sec": c.holding_sec,
        "exit_reason": c.exit_reason,
        "shadow_r": s.r if s.r is not None else 0.0,
        "shadow_mfe_r": s.mfe_r if s.mfe_r is not None else 0.0,
        "shadow_mae_r": s.mae_r if s.mae_r is not None else 0.0,
        "delta_r": outcome.delta_r if outcome.delta_r is not None else 0.0,
        "outcome_status": STATUS_RESOLVED if both_resolved else STATUS_NOT_RECORDED,
    }
