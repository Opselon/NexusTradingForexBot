"""
Configuration Management Engine
===============================
Provides environment-aware configuration parsing and validation.
Loads YAML configurations with fallback to environment overrides.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from nexus_scalp.domain.enums import ExecutionMode


class ExecutionConfig(BaseModel):
    symbol: str = "XAUUSD"
    mode: ExecutionMode = ExecutionMode.LIVE
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
    model_artifact_path: str = "artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"

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