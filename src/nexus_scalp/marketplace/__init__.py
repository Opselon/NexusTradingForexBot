"""Strategy Marketplace (CHG-0056) — isolated marketplace domain.

Package layout (ARCHITECTURE_SPEC §2):
- models.py     lifecycle/enablement enums + SeedSpec/SeedPackage contracts
- store.py      isolated artifacts/marketplace.db (research_store recipe)
- packs/        deterministic seed-package generators
- scoring.py    14-factor configurable fitness model (versioned profiles)
- measurement.py honest windows/regime measurement over research evidence
- repair.py     repair lifecycle over EXISTING factory evolution operators
- snapshot.py   immutable runtime-snapshot store (RuntimeConfig pattern)
- service.py    install/enable gates/research routing/query APIs
"""

from nexus_scalp.marketplace.models import (
    TRANSITIONS,
    EnablementMode,
    LifecycleTransitionError,
    MarketplaceLifecycle,
    SeedPackage,
    SeedSpec,
    can_transition,
)

__all__ = [
    "TRANSITIONS",
    "EnablementMode",
    "LifecycleTransitionError",
    "MarketplaceLifecycle",
    "SeedPackage",
    "SeedSpec",
    "can_transition",
]
