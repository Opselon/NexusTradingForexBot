"""Part 3 of shadow/replay.py — outcome walking + promotion verdict."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from nexus_scalp.shadow._replay_pair import classify_pair
from nexus_scalp.shadow.replay import (
    BAR_MODE_SYNTHETIC_SPREAD_USD,
    MATERIAL_DELTA_R,
    MIN_RESOLVED_PAIRS,
    VERDICT_INSUFFICIENT,
    VERDICT_REJECTED,
    VERDICT_SUPPORTED,
)


def walk_pair_outcomes(
    pair: dict[str, Any],
    tick_path: list[tuple[Any, float, float]],
    decision_ts: Any,
    horizon_minutes: int,
) -> dict[str, Any]:
    """Resolves ONE pair's outcome from the shared replay market path.

    The tick path is the engine's own bar-mode execution convention
    (bid=close, ask=close+BAR_MODE_SYNTHETIC_SPREAD_USD) — applied
    IDENTICALLY to both sides, which is exactly the paired-comparison
    requirement (same market path, same costs, same assumptions).
    Delegates to the certified shadow.outcomes.resolve_paired.
    """
    from datetime import datetime

    from nexus_scalp.shadow.outcomes import PairedTick, resolve_paired

    ticks = [PairedTick(timestamp=t, bid=b, ask=a) for (t, b, a) in tick_path]
    ts = (
        decision_ts
        if isinstance(decision_ts, datetime)
        else datetime.fromisoformat(str(decision_ts))
    )
    outcome = resolve_paired(
        champion_action=pair["champ_action"],
        champion_entry=pair["champ_geometry"].entry,
        champion_sl=pair["champ_geometry"].sl,
        champion_tp=pair["champ_geometry"].tp,
        shadow_action=pair["chal_action"],
        shadow_entry=pair["chal_geometry"].entry,
        shadow_sl=pair["chal_geometry"].sl,
        shadow_tp=pair["chal_geometry"].tp,
        ticks=ticks,
        decision_ts=ts,
        horizon_minutes=horizon_minutes,
    )
    c, s = outcome.champion, outcome.shadow
    both = c.r is not None and s.r is not None
    return {
        "resolved": bool(both),
        "outcome_status": ("RESOLVED" if both else "NOT_RECORDED"),
        "champ_r": c.r,
        "chal_r": s.r,
        "delta_r": outcome.delta_r,
        "champ_exit_reason": c.exit_reason,
        "chal_exit_reason": s.exit_reason,
        "champ_ticks_seen": c.ticks_seen,
        "champ_mae_r": c.mae_r,
        "champ_mfe_r": c.mfe_r,
        "champ_direction": c.direction,
        "chal_direction": s.direction,
    }


def build_replay_evidence(
    *,
    run_champion: Any,
    run_challenger: Any,
    bar_records: list[dict[str, Any]],
    dataset_id: str,
    horizon_minutes: int = 120,
    min_resolved_pairs: int = MIN_RESOLVED_PAIRS,
    extra_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Joins two replay traces into the promotion-readiness evidence dict.

    `run_champion` / `run_challenger` are research StreamingReplayEngine
    ReplayRunResult objects (used read-only). `bar_records` are the SAME
    raw bar records both engines consumed (fingerprinted here).
    """
    from nexus_scalp.shadow.replay import dataset_fingerprint

    champ_trace = list(run_champion.decision_trace)
    chal_trace = list(run_challenger.decision_trace)
    n = min(len(champ_trace), len(chal_trace))

    # Shared market path in the engine's bar-mode convention.
    tick_path: list[tuple[Any, float, float]] = []
    for r in bar_records:
        ts = r.get("timestamp")
        close = float(r.get("close", 0.0))
        tick_path.append((ts, close, close + BAR_MODE_SYNTHETIC_SPREAD_USD))

    resolved: list[float] = []  # delta_r series
    champ_rs: list[float] = []
    chal_rs: list[float] = []
    champ_maes: list[float] = []
    invalid = 0
    unresolved = 0
    by_regime: dict[str, list[float]] = {}
    by_session: dict[str, list[float]] = {}
    by_policy_class: Counter[str] = Counter()
    by_model_class: Counter[str] = Counter()
    conf_deltas: list[float] = []
    argmax_disagreements = 0
    policy_disagreements = 0
    rows: list[dict[str, Any]] = []

    from datetime import datetime as _dt

    for i in range(n):
        c_row, l_row = champ_trace[i], chal_trace[i]
        try:
            ts_dt = _dt.fromisoformat(str(c_row.get("ts")))
        except (TypeError, ValueError):
            ts_dt = None
        c_row = dict(c_row)
        c_row["ts_dt"] = ts_dt
        pair = classify_pair(c_row, l_row)
        if not pair.get("valid"):
            invalid += 1
            rows.append(
                {"index": i, "valid": False, **{k: v for k, v in pair.items() if k != "valid"}}
            )
            continue
        by_policy_class[pair["policy_class"]] += 1
        by_model_class[pair["disagreement_model"]] += 1
        if pair["champ_argmax"] != pair["chal_argmax"]:
            argmax_disagreements += 1
        if pair["policy_class"] != "AGREEMENT":
            policy_disagreements += 1
        conf_deltas.append(pair["confidence_delta"])
        out = walk_pair_outcomes(pair, tick_path, c_row["ts_dt"], horizon_minutes)
        if not out["resolved"]:
            unresolved += 1
            rows.append({"index": i, "valid": True, **pair, **out})
            continue
        d = out["delta_r"]
        resolved.append(d)
        champ_rs.append(out["champ_r"])
        chal_rs.append(out["chal_r"])
        if out["champ_mae_r"] is not None:
            champ_maes.append(out["champ_mae_r"])
        regime = pair["regime"]
        sess = pair["session"]
        by_regime.setdefault(regime, []).append(d)
        by_session.setdefault(sess, []).append(d)
        rows.append({"index": i, "valid": True, **pair, **out})

    def _mean(xs: list[float]) -> float:
        return statistics.fmean(xs) if xs else 0.0

    def _median(xs: list[float]) -> float:
        return statistics.median(xs) if xs else 0.0

    def _bucket(b: dict[str, list[float]]) -> dict[str, dict[str, float]]:
        return {
            k: {
                "n": len(v),
                "mean_delta_r": round(_mean(v), 6),
                "median_delta_r": round(_median(v), 6),
            }
            for k, v in sorted(b.items())
        }

    mean_d = _mean(resolved)
    evidence: dict[str, Any] = {
        "schema": "SHADOW_REPLAY_EVIDENCE v1",
        "dataset_fingerprint": dataset_fingerprint(bar_records, dataset_id),
        "dataset_id": dataset_id,
        "dataset_bars": len(bar_records),
        "identity": extra_identity or {},
        "pairs_total": n,
        "pairs_invalid": invalid,
        "pairs_unresolved": unresolved,
        "pairs_resolved": len(resolved),
        "model_level": {
            "argmax_agreement": n - argmax_disagreements,
            "argmax_disagreement": argmax_disagreements,
            "by_disagreement_class": dict(by_model_class),
            "mean_confidence_delta": round(_mean(conf_deltas), 6),
        },
        "policy_level": {
            "agreement": by_policy_class.get("AGREEMENT", 0),
            "action_disagreement": by_policy_class.get("ACTION_DISAGREEMENT", 0),
            "direction_disagreement": by_policy_class.get("DIRECTION_DISAGREEMENT", 0),
        },
        "paired_outcomes": {
            "mean_champion_r": round(_mean(champ_rs), 6),
            "mean_challenger_r": round(_mean(chal_rs), 6),
            "mean_delta_r": round(mean_d, 6),
            "median_delta_r": round(_median(resolved), 6),
            "mean_champion_mae_r": round(_mean(champ_maes), 6),
            "delta_r_positive": sum(1 for d in resolved if d > 0),
            "delta_r_negative": sum(1 for d in resolved if d < 0),
            "delta_r_zero": sum(1 for d in resolved if d == 0),
        },
        "regime_breakdown": _bucket(by_regime),
        "session_breakdown": _bucket(by_session),
        "per_pair_rows": rows,
    }
    evidence["promotion_readiness"] = promotion_verdict(evidence, min_resolved_pairs)
    return evidence


