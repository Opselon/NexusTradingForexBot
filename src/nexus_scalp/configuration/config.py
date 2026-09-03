"""
Configuration Management Engine
===============================
Provides environment-aware configuration parsing and validation.
Loads YAML configurations with fallback to environment overrides.

ROLE IN THE RUNTIME CONFIGURATION ARCHITECTURE (see `runtime_config.py`):

* ``AppConfig`` / section models here are the **bootstrap / import / export /
  compatibility schema** (a.k.a. "live.yaml role"). They are pure
  declarative dataclasses — *never* the authoritative runtime state.
* The authoritative runtime state is the versioned, immutable
  ``RuntimeConfiguration`` snapshot built by
  ``nexus_scalp.configuration.runtime_config`` (persistent store →
  validation → version → ConfigurationChanged → atomic runtime swap).
* Consumers MUST read the current runtime snapshot through
  ``RuntimeConfigStore.get_snapshot()`` (thread-safe, lock-free reads);
  they MUST NOT read / cache values captured at constructor time for the
  live-hot-path parameters documented in skill.md (effective scope
  LIVE_IMMEDIATE / NEXT_DECISION).
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.news.config import NewsConfig


class ExecutionConfig(BaseModel):
    symbol: str = "XAUUSD"
    mode: ExecutionMode = ExecutionMode.PAPER
    timeframe: str = "M1"
    magic_number: int = 888101
    max_slippage_points: int = 30


class RiskConfig(BaseModel):
    max_account_drawdown_pct: float = Field(default=2.0, gt=0.0, le=100.0)
    risk_per_trade_pct: float = Field(default=0.5, gt=0.0, le=100.0)
    max_concurrent_positions: int = Field(default=1, ge=1)
    max_spread_points: int = Field(default=60, ge=0)
    enforce_stop_loss: bool = True
    max_margin_usage_pct: float = Field(default=10.0, gt=0.0, le=100.0)
    max_allowed_lots: float = Field(default=2.0, gt=0.0)


# [EXPANDED] Telegram Notification Subsystem Settings Schema
class TelegramConfig(BaseModel):
    enabled: bool = True
    bot_token: str = ""
    admin_id: str = ""


class MT5Config(BaseModel):
    account: int | None = None
    password: str | None = None
    server: str | None = None
    timeout_ms: int = 5000
    retries: int = 3
    path: str | None = None
    portable_mode: bool = False


class ModelConfig(BaseModel):
    confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    feature_schema_version: str = "v1.0"
    model_artifact_path: str = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
    #: TASK-01-60D-LIQUIDITY explicit switch. False (default) -> EXACTLY the
    #: existing 50D behavior; True -> the 60D liquidity feature layer is
    #: available to candidate pipelines. The switch NEVER silently alters
    #: schema expectations: enabled input schemas are explicitly 60D
    #: (scalp_liquidity_v1) and manifests record feature_dimension=60.
    #: BUG-185 P3: default is now True — the production contract is the 70D
    #: scalp_v3 champion (Base|News|Liquidity); the governor's causal snapshot
    #: must be VALID for 70D records/inference. 50D is legacy; explicit config
    #: (live.yaml / runtime settings DB) can still override.
    liquidity_features_enabled: bool = True


class AlgoConfig(BaseModel):
    atr_sl_buffer_multiplier: float = Field(default=1.5, ge=0.5, le=4.0)
    min_risk_reward_ratio: float = Field(default=1.8, ge=1.0, le=5.0)
    min_rr_high_confidence: float = Field(default=1.2, ge=0.5, le=5.0)
    high_confidence_threshold: float = Field(default=0.95, ge=0.5, le=1.0)
    ai_zone_confidence_threshold: float = Field(default=0.60, ge=0.50, le=0.99)
    fvg_mitigation_sensitivity: float = Field(default=0.5, ge=0.1, le=1.0)
    order_block_lookback_bars: int = Field(default=30, ge=10, le=100)
    ai_flip_relative_bias_threshold: float = Field(default=0.60, ge=0.51, le=0.85)
    ai_flip_min_delta: float = Field(default=0.10, ge=0.02, le=0.30)

    # State Machine & Hysteresis
    min_confirmation_duration: float = Field(default=2.5, ge=0.0, le=60.0)
    min_observation_count: int = Field(default=10, ge=1, le=200)

    # BUG-TDF-Q2: max age (seconds) of a REUSED regime state (BUG-169
    # duplicate-tick path) before the tick-freshness guard alarms.
    # Generous default: normal duplicate reuse spans only seconds.
    regime_state_max_age_sec: float = Field(default=300.0, ge=1.0, le=86400.0)

    # Recovery Manager Parameters
    recovery_budget_pct_of_r: float = Field(default=0.50, ge=0.05, le=1.0)
    min_recovery_horizon_sec: float = Field(default=30.0, ge=5.0, le=300.0)
    max_recovery_horizon_sec: float = Field(default=600.0, ge=60.0, le=3600.0)
    default_recovery_horizon_sec: float = Field(default=180.0, ge=10.0, le=1200.0)

    # Adaptive Weight Settings for Decision Engine
    w_profit_retention: float = Field(default=0.30, ge=0.0, le=1.0)
    w_pnl_trajectory: float = Field(default=0.15, ge=0.0, le=1.0)
    w_drawdown_velocity: float = Field(default=0.15, ge=0.0, le=1.0)
    w_market_reversal: float = Field(default=0.20, ge=0.0, le=1.0)
    w_recovery_probability: float = Field(default=0.10, ge=0.0, le=1.0)
    w_hold_score: float = Field(default=0.10, ge=0.0, le=1.0)


class ForensicReportConfig(BaseModel):
    """TASK-12: periodic forensic Telegram report (bounded, dedup, config-driven).

    Mirrors the shape read by nexus_scalp.forensics.telegram_report /
    experience_gap (raw-YAML sections). Optional — disabled by default.
    """

    enabled: bool = False
    interval_sec: int = 21600
    minimum_severity: str = "WARNING"
    aggregation_window_sec: int = 3600
    experience_gap: dict[str, float] = Field(default_factory=dict)


class RetentionsConfig(BaseModel):
    """TASK-22: retention windows consumed by the runtime hygiene engine."""

    telemetry_days: int = 7
    cache_hours: int = 24
    failed_jobs_days: int = 14
    audit_days: int = 0  # 0 = unlimited (financial truth is never purged)


class DatabaseHygieneConfig(BaseModel):
    """TASK-22: runtime database hygiene engine (continuous, config-driven).

    Mirrors the `database_hygiene` YAML section. The hygiene worker itself
    remains AUDIT_ONLY/safe-clean by construction (TASK-11 contract); this
    config only drives the SCHEDULER cadence, cycle depth, and reporting.
    """

    enabled: bool = True
    interval_minutes: int = 30
    deep_maintenance_interval_hours: int = 6
    aggressive_cleanup: bool = False
    dry_run: bool = True
    apply_deletes: bool = False
    batch_size: int = 200
    telegram_report: bool = True
    telegram_min_interval_sec: int = 3600
    retention: RetentionsConfig = RetentionsConfig()


class FreshnessConfig(BaseModel):
    """NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: live-freshness truth model.

    Drives the per-stage STALE threshold (seconds). A stage whose last
    successful update is older than `max_age_sec` is reported STALE,
    independent of process uptime / state_version / HTTP 200. Conservative
    default (30s) because the live path is expected to refresh on every tick;
    QA asserts against this value (see tests).
    """

    enabled: bool = True
    max_age_sec: float = 30.0


class AppConfig(BaseSettings):
    """
    Root application settings holding configuration sections.
    """

    model_config = SettingsConfigDict(
        env_prefix="NSE_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    execution: ExecutionConfig = ExecutionConfig()
    risk: RiskConfig = RiskConfig()
    # [EXPANDED] Telegram Section
    telegram: TelegramConfig = TelegramConfig()
    mt5: MT5Config = MT5Config()
    model: ModelConfig = ModelConfig()
    algo: AlgoConfig = AlgoConfig()
    # PHASE 12: NEWS INTELLIGENCE (optional; disabled by default via NewsConfig.enabled)
    news: NewsConfig | None = None
    # BUG-061: CANDLE INTELLIGENCE (local candle-close gate; isolated DB)
    candle_intel: CandleIntelligenceConfig | None = None
    # TASK-12: FORENSIC REPORTING (periodic Telegram safety-net reports; optional)
    forensic_report: ForensicReportConfig | None = None
    # TASK-22: DATABASE HYGIENE (continuous runtime cleanup; optional)
    database_hygiene: DatabaseHygieneConfig | None = None
    # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: live-freshness truth model
    freshness: FreshnessConfig = FreshnessConfig()

    @classmethod
    def load_from_yaml(cls, yaml_path: Path) -> "AppConfig":
        """
        Constructs AppConfig instance by combining a YAML file with environment variables.
        """
        if not yaml_path.exists():
            raise FileNotFoundError(f"Config file not found at: {yaml_path}")

        with open(yaml_path, encoding="utf-8") as f:
            raw_data: dict[str, Any] = yaml.safe_load(f) or {}

        return cls(**raw_data)
