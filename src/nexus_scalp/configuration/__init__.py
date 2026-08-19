"""Configuration management — type-safe settings loading + runtime hot reload.

Public surface:
    RuntimeConfigStore          — authoritative versioned runtime provider
    RuntimeConfiguration        — immutable runtime snapshot (atomic swap)
    ConfigChangeEvent           — published on every successful change
    ConfigurationApplyReport    — UI-facing apply outcome
    PersistentConfigStore       — settings-DB-backed durable store
    build_runtime_configuration — validated immutable snapshot builder
"""

from nexus_scalp.configuration.config import (
    AlgoConfig,
    AppConfig,
    ExecutionConfig,
    ModelConfig,
    RiskConfig,
    TelegramConfig,
)
from nexus_scalp.configuration.runtime_config import (
    ACTIVE_POSITION,
    LIVE_IMMEDIATE,
    NEXT_ORDER,
    NEXT_SESSION,
    NEXT_SIGNAL,
    RESTART_REQUIRED,
    AlgorithmSnapshot,
    ConfigChangeEvent,
    ConfigurationApplyReport,
    ExecutionSnapshot,
    ModelSnapshot,
    NewsSnapshot,
    PersistentConfigStore,
    RiskSnapshot,
    RuleMatrixSnapshot,
    RuntimeConfigStore,
    RuntimeConfiguration,
    TelemetrySnapshot,
    build_runtime_configuration,
    config_file_hash,
    mask_token,
    snapshot_to_flat,
)

__all__ = [
    "ACTIVE_POSITION",
    "AlgoConfig",
    "AlgorithmSnapshot",
    "AppConfig",
    "ConfigChangeEvent",
    "ConfigurationApplyReport",
    "ExecutionConfig",
    "ExecutionSnapshot",
    "LIVE_IMMEDIATE",
    "ModelConfig",
    "ModelSnapshot",
    "NEXT_ORDER",
    "NEXT_SESSION",
    "NEXT_SIGNAL",
    "NewsSnapshot",
    "PersistentConfigStore",
    "RESTART_REQUIRED",
    "RiskConfig",
    "RiskSnapshot",
    "RuleMatrixSnapshot",
    "RuntimeConfigStore",
    "RuntimeConfiguration",
    "TelegramConfig",
    "TelemetrySnapshot",
    "build_runtime_configuration",
    "config_file_hash",
    "mask_token",
    "snapshot_to_flat",
]