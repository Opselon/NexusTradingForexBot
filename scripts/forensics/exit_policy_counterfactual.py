#!/usr/bin/env python3
"""
Counterfactual exit-policy replay harness (Nexus-QA-REPLAY, wave 3).

Answers, on a *bounds* basis: "What would the closed-trade ledger have looked
like under a different exit policy?"  Replays each closed trade in
``audit_experience_outcomes`` through a parameterized exit policy (BE trigger,
BE lock pips, giveback retention floor + arming threshold, trailing-stop ATR
multiple, AI-flip on/off) and reports baseline vs. per-grid-cell stats.

=============================  HONESTY MODEL  ==============================

This is a BOUNDS model, NOT a tick replay.  ``audit_experience_outcomes``
stores per-trade aggregates (``mfe_r``, ``mae_r``, realized R) -- not tick
paths -- so every counterfactual below is an approximation.  Approximations,
exhaustive:

1. Path availability.  Only three points of each path are known: realized R,
   MFE (best R reached), MAE (worst R reached).  The intra-trade sequence
   (did MFE precede MAE?) is unknown, so a "the protective stop would have
   been hit first" scenario is NOT distinguishable.  All replayed outcomes
   are therefore clipped to [mae_r, mfe_r] -- the only honest path bound
   available -- before any other rule applies (see
   :func:`replay_trade` step 1).  Reported numbers are BOUNDS on what the
   alternative policy could have yielded, not point estimates.
2. BE-lock pips -> R conversion.  The schema has no pip denominator, so the
   lock size in pips is carried per-trade as ``lock_r`` = pips * R-per-pip
   (``--lock-pip-value-r``, default 0.1: for a 10-pip-risk scalp, 1 pip =
   0.1R, so a 0.6-pip lock retains 0.06R and a 2.0-pip lock retains 0.20R).
   Override per instrument as needed.
3. Trail cap.  A trailing stop ``k`` x ATR below price retains
   ``mfe_r - k * atr_r`` where ``atr_r`` (ATR expressed in R units) is
   unknown per trade; ``--atr-r`` (default 0.35: ATR ~ 35% of the risk
   distance for the scalp book) converts it.  Trail floor = lock floor
   (0 before BE arms), so the trail never cuts deeper than the BE lock and
   the binding constraint is max(lock_floor, giveback_floor, trail_floor)
   as coded in :func:`replay_trade`.  Wide trails therefore act as
   *ceilings on retention loss*, not as extra profit.
4. Friction.  Real replays face spread + commission + slippage.
   ``--friction-r`` (default 0.0, documented) is subtracted from every
   replayed outcome of trades whose replayed path differs from the realized
   path (a changed exit re-crosses the spread); it is NOT subtracted from
   the baseline, because realized R already embeds real frictions.
5. AI-flip off.  If the trade's exit_mechanism was ``AI_REVERSAL_EXIT``
   (the only flip-family mechanism in the ledger taxonomy; matched
   case-insensitively) and the flip is disabled in the counterfactual, the
   trade is assumed to run to its rule-based exit, approximated as
   ``min(mfe_r, 1.1R target)``.  If the flip was NOT the realized exit
   mechanism, the replay is identical for flip on/off (test-enforced).
6. Giveback retention floor: applies only when ``mfe_r >= arm_threshold``
   (the policy never protected a peak it never saw); then the replayed
   outcome cannot be worse than ``retention * mfe_r`` (bounded by the path
   clip and the other floors).
7. Baseline vs counterfactual: baseline stats come straight from the stored
   realized R (no modeling); every counterfactual cell recomputes each trade
   from the same stored aggregates.  Baseline and counterfactual are
   reported side by side, never merged.
8. Rows with NULL mfe_r / mae_r / realized R are skipped and counted
   (``rows_skipped_null_path``) -- never guessed, never imputed.

REAL MODE (``--db PATH``): opens sqlite with a read-only URI
(``file:...?mode=ro``).  If the table is missing or has 0 rows, prints an
honest ``NO_DATA`` error and exits 2.  NEVER fabricates ledger statistics,
NEVER mixes synthetic rows into real stats.

SYNTHETIC MODE (``--synthetic N``): generates N deterministic trades (fixed
seed) purely to exercise harness mechanics (grid sweep, report shape,
determinism).  Every synthetic output is stamped ``"mode": "SYNTHETIC"``
(top level and per cell) and the header line repeats the mode.  Synthetic
stats are NEVER ledger evidence.

Grid: be_trigger(5) x be_lock_pips(2) x retention(3) x arm(2) x trail(3) x
ai_flip(2) = 360 cells per run.

Usage::

    # mechanics / CI smoke (labeled SYNTHETIC):
    python scripts/forensics/exit_policy_counterfactual.py --synthetic 200

    # real ledger run (requires the prod-host audit.db export):
    python scripts/forensics/exit_policy_counterfactual.py \
        --db artifacts/audit.db --out replay.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nexus_scalp.experience.models import BREAKEVEN_R_BAND

# --------------------------------------------------------------------------
# Exit-mechanism taxonomy (mirrors execution/order_manager.py ExitMechanism
# + experience/models.py ExitReason; read as constants, not imported, so the
# forensic script stays dependency-light on the execution package).
# --------------------------------------------------------------------------
FLIP_EXIT_MECHANISM = "AI_REVERSAL_EXIT"
#: Rule-based target in R -- the approximation for "runs to its rule-based
#: exit" when the AI flip is disabled and the flip WAS the realized exit.
RULE_TARGET_R = 1.1

#: Policy grid (user brief 2026-09-09: suspected scissors in the paper book).
BE_TRIGGERS_R: tuple[float, ...] = (0.15, 0.2, 0.3, 0.4, 0.6)
BE_LOCK_PIPS: tuple[float, ...] = (0.6, 2.0)
RETENTION_FLOORS: tuple[float, ...] = (0.60, 0.70, 0.80)
ARM_THRESHOLDS_R: tuple[float, ...] = (0.5, 1.0)
TRAIL_ATR_MULTS: tuple[float, ...] = (1.15, 1.5, 2.0)
AI_FLIP_FLAGS: tuple[bool, ...] = (True, False)

TABLE = "audit_experience_outcomes"

#: Column adaptation map: PRAGMA the table at runtime, then fall back across
#: candidate names so minor schema drift does not silently break the harness.
CANDIDATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "realized_r": ("realized_r_multiple", "realized_r"),
    "mfe_r": ("mfe_r",),
    "mae_r": ("mae_r",),
    "exit_reason": ("exit_reason",),
    "exit_mechanism": ("exit_mechanism", "exit_mechanism_raw", "mechanism"),
    "entry_price": ("entry_price", "price_open", "open_price"),
    "planned_risk": ("planned_risk_usd", "risk_usd", "planned_risk", "risk_amount_usd"),
    "execution_id": ("execution_id", "ticket", "position_id"),
}


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TradeRow:
    """One closed trade with the fields the replay model consumes."""

    realized_r: float
    mfe_r: float
    mae_r: float
    exit_reason: str
    exit_mechanism: str
    entry_price: float | None = None
    planned_risk_usd: float | None = None
    execution_id: str | None = None


@dataclass(frozen=True)
class PolicyParams:
    """One grid cell of the exit policy."""

    be_trigger_r: float
    be_lock_pips: float
    retention: float
    arm_threshold_r: float
    trail_atr_mult: float
    ai_flip_enabled: bool

    def label(self) -> str:
        flip = "on" if self.ai_flip_enabled else "off"
        return (
            f"be={self.be_trigger_r:g}R lock={self.be_lock_pips:g}p "
            f"floor={self.retention:.0%}@{self.arm_threshold_r:g}R "
            f"trail={self.trail_atr_mult:g}xATR flip={flip}"
        )


@dataclass(frozen=True)
class ReplayResult:
    """Outcome of replaying one trade under one policy cell."""

    outcome_r: float
    armed_be: bool
    #: which rule produced the exit: unchanged | be_lock | giveback_floor |
    #: trail_cap | rule_target | realized (flip-on no-arming passthrough)
    binding: str


@dataclass(frozen=True)
class CellStats:
    """Aggregate stats for one policy grid cell."""

    policy: PolicyParams
    n: int
    net_r: float
    avg_r: float
    win_rate: float
    profit_factor: float
    wins: int
    losses: int
    be_armed_count: int
    floor_binding_count: int


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------
def _profit_factor(rs: Sequence[float]) -> float:
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    if gross_loss == 0.0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def compute_stats(rs: Sequence[float]) -> dict[str, Any]:
    """win = R > +BREAKEVEN_R_BAND, loss = R < -BREAKEVEN_R_BAND (mirrors
    nexus_scalp.experience.models OutcomeClass thresholds)."""
    net_r = sum(rs)
    wins = sum(1 for r in rs if r > BREAKEVEN_R_BAND)
    losses = sum(1 for r in rs if r < -BREAKEVEN_R_BAND)
    n = len(rs)
    return {
        "n": n,
        "net_r": net_r,
        "avg_r": (net_r / n) if n else 0.0,
        "win_rate": (wins / n) if n else 0.0,
        "profit_factor": _profit_factor(rs),
        "wins": wins,
        "losses": losses,
    }


# --------------------------------------------------------------------------
# Replay model
# --------------------------------------------------------------------------
def _is_flip_exit(mechanism: str) -> bool:
    return (mechanism or "").strip().upper() == FLIP_EXIT_MECHANISM


def replay_trade(
    trade: TradeRow,
    policy: PolicyParams,
    *,
    friction_r: float = 0.0,
    lock_pip_value_r: float = 0.1,
    atr_r: float = 0.35,
) -> ReplayResult:
    """Replay one trade under one policy cell (bounds model; see module
    docstring for every approximation).

    Order of operations:
      1. clip the known path: outcome starts inside [mae_r, mfe_r];
      2. if mfe_r < be_trigger -> the policy never armed: outcome unchanged
         (baseline realized R), binding="unchanged";
      3. floors: BE lock floor (lock_pips * lock_pip_value_r, once armed),
         giveback retention floor (only when mfe_r >= arm_threshold),
         trail floor (mfe_r - trail_atr_mult * atr_r, >= lock floor);
      4. flip-off on a flip-exited trade -> rule exit min(mfe_r, 1.1R);
      5. friction subtracted only when the replayed exit differs from the
         realized one; result clipped to >= -1R (a full stop-out).
    """
    mfe = trade.mfe_r

    # (1) path clip -- never claim anything outside the observed path bounds
    def _clip(x: float) -> float:
        return max(trade.mae_r, min(mfe, x))

    # (2) BE never armed -> policy inert for this trade
    if mfe < policy.be_trigger_r:
        return ReplayResult(outcome_r=trade.realized_r, armed_be=False, binding="unchanged")

    lock_r = policy.be_lock_pips * lock_pip_value_r
    floors: list[tuple[float, str]] = [(lock_r, "be_lock")]

    # (3) giveback retention floor -- only armed when the policy saw a peak
    giveback_armed = mfe >= policy.arm_threshold_r
    if giveback_armed:
        floors.append((policy.retention * mfe, "giveback_floor"))

    # trail floor: k*ATR below peak; never deeper than the BE lock floor
    floors.append((max(lock_r, mfe - policy.trail_atr_mult * atr_r), "trail_cap"))

    # (4) flip disabled + flip was the realized exit -> run to rule exit
    # (the giveback/BE/trail floors no longer apply to a trade the protective
    # machinery never exited; the rule target caps it instead)
    if not policy.ai_flip_enabled and _is_flip_exit(trade.exit_mechanism):
        outcome = _clip(min(mfe, RULE_TARGET_R))
        binding = "rule_target"
        changed = outcome != trade.realized_r
        if changed and friction_r:
            outcome -= friction_r
        outcome = max(-1.0, _clip(outcome))
        return ReplayResult(outcome_r=outcome, armed_be=True, binding=binding)

    # (5) flip-on path (or flip-off on a non-flip trade): release at the best
    # floor, capped by the peak itself (the floor is a *retention* promise).
    best_floor_value, best_floor_name = max(floors, key=lambda f: f[0])
    outcome = _clip(min(mfe, max(best_floor_value, 0.0)))
    binding = best_floor_name
    changed = abs(outcome - trade.realized_r) > 1e-12
    if changed and friction_r:
        outcome -= friction_r
    outcome = max(-1.0, _clip(outcome))
    return ReplayResult(outcome_r=outcome, armed_be=True, binding=binding)


def sweep_grid(
    trades: list[TradeRow],
    *,
    friction_r: float = 0.0,
    lock_pip_value_r: float = 0.1,
    atr_r: float = 0.35,
) -> list[CellStats]:
    """Replay every trade through every grid cell; returns cells sorted by
    net_r descending."""
    cells: list[CellStats] = []
    for be_trig, lock_pips, retention, arm, trail, flip in itertools.product(
        BE_TRIGGERS_R, BE_LOCK_PIPS, RETENTION_FLOORS, ARM_THRESHOLDS_R, TRAIL_ATR_MULTS, AI_FLIP_FLAGS
    ):
        policy = PolicyParams(
            be_trigger_r=be_trig,
            be_lock_pips=lock_pips,
            retention=retention,
            arm_threshold_r=arm,
            trail_atr_mult=trail,
            ai_flip_enabled=flip,
        )
        outcomes: list[float] = []
        armed = 0
        floor_bound = 0
        for t in trades:
            res = replay_trade(
                t,
                policy,
                friction_r=friction_r,
                lock_pip_value_r=lock_pip_value_r,
                atr_r=atr_r,
            )
            outcomes.append(res.outcome_r)
            armed += res.armed_be
            floor_bound += res.binding in ("be_lock", "giveback_floor", "trail_cap")
        stats = compute_stats(outcomes)
        cells.append(
            CellStats(
                policy=policy,
                n=stats["n"],
                net_r=stats["net_r"],
                avg_r=stats["avg_r"],
                win_rate=stats["win_rate"],
                profit_factor=stats["profit_factor"],
                wins=stats["wins"],
                losses=stats["losses"],
                be_armed_count=armed,
                floor_binding_count=floor_bound,
            )
        )
    cells.sort(key=lambda c: c.net_r, reverse=True)
    return cells


# --------------------------------------------------------------------------
# Data loading (real mode)
# --------------------------------------------------------------------------
def _adapt_columns(conn: sqlite3.Connection) -> dict[str, str]:
    """PRAGMA the table and map logical names -> physical column names."""
    pragma = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
    present = {row[1] for row in pragma}
    mapping: dict[str, str] = {}
    for logical, candidates in CANDIDATE_COLUMNS.items():
        for cand in candidates:
            if cand in present:
                mapping[logical] = cand
                break
    return mapping


def load_trades(db_path: str | Path) -> tuple[list[TradeRow], dict[str, Any]]:
    """READ-ONLY load of closed trades.  Raises SystemExit(NO_DATA) when the
    file is unreadable, the table is missing, or the table is empty --
    fail-closed, never fabricate."""
    db_file = Path(db_path)
    if not db_file.is_file():
        _no_data(db_path, "database file not found")
    uri = f"file:{db_file.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if TABLE not in tables:
            _no_data(db_path, f"table {TABLE} missing")
        mapping = _adapt_columns(conn)
        required = ("realized_r", "mfe_r", "mae_r")
        missing = [k for k in required if k not in mapping]
        if missing:
            _no_data(db_path, f"required columns absent after PRAGMA adaptation: {missing}")
        cols = mapping
        select = ", ".join(
            [
                cols["realized_r"],
                cols["mfe_r"],
                cols["mae_r"],
                cols.get("exit_reason", "''"),
                cols.get("exit_mechanism", "''"),
                cols.get("entry_price", "NULL"),
                cols.get("planned_risk", "NULL"),
                cols.get("execution_id", "NULL"),
            ]
        )
        rows = conn.execute(
            f"SELECT {select} FROM {TABLE} WHERE is_closed = 1"
        ).fetchall()
        row_count_all = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    finally:
        conn.close()

    if row_count_all == 0:
        _no_data(db_path, f"{TABLE} has 0 rows")

    trades: list[TradeRow] = []
    skipped_null = 0
    for r in rows:
        realized, mfe, mae = r[0], r[1], r[2]
        if realized is None or mfe is None or mae is None:
            skipped_null += 1
            continue
        trades.append(
            TradeRow(
                realized_r=float(realized),
                mfe_r=float(mfe),
                mae_r=float(mae),
                exit_reason=str(r[3] or ""),
                exit_mechanism=str(r[4] or ""),
                entry_price=(float(r[5]) if r[5] is not None else None),
                planned_risk_usd=(float(r[6]) if r[6] is not None else None),
                execution_id=(str(r[7]) if r[7] is not None else None),
            )
        )
    info = {
        "db_path": str(Path(db_path).resolve()),
        "rows_total": row_count_all,
        "rows_closed": len(rows),
        "rows_skipped_null_path": skipped_null,
        "column_map": mapping,
    }
    return trades, info


def _no_data(db_path: str | Path, detail: str) -> None:
    print(
        f"NO_DATA: {detail} in {db_path}; refusing to fabricate ledger stats "
        f"(honesty rule). Export the prod-host audit_experience_outcomes first.",
        file=sys.stderr,
    )
    raise SystemExit(2)


# --------------------------------------------------------------------------
# Synthetic mode (mechanics testing only -- NEVER ledger evidence)
# --------------------------------------------------------------------------
SYNTHETIC_SEED = 20260909


def generate_synthetic_trades(n: int, seed: int = SYNTHETIC_SEED) -> list[TradeRow]:
    """Deterministic synthetic trade population for harness mechanics tests.
    Distribution is chosen to resemble the suspected pathology (winners cut
    early near +0.14R, losers near -1R) WITHOUT claiming to be ledger data."""
    import random

    rng = random.Random(seed)
    trades: list[TradeRow] = []
    for i in range(n):
        # mixture: ~48% small winners cut early, ~42% full losers, ~10% runners
        u = rng.random()
        if u < 0.48:
            realized = rng.uniform(0.05, 0.20)
            mfe = realized + rng.uniform(0.02, 0.45)
        elif u < 0.90:
            realized = rng.uniform(-1.05, -0.55)
            mfe = rng.uniform(0.0, 0.35)
        else:
            realized = rng.uniform(0.4, 1.05)
            mfe = realized + rng.uniform(0.05, 0.5)
        mae = min(realized, -abs(rng.uniform(0.0, 1.0)))
        mechanisms = ["TAKE_PROFIT_HIT", "HARD_SL_HIT", "BREAK_EVEN_SL_HIT",
                      "TRAILING_STOP_HIT", "AI_REVERSAL_EXIT", "RISK_FREE_SL_HIT"]
        mech = mechanisms[i % len(mechanisms)]
        trades.append(
            TradeRow(
                realized_r=realized,
                mfe_r=mfe,
                mae_r=mae,
                exit_reason=mech,
                exit_mechanism=mech,
                entry_price=1.0 + rng.uniform(-0.01, 0.01),
                planned_risk_usd=rng.uniform(8.0, 12.0),
                execution_id=f"SYN-{seed}-{i:05d}",
            )
        )
    return trades


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def build_report(
    *,
    mode: str,
    trades: list[TradeRow],
    baseline: dict[str, Any],
    cells: list[CellStats],
    run_info: dict[str, Any],
    friction_r: float,
    lock_pip_value_r: float,
    atr_r: float,
) -> dict[str, Any]:
    cells_payload = []
    for c in cells:
        cells_payload.append(
            {
                "policy": {
                    "be_trigger_r": c.policy.be_trigger_r,
                    "be_lock_pips": c.policy.be_lock_pips,
                    "retention": c.policy.retention,
                    "arm_threshold_r": c.policy.arm_threshold_r,
                    "trail_atr_mult": c.policy.trail_atr_mult,
                    "ai_flip_enabled": c.policy.ai_flip_enabled,
                },
                "label": c.policy.label(),
                "n": c.n,
                "net_r": c.net_r,
                "avg_r": c.avg_r,
                "win_rate": c.win_rate,
                "profit_factor": c.profit_factor,
                "wins": c.wins,
                "losses": c.losses,
                "be_armed_count": c.be_armed_count,
                "floor_binding_count": c.floor_binding_count,
                "mode": mode,
            }
        )
    report: dict[str, Any] = {
        "mode": mode,
        "honesty": "BOUNDS MODEL - aggregate MFE/MAE path bounds, not a tick replay; "
        "see module docstring for the exhaustive approximation list",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "friction_r": friction_r,
        "lock_pip_value_r": lock_pip_value_r,
        "atr_r": atr_r,
        "run_info": run_info,
        "baseline": baseline,
        "grid_size": len(cells),
        "cells_sorted_by_net_r": cells_payload,
    }
    return report


def print_summary(report: dict[str, Any]) -> None:
    mode = report["mode"]
    print(f"mode={mode}  grid={report['grid_size']} cells  friction_r={report['friction_r']}")
    b = report["baseline"]
    print(
        f"baseline: n={b['n']} net_r={b['net_r']:.2f} avg_r={b['avg_r']:.3f} "
        f"win_rate={b['win_rate']:.1%} pf={b['profit_factor']:.3f}"
    )
    print("top 8 cells by net_r:")
    for c in report["cells_sorted_by_net_r"][:8]:
        print(
            f"  {c['label']}  net_r={c['net_r']:.2f} avg_r={c['avg_r']:.3f} "
            f"wr={c['win_rate']:.1%} pf={c['profit_factor']:.3f} be_armed={c['be_armed_count']}"
        )
    if mode == "SYNTHETIC":
        print("NOTE: SYNTHETIC run - mechanics test only, NOT ledger evidence.")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Counterfactual exit-policy replay harness (bounds model)."
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--db",
        help="path to audit.db (real mode; read-only). Fail-closed NO_DATA if "
        "audit_experience_outcomes is missing/empty.",
    )
    mode_group.add_argument(
        "--synthetic",
        type=int,
        metavar="N",
        help="generate N deterministic synthetic trades (mechanics testing; "
        "output stamped SYNTHETIC, never ledger evidence).",
    )
    parser.add_argument("--out", help="write the JSON report to this path")
    parser.add_argument(
        "--friction-r",
        type=float,
        default=0.0,
        help="R subtracted from each replayed outcome whose exit differs from "
        "the realized one (spread/commission/slippage proxy). Default 0.0. "
        "NOT subtracted from the baseline (realized R embeds real frictions).",
    )
    parser.add_argument(
        "--lock-pip-value-r",
        type=float,
        default=0.1,
        help="R per pip used to convert BE lock pips to R (default 0.1 => "
        "0.6-pip lock retains 0.06R).",
    )
    parser.add_argument(
        "--atr-r",
        type=float,
        default=0.35,
        help="ATR expressed in R units for the trailing-stop cap (default 0.35).",
    )
    args = parser.parse_args(argv)

    if args.friction_r < 0:
        parser.error("--friction-r must be >= 0")

    if args.synthetic is not None:
        if args.synthetic <= 0:
            parser.error("--synthetic N must be >= 1")
        mode = "SYNTHETIC"
        trades = generate_synthetic_trades(args.synthetic)
        run_info = {
            "synthetic_trades": len(trades),
            "seed": SYNTHETIC_SEED,
            "warning": "SYNTHETIC - not ledger data",
        }
    else:
        mode = "REAL"
        trades, load_info = load_trades(args.db)
        run_info = load_info
        if not trades:
            # all rows were closed=1 but null-path rows only
            _no_data(args.db, "0 usable rows after null-path filtering")

    baseline = compute_stats([t.realized_r for t in trades])
    baseline["mode"] = mode
    cells = sweep_grid(
        trades,
        friction_r=args.friction_r,
        lock_pip_value_r=args.lock_pip_value_r,
        atr_r=args.atr_r,
    )
    report = build_report(
        mode=mode,
        trades=trades,
        baseline=baseline,
        cells=cells,
        run_info=run_info,
        friction_r=args.friction_r,
        lock_pip_value_r=args.lock_pip_value_r,
        atr_r=args.atr_r,
    )
    print_summary(report)
    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"report written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
