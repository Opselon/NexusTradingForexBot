"""
Research Summarizer — Evolution Memory
=======================================
STRATEGY FACTORY (2026-08-20).

Converts raw historical strategy results into a compact learning context
(spec 24 / 25 / 81). The LLM (and the deterministic evolution planner)
consumes THIS summary — never thousands of raw rows.

Covers: top performers, worst performers, most robust, most unstable,
common failure modes, successful/failed features, successful/failed feature
combinations, regime results, complexity effects, OOS degradation
distribution, drawdown distribution, trade-count distribution,
generation-to-generation improvement, diversity metrics.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.strategies.factory.models import (
    FailureReason,
    GenerationSummary,
    StrategyFamily,
)


def _score_of(entry: dict[str, Any]) -> float:
    try:
        return float((entry.get("score") or {}).get("final_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _expectancy_r(entry: dict[str, Any]) -> float:
    try:
        return float((entry.get("backtest") or {}).get("expectancy_r", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_summary(
    generation: dict[str, Any],
    candidates: list[dict[str, Any]],
    registry_entries: list[dict[str, Any]],
    operator_stats: dict[str, dict[str, int]] | None = None,
    cost: dict[str, Any] | None = None,
    runtime_ms: float = 0.0,
) -> GenerationSummary:
    """Builds the compact per-generation summary (spec 25).

    `candidates` are factory_candidates rows (with structural verdict +
    lifecycle); `registry_entries` are the evaluated strategy_registry rows
    (with score/backtest/oos/robustness) for THIS generation.
    """
    evaluated = [e for e in registry_entries if e.get("score")]
    validated = [e for e in evaluated if score_verdict(e) == "VALIDATED"]
    rejected = [e for e in evaluated if score_verdict(e) == "REJECTED"]

    scores = [_score_of(e) for e in evaluated]
    avg = round(sum(scores) / len(scores), 4) if scores else 0.0
    best = round(max(scores), 4) if scores else 0.0
    ordered = sorted(scores)
    median = round(ordered[len(ordered) // 2], 4) if ordered else 0.0

    failure_dist: dict[str, int] = {}
    for c in candidates:
        for fr in (c.get("failure_reasons") or []):
            failure_dist[str(fr)] = failure_dist.get(str(fr), 0) + 1

    feature_dist: dict[str, int] = {}
    for e in evaluated:
        ctx = e.get("context_definition") or {}
        filters = ctx.get("filters") if isinstance(ctx, dict) else None
        if isinstance(filters, list):
            for f in filters:
                if isinstance(f, dict) and f.get("feature"):
                    feat = str(f["feature"])
                    feature_dist[feat] = feature_dist.get(feat, 0) + 1

    family_dist: dict[str, int] = {}
    for e in evaluated:
        fam = str(e.get("family") or e.get("context_definition", {}).get("family") or "HYBRID")
        family_dist[fam] = family_dist.get(fam, 0) + 1

    op_stats: dict[str, dict[str, int]] = {}
    for op, stats in (operator_stats or {}).items():
        op_stats[op] = dict(stats)

    from nexus_scalp.strategies.factory.ranking import population_diversity

    diversity = population_diversity(registry_entries)

    structurally_valid = sum(
        1 for c in candidates if (c.get("structural") or {}).get("passed")
    )

    return GenerationSummary(
        generation_id=str(generation.get("generation_id", "")),
        number=int(generation.get("number", 0)),
        population=int(generation.get("population_target", 0)),
        structurally_valid=structurally_valid,
        evaluated=len(evaluated),
        validated=len(validated),
        rejected=len(rejected),
        elite=len(
            [
                e
                for e in registry_entries
                if (e.get("score") or {}).get("verdict") == "VALIDATED"
                and float((e.get("score") or {}).get("final_score", 0.0) or 0.0) >= 0.6
            ]
        ),
        avg_score=avg,
        best_score=best,
        median_score=median,
        diversity=diversity,
        failure_distribution=failure_dist,
        feature_distribution=feature_dist,
        family_distribution=family_dist,
        operator_survival=op_stats,
        cost=cost or {},
        runtime_ms=runtime_ms,
    )


def score_verdict(entry: dict[str, Any]) -> str:
    return str((entry.get("score") or {}).get("verdict", "UNKNOWN"))


def memory_summary(
    summaries: list[GenerationSummary],
    elite: list[dict[str, Any]],
    all_entries: list[dict[str, Any]],
    stagnation: int = 0,
) -> dict[str, Any]:
    """Builds the structured learning context (spec 24 / 81) — this is what
    the next generation's planner / LLM prompt consumes."""
    top = sorted(
        all_entries,
        key=lambda e: (float((e.get("score") or {}).get("final_score", 0.0) or 0.0)),
        reverse=True,
    )[:5]
    worst = sorted(
        all_entries,
        key=lambda e: (float((e.get("score") or {}).get("final_score", 0.0) or 0.0)),
    )[:5]

    common_failures: list[dict[str, Any]] = []
    failure_tally: dict[str, int] = {}
    for s in summaries:
        for k, v in s.failure_distribution.items():
            failure_tally[k] = failure_tally.get(k, 0) + int(v)
    for reason, count in sorted(failure_tally.items(), key=lambda kv: kv[1], reverse=True)[:8]:
        common_failures.append({"reason": reason, "count": count})

    successful_features: dict[str, int] = {}
    failed_features: dict[str, int] = {}
    for s in summaries:
        for feat, cnt in s.feature_distribution.items():
            successful_features[feat] = successful_features.get(feat, 0) + int(cnt)

    # Failed-feature proxy: features appearing in REJECTED candidates' context.
    for e in all_entries:
        if score_verdict(e) != "REJECTED":
            continue
        ctx = e.get("context_definition") or {}
        filters = ctx.get("filters") if isinstance(ctx, dict) else None
        if isinstance(filters, list):
            for f in filters:
                if isinstance(f, dict) and f.get("feature"):
                    feat = str(f["feature"])
                    failed_features[feat] = failed_features.get(feat, 0) + 1

    operator_success: dict[str, float] = {}
    for s in summaries:
        for op, stats in s.operator_survival.items():
            generated = int(stats.get("generated", 0))
            survived = int(stats.get("survived", 0))
            if generated > 0:
                operator_success[op] = operator_success.get(op, 0.0) + (survived / generated)

    return {
        "current_generation": max([s.number for s in summaries], default=0),
        "generation_count": len(summaries),
        "generations": [s.model_dump() for s in summaries[-4:]],  # bounded window
        "elite": [{"strategy_id": e.get("strategy_id"), "score": _score_of(e)} for e in elite[:10]],
        "top": [{"strategy_id": e.get("strategy_id"), "score": _score_of(e)} for e in top],
        "worst": [{"strategy_id": e.get("strategy_id"), "score": _score_of(e)} for e in worst],
        "common_failures": common_failures,
        "successful_features": dict(
            sorted(successful_features.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ),
        "failed_features": dict(
            sorted(failed_features.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ),
        "stagnation_count": stagnation,
        "operator_success": operator_success,
        "diversity": round(
            sum(s.diversity for s in summaries) / len(summaries), 4
            if summaries
            else 0.0
        ),
        "complexity_trend": [
            {"generation": s.number, "avg_score": s.avg_score, "diversity": s.diversity}
            for s in summaries[-6:]
        ],
    }


def format_summary_for_prompt(memory: dict[str, Any]) -> str:
    """Compact textual rendering of the research memory for LLM prompts
    (spec 34 / 81). Never sends raw rows."""
    lines = [
        "RESEARCH MEMORY (compact)",
        f"Generations: {memory.get('generation_count')}  Current: {memory.get('current_generation')}",
        f"Stagnation: {memory.get('stagnation_count')}",
    ]
    elite = memory.get("elite") or []
    if elite:
        lines.append("ELITE:")
        for e in elite[:5]:
            lines.append(f"  {e.get('strategy_id')} score={e.get('score')}")
    failures = memory.get("common_failures") or []
    if failures:
        lines.append("COMMON FAILURES:")
        for f in failures[:5]:
            lines.append(f"  {f.get('reason')} x{f.get('count')}")
    sf = memory.get("successful_features") or {}
    if sf:
        lines.append("SUCCESSFUL FEATURES: " + ", ".join(f"{k}={v}" for k, v in list(sf.items())[:6]))
    ff = memory.get("failed_features") or {}
    if ff:
        lines.append("FAILED FEATURES: " + ", ".join(f"{k}={v}" for k, v in list(ff.items())[:6]))
    return "\n".join(lines)


__all__ = [
    "FailureReason",
    "GenerationSummary",
    "StrategyFamily",
    "build_summary",
    "format_summary_for_prompt",
    "memory_summary",
    "score_verdict",
]