def promotion_verdict(evidence: dict[str, Any], min_resolved_pairs: int) -> dict[str, Any]:
    """Grades the evidence — NEVER promotes (steer §9: no superiority from
    insufficient samples; OOS/walk-forward gates remain the promotion path)."""
    resolved = int(evidence["pairs_resolved"])
    if resolved < min_resolved_pairs:
        return {
            "verdict": VERDICT_INSUFFICIENT,
            "resolved_pairs": resolved,
            "required_pairs": min_resolved_pairs,
            "reasons": [f"resolved pairs {resolved} < required {min_resolved_pairs}"],
        }
    mean_d = float(evidence["paired_outcomes"]["mean_delta_r"])
    med_d = float(evidence["paired_outcomes"]["median_delta_r"])
    reasons: list[str] = []
    if mean_d > 0 and med_d >= 0:
        reasons.append(
            f"positive paired delta (mean {mean_d:.4f}R, median {med_d:.4f}R) over {resolved} pairs"
        )
        return {
            "verdict": VERDICT_SUPPORTED,
            "resolved_pairs": resolved,
            "required_pairs": min_resolved_pairs,
            "reasons": reasons,
        }
    if mean_d < -MATERIAL_DELTA_R:
        reasons.append(f"material negative paired delta mean {mean_d:.4f}R")
        return {
            "verdict": VERDICT_REJECTED,
            "resolved_pairs": resolved,
            "required_pairs": min_resolved_pairs,
            "reasons": reasons,
        }
    reasons.append(f"paired delta within noise band (mean {mean_d:.4f}R, median {med_d:.4f}R)")
    return {
        "verdict": VERDICT_INSUFFICIENT,
        "resolved_pairs": resolved,
        "required_pairs": min_resolved_pairs,
        "reasons": reasons,
    }
