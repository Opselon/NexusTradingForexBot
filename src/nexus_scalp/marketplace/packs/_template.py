"""Pack module template — every pack is a DETERMINISTIC generator.

Contract (ARCH_SPEC §2): generate(count=25, version="1.0.0") -> list[SeedSpec];
same inputs => same seeds. DSLs are built from dsl.py templates + catalog ids
ONLY and re-validated structurally before packaging.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedPackage, SeedSpec
from nexus_scalp.strategies.factory.dsl import (
    DEFAULT_SYMBOLS,
    SUPPORTED_TIMEFRAMES,
    _family_hypothesis,
    feature_ids,
)
from nexus_scalp.strategies.factory.models import StrategyDsl, StrategyFamily
from nexus_scalp.strategies.factory.validators import (
    validate_causality,
    validate_complexity,
    validate_features,
    validate_schema,
)

DEFAULT_COUNT = 25

# Family-role wiring used across packs: each pack maps its concept variants to
# concrete (family, entry logic, confirmations, filters, exit) drawn ONLY from
# the approved factory templates / catalog.


def build_dsl(
    family: StrategyFamily,
    *,
    logic: str,
    confirmations: list[str],
    filters: list[dict[str, Any]],
    exit_spec: dict[str, Any],
    context: dict[str, Any] | None = None,
    timeframe: str = "M15",
    extra_constraints: dict[str, Any] | None = None,
) -> StrategyDsl:
    hypothesis = _family_hypothesis(family)
    tf = timeframe if timeframe in SUPPORTED_TIMEFRAMES else "M15"
    constraints = {"max_conditions": 6, "no_future_data": True}
    if extra_constraints:
        constraints.update(extra_constraints)
    return StrategyDsl(
        schema_version="1.0",
        hypothesis=hypothesis,
        family=family,
        market={"symbols": list(DEFAULT_SYMBOLS), "timeframes": [tf]},
        context=context or {},
        setup={"structure": {"use": True}},
        entry={"logic": logic, "confirmation": list(confirmations)},
        filters=list(filters),
        exit=dict(exit_spec),
        risk={"risk_governance": "global_risk_authority", "max_risk_per_trade_pct": 0.5},
        constraints=constraints,
    )


def validate_dsl(dsl: StrategyDsl) -> bool:
    from nexus_scalp.strategies.factory.dsl import dsl_hash
    from nexus_scalp.strategies.factory.models import (
        CandidateSource,
        EvolutionOperator,
        FactoryCandidate,
    )

    cand = FactoryCandidate(
        candidate_id=f"CHK-{dsl_hash(dsl)[:10]}",
        definition_hash=dsl_hash(dsl),
        generation_id="PACK-CHK",
        source=CandidateSource.TEMPLATE,
        operator=EvolutionOperator.NONE,
        dsl=dsl,
        family=dsl.family,
    )
    verdict_ok = all(
        v.passed
        for v in (
            validate_schema(cand.dsl),
            validate_features(cand.dsl),
            validate_causality(cand.dsl),
            validate_complexity(
                cand.dsl, {"max_conditions": 9, "max_features": 6, "max_timeframes": 2}
            ),
        )
    )
    return verdict_ok


def make_package(
    pack_id: str,
    name: str,
    family: str,
    description: str,
    seeds: list[SeedSpec],
    version: str = "1.0.0",
) -> SeedPackage:
    return SeedPackage(
        pack_id=pack_id,
        name=name,
        family=family,
        description=description,
        version=version,
        seeds=tuple(seeds),
    )


def _features() -> list[str]:
    return feature_ids()


__all__ = [
    "DEFAULT_COUNT",
    "StrategyDsl",
    "StrategyFamily",
    "build_dsl",
    "make_package",
    "validate_dsl",
]
