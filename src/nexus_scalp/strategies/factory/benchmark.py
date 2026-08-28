"""
Strategy Factory — Benchmark Engine
==================================
STRATEGY FACTORY BENCHMARK (2026-08-21).

PROBLEM (forensic 2026-08-21 07:15):
  Every factory candidate (SF-*) was grading the SAME 90-sample ledger
  slice (mean -0.079R) regardless of its DSL hypothesis. The research
  pipeline's family-select fallback requires `discovery_evidence.sample_ids`
  but `StrategyFactory._to_strategy_candidate` never populated it, so
  _select_family returned the full dataset. Result: all 200+ candidates
  produced IDENTICAL BacktestResult (expectancy -0.060669, OOS -0.140653,
  score 0.3516, WALK_FORWARD_FAILURE + OOS_FAILURE) — a systemic collapse
  the 08-21 user dump captured (40 failures in one second, two reasons per
  candidate). The benchmark was useless for AI decision-making.

FIX (this module):
  * DSL-aware sample filtering: a candidate's DSL filters (feature/op/value)
    are evaluated against each ledger sample's real 50D feature_snapshot
    vector (the same vector the live ScalpFeatureEngine produced at decision
    time). Only samples the strategy WOULD HAVE entered become its benchmark
    dataset (strategy-aware replay, not ledger-average grading).
  * Walk-forward / OOS / robustness remain the authoritative research gates —
    but now each gate runs on the candidate's OWN filtered slice, so scores
    diverge and the AI can rank.
  * Benchmark payload: deterministic, explainable artifact per candidate
    (entry coverage, per-family expectancy, why OOS/WF passed or failed,
    what the hypothesis actually filtered) — the "help AI decides" surface
    the user requested.
  * Stitched to the existing ResearchPipeline: this module ONLY selects the
    dataset subset; the pipeline still computes BacktestResult/WalkForward/
    OOS/Robustness/Score immutably. Zero pipeline mutation required.

The module is pure (no DB, no MT5) and callable from orchestrator or API.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from nexus_scalp.features.schema_contract import canonical_feature_names
from nexus_scalp.strategies.factory.models import FactoryCandidate, StrategyDsl

# ---------------------------------------------------------------------------
# Feature index map (canonical 70D — base 0..49 suffices for ledger 50D rows)
# ---------------------------------------------------------------------------

_FEATURE_INDEX: dict[str, int] = {name: i for i, name in enumerate(canonical_feature_names())}


# ---------------------------------------------------------------------------
# DSL filter evaluation over one ledger feature vector
# ---------------------------------------------------------------------------

_OPS = {
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: abs(a - b) < 1e-9,
    "neq": lambda a, b: abs(a - b) >= 1e-9,
    "between": lambda a, b: (
        (b[0] <= a <= b[1]) if isinstance(b, (list, tuple)) and len(b) == 2 else False
    ),
}


def _eval_filter(flt: dict[str, Any], values: list[float]) -> bool:
    feat = str(flt.get("feature", "")).strip()
    if not feat or feat not in _FEATURE_INDEX:
        # Unknown feature: treat as pass (do not falsely exclude). The
        # validators already hard-reject unsupported features before this.
        return True
    idx = _FEATURE_INDEX[feat]
    if idx >= len(values):
        return True  # 70D filter on 50D ledger row — not applicable, pass
    op = str(flt.get("op", "gt")).lower()
    fn = _OPS.get(op)
    if fn is None:
        return True
    thresh = flt.get("value", 0.0)
    # between uses [lo, hi]
    if op == "between":
        lo = flt.get("value", [0, 0])
        hi = flt.get("value_max", None)
        if hi is not None:
            thresh = [float(lo), float(hi)]  # type: ignore[arg-type]
        return bool(fn(float(values[idx]), thresh))
    try:
        return bool(fn(float(values[idx]), float(thresh)))  # type: ignore[arg-type]
    except Exception:
        return True


def dsl_matches_snapshot(dsl: StrategyDsl, values: list[float]) -> bool:
    """True when a ledger sample's feature vector WOULD HAVE triggered the DSL.

    Semantics: ALL filters must pass (AND). An empty filters list matches
    every sample (the DSL is permissive — e.g. trend-following entry with
    only entry.confirmation features). This mirrors the discovery contract
    where families group by context fingerprint, not by DSL runtime.
    """
    filters = list(dsl.filters or [])
    if not filters:
        return True
    return all(_eval_filter(f, values) for f in filters if isinstance(f, dict))


# ---------------------------------------------------------------------------
# Dataset subset selection (pure helper for the ledger audit layer)
# ---------------------------------------------------------------------------


def benchmark_subset_for_candidate(
    candidate: FactoryCandidate,
    ledger_samples: list[dict[str, Any]],
) -> list[str]:
    """Returns the idempotency_keys the candidate would have traded.

    `ledger_samples` is a list of dicts each with
      - idempotency_key: str
      - feature_values: list[float] (50D from feature_snapshot.values)

    The contract mirrors discovery.sample_ids: a sorted list of keys.
    """
    dsl = candidate.dsl
    matched: list[str] = []
    for row in ledger_samples:
        vals = row.get("feature_values") or []
        if not isinstance(vals, list) or not vals:
            continue
        if dsl_matches_snapshot(dsl, vals):
            key = str(row.get("idempotency_key", "")).strip()
            if key:
                matched.append(key)
    matched.sort()
    return matched


def candidate_coverage_stats(
    candidate: FactoryCandidate,
    ledger_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    """Explainable coverage artifact (AI-facing)."""
    total = len(ledger_samples)
    matched_keys = set(benchmark_subset_for_candidate(candidate, ledger_samples))
    matched = len(matched_keys)
    # Per-family coverage from ledger context
    family = candidate.family.value
    dsl = candidate.dsl
    return {
        "candidate_id": candidate.candidate_id,
        "family": family,
        "dsl_filter_count": len(list(dsl.filters or [])),
        "dsl_filters": [dict(f) for f in (dsl.filters or [])][:6],
        "entry_logic": dict(dsl.entry or {}),
        "context": dict(dsl.context or {}),
        "coverage": {
            "total_ledger_samples": total,
            "matched": matched,
            "coverage_pct": round(100.0 * matched / total, 2) if total else 0.0,
            "unmatched": total - matched,
        },
        "verdict_hint": (
            "NO_DATA" if matched < 8 else ("LOW_EVIDENCE" if matched < 20 else "EVALUABLE")
        ),
    }


# ---------------------------------------------------------------------------
# Post-evaluation benchmark artifact (what the API/AI consumes)
# ---------------------------------------------------------------------------


def build_benchmark_artifact(
    candidate: FactoryCandidate,
    pipeline_result: dict[str, Any],
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic benchmark artifact stamped onto factory_runs/registry.

    Combines the authoritative research pipeline result (backtest/walkforward/
    oos/robustness/score) with the DSL coverage explainability so an LLM or
    dashboard can decide WITHOUT re-running the pipeline.
    """
    bt = pipeline_result.get("backtest") or {}
    wf = pipeline_result.get("walkforward") or pipeline_result.get("walk_forward") or {}
    oos = pipeline_result.get("oos") or {}
    rob = pipeline_result.get("robustness") or {}
    score = pipeline_result.get("score") or {}
    digest = hashlib.sha256(
        json.dumps(pipeline_result, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]

    wf_folds = wf.get("folds") or []
    wf_summary = None
    if isinstance(wf_folds, list) and wf_folds:
        passes = sum(1 for f in wf_folds if isinstance(f, dict) and f.get("status") == "PASS")
        wf_summary = {
            "folds": len(wf_folds),
            "passes": passes,
            "pass_rate": round(passes / len(wf_folds), 3) if wf_folds else 0.0,
            "avg_val_expectancy_r": wf.get("avg_val_expectancy_r"),
            "avg_oos_expectancy_r": wf.get("avg_oos_expectancy_r"),
            "degradation": wf.get("degradation"),
            "passed": bool(wf.get("passed")),
        }

    oos_explain = None
    if oos:
        oos_explain = {
            "status": oos.get("status"),
            "oos_expectancy_r": oos.get("oos_expectancy_r"),
            "in_sample_expectancy_r": oos.get("in_sample_expectancy_r"),
            "oos_samples": oos.get("oos_samples"),
            "oos_win_rate": oos.get("oos_win_rate"),
            "reason": oos.get("reason", ""),
            "degradation": (
                round(
                    (oos["in_sample_expectancy_r"] - oos["oos_expectancy_r"])
                    / abs(oos["in_sample_expectancy_r"]),
                    4,
                )
                if oos.get("in_sample_expectancy_r") and abs(oos["in_sample_expectancy_r"]) > 1e-9
                else 0.0
            ),
        }

    bt_explain = None
    if bt:
        bt_explain = {
            "total_trades": bt.get("total_trades"),
            "wins": bt.get("wins"),
            "losses": bt.get("losses"),
            "expectancy_r": bt.get("expectancy_r"),
            "expectancy_usd": bt.get("expectancy_usd"),
            "profit_factor": bt.get("profit_factor"),
            "max_drawdown_r": bt.get("max_drawdown_r"),
            "avg_win_r": bt.get("avg_win_r"),
            "avg_loss_r": bt.get("avg_loss_r"),
        }

    rob_explain = None
    if rob:
        rob_explain = {
            "status": rob.get("status"),
            "baseline_expectancy_r": rob.get("baseline_expectancy_r"),
            "max_degradation": rob.get("max_degradation"),
            "reason": rob.get("reason", ""),
        }

    lifecycle = pipeline_result.get("lifecycle", "UNKNOWN")
    verified = score.get("verdict") == "VALIDATED"

    # Decision label (AI-facing, derived, not authoritative)
    decision = "REJECTED"
    if verified and oos.get("status") == "PASS" and wf.get("passed"):
        decision = "CANDIDATE_ELITE"
    elif lifecycle == "REJECTED":
        decision = "REJECTED"
    elif score.get("verdict") == "INCONCLUSIVE":
        decision = "INCONCLUSIVE_NEEDS_MORE_DATA"

    return {
        "benchmark_id": f"bm_{digest}",
        "candidate_id": candidate.candidate_id,
        "definition_hash": candidate.definition_hash,
        "generation_id": candidate.generation_id,
        "family": candidate.family.value,
        "source": candidate.source.value,
        "lifecycle": lifecycle,
        "decision": decision,
        "eligible_for_next_gen": verified,
        "coverage": coverage or {},
        "backtest": bt_explain,
        "walk_forward": wf_summary,
        "oos": oos_explain,
        "robustness": rob_explain,
        "score": {
            "final_score": score.get("final_score"),
            "verdict": score.get("verdict"),
            "reasons": score.get("reasons") or [],
            "dimensions": {
                k: score.get(k)
                for k in (
                    "performance_score",
                    "risk_score",
                    "stability_score",
                    "oos_score",
                    "robustness_score",
                    "sample_confidence",
                    "regime_coverage",
                    "recency_score",
                    "execution_resilience",
                    "degradation_score",
                )
                if k in score
            },
        },
        "failure_reasons": pipeline_result.get("failure_reasons")
        or candidate.model_dump().get("failure_reasons")
        or [],
        "primary_failure": (
            "OOS"
            if oos.get("status") != "PASS"
            else ("WALK_FORWARD" if not wf.get("passed") else None)
        ),
    }


def behavioral_preview_signature(
    candidate: FactoryCandidate,
    ledger_samples: list[dict[str, Any]],
) -> str:
    """Semantic (behavioral) fingerprint of a candidate, computable BEFORE the
    expensive research pipeline runs.

    Two candidates that trade the SAME experience subset under the SAME
    structural contract are behavioral clones: the deterministic backtest is a
    pure function of the sample partition (ids/versions are labels only), so
    identical ``(family, timeframes, sorted-sample-subset-hash)`` => identical
    backtest/WF/OOS/robustness. This is exactly the equivalence the DSL-level
    ``dsl_hash`` dedup CANNOT see (different filters can select the same
    samples — the 345-cluster pathology).

    The signature is content-addressed and deterministic, so a known
    pathological cluster (e.g. the 345-clone cluster) maps to ONE stable key
    that is then comparable against persisted cluster evidence (member count +
    OOS-pass count) built from `strategy_registry`.

    Determinism note: folds/versions are NOT included, by design — a clone must
    match regardless of the generation it was produced in. The sample subset is
    hashed via its SORTED idempotency_keys, so order-independent.
    """
    dsl = candidate.dsl
    family = candidate.family.value
    timeframes = ",".join(sorted(str(t) for t in (dsl.market or {}).get("timeframes") or []))
    sample_ids = benchmark_subset_for_candidate(candidate, ledger_samples)
    subset_hash = hashlib.sha256(("|".join(sample_ids)).encode("utf-8")).hexdigest()[:24]
    raw = f"{family}|{timeframes}|{subset_hash}|{len(sample_ids)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "behavioral_preview_signature",
    "benchmark_subset_for_candidate",
    "build_benchmark_artifact",
    "candidate_coverage_stats",
    "dsl_matches_snapshot",
]
