"""
Marketplace repair — mutation-based repair lifecycle over EXISTING
factory evolution operators (CHG-0056, ARCH_SPEC §2).

  trigger -> new candidate version (parent preserved, parent_ids set)
  -> independent validate_candidate through the EXISTING pipeline
  -> comparison vs parent (OOS+score; strictly-better)
  -> promotion ONLY if strictly better, never auto-live.

Repair events are recorded as mk_repairs rows (append-only semantics).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.marketplace.models import SeedSpec


def new_version_for_repair(base_version: str, trigger: str) -> str:
    """Deterministic repair suffix: 1.0.0 + trigger -> 1.0.0-repair-<slug>n."""
    slug = (
        "".join(c if c.isalnum() else "-" for c in (trigger or "repair")).strip("-").lower()[:20]
        or "repair"
    )
    # bump suffix only: keep base semver semantics source-of-truth
    return f"{base_version}-repair-{slug}"


def mutated_seed_from_trigger(
    seed: SeedSpec,
    trigger: str,
) -> SeedSpec | None:
    """Creates a repaired child SeedSpec via EXISTING evolution operators.

    Imports mutate/explore/signals only to avoid top-level side-effects in tests.
    Returns None on operator failure (caller records a FAILED mk_repairs row).
    """
    try:
        import random

        from nexus_scalp.strategies.factory.dsl import dsl_hash, feature_ids
        from nexus_scalp.strategies.factory.evolution import mutate
        from nexus_scalp.strategies.factory.models import (
            CandidateSource,
            FactoryCandidate,
            StrategyDsl,
        )

        pool = feature_ids()
        # minimal factory candidate wrapper around the DSL — needed by mutate()
        digest = dsl_hash(seed.dsl)
        cand = FactoryCandidate(
            candidate_id=seed.seed_id,
            definition_hash=digest,
            generation_id=f"REPAIR-{seed.seed_id}",
            source=CandidateSource.REPAIR,
            dsl=seed.dsl,
            family=seed.dsl.family,  # type: ignore[arg-type]
        )
        rng = random.Random(sum(ord(c) for c in trigger) + 271)
        actions = (
            "change_threshold",
            "replace_indicator",
            "change_condition",
            "add_filter",
            "simplify",
        )
        # try actions until one produces a mutated child
        mutated = None
        for a in actions:
            mutated = mutate(cand, rng=rng, feature_pool=pool, action=a)
            if mutated is not None:
                break
        if mutated is None:
            return None
        new_version = new_version_for_repair(seed.version, trigger)
        seed_id = f"{seed.seed_id}-REPAIR-{new_version.replace('.', '-').replace('-', '-')}"
        # Use deterministic but stable repair seed id: reuse base + trigger slug
        seed_id = f"{seed.seed_id}__repair__{slug_for(trigger)}"
        child_dsl: StrategyDsl = mutated.dsl  # type: ignore[assignment]
        # Preserve packaging metadata; version + parent pointer is the lineage
        return SeedSpec(
            seed_id=seed_id,
            name=f"{seed.name} [repair: {trigger[:40]}]",
            family=str(
                child_dsl.family.value if hasattr(child_dsl.family, "value") else child_dsl.family
            ),
            version=new_version,
            author=seed.author,
            description=f"Repair of {seed.seed_id} v{seed.version} triggered by: {trigger}",
            source=seed.source,
            license=seed.license,
            instrument_scope=list(seed.instrument_scope),
            timeframe_scope=list(seed.timeframe_scope),
            required_features=list(seed.required_features),
            parameter_schema=dict(seed.parameter_schema),
            default_parameters=dict(seed.default_parameters),
            risk_profile=seed.risk_profile,
            expected_market_regimes=list(seed.expected_market_regimes),
            unsupported_market_regimes=list(seed.unsupported_market_regimes),
            compatibility_contract=dict(seed.compatibility_contract),
            dsl=child_dsl,
        )
    except Exception:
        return None


def slug_for(trigger: str) -> str:
    txt = (trigger or "repair").strip().lower()
    out = "".join(c if c.isalnum() else "-" for c in txt).strip("-")
    return out[:32] or "repair"


def repair_record_payload(
    seed_id: str, parent_seed_id: str, trigger: str, status: str = "PENDING"
) -> dict[str, Any]:
    return {
        "repair_id": "REPAIR-" + uuid.uuid4().hex[:10].upper(),
        "seed_id": seed_id,
        "parent_seed_id": parent_seed_id,
        "trigger": trigger,
        "status": status,
        "outcome": json.dumps({}),
        "created_at": datetime.now(UTC).isoformat(),
    }


def strictly_better(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    """Strict dominance: child wins BOTH OOS gate and total score."""

    def _oos_pick(d: dict[str, Any]) -> str | None:
        o = d.get("oos") if isinstance(d.get("oos"), dict) else None
        if isinstance(o, dict):
            return o.get("status")
        f = (
            d.get("factors", {}).get("oos_generalization", {})
            if isinstance(d.get("factors"), dict)
            else {}
        )
        return f.get("value")

    c_oos = _oos_pick(child) or "FAIL"
    p_oos = _oos_pick(parent) or "FAIL"
    c_pass = str(c_oos) == "PASS"
    p_pass = str(p_oos) == "PASS"
    if c_pass != p_pass:
        return c_pass and not p_pass
    # same OOS: score strictly better
    c_total = float(child.get("total") or child.get("final_score") or 0)
    p_total = float(parent.get("total") or parent.get("final_score") or 0)
    return c_total > p_total


__all__ = [
    "mutated_seed_from_trigger",
    "new_version_for_repair",
    "repair_record_payload",
    "slug_for",
    "strictly_better",
]
