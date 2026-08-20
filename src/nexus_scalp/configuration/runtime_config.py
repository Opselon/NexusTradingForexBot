"""Versioned immutable runtime configuration store (hot-reload core).

ARCHITECTURE (see agents/skill.md §13b — Runtime Configuration & Hot Reload):

::

    UI  ->  Config API  ->  Validation  ->  Persistent Config Store
                                                    |
                                              Version N+1
                                                    |
                                         ConfigurationChanged event
                                                    |
                                    Runtime Config Snapshot (IMMUTABLE)
                                                    |
        Strategy / Risk / Execution / Rule Matrix / News / Model services
        (all new evaluations read the CURRENT snapshot; no constructor caches)

Roles:
* ``RuntimeConfiguration``          — frozen (immutable) runtime domain model.
* ``RuntimeConfigStore``            — authoritative in-memory provider:
  lock-free snapshot reads, atomic swap, monotonic versioning, event bus.
* ``ConfigChangeEvent``             — what changed, when, from which source.
* ``ConfigurationApplyReport``      — UI-facing result (persisted / applied /
  version / runtime status / reason).
* ``PersistentConfigStore``         — durable (settings-DB-backed) projection
  of the authoritative configuration (secrets never stored here).
* ``build_runtime_configuration``   — construct a validated immutable snapshot
  from a bootstrap AppConfig / YAML import / partial field update.

live.yaml role: BOOTSTRAP / IMPORT / EXPORT / COMPATIBILITY only. It is NEVER
the authoritative runtime source: after startup the engine consumes the store
snapshot; live.yaml is a projection (export) for diagnostics and legacy tooling.

Hot-reload rule: after a successful apply, ALL NEW runtime evaluations must
use the new snapshot. Existing positions are NOT retroactively rewritten
(effective scope per setting is documented in the audit table in skill.md).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.configuration.config import (
    AlgoConfig,
    AppConfig,
    ExecutionConfig,
    ModelConfig,
    RiskConfig,
    TelegramConfig,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Effective scopes (skill.md §55)
# ---------------------------------------------------------------------------
LIVE_IMMEDIATE = "LIVE_IMMEDIATE"  # takes effect on the next operation, now
NEXT_SIGNAL = "NEXT_SIGNAL"  # new signal evaluations only
NEXT_ORDER = "NEXT_ORDER"  # new order dispatch only
ACTIVE_POSITION = "ACTIVE_POSITION"  # may influence open-position management
NEXT_SESSION = "NEXT_SESSION"  # next engine session only
RESTART_REQUIRED = "RESTART_REQUIRED"  # cannot safely change without restart

# ---------------------------------------------------------------------------
# Runtime domain model — IMMUTABLE (frozen dataclasses, never mutated in place)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionSnapshot:
    symbol: str = "XAUUSD"
    mode: str = "LIVE"
    timeframe: str = "M1"
    magic_number: int = 888101
    max_slippage_points: int = 30
    effective_scope: str = NEXT_ORDER


@dataclass(frozen=True)
class RiskSnapshot:
    max_account_drawdown_pct: float = 2.0
    risk_per_trade_pct: float = 0.5
    max_concurrent_positions: int = 1
    max_spread_points: int = 60
    enforce_stop_loss: bool = True
    max_margin_usage_pct: float = 10.0
    max_allowed_lots: float = 2.0
    effective_scope: str = NEXT_ORDER


@dataclass(frozen=True)
class AlgorithmSnapshot:
    atr_sl_buffer_multiplier: float = 1.5
    min_risk_reward_ratio: float = 1.8
    min_rr_high_confidence: float = 1.2
    high_confidence_threshold: float = 0.95
    ai_zone_confidence_threshold: float = 0.60
    fvg_mitigation_sensitivity: float = 0.5
    order_block_lookback_bars: int = 30
    ai_flip_relative_bias_threshold: float = 0.60
    ai_flip_min_delta: float = 0.10
    min_confirmation_duration: float = 2.5
    min_observation_count: int = 10
    recovery_budget_pct_of_r: float = 0.50
    min_recovery_horizon_sec: float = 30.0
    max_recovery_horizon_sec: float = 600.0
    default_recovery_horizon_sec: float = 180.0
    w_profit_retention: float = 0.30
    w_pnl_trajectory: float = 0.15
    w_drawdown_velocity: float = 0.15
    w_market_reversal: float = 0.20
    w_recovery_probability: float = 0.10
    w_hold_score: float = 0.10
    effective_scope: str = NEXT_SIGNAL


@dataclass(frozen=True)
class ModelSnapshot:
    confidence_threshold: float = 0.35
    feature_schema_version: str = "v1.0"
    model_artifact_path: str = "artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"
    liquidity_features_enabled: bool = False
    model_version: str = ""
    model_hash: str = ""
    effective_scope: str = NEXT_SIGNAL


@dataclass(frozen=True)
class TelemetrySnapshot:
    enabled: bool = True
    configured: bool = False
    token_present: bool = False
    token_masked: str = ""
    admin_id_present: bool = False
    bot_token: str = ""  # never logged / never exposed in UIs (masked only)
    admin_id: str = ""  # never logged / never exposed in UIs (masked only)
    effective_scope: str = NEXT_SESSION


@dataclass(frozen=True)
class NewsSnapshot:
    enabled: bool = False
    worker_interval_sec: int = 60
    poll_fast_interval_sec: int = 300
    poll_medium_interval_sec: int = 900
    poll_slow_interval_sec: int = 3600
    max_queue_size: int = 1000
    effective_scope: str = NEXT_SESSION


@dataclass(frozen=True)
class RuleMatrixSnapshot:
    cache_ttl_seconds: float = 5.0
    effective_scope: str = LIVE_IMMEDIATE


@dataclass(frozen=True)
class RuntimeConfiguration:
    """Immutable, versioned runtime configuration (atomic snapshot).

    One snapshot object = ONE consistent configuration version. Consumers
    capture the whole object (never individual scalars) so a single decision
    cycle can never mix values from different versions.
    """

    version: int
    updated_at: str  # ISO-8601 UTC
    source: str
    correlation_id: str
    changed_fields: tuple[str, ...] = ()
    execution: ExecutionSnapshot = ExecutionSnapshot()
    risk: RiskSnapshot = RiskSnapshot()
    algo: AlgorithmSnapshot = AlgorithmSnapshot()
    model: ModelSnapshot = ModelSnapshot()
    telegram: TelemetrySnapshot = TelemetrySnapshot()
    news: NewsSnapshot = NewsSnapshot()
    rule_matrix: RuleMatrixSnapshot = RuleMatrixSnapshot()

    # ------------------------------------------------------------------
    # Hot-path accessors (lock-free; snapshot is frozen)
    # ------------------------------------------------------------------
    @property
    def atr_sl_buffer_multiplier(self) -> float:
        return self.algo.atr_sl_buffer_multiplier

    @property
    def min_risk_reward_ratio(self) -> float:
        return self.algo.min_risk_reward_ratio

    @property
    def ai_zone_confidence_threshold(self) -> float:
        return self.algo.ai_zone_confidence_threshold

    @property
    def fvg_mitigation_sensitivity(self) -> float:
        return self.algo.fvg_mitigation_sensitivity

    @property
    def order_block_lookback_bars(self) -> int:
        return self.algo.order_block_lookback_bars

    @property
    def risk_per_trade_pct(self) -> float:
        return self.risk.risk_per_trade_pct

    @property
    def max_spread_points(self) -> int:
        return self.risk.max_spread_points

    @property
    def max_allowed_lots(self) -> float:
        return self.risk.max_allowed_lots

    @property
    def max_concurrent_positions(self) -> int:
        return self.risk.max_concurrent_positions

    @property
    def max_account_drawdown_pct(self) -> float:
        return self.risk.max_account_drawdown_pct

    @property
    def max_slippage_points(self) -> int:
        return self.execution.max_slippage_points

    @property
    def enforce_stop_loss(self) -> bool:
        return self.risk.enforce_stop_loss

    @property
    def confidence_threshold(self) -> float:
        return self.model.confidence_threshold

    @property
    def model_artifact_path(self) -> str:
        return self.model.model_artifact_path

    @property
    def telegram_enabled(self) -> bool:
        return self.telegram.enabled

    def to_dict(self) -> dict[str, Any]:
        """Full safe representation (secrets masked)."""
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "changed_fields": list(self.changed_fields),
            "execution": {
                "symbol": self.execution.symbol,
                "mode": self.execution.mode,
                "timeframe": self.execution.timeframe,
                "magic_number": self.execution.magic_number,
                "max_slippage_points": self.execution.max_slippage_points,
                "effective_scope": self.execution.effective_scope,
            },
            "risk": {
                "max_account_drawdown_pct": self.risk.max_account_drawdown_pct,
                "risk_per_trade_pct": self.risk.risk_per_trade_pct,
                "max_concurrent_positions": self.risk.max_concurrent_positions,
                "max_spread_points": self.risk.max_spread_points,
                "enforce_stop_loss": self.risk.enforce_stop_loss,
                "max_margin_usage_pct": self.risk.max_margin_usage_pct,
                "max_allowed_lots": self.risk.max_allowed_lots,
                "effective_scope": self.risk.effective_scope,
            },
            "algo": {
                "atr_sl_buffer_multiplier": self.algo.atr_sl_buffer_multiplier,
                "min_risk_reward_ratio": self.algo.min_risk_reward_ratio,
                "min_rr_high_confidence": self.algo.min_rr_high_confidence,
                "high_confidence_threshold": self.algo.high_confidence_threshold,
                "ai_zone_confidence_threshold": self.algo.ai_zone_confidence_threshold,
                "fvg_mitigation_sensitivity": self.algo.fvg_mitigation_sensitivity,
                "order_block_lookback_bars": self.algo.order_block_lookback_bars,
                "ai_flip_relative_bias_threshold": self.algo.ai_flip_relative_bias_threshold,
                "ai_flip_min_delta": self.algo.ai_flip_min_delta,
                "recovery_budget_pct_of_r": self.algo.recovery_budget_pct_of_r,
                "min_recovery_horizon_sec": self.algo.min_recovery_horizon_sec,
                "max_recovery_horizon_sec": self.algo.max_recovery_horizon_sec,
                "default_recovery_horizon_sec": self.algo.default_recovery_horizon_sec,
                "w_profit_retention": self.algo.w_profit_retention,
                "w_pnl_trajectory": self.algo.w_pnl_trajectory,
                "w_drawdown_velocity": self.algo.w_drawdown_velocity,
                "w_market_reversal": self.algo.w_market_reversal,
                "w_recovery_probability": self.algo.w_recovery_probability,
                "w_hold_score": self.algo.w_hold_score,
                "effective_scope": self.algo.effective_scope,
            },
            "model": {
                "confidence_threshold": self.model.confidence_threshold,
                "feature_schema_version": self.model.feature_schema_version,
                "model_artifact_path": self.model.model_artifact_path,
                "liquidity_features_enabled": self.model.liquidity_features_enabled,
                "model_version": self.model.model_version,
                "model_hash": self.model.model_hash,
                "effective_scope": self.model.effective_scope,
            },
            "telegram": {
                "enabled": self.telegram.enabled,
                "configured": self.telegram.configured,
                "token_present": self.telegram.token_present,
                "token_masked": self.telegram.token_masked,
                "admin_id_present": self.telegram.admin_id_present,
                "effective_scope": self.telegram.effective_scope,
            },
            "news": {
                "enabled": self.news.enabled,
                "worker_interval_sec": self.news.worker_interval_sec,
                "poll_fast_interval_sec": self.news.poll_fast_interval_sec,
                "poll_medium_interval_sec": self.news.poll_medium_interval_sec,
                "poll_slow_interval_sec": self.news.poll_slow_interval_sec,
                "max_queue_size": self.news.max_queue_size,
                "effective_scope": self.news.effective_scope,
            },
            "rule_matrix": {
                "cache_ttl_seconds": self.rule_matrix.cache_ttl_seconds,
                "effective_scope": self.rule_matrix.effective_scope,
            },
        }

    def to_flat_dict(self) -> dict[str, Any]:
        """Flat `section.field` -> value (the persistent-store shape)."""
        d = self.to_dict()
        out: dict[str, Any] = {}
        for section in ("execution", "risk", "algo", "model", "telegram", "news", "rule_matrix"):
            for key, value in d[section].items():
                if key == "effective_scope":
                    continue
                out[f"{section}.{key}"] = value
        return out

    def to_algo_config(self) -> AlgoConfig:
        """Project the snapshot back to the bootstrap AlgoConfig schema."""
        return AlgoConfig(
            atr_sl_buffer_multiplier=self.algo.atr_sl_buffer_multiplier,
            min_risk_reward_ratio=self.algo.min_risk_reward_ratio,
            min_rr_high_confidence=self.algo.min_rr_high_confidence,
            high_confidence_threshold=self.algo.high_confidence_threshold,
            ai_zone_confidence_threshold=self.algo.ai_zone_confidence_threshold,
            fvg_mitigation_sensitivity=self.algo.fvg_mitigation_sensitivity,
            order_block_lookback_bars=self.algo.order_block_lookback_bars,
            ai_flip_relative_bias_threshold=self.algo.ai_flip_relative_bias_threshold,
            ai_flip_min_delta=self.algo.ai_flip_min_delta,
        )

    def to_app_config(self) -> AppConfig:
        """Project the snapshot back to the bootstrap AppConfig schema."""
        from nexus_scalp.domain.enums import ExecutionMode

        execution = ExecutionConfig(
            symbol=self.execution.symbol,
            mode=ExecutionMode(self.execution.mode),
            timeframe=self.execution.timeframe,
            magic_number=self.execution.magic_number,
            max_slippage_points=self.execution.max_slippage_points,
        )
        risk = RiskConfig(
            max_account_drawdown_pct=self.risk.max_account_drawdown_pct,
            risk_per_trade_pct=self.risk.risk_per_trade_pct,
            max_concurrent_positions=self.risk.max_concurrent_positions,
            max_spread_points=self.risk.max_spread_points,
            enforce_stop_loss=self.risk.enforce_stop_loss,
            max_margin_usage_pct=self.risk.max_margin_usage_pct,
            max_allowed_lots=self.risk.max_allowed_lots,
        )
        model = ModelConfig(
            confidence_threshold=self.model.confidence_threshold,
            feature_schema_version=self.model.feature_schema_version,
            model_artifact_path=self.model.model_artifact_path,
            liquidity_features_enabled=self.model.liquidity_features_enabled,
        )
        telegram = TelegramConfig(
            enabled=self.telegram.enabled,
            bot_token=self.telegram.bot_token,
            admin_id=self.telegram.admin_id,
        )
        return AppConfig(
            execution=execution,
            risk=risk,
            model=model,
            telegram=telegram,
            algo=self.to_algo_config(),
        )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigChangeEvent:
    """Published after every successful runtime configuration change."""

    configuration_version: int
    timestamp: str
    changed_sections: tuple[str, ...]
    changed_fields: tuple[str, ...]
    source: str
    correlation_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration_version": self.configuration_version,
            "timestamp": self.timestamp,
            "changed_sections": list(self.changed_sections),
            "changed_fields": list(self.changed_fields),
            "source": self.source,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class ConfigurationApplyReport:
    """UI-facing outcome of a configuration update request."""

    success: bool
    persisted: bool
    runtime_applied: bool
    configuration_version: int
    correlation_id: str
    reason: str = ""
    requested_fields: tuple[str, ...] = ()
    previous_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "persisted": self.persisted,
            "runtime_applied": self.runtime_applied,
            "configuration_version": self.configuration_version,
            "correlation_id": self.correlation_id,
            "reason": self.reason,
            "requested_fields": list(self.requested_fields),
            "previous_version": self.previous_version,
        }


# ---------------------------------------------------------------------------
# Persistent store (settings DB projection)
# ---------------------------------------------------------------------------


class PersistentConfigStore:
    """Durable authoritative configuration (settings DB).

    Flat `section.field` keys in the settings database. Secrets (bot token)
    are handled by SecureSecretStore — this class NEVER persists them.
    """

    def __init__(self, settings_service: Any) -> None:
        self._svc = settings_service
        self._db = settings_service.db

    # ---- metadata -----------------------------------------------------
    def get_config_version(self) -> int:
        raw = self._db.get_meta("runtime_config.version")
        try:
            return int(raw) if raw else 0
        except (TypeError, ValueError):
            return 0

    def set_config_version(self, version: int) -> None:
        self._db.set_meta("runtime_config.version", str(version))

    def get_last_apply_status(self) -> str:
        return self._db.get_meta("runtime_config.last_apply_status") or "NEVER"

    def set_last_apply_status(self, status: str) -> None:
        self._db.set_meta("runtime_config.last_apply_status", status)

    def get_last_apply_error(self) -> str:
        return self._db.get_meta("runtime_config.last_apply_error") or ""

    def set_last_apply_error(self, err: str) -> None:
        self._db.set_meta("runtime_config.last_apply_error", err)

    def get_last_update_source(self) -> str:
        return self._db.get_meta("runtime_config.last_update_source") or ""

    def set_last_update_source(self, source: str) -> None:
        self._db.set_meta("runtime_config.last_update_source", source)

    def get_last_update_at(self) -> str:
        return self._db.get_meta("runtime_config.last_update_at") or ""

    def set_last_update_at(self, ts: str) -> None:
        self._db.set_meta("runtime_config.last_update_at", ts)

    # ---- values -------------------------------------------------------
    def get_all(self) -> dict[str, Any]:
        """All persisted, typed, non-secret values keyed `section.field`."""
        out: dict[str, Any] = {}
        for key, sv in self._db.all().items():
            if key.startswith("telegram.") and key != "telegram.enabled":
                continue  # secrets via SecureSecretStore only
            out[key] = sv.value
        return out

    def set_many(self, values: dict[str, Any], *, source: str, actor: str) -> None:
        for key, value in values.items():
            self._db.set(
                key,
                value,
                source=source,
                actor=actor,
                correlation_id=None,
                audit=True,
            )


# ---------------------------------------------------------------------------
# Snapshot builder (validation + normalization)
# ---------------------------------------------------------------------------

# Validation bounds mirroring the bootstrap schema Field() constraints.
_VALIDATORS: dict[str, Callable[[Any], bool]] = {
    "algo.atr_sl_buffer_multiplier": lambda v: (
        isinstance(v, (int, float)) and 0.5 <= float(v) <= 4.0
    ),
    "algo.min_risk_reward_ratio": lambda v: isinstance(v, (int, float)) and 1.0 <= float(v) <= 5.0,
    "algo.min_rr_high_confidence": lambda v: isinstance(v, (int, float)) and 0.5 <= float(v) <= 5.0,
    "algo.high_confidence_threshold": lambda v: (
        isinstance(v, (int, float)) and 0.5 <= float(v) <= 1.0
    ),
    "algo.ai_zone_confidence_threshold": lambda v: (
        isinstance(v, (int, float)) and 0.50 <= float(v) <= 0.99
    ),
    "algo.fvg_mitigation_sensitivity": lambda v: (
        isinstance(v, (int, float)) and 0.1 <= float(v) <= 1.0
    ),
    "algo.order_block_lookback_bars": lambda v: isinstance(v, int) and 10 <= int(v) <= 100,
    "risk.max_account_drawdown_pct": lambda v: (
        isinstance(v, (int, float)) and 0.0 < float(v) <= 100.0
    ),
    "risk.risk_per_trade_pct": lambda v: isinstance(v, (int, float)) and 0.0 < float(v) <= 100.0,
    "risk.max_concurrent_positions": lambda v: isinstance(v, int) and int(v) >= 1,
    "risk.max_spread_points": lambda v: isinstance(v, int) and int(v) >= 0,
    "risk.max_margin_usage_pct": lambda v: isinstance(v, (int, float)) and 0.0 < float(v) <= 100.0,
    "risk.max_allowed_lots": lambda v: isinstance(v, (int, float)) and float(v) > 0.0,
    "risk.enforce_stop_loss": lambda v: isinstance(v, bool),
    "execution.magic_number": lambda v: isinstance(v, int) and int(v) > 0,
    "execution.max_slippage_points": lambda v: isinstance(v, int) and int(v) >= 0,
    "execution.symbol": lambda v: isinstance(v, str) and bool(v.strip()),
    "execution.timeframe": lambda v: isinstance(v, str) and bool(v.strip()),
    "execution.mode": lambda v: isinstance(v, str) and v.strip().upper()
    in ("LIVE", "PAPER", "SHADOW", "SIMULATION", "NO_OP"),
    "model.confidence_threshold": lambda v: isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0,
    "model.model_artifact_path": lambda v: isinstance(v, str) and bool(v.strip()),
    "model.liquidity_features_enabled": lambda v: isinstance(v, bool),
    "telegram.enabled": lambda v: isinstance(v, bool),
}


def validate_field(key: str, value: Any) -> tuple[bool, str]:
    validator = _VALIDATORS.get(key)
    if validator is None:
        return True, ""
    if not validator(value):
        return False, f"invalid value {value!r} for {key} (schema bounds violated)"
    return True, ""


def _coerce(key: str, value: Any) -> Any:
    if value is None:
        return value
    if key.endswith("_bars") or key in (
        "risk.max_concurrent_positions",
        "risk.max_spread_points",
        "execution.magic_number",
        "execution.max_slippage_points",
    ):
        return int(value)
    if key in ("risk.enforce_stop_loss", "model.liquidity_features_enabled", "telegram.enabled"):
        return bool(value)
    if (
        key.endswith("_pct")
        or key.endswith("_multiplier")
        or key.endswith("_ratio")
        or key.endswith("_sensitivity")
        or key.endswith("_threshold")
        or key.endswith("_delta")
    ):
        return float(value)
    return value


def _section_of(key: str) -> str:
    return key.split(".", 1)[0]


@dataclass
class SnapshotBuildResult:
    snapshot: RuntimeConfiguration | None
    errors: list[str]
    changed_fields: list[str]
    sections: list[str]


def build_runtime_configuration(
    *,
    version: int,
    base: RuntimeConfiguration | None = None,
    bootstrap: AppConfig | None = None,
    updates: dict[str, Any] | None = None,
    source: str = "SYSTEM_BOOTSTRAP",
    correlation_id: str | None = None,
) -> SnapshotBuildResult:
    """Build a validated immutable snapshot.

    ``base`` — previous snapshot (previous known-good values survive invalid
    updates untouched — the whole request is rejected, never partially applied).
    ``bootstrap`` — AppConfig from live.yaml / code defaults used once at boot.
    ``updates``   — `section.field` -> value overrides (the UI save payload).
    """
    errors: list[str] = []
    changed: list[str] = []
    sections: list[str] = []

    # ---- 1. resolve the base values -----------------------------------
    if base is not None:
        cur = base.to_flat_dict()
    else:
        cur = _empty_values()

    if bootstrap is not None:
        cur = _apply_bootstrap(cur, bootstrap)

    updates = updates or {}
    for key, value in updates.items():
        if key not in _VALIDATORS and not _is_known_flat_key(key):
            errors.append(f"unknown configuration key: {key!r}")
            continue
        ok, err = validate_field(key, value)
        if not ok:
            errors.append(err)
            continue
        coerced = _coerce(key, value)
        if key in cur and cur[key] == coerced:
            continue  # no change
        cur[key] = coerced
        changed.append(key)
        sec = _section_of(key)
        if sec not in sections:
            sections.append(sec)

    if errors:
        return SnapshotBuildResult(None, errors, [], [])

    # ---- 2. cross-field constraints (atomicity) -----------------------
    xerr = _cross_field_validate(cur)
    if xerr:
        return SnapshotBuildResult(None, xerr, [], [])

    # ---- 3. assemble immutable snapshot -------------------------------
    snapshot = RuntimeConfiguration(
        version=version,
        updated_at=_utcnow(),
        source=source,
        correlation_id=correlation_id or new_corr_id("cfg"),
        changed_fields=tuple(changed),
        execution=ExecutionSnapshot(
            symbol=str(cur["execution.symbol"]),
            mode=str(cur["execution.mode"]),
            timeframe=str(cur["execution.timeframe"]),
            magic_number=int(cur["execution.magic_number"]),
            max_slippage_points=int(cur["execution.max_slippage_points"]),
        ),
        risk=RiskSnapshot(
            max_account_drawdown_pct=float(cur["risk.max_account_drawdown_pct"]),
            risk_per_trade_pct=float(cur["risk.risk_per_trade_pct"]),
            max_concurrent_positions=int(cur["risk.max_concurrent_positions"]),
            max_spread_points=int(cur["risk.max_spread_points"]),
            enforce_stop_loss=bool(cur["risk.enforce_stop_loss"]),
            max_margin_usage_pct=float(cur["risk.max_margin_usage_pct"]),
            max_allowed_lots=float(cur["risk.max_allowed_lots"]),
        ),
        algo=AlgorithmSnapshot(
            atr_sl_buffer_multiplier=float(cur["algo.atr_sl_buffer_multiplier"]),
            min_risk_reward_ratio=float(cur["algo.min_risk_reward_ratio"]),
            min_rr_high_confidence=float(cur["algo.min_rr_high_confidence"]),
            high_confidence_threshold=float(cur["algo.high_confidence_threshold"]),
            ai_zone_confidence_threshold=float(cur["algo.ai_zone_confidence_threshold"]),
            fvg_mitigation_sensitivity=float(cur["algo.fvg_mitigation_sensitivity"]),
            order_block_lookback_bars=int(cur["algo.order_block_lookback_bars"]),
            ai_flip_relative_bias_threshold=float(cur["algo.ai_flip_relative_bias_threshold"]),
            ai_flip_min_delta=float(cur["algo.ai_flip_min_delta"]),
        ),
        model=ModelSnapshot(
            confidence_threshold=float(cur["model.confidence_threshold"]),
            feature_schema_version=str(cur["model.feature_schema_version"]),
            model_artifact_path=str(cur["model.model_artifact_path"]),
            liquidity_features_enabled=bool(cur["model.liquidity_features_enabled"]),
        ),
        telegram=TelemetrySnapshot(
            enabled=bool(cur["telegram.enabled"]),
        ),
        news=NewsSnapshot(),
        rule_matrix=RuleMatrixSnapshot(),
    )
    return SnapshotBuildResult(snapshot, [], changed, sections)


def _empty_values() -> dict[str, Any]:
    return {
        "execution.symbol": "XAUUSD",
        "execution.mode": "LIVE",
        "execution.timeframe": "M1",
        "execution.magic_number": 888101,
        "execution.max_slippage_points": 30,
        "risk.max_account_drawdown_pct": 2.0,
        "risk.risk_per_trade_pct": 0.5,
        "risk.max_concurrent_positions": 1,
        "risk.max_spread_points": 60,
        "risk.enforce_stop_loss": True,
        "risk.max_margin_usage_pct": 10.0,
        "risk.max_allowed_lots": 2.0,
        "algo.atr_sl_buffer_multiplier": 1.5,
        "algo.min_risk_reward_ratio": 1.8,
        "algo.min_rr_high_confidence": 1.2,
        "algo.high_confidence_threshold": 0.95,
        "algo.ai_zone_confidence_threshold": 0.60,
        "algo.fvg_mitigation_sensitivity": 0.5,
        "algo.order_block_lookback_bars": 30,
        "algo.ai_flip_relative_bias_threshold": 0.60,
        "algo.ai_flip_min_delta": 0.10,
        "model.confidence_threshold": 0.35,
        "model.feature_schema_version": "v1.0",
        "model.model_artifact_path": "artifacts/models/scalp/XAUUSD/v1.0.0/model.pt",
        "model.liquidity_features_enabled": False,
        "telegram.enabled": True,
    }


def _is_known_flat_key(key: str) -> bool:
    return key in _empty_values()


def _apply_bootstrap(cur: dict[str, Any], bootstrap: AppConfig) -> dict[str, Any]:
    out = dict(cur)
    ex = bootstrap.execution
    out["execution.symbol"] = ex.symbol
    out["execution.mode"] = ex.mode.value if hasattr(ex.mode, "value") else str(ex.mode)
    out["execution.timeframe"] = ex.timeframe
    out["execution.magic_number"] = ex.magic_number
    out["execution.max_slippage_points"] = ex.max_slippage_points
    rk = bootstrap.risk
    out["risk.max_account_drawdown_pct"] = rk.max_account_drawdown_pct
    out["risk.risk_per_trade_pct"] = rk.risk_per_trade_pct
    out["risk.max_concurrent_positions"] = rk.max_concurrent_positions
    out["risk.max_spread_points"] = rk.max_spread_points
    out["risk.enforce_stop_loss"] = rk.enforce_stop_loss
    out["risk.max_margin_usage_pct"] = rk.max_margin_usage_pct
    out["risk.max_allowed_lots"] = rk.max_allowed_lots
    al = bootstrap.algo
    out["algo.atr_sl_buffer_multiplier"] = al.atr_sl_buffer_multiplier
    out["algo.min_risk_reward_ratio"] = al.min_risk_reward_ratio
    out["algo.min_rr_high_confidence"] = al.min_rr_high_confidence
    out["algo.high_confidence_threshold"] = al.high_confidence_threshold
    out["algo.ai_zone_confidence_threshold"] = al.ai_zone_confidence_threshold
    out["algo.fvg_mitigation_sensitivity"] = al.fvg_mitigation_sensitivity
    out["algo.order_block_lookback_bars"] = al.order_block_lookback_bars
    out["algo.ai_flip_relative_bias_threshold"] = al.ai_flip_relative_bias_threshold
    out["algo.ai_flip_min_delta"] = al.ai_flip_min_delta
    md = bootstrap.model
    out["model.confidence_threshold"] = md.confidence_threshold
    out["model.feature_schema_version"] = md.feature_schema_version
    out["model.model_artifact_path"] = md.model_artifact_path
    out["model.liquidity_features_enabled"] = md.liquidity_features_enabled
    tg = bootstrap.telegram
    out["telegram.enabled"] = bool(tg.enabled)
    return out


def _cross_field_validate(cur: dict[str, Any]) -> list[str]:
    """Atomic cross-field constraints (skill.md §30)."""
    errs: list[str] = []
    max_dd = float(cur["risk.max_account_drawdown_pct"])
    rpt = float(cur["risk.risk_per_trade_pct"])
    # Max Drawdown must be >= Risk Per Trade (per-trade risk cannot exceed the
    # account-level stop). This mirrors the project's risk semantics: the
    # drawdown ceiling is a hard account stop and is conservatively bounded.
    if rpt > max_dd:
        errs.append(
            f"cross-field: risk.risk_per_trade_pct ({rpt}) must be <= "
            f"risk.max_account_drawdown_pct ({max_dd})"
        )
    return errs


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_corr_id(prefix: str = "cfg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return "*" * (len(token) - 4) + token[-4:]


# ---------------------------------------------------------------------------
# The store — authoritative runtime provider
# ---------------------------------------------------------------------------


class RuntimeConfigStore:
    """Thread-safe immutable snapshot provider with atomic swap + event bus.

    Reads (``get_snapshot`` / ``get_version``) are lock-free: consumers grab
    the current immutable snapshot reference (a single pointer read). Writes
    build a NEW snapshot and swap it atomically under a lock — old in-flight
    evaluations finish against the old snapshot and never observe a mix.
    """

    def __init__(
        self,
        persistent: PersistentConfigStore | None = None,
        bootstrap: AppConfig | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._persistent = persistent
        self._listeners: list[Callable[[RuntimeConfiguration, ConfigChangeEvent], None]] = []
        self._last_event: ConfigChangeEvent | None = None
        self._last_apply_status = "NEVER"
        self._last_apply_error = ""
        # Startup: build version 1 from bootstrap (live.yaml role = bootstrap)
        base_version = persistent.get_config_version() if persistent else 0
        start_version = max(1, base_version + 1) if base_version >= 0 else 1
        result = build_runtime_configuration(
            version=start_version,
            base=None,
            bootstrap=bootstrap,
            source="SYSTEM_BOOTSTRAP",
        )
        if result.snapshot is None:
            logger.error(
                "[RUNTIME_CONFIG] bootstrap snapshot invalid: %s — falling back to defaults",
                "; ".join(result.errors),
            )
            result = build_runtime_configuration(version=start_version, source="SYSTEM_DEFAULTS")
        self._snapshot: RuntimeConfiguration = result.snapshot  # type: ignore[assignment]
        if persistent is not None:
            # Boot hydration (§60 crash recovery / §68 restart persistence):
            # the persisted settings DB is authoritative at startup — saved
            # values are layered over the bootstrap, semantics unchanged.
            persisted_values = persistent.get_all()
            persisted_values.pop("rule_matrix.cache_ttl_seconds", None)
            persisted_values.pop("telegram.enabled", None)
            if persisted_values:
                hyd = build_runtime_configuration(
                    version=start_version + 1,
                    base=self._snapshot,
                    updates=persisted_values,
                    source="PERSISTED_RESTORE",
                )
                if hyd.snapshot is not None:
                    self._snapshot = hyd.snapshot
                    persistent.set_config_version(self._snapshot.version)
            else:
                persistent.set_config_version(self._snapshot.version)

    def rehydrate(self, persistent: PersistentConfigStore) -> None:
        """Attach a persistent store and re-hydrate from it (engine boot).

        Used when the engine constructs the store before the settings
        service exists; persisted values layer over the bootstrap snapshot
        (restart persistence / crash recovery).
        """
        with self._lock:
            self._persistent = persistent
            persisted_values = persistent.get_all()
            persisted_values.pop("rule_matrix.cache_ttl_seconds", None)
            persisted_values.pop("telegram.enabled", None)
            if not persisted_values:
                persistent.set_config_version(self._snapshot.version)
                return
            hyd = build_runtime_configuration(
                version=self._snapshot.version + 1,
                base=self._snapshot,
                updates=persisted_values,
                source="PERSISTED_RESTORE",
            )
            if hyd.snapshot is not None:
                self._snapshot = hyd.snapshot
                persistent.set_config_version(self._snapshot.version)
                logger.info(
                    "[RUNTIME_CONFIG] rehydrated from persistent store version=%d",
                    self._snapshot.version,
                )
            else:
                logger.warning(
                    "[RUNTIME_CONFIG] rehydrate rejected (keeping bootstrap): %s",
                    "; ".join(hyd.errors),
                )

    # ------------------------------------------------------------ reads
    def get_snapshot(self) -> RuntimeConfiguration:
        """Lock-free read returning the current immutable snapshot."""
        return self._snapshot

    def get_version(self) -> int:
        return self._snapshot.version

    def diagnostics(self) -> dict[str, Any]:
        snap = self._snapshot
        return {
            "runtime_version": snap.version,
            "runtime_updated_at": snap.updated_at,
            "runtime_source": snap.source,
            "persistent_version": self._persistent.get_config_version()
            if self._persistent
            else None,
            "last_apply_status": self._last_apply_status,
            "last_apply_error": self._last_apply_error,
            "last_event": self._last_event.to_dict() if self._last_event else None,
        }

    # ------------------------------------------------------------ events
    def add_listener(self, fn: Callable[[RuntimeConfiguration, ConfigChangeEvent], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def _notify(self, snap: RuntimeConfiguration, event: ConfigChangeEvent) -> None:
        for fn in list(self._listeners):
            try:
                fn(snap, event)
            except Exception:
                logger.exception("[RUNTIME_CONFIG] listener error (isolated)")

    # ------------------------------------------------------------ apply
    def apply(
        self,
        updates: dict[str, Any],
        *,
        source: str = "WEB_UI",
        actor: str = "web",
        correlation_id: str | None = None,
    ) -> ConfigurationApplyReport:
        """Full apply pipeline (skill.md §27): validate -> persist -> version
        -> build snapshot -> publish event -> atomic swap -> confirm.

        Any validation failure rejects the WHOLE request; the previous
        known-good snapshot remains active (never partially applied).
        """
        cid = correlation_id or new_corr_id()
        with self._lock:
            prev = self._snapshot
            new_version = prev.version + 1
            result = build_runtime_configuration(
                version=new_version,
                base=prev,
                updates=updates,
                source=source,
                correlation_id=cid,
            )
            if result.snapshot is None:
                logger.warning(
                    "[RUNTIME_CONFIG] REJECTED source=%s version=%d errors=%s",
                    source,
                    new_version,
                    "; ".join(result.errors),
                )
                if self._persistent is not None:
                    self._persistent.set_last_apply_status("REJECTED")
                    self._persistent.set_last_apply_error("; ".join(result.errors))
                self._last_apply_status = "REJECTED"
                self._last_apply_error = "; ".join(result.errors)
                return ConfigurationApplyReport(
                    success=False,
                    persisted=False,
                    runtime_applied=False,
                    configuration_version=prev.version,
                    correlation_id=cid,
                    reason="; ".join(result.errors),
                    requested_fields=tuple(updates.keys()),
                    previous_version=prev.version,
                )

            snapshot = result.snapshot
            logger.info(
                "[RUNTIME_CONFIG] event=RUNTIME_CONFIG_UPDATE_REQUESTED source=%s "
                "configuration_version=%d changed_fields=%s",
                source,
                new_version,
                ",".join(result.changed_fields) or "-",
            )

            # 1. persist authoritative store (settings DB)
            if self._persistent is not None:
                self._persistent.set_many(snapshot_to_flat(snapshot), source=source, actor=actor)
                self._persistent.set_config_version(new_version)
                self._persistent.set_last_apply_status("APPLIED")
                self._persistent.set_last_apply_error("")
                self._persistent.set_last_update_source(source)
                self._persistent.set_last_update_at(_utcnow())
            persisted = self._persistent is not None
            logger.info(
                "[RUNTIME_CONFIG] event=RUNTIME_CONFIG_PERSISTED configuration_version=%d",
                new_version,
            )

            # 2. publish ConfigurationChanged
            event = ConfigChangeEvent(
                configuration_version=new_version,
                timestamp=_utcnow(),
                changed_sections=tuple(result.sections),
                changed_fields=tuple(result.changed_fields),
                source=source,
                correlation_id=cid,
            )
            self._last_event = event

            # 3. atomic swap (all new evaluations use the new snapshot)
            self._snapshot = snapshot
            self._last_apply_status = "APPLIED"
            self._last_apply_error = ""
            logger.info(
                "[RUNTIME_CONFIG] event=RUNTIME_CONFIG_APPLIED configuration_version=%d "
                "source=%s runtime_applied=YES",
                new_version,
                source,
            )

            # 4. notify subscribers (engine service re-sync, UI refresh, ...)
            self._notify(snapshot, event)

            return ConfigurationApplyReport(
                success=True,
                persisted=persisted,
                runtime_applied=True,
                configuration_version=new_version,
                correlation_id=cid,
                reason="",
                requested_fields=tuple(updates.keys()),
                previous_version=prev.version,
            )


def snapshot_to_flat(snap: RuntimeConfiguration) -> dict[str, Any]:
    """Flatten a snapshot to `section.field` keys for the persistent store.

    Only keys the runtime builder understands are persisted (extra snapshot
    observability fields such as model_version / hash / news tuning are NOT
    persisted — the rebuild path would reject them as unknown).
    """
    d = snap.to_dict()
    out: dict[str, Any] = {}
    for section in ("execution", "risk", "algo", "model", "news"):
        for key, value in d[section].items():
            if key == "effective_scope":
                continue
            flat_key = f"{section}.{key}"
            if not _is_known_flat_key(flat_key):
                continue
            out[flat_key] = value
    out["telegram.enabled"] = d["telegram"]["enabled"]
    out["rule_matrix.cache_ttl_seconds"] = d["rule_matrix"]["cache_ttl_seconds"]
    return out


def config_file_hash(path: Path | str) -> str:
    """SHA-256 of a live.yaml projection (for version diagnostics)."""
    p = Path(path)
    try:
        if not p.exists():
            return ""
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""
