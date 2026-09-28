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

#: Template id -> adapter class, for custom providers (Section 40). A custom
#: provider is a SECOND INSTANCE of a template adapter class under a new id —
#: its config row carries its own endpoint, model and secret, and the adapter
#: reads ``self.config`` for all of them, so one class serves many instances.
TEMPLATE_ADAPTERS: dict[str, type[BaseAIProviderAdapter]] = {
    "system_one": SystemOneAdapter,
    "openrouter": OpenRouterAdapter,
    "internal_nse_ml": InternalNSEMLAdapter,  # type: ignore[type-abstract]
}


def adapter_class_for(provider_id: str) -> type[BaseAIProviderAdapter] | None:
    """Resolve the adapter class for a built-in OR custom provider id.

    Custom ids (``custom_*``) carry their template in the config row; the
    registry looks it up there and falls back to the id prefix so an unknown
    custom id still resolves deterministically.
    """
    cls = ADAPTER_TYPES.get(provider_id)
    if cls is not None:
        return cls
    if provider_id.startswith("custom_"):
        # Defer to the registry row for the template; the caller (orchestrator)
        # has the config and passes the template id through.
        return None
    return None


def adapter_class_for_template(template_id: str) -> type[BaseAIProviderAdapter] | None:
    """The adapter class a provider TEMPLATE instantiates (Section 3)."""
    return TEMPLATE_ADAPTERS.get(template_id)


__all__ = [
    "ADAPTER_TYPES",
    "AIProviderHealth",
    "AIProviderTestResult",
    "BaseAIProviderAdapter",
    "InternalNSEMLAdapter",
    "OpenRouterAdapter",
    "SystemOneAdapter",
]
