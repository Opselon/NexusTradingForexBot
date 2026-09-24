"""Provider adapters (ECOSYSTEM-001, Sections 3, 4, 5, 6).

Every provider is one adapter behind the canonical
:class:`~nexus_scalp.ai_providers.adapters.base.BaseAIProviderAdapter` interface.
Adding a provider means adding a module here and one entry in the registry --
never a change to the decision pipeline (Section 2).

    internal_nse_ml  -> the existing Layer-2 position adviser (Section 6)
    system_one       -> the operator's System One endpoint (Section 4)
    openrouter       -> OpenRouter chat completions (Section 5)
"""

from __future__ import annotations

from nexus_scalp.ai_providers.adapters.base import (
    AIProviderHealth,
    AIProviderTestResult,
    BaseAIProviderAdapter,
)
from nexus_scalp.ai_providers.adapters.internal_ml import InternalNSEMLAdapter
from nexus_scalp.ai_providers.adapters.openrouter import OpenRouterAdapter
from nexus_scalp.ai_providers.adapters.system_one import SystemOneAdapter

#: Registry id -> adapter class. This mapping IS the provider catalogue: the
#: orchestrator builds an adapter from here and never constructs one directly.
ADAPTER_TYPES: dict[str, type[BaseAIProviderAdapter]] = {
    InternalNSEMLAdapter.provider_id: InternalNSEMLAdapter,  # type: ignore[type-abstract]
    SystemOneAdapter.provider_id: SystemOneAdapter,
    OpenRouterAdapter.provider_id: OpenRouterAdapter,
}

__all__ = [
    "ADAPTER_TYPES",
    "AIProviderHealth",
    "AIProviderTestResult",
    "BaseAIProviderAdapter",
    "InternalNSEMLAdapter",
    "OpenRouterAdapter",
    "SystemOneAdapter",
]
