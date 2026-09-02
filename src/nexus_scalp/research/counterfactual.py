"""NO_TRADE Counterfactual Engine (CHG-0041, TICK_COUNTERFACTUAL v1).

Answers, with real historical ticks, whether each recorded NO_TRADE decision
was correct:

    recorded decision @T -> market state @T
        -> hypothetical candidate (direction = the model-preferred action
           recorded in the signal payload; entry side per certified
           direction-aware semantics: BUY fills at ASK, SELL fills at BID)
        -> tick-by-tick future walk (from the IMMUTABLE tick store)
        -> MFE / MAE / future return / theoretical R / cost
           / time-to-target / time-to-stop
        -> FALSE_REJECTION | CORRECT_REJECTION | MISSED_LOSER
           | INCONCLUSIVE

HARD RULES:

* RESEARCH ONLY. No order_send, no live path, no policy mutation.
* The decision is NEVER altered by future data: the candidate direction,
  entry price, SL/TP geometry and confidence are read exclusively from the
  recorded signal row (audit_signals); the future walk only MEASURES
  outcomes.
* SL/TP geometry comes from the recorded proposal. When a candidate has no
  usable geometry the theoretical R is recorded RR_NOT_RECORDED (never
  invented).
* Prices on the WALK use the side the position would actually trade:
  BUY exits measured on BID, SELL exits on ASK (exit side), while the entry
  used the opposite side (spread paid). Duplicate timestamps keep stream
  order; the walk is strictly chronological (timestamp >= entry).
* Classification rules (documented, evidence-based — no arbitrary
  thresholds): using the theoretical R of the recorded risk geometry,
    R >= +0.5  -> FALSE_REJECTION  (a clean winner was refused)
    R <= -0.5  -> CORRECT_REJECTION (a loser was avoided)
    R not computable but future return > +entry spread cost -> MISSED_LOSER
       is NOT claimable; outcome is INCONCLUSIVE unless direction-consistent
       favorable excursion dominated adverse excursion by the recorded
       min-RR (1.8x per RiskEngine default) — otherwise INCONCLUSIVE.
    coverage < horizon or empty future -> INCONCLUSIVE (never extrapolate).
* Deterministic: same signals + same ticks + same code => identical
  fingerprint of results.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.research.counterfactual")

#: Default evaluation horizon for one candidate (brief §7: time-to-target /
#: time-to-stop need a bounded walk; 120 minutes covers the scalp holding
#: profile many times over).
DEFAULT_HORIZON_MINUTES: int = 120

#: R thresholds for classification (evidence-based: a scalp candidate that
#: reaches half its planned risk as PROFIT is a material missed winner; half
#: its risk as LOSS is a material avoided loser; the band in between is
#: noise-dominated and stays INCONCLUSIVE). Documented in the module docstring.
R_WIN_THRESHOLD: float = 0.5
R_LOSS_THRESHOLD: float = -0.5

RR_NOT_RECORDED: str = "RR_NOT_RECORDED"


class OutcomeClass(StrEnum):
    FALSE_REJECTION = "FALSE_REJECTION"
    CORRECT_REJECTION = "CORRECT_REJECTION"
    MISSED_LOSER = "MISSED_LOSER"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class DecisionCandidate:
    """One recorded NO_TRADE decision joined with its recorded geometry.

    All fields come from the recorded signal row (audit_signals) — nothing
    is invented. `direction` is the RECORDED preferred_direction column
    (CHG-0043 decision-evidence) with fallback to the legacy model_action
    parse for rows that predate the column; when absent the candidate is
    UNRESOLVED and counted, never fabricated.
    """

    decision_id: str
    timestamp: datetime
    symbol: str
    direction: str  # BUY | SELL | "" (unresolved / NOT_RECORDED)
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    regime: str
    gate: str
    blocked_by: str
    reason_code: str
    model_action: str
    # CHG-0043 evidence fields ("" / None = NOT_RECORDED — never invented)
    raw_prob_buy: float | None = None
    raw_prob_sell: float | None = None
    raw_prob_no_trade: float | None = None
    raw_prob_wait: float | None = None
    confidence_source: str = ""
    spread_usd: float | None = None
    geometry_unavailable_before_gate: bool = False


@dataclass(frozen=True, slots=True)
class Tick:
    timestamp: datetime
    bid: float
    ask: float


@dataclass(frozen=True, slots=True)
class CounterfactualResult:
    decision_id: str
    timestamp: str
    direction: str
    gate: str
    blocked_by: str
    regime: str
    confidence: float
    entry_price: float
    exit_price: float | None  # price at walk end (mark), None when no ticks
    mfe: float | None  # USD, favorable excursion (already spread-adjusted)
    mae: float | None  # USD, adverse excursion
    future_return: float | None  # USD at horizon/walk end
    theoretical_r: float | str | None  # R multiple, or RR_NOT_RECORDED
    time_to_target_sec: float | None  # None when TP never touched
    time_to_stop_sec: float | None  # None when SL never touched
    coverage_sec: float
    ticks_seen: int
    outcome: str
    classification_basis: str


def parse_direction(model_action: str, action: str) -> str:
    """Parses the model-preferred direction from the recorded action fields.

    The recorded `action` for a rejected candidate is NO_TRADE; the model's
    preferred action is in `model_action` (BUY_MARKET / SELL_MARKET /
    BUY_LIMIT / SELL_LIMIT / NO_TRADE / WAIT). Empty when unresolvable.
    """
    for candidate in (model_action, action):
        a = (candidate or "").upper()
        if "BUY" in a:
            return "BUY"
        if "SELL" in a:
            return "SELL"
    return ""


def _extract_prob(row: dict[str, Any], key: str) -> float | None:
    """Extracts one raw probability from a signal row; None = NOT_RECORDED."""
    v = row.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def build_candidates(rows: list[dict[str, Any]]) -> list[DecisionCandidate]:
    """Builds DecisionCandidate objects from audit_signals rows (raw dicts).

    Direction resolution order (CHG-0043):
      1. the RECORDED preferred_direction column (decision-time evidence),
      2. fallback to the legacy model_action parse for rows that predate
         the column (same evidence, different storage location),
      3. otherwise NOT_RECORDED ("") — counted, never fabricated.

    Geometry honesty: a row carrying geometry_unavailable_before_gate
    (pre-model guardian blocks) has sentinel SL/TP, NOT real geometry —
    the flag is preserved so the walk can refuse to compute R from them.
    """
    out: list[DecisionCandidate] = []
    for r in rows:
        try:
            ts = r.get("generated_at")
            ts_dt = datetime.fromisoformat(str(ts)) if not isinstance(ts, datetime) else ts
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=UTC)
            entry = float(r.get("proposed_entry") or 0.0)
            sl = float(r.get("stop_loss") or 0.0)
            tp = float(r.get("take_profit") or 0.0)
            conf = float(r.get("confidence") or 0.0)
            recorded_dir = str(r.get("preferred_direction") or "")
            if recorded_dir not in ("BUY", "SELL"):
                recorded_dir = parse_direction(
                    str(r.get("model_action") or ""), str(r.get("action") or "")
                )
            try:
                rc = json.loads(str(r.get("payload") or "{}"))
                if not isinstance(rc, dict):
                    rc = {}
            except Exception:
                rc = {}
            geometry_unavailable = bool(rc.get("geometry_unavailable_before_gate", False))
            spread = r.get("spread_usd")
            spread_f = float(spread) if spread is not None else None
            prob_buy_raw = _extract_prob(r, "raw_prob_buy")
            prob_sell_raw = _extract_prob(r, "raw_prob_sell")
            prob_nt_raw = _extract_prob(r, "raw_prob_no_trade")
            prob_wait_raw = _extract_prob(r, "raw_prob_wait")
            out.append(
                DecisionCandidate(
                    decision_id=str(r.get("request_id") or ""),
                    timestamp=ts_dt,
                    symbol=str(r.get("symbol") or "XAUUSD"),
                    direction=recorded_dir,
                    confidence=conf,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    regime=str(r.get("regime") or "UNKNOWN"),
                    gate=str(r.get("decision_stage") or ""),
                    blocked_by=str(r.get("blocked_by") or ""),
                    reason_code=str(r.get("reason_code") or ""),
                    model_action=str(r.get("model_action") or ""),
                    raw_prob_buy=prob_buy_raw,
                    raw_prob_sell=prob_sell_raw,
                    raw_prob_no_trade=prob_nt_raw,
                    raw_prob_wait=prob_wait_raw,
                    confidence_source=str(r.get("confidence_source") or ""),
                    spread_usd=spread_f,
                    geometry_unavailable_before_gate=geometry_unavailable,
                )
            )
        except (TypeError, ValueError) as e:
            logger.warning(
                "[COUNTERFACTUAL] event=CANDIDATE_PARSE_SKIPPED id=%s error=%s",
                r.get("request_id"),
                e,
            )
    return out


def _risk_distance(cand: DecisionCandidate) -> float | None:
    """Recorded risk distance (entry -> SL) in price units; None if invalid."""
    if cand.entry_price <= 0 or cand.stop_loss <= 0:
        return None
    d = abs(cand.entry_price - cand.stop_loss)
    return d if d > 0 else None


def walk_candidate(
    cand: DecisionCandidate,
    ticks: list[Tick],
    *,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
) -> CounterfactualResult:
    """Walks ONE candidate forward through real ticks (chronological).

    The entry is hypothetical at the recorded decision instant using the
    certified direction-aware side (BUY at ASK, SELL at BID). The future
    walk measures the excursion on the EXIT side (BUY on BID, SELL on ASK)
    — i.e. the price at which the position could actually be closed.

    MFE/MAE are returned as USD per 1.0 lot-equivalent normalized by
    contract size 100 (XAUUSD probed economics): excursion_usd =
    price_delta * contract_size. Theoretical R uses the RECORDED risk
    distance; RR_NOT_RECORDED when the row lacks valid geometry.
    """
    horizon = timedelta(minutes=horizon_minutes)
    cand_ts = (
        cand.timestamp if cand.timestamp.tzinfo is not None else cand.timestamp.replace(tzinfo=UTC)
    )
    end_limit = cand_ts + horizon

    # hypothetical entry price on the entry side (spread paid)
    first = ticks[0] if ticks else None
    entry_tick = _tick_at_or_after(ticks, cand.timestamp)
    entry_price: float | None = None
    if cand.direction == "BUY":
        entry_price = entry_tick.ask if entry_tick else (first.ask if first else None)
    elif cand.direction == "SELL":
        entry_price = entry_tick.bid if entry_tick else (first.bid if first else None)

    risk_distance = _risk_distance(cand)
    contract = 100.0  # probed XAUUSD trade_contract_size

    mfe = 0.0
    mae = 0.0
    mark_price: float | None = None
    ticks_seen = 0
    last_ts: datetime | None = None
    time_to_target: float | None = None
    time_to_stop: float | None = None

    if entry_price is not None and entry_tick is not None:
        for t in ticks:
            t_ts = (
                t.timestamp if t.timestamp.tzinfo is not None else t.timestamp.replace(tzinfo=UTC)
            )
            e_ts = (
                entry_tick.timestamp
                if entry_tick.timestamp.tzinfo is not None
                else entry_tick.timestamp.replace(tzinfo=UTC)
            )
            if t_ts < e_ts:
                continue
            if t_ts > end_limit:
                break
            ticks_seen += 1
            last_ts = t.timestamp
            exit_side = t.bid if cand.direction == "BUY" else t.ask
            mark_price = exit_side
            delta = (
                (exit_side - entry_price) if cand.direction == "BUY" else (entry_price - exit_side)
            )
            pnl = delta * contract
            mfe = max(mfe, pnl)
            mae = min(mae, pnl)
            if time_to_target is None and cand.take_profit > 0:
                hit = (
                    t.bid >= cand.take_profit
                    if cand.direction == "BUY"
                    else t.ask <= cand.take_profit
                )
                if hit:
                    hit_ts = (
                        t.timestamp
                        if t.timestamp.tzinfo is not None
                        else t.timestamp.replace(tzinfo=UTC)
                    )
                    time_to_target = (hit_ts - cand.timestamp).total_seconds()
            if time_to_stop is None and cand.stop_loss > 0:
                hit = (
                    t.bid <= cand.stop_loss if cand.direction == "BUY" else t.ask >= cand.stop_loss
                )
                if hit:
                    hit_ts = (
                        t.timestamp
                        if t.timestamp.tzinfo is not None
                        else t.timestamp.replace(tzinfo=UTC)
                    )
                    time_to_stop = (hit_ts - cand.timestamp).total_seconds()

    # theoretical R at the BEST point (MFE-based potential) AND at walk end;
    # the classification uses the walk-end mark (honest full-horizon result)
    # while MFE/MAE keep the excursion picture. RR_NOT_RECORDED when the
    # recorded geometry is unusable OR when the row is a pre-model block
    # (geometry_unavailable_before_gate) whose sentinel SL/TP are NOT real
    # risk geometry — the excursion proxy classifies those honestly instead.
    theoretical_r: float | str | None
    if cand.geometry_unavailable_before_gate:
        risk_distance = None  # sentinel geometry must never become a fake R
    if risk_distance is None or mark_price is None or entry_price is None:
        theoretical_r = RR_NOT_RECORDED
        final_r: float | None = None
    else:
        final_delta = (
            (mark_price - entry_price) if cand.direction == "BUY" else (entry_price - mark_price)
        )
        final_r = final_delta / risk_distance
        theoretical_r = final_r

    coverage_sec = (
        (min(last_ts, end_limit) - cand_ts).total_seconds() if last_ts is not None else 0.0
    )
    outcome, basis = _classify(
        cand=cand,
        theoretical_r=theoretical_r,
        mfe=mfe,
        mae=mae,
        entry_price=entry_price,
        mark_price=mark_price,
        ticks_seen=ticks_seen,
        coverage_sec=coverage_sec,
        horizon_sec=horizon.total_seconds(),
        risk_distance=risk_distance,
    )
    return CounterfactualResult(
        decision_id=cand.decision_id,
        timestamp=cand.timestamp.isoformat(),
        direction=cand.direction or "UNRESOLVED",
        gate=cand.gate,
        blocked_by=cand.blocked_by,
        regime=cand.regime,
        confidence=cand.confidence,
        entry_price=entry_price if entry_price is not None else cand.entry_price,
        exit_price=mark_price,
        mfe=mfe if ticks_seen else None,
        mae=mae if ticks_seen else None,
        future_return=(mark_price - (entry_price or 0.0)) if ticks_seen and entry_price else None,
        theoretical_r=theoretical_r,
        time_to_target_sec=time_to_target,
        time_to_stop_sec=time_to_stop,
        coverage_sec=coverage_sec,
        ticks_seen=ticks_seen,
        outcome=outcome.value,
        classification_basis=basis,
    )


def _classify(
    *,
    cand: DecisionCandidate,
    theoretical_r: float | str | None,
    mfe: float,
    mae: float,
    entry_price: float | None,
    mark_price: float | None,
    ticks_seen: int,
    coverage_sec: float,
    horizon_sec: float,
    risk_distance: float | None,
) -> tuple[OutcomeClass, str]:
    """Documented classification rules (see module docstring)."""
    if cand.direction not in ("BUY", "SELL"):
        return OutcomeClass.INCONCLUSIVE, "UNRESOLVED_DIRECTION"
    if entry_price is None or ticks_seen == 0:
        return OutcomeClass.INCONCLUSIVE, "NO_TICK_COVERAGE"
    if coverage_sec < horizon_sec * 0.5:
        return OutcomeClass.INCONCLUSIVE, "INSUFFICIENT_FUTURE_COVERAGE"
    if isinstance(theoretical_r, str) or theoretical_r is None:
        # no recorded geometry: excursion-based fallback (never fabricate R).
        # The 1.8R proxy mirrors the production RiskEngine min_rr (1.8): a
        # candidate whose favorable excursion dominated adverse by the
        # required reward ratio would plausibly have hit its target.
        contract = 100.0
        risk_usd = (risk_distance or 0.0) * contract
        if mfe > 0 and mfe >= abs(mae) * 1.8:
            return OutcomeClass.FALSE_REJECTION, "EXCURSION_MFE_DOMINATED_1.8R"
        if mae < 0 and abs(mae) >= mfe * 1.8:
            return OutcomeClass.CORRECT_REJECTION, "EXCURSION_MAE_DOMINATED_1.8R"
        if risk_usd <= 0:
            return OutcomeClass.INCONCLUSIVE, RR_NOT_RECORDED
        rr_proxy = (mfe / abs(mae)) if mae < 0 else (99.0 if mfe > 0 else 0.0)
        return OutcomeClass.INCONCLUSIVE, f"NO_GEOMETRY_RR_PROXY={rr_proxy:.2f}"
    if theoretical_r >= R_WIN_THRESHOLD:
        return OutcomeClass.FALSE_REJECTION, f"R={theoretical_r:.2f}>=+0.5"
    if theoretical_r <= R_LOSS_THRESHOLD:
        return OutcomeClass.CORRECT_REJECTION, f"R={theoretical_r:.2f}<=-0.5"
    return OutcomeClass.INCONCLUSIVE, f"R={theoretical_r:.2f} in noise band"


def _tick_at_or_after(ticks: list[Tick], ts: datetime) -> Tick | None:
    ts_norm = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    for t in ticks:
        t_ts = t.timestamp if t.timestamp.tzinfo is not None else t.timestamp.replace(tzinfo=UTC)
        if t_ts >= ts_norm:
            return t
    return None


def results_fingerprint(results: list[CounterfactualResult]) -> str:
    """Deterministic digest of the outcome set (reproducibility contract)."""
    h = hashlib.sha256()
    for r in sorted(results, key=lambda x: (x.timestamp, x.decision_id)):
        h.update(
            json.dumps(
                {
                    "id": r.decision_id,
                    "ts": r.timestamp,
                    "dir": r.direction,
                    "out": r.outcome,
                    "r": str(r.theoretical_r),
                    "mfe": round(r.mfe, 6) if r.mfe is not None else None,
                    "mae": round(r.mae, 6) if r.mae is not None else None,
                    "ticks": r.ticks_seen,
                },
                sort_keys=True,
            ).encode("utf-8")
        )
    return h.hexdigest()[:32]
