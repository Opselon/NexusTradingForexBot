"""
Ranking & Score Engine
======================
STRATEGY FACTORY (2026-08-20).

Multi-dimensional ranking (spec 21 / 22 / 53). The authoritative per-strategy
validation score comes from `research/scoring.py` (compute_strategy_score,
with hard gates OOS/robustness/sample). This module adds the FACTORY-level
ranking layer:

  * selection score — combines the research score with robustness, OOS
    degradation, walk-forward consistency, complexity penalty and sample-size
    confidence (spec 22 concept).
  * ranking dimensions — OVERALL / OOS / ROBUSTNESS / RISK_ADJUSTED /
    CONSISTENCY / REGIME / LOW_DRAWDOWN / HIGH_EXPECTANCY / DIVERSITY.
  * explainable rank: for every rank position the component scores are
    exposed so "why did this strategy rank #7?" is answerable (spec 22).

Score components are stored individually; weights are explicit constants
(documented, configurable via the orchestrator).
"""

from __future__ import annotations

import math
from typing import Any

from nexus_scalp.strategies.factory.models import (
    RankDimension,
    StrategyFamily,
)

#: Documented selection weights (spec 22 / 107). Each is a bounded [0,1]
#: contribution to the selection score.
WEIGHTS: dict[str, float] = {
    "research_score": 0.35,    # validated deterministic research score
    "oos": 0.15,               # OOS quality (pass + low degradation)
    "robustness": 0.15,        # robustness pass + low max degradation
    "consistency": 0.10,       # walk-forward pass fraction
    "complexity": 0.10,        # 1.0 = simple, 0.0 = at budget
    "sample": 0.05,            # sample-size confidence
    "regime": 0.05,            # regime coverage
    "drawdown": 0.05,          # 1.0 = low drawdown
}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _true(flag: Any) -> float:
    return 1.0 if flag else 0.0


def strategy_error(entry: dict[str, Any]) -> str:
    """One-line error context for a strategy entry (used in explainability)."""
    score = entry.get("score") or {}
    if isinstance(score, dict):
        return str(score.get("error", ""))
    return ""


