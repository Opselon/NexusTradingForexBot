"""Evidence-based layer + criticality classification for NSE packages.

Classification is derived from real repository package paths (observed during
Phase 1 discovery), not from hard-coded guesswork. The maps below are
explicit and reviewable; an unknown package falls back to UNKNOWN rather than
being silently assigned a layer.
"""

from __future__ import annotations

from nexus_scalp.dependency_intelligence.models import Criticality, Layer

# Package (top-level src/nexus_scalp/<pkg>) -> architectural Layer.
PACKAGE_LAYER: dict[str, Layer] = {
    "web": Layer.PRESENTATION,
    "settings": Layer.PRESENTATION,
    "observability": Layer.INFRASTRUCTURE,
    "release": Layer.INFRASTRUCTURE,
    "hygiene": Layer.INFRASTRUCTURE,
    "database": Layer.INFRASTRUCTURE,
    "adapters": Layer.INFRASTRUCTURE,
    "application": Layer.APPLICATION,
    "domain": Layer.DOMAIN,
    "ports": Layer.PORTS,
    "risk": Layer.DOMAIN,
    "execution": Layer.APPLICATION,
    "signals": Layer.APPLICATION,
    "accounting": Layer.APPLICATION,
    "governance": Layer.APPLICATION,
    "incidents": Layer.APPLICATION,
    "market_data": Layer.APPLICATION,
    "news": Layer.APPLICATION,
    "intelligence": Layer.APPLICATION,
    "strategies": Layer.APPLICATION,
    "research": Layer.APPLICATION,
    "model_generation": Layer.APPLICATION,
    "model_lifecycle": Layer.APPLICATION,
    "mslie": Layer.APPLICATION,
    "features": Layer.APPLICATION,
    "candle_intelligence": Layer.APPLICATION,
    "labeling": Layer.APPLICATION,
    "training": Layer.APPLICATION,
    "shadow": Layer.APPLICATION,
    "forensics": Layer.TOOLING,
    "diagnostics": Layer.TOOLING,
    "cli": Layer.TOOLING,
}

# Packages that are operationally critical to the live trading engine.
CRITICAL_PACKAGES = {
    "application",  # LiveEngine composition root lives here
    "execution",
    "risk",
    "market_data",
    "strategies",
    "model_generation",
    "model_lifecycle",
    "mslie",
    "features",
    "adapters",  # MT5 / DB adapters
    "accounting",
    "web",  # API availability
    "configuration",
}

# Substring hints that raise criticality for specific modules/files.
CRITICAL_MODULE_HINTS = (
    "live_engine",
    "risk_engine",
    "order_manager",
    "execution",
    "mt5",
    "market_data",
    "liquidity",
    "model",
    "strategy",
    "server",
    "runtime_config",
)


def classify_layer(package: str, module: str) -> Layer:
    """Return the architectural layer for a package/module."""
    if package in PACKAGE_LAYER:
        return PACKAGE_LAYER[package]
    # heuristics for nested-but-unmapped presentation/runtime concerns
    low = (package + "." + module).lower()
    if "web" in low or "ui" in low:
        return Layer.PRESENTATION
    if "test" in low:
        return Layer.TEST
    return Layer.UNKNOWN


def classify_criticality(package: str, module: str) -> Criticality:
    """Return operational criticality from package/module evidence."""
    if package in CRITICAL_PACKAGES:
        return Criticality.CRITICAL
    low = (package + "." + module).lower()
    if any(h in low for h in CRITICAL_MODULE_HINTS):
        return Criticality.HIGH
    if package in {
        "news",
        "intelligence",
        "shadow",
        "incidents",
        "governance",
        "candle_intelligence",
        "labeling",
        "training",
    }:
        return Criticality.MEDIUM
    return Criticality.UNKNOWN
