"""Marketplace packs — shared helpers (CHG-0056).

Deterministic SeedSpec construction over the factory DSL. Every helper draws
features ONLY from the canonical 70D catalog via dsl.py so the factory never
invents features (ARCH_SPEC §2).
"""

from __future__ import annotations

import hashlib
import random

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.strategies.factory.dsl import (
    DSL_SCHEMA_VERSION,
    RANDOM_SEED,
    StrategyDsl,
    feature_ids,
)
from nexus_scalp.strategies.factory.models import StrategyDsl as FactoryDsl  # noqa: F401


def pack_seed(pack_id: str, version: str) -> int:
    """Deterministic integer seed for a pack+version (stable across runs)."""
    h = hashlib.sha256(f"{pack_id}:{version}".encode()).hexdigest()
    return int(h[:8], 16) ^ RANDOM_SEED


def required_features_from_dsl(dsl: StrategyDsl) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for f in dsl.filters or []:
        fid = f.get("feature") if isinstance(f, dict) else None
        if isinstance(fid, str) and fid not in seen:
            seen.add(fid)
            out.append(fid)
    entry = dsl.entry or {}
    for c in entry.get("confirmation") or []:
        if isinstance(c, str) and c not in seen and c in feature_ids():
            seen.add(c)
            out.append(c)
    return out


def timeframe_scope_from_dsl(dsl: StrategyDsl) -> list[str]:
    m = dsl.market or {}
    tfs = m.get("timeframes") or []
    return [str(x) for x in tfs]


def make_seed_spec(
    pack_id: str,
    version: str,
    idx: int,
    dsl: StrategyDsl,
    *,
    name_prefix: str,
    description: str,
    author: str = "nexus-marketplace",
    risk_profile: str = "MODERATE",
    expected_regimes: list[str] | None = None,
    unsupported_regimes: list[str] | None = None,
) -> SeedSpec:
    seed_id = f"{pack_id.upper()}-{version}-{idx:04d}"
    name = f"{name_prefix} #{idx + 1:03d}"
    tfs = timeframe_scope_from_dsl(dsl)
    req = required_features_from_dsl(dsl)
    # compatibility contract: schema + dimension pinned to current canonical
    try:
        from nexus_scalp.features.schema import active_dimension as _active_dim
        from nexus_scalp.research.candidates import CANONICAL_FEATURE_SCHEMA_ID

        CANONICAL_FEATURE_DIMENSION = int(_active_dim())
    except Exception:
        CANONICAL_FEATURE_SCHEMA_ID = "scalp_v3"  # type: ignore[no-redef]
        CANONICAL_FEATURE_DIMENSION = 70  # type: ignore[no-redef]
    return SeedSpec(
        seed_id=seed_id,
        name=name,
        family=str(dsl.family.value if hasattr(dsl.family, "value") else dsl.family),
        version=version,
        author=author,
        description=description,
        source=f"pack:{pack_id}",
        license="proprietary",
        instrument_scope=list((dsl.market or {}).get("symbols") or ["XAUUSD"]),
        timeframe_scope=tfs,
        required_features=req,
        parameter_schema={},
        default_parameters={},
        risk_profile=risk_profile,
        expected_market_regimes=list(expected_regimes or []),
        unsupported_market_regimes=list(unsupported_regimes or []),
        compatibility_contract={
            "feature_schema_id": CANONICAL_FEATURE_SCHEMA_ID,
            "feature_dimension": CANONICAL_FEATURE_DIMENSION,
            "dsl_schema_version": DSL_SCHEMA_VERSION,
            "pack_id": pack_id,
        },
        dsl=dsl,
    )


def rng_for(pack_id: str, version: str, salt: int = 0) -> random.Random:
    return random.Random(pack_seed(pack_id, version) + salt)