def score_components(
    entry: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Computes the decomposable selection-score components for a registry row.

    `entry` is a strategy_registry-style dict (backtest/walkforward/oos/
    robustness/score as dicts or None). All component values are bounded
    [0,1] and individually inspectable.
    """
    import json as _json

    def _decoded(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        text_val = str(value).strip()
        if text_val == "" or text_val.lower() in ("null", "none", "{}"):
            return {}
        try:
            parsed = _json.loads(text_val)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    score = _decoded(entry.get("score"))
    bt = _decoded(entry.get("backtest"))
    wf = _decoded(entry.get("walkforward"))
    oos = _decoded(entry.get("oos"))
    rob = _decoded(entry.get("robustness"))

    research = _clamp(float(score.get("final_score", 0.0) or 0.0))

    # OOS quality: pass flag * (1 - degradation) * expectancy bump.
    oos_ok = 1.0 if oos.get("status") == "PASS" else 0.0
    oos_degradation = _clamp(abs(float(oos.get("_degradation", 0.0) or 0.0)))
    oos_exp = _clamp(0.5 + float(oos.get("oos_expectancy_r", 0.0) or 0.0))
    oos_score = oos_ok * oos_exp * (1.0 - oos_degradation)

    # Robustness quality.
    rob_ok = 1.0 if rob.get("status") == "PASS" else 0.0
    rob_deg = _clamp(float(rob.get("max_degradation", 0.0) or 0.0) / 0.5)
    rob_score = rob_ok * (1.0 - rob_deg)

    # Walk-forward consistency: pass fraction across folds.
    folds = wf.get("folds") or []
    if folds:
        pass_fraction = sum(1 for f in folds if f.get("status") == "PASS") / len(folds)
    else:
        pass_fraction = 0.0
    consistency = _clamp(pass_fraction)

    # Complexity penalty: near the budget => low complexity score.
    n_conditions = 0
    raw_ctx = entry.get("context_definition") or {}
    n_conditions = _count_conditions(raw_ctx)
    budget = 9
    complexity = _clamp(1.0 - (n_conditions / max(1, budget)))

    # Sample-size confidence from the research score's sample_confidence.
    sample = _clamp(float(score.get("sample_confidence", 0.0) or 0.0))

    regime = _clamp(float(score.get("regime_coverage", 0.0) or 0.0))

    dd = float(bt.get("max_drawdown_r", 0.0) or 0.0)
    drawdown = _clamp(1.0 - (dd / 8.0))

    return {
        "research_score": round(research, 4),
        "oos": round(oos_score, 4),
        "robustness": round(rob_score, 4),
        "consistency": round(consistency, 4),
        "complexity": round(complexity, 4),
        "sample": round(sample, 4),
        "regime": round(regime, 4),
        "drawdown": round(drawdown, 4),
    }


def _count_conditions(obj: Any) -> int:
    total = 0
    if isinstance(obj, dict):
        if any(k in obj for k in ("op", "logic", "confirmation", "require")):
            total += 1
        for v in obj.values():
            total += _count_conditions(v)
    elif isinstance(obj, list):
        for item in obj:
            total += _count_conditions(item)
    return total


def selection_score(
    entry: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Weighted selection score (spec 22). Returns components + total."""
    comps = score_components(entry, weights)
    w = weights or WEIGHTS
    total = sum(comps[k] * w.get(k, 0.0) for k in comps)
    return {**comps, "total": round(_clamp(total), 4)}


def explain_rank(entry: dict[str, Any], position: int) -> dict[str, Any]:
    """Explainable rank context: why is this strategy at `position`?"""
    comps = selection_score(entry)
    score = entry.get("score") or {}
    verdict = score.get("verdict", "UNKNOWN")
    oos_status = (entry.get("oos") or {}).get("status", "NONE")
    reasons = score.get("reasons") or []
    return {
        "position": position,
        "strategy_id": entry.get("strategy_id", ""),
        "total": comps["total"],
        "components": {k: v for k, v in comps.items() if k != "total"},
        "verdict": verdict,
        "oos_status": oos_status,
        "score_reasons": reasons[:10],
    }


# ---------------------------------------------------------------------------
# Per-dimension scores (spec 53)
# ---------------------------------------------------------------------------


def dimension_score(entry: dict[str, Any], dimension: RankDimension) -> float:
    comps = score_components(entry)
    bt = entry.get("backtest") or {}
    entry.get("oos") or {}
    entry.get("score") or {}
    if dimension == RankDimension.OVERALL:
        return comps["total"] if "total" in comps else selection_score(entry)["total"]
    if dimension == RankDimension.OOS:
        return comps["oos"]
    if dimension == RankDimension.ROBUSTNESS:
        return comps["robustness"]
    if dimension == RankDimension.RISK_ADJUSTED:
        return _clamp(comps["research_score"] * (0.6 + 0.4 * comps["drawdown"]))
    if dimension == RankDimension.CONSISTENCY:
        return comps["consistency"]
    if dimension == RankDimension.REGIME:
        return comps["regime"]
    if dimension == RankDimension.LOW_DRAWDOWN:
        return comps["drawdown"]
    if dimension == RankDimension.HIGH_EXPECTANCY:
        exp = float(bt.get("expectancy_r", 0.0) or 0.0)
        return _clamp(0.5 + exp)
    if dimension == RankDimension.DIVERSITY:
        # Family diversity bonus: hybrid/novel families get a small edge.
        fam = str(entry.get("family", ""))
        novelty = 0.0
        if fam in (StrategyFamily.HYBRID.value, "LIQUIDITY_SWEEP", "VOLATILITY_EXPANSION"):
            novelty = 0.1
        return _clamp(0.5 + novelty)
    return comps["total"]


def rank_strategies(
    entries: list[dict[str, Any]],
    dimension: RankDimension = RankDimension.OVERALL,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Ranks registry-style entries by a dimension; returns annotated rows.

    Rows are copied + annotated with `_rank`, `_dimension_score`, `_components`
    and `_explain` so the UI can answer "why this rank?" without recomputation.
    """
    scored: list[tuple[float, dict[str, Any]]] = []
    for e in entries:
        d = dimension_score(e, dimension)
        comps = selection_score(e)
        ann = dict(e)
        ann["_dimension_score"] = round(d, 4)
        ann["_components"] = {k: v for k, v in comps.items() if k != "total"}
        scored.append((d, ann))
    scored.sort(key=lambda t: t[0], reverse=True)
    out: list[dict[str, Any]] = []
    for i, (_d, ann) in enumerate(scored[:limit], start=1):
        ann["_rank"] = i
        out.append(ann)
    return out


# ---------------------------------------------------------------------------
# Diversity metrics (spec 25 / 36 / 57)
# ---------------------------------------------------------------------------


def family_diversity(entries: list[dict[str, Any]]) -> float:
    """Shannon-style normalized family diversity in [0,1].

    Handles both registry rows (family nested in context_definition) and
    factory candidate rows (top-level family).
    """
    if not entries:
        return 0.0
    counts: dict[str, int] = {}
    for e in entries:
        fam = str(e.get("family", "") or (e.get("context_definition") or {}).get("family", "") or "HYBRID")
        counts[fam] = counts.get(fam, 0) + 1
    n = len(entries)
    distinct = len(counts)
    if distinct <= 1:
        return 0.0
    entropy = 0.0
    for c in counts.values():
        p = c / n
        entropy -= p * math.log(p) if p > 0 else 0.0
    return _clamp(entropy / math.log(distinct))


def feature_diversity(entries: list[dict[str, Any]]) -> float:
    """Fraction of distinct features used across the population."""
    if not entries:
        return 0.0
    all_features: set[str] = set()
    for e in entries:
        ctx = e.get("context_definition") or {}
        if not isinstance(ctx, dict):
            try:
                import json as _json

                ctx = _json.loads(str(ctx))
            except Exception:
                ctx = {}
        filters = ctx.get("filters") if isinstance(ctx, dict) else None
        if not isinstance(filters, list):
            dsl = e.get("dsl") or {}
            if isinstance(dsl, str):
                try:
                    import json as _json

                    dsl = _json.loads(str(dsl))
                except Exception:
                    dsl = {}
            filters = dsl.get("filters") if isinstance(dsl, dict) else None
        if isinstance(filters, list):
            for f in filters:
                if isinstance(f, dict) and f.get("feature"):
                    all_features.add(str(f["feature"]))
    universe = 70  # canonical 70D contract
    return _clamp(len(all_features) / universe)


def population_diversity(entries: list[dict[str, Any]]) -> float:
    """Composite diversity: family + feature diversity averaged."""
    fam = family_diversity(entries)
    feat = feature_diversity(entries)
    return round((fam + feat) / 2.0, 4)


__all__ = [
    "WEIGHTS",
    "RankDimension",
    "dimension_score",
    "explain_rank",
    "family_diversity",
    "feature_diversity",
    "population_diversity",
    "rank_strategies",
    "score_components",
    "selection_score",
]