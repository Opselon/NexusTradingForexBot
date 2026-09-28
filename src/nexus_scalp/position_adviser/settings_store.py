"""Position Adviser settings persistence — SQLite (TASK-POSA-005).

The adviser's OPERATIONAL SETTINGS live in the application settings database
(table ``application_settings``), never in PostgreSQL. This is the mandated
split: SQLite owns operator preferences + the selected adviser (small, hot,
must survive a restart and be readable before the audit domain is
provisioned); PostgreSQL owns the high-volume history (training runs,
advisories, registry — see ``position_adviser.store``).

Settings keys (all prefixed ``position_adviser.`` so the settings audit log
groups them):

    position_adviser.activation        DISABLED | PAPER | LIVE
    position_adviser.active_model_id   the selected checkpoint stem
    position_adviser.active_weights    repo-relative weights path
    position_adviser.active_scaler     repo-relative scaler path
    position_adviser.config            JSON: the bounded AdviserConfig
    position_adviser.auto_load         bool: rehydrate at startup
    position_adviser.settings_version  schema version of this module

Every write is versioned and audited through the settings DB's own machinery
(``SettingsDatabase.set`` writes a ``settings_audit`` row), so the operator
can see WHO changed the adviser state and WHEN. Reads degrade to defaults
when the settings DB is unavailable — the adviser must never fail to boot
because a preference could not be read.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.settings.service import SettingsDatabase

logger = get_logger("nexus_scalp.position_adviser.settings")

#: Bump when the persisted shape changes in a way an old reader cannot honour.
SETTINGS_VERSION = 1

_KEY_ACTIVATION = "position_adviser.activation"
_KEY_MODEL_ID = "position_adviser.active_model_id"
_KEY_WEIGHTS = "position_adviser.active_weights"
_KEY_SCALER = "position_adviser.active_scaler"
_KEY_CONFIG = "position_adviser.config"
_KEY_AUTO_LOAD = "position_adviser.auto_load"
_KEY_VERSION = "position_adviser.settings_version"

_DEFAULT_CONFIG: dict[str, Any] = {
    "max_hold_score_penalty": 25.0,
    "min_confidence_to_apply": 0.55,
    "min_action_advantage": 0.10,
    "min_eval_interval_sec": 2.0,
    "max_snapshot_age_sec": 5.0,
    "artifact_dir": "artifacts/position_adviser",
}


def _default_config() -> dict[str, Any]:
    """Fresh copy of the default config (never shared between instances)."""
    return dict(_DEFAULT_CONFIG)


@dataclass(frozen=True)
class AdviserSettings:
    """The persisted adviser selection + activation (POST-selection state)."""

    activation: str = "DISABLED"
    model_id: str = ""
    weights_path: str = ""
    scaler_path: str = ""
    auto_load: bool = True
    config: dict[str, Any] = field(default_factory=_default_config)

    @staticmethod
    def defaults() -> AdviserSettings:
        return AdviserSettings(config=_default_config())

    def to_dict(self) -> dict[str, Any]:
        return {
            "activation": self.activation,
            "model_id": self.model_id,
            "weights_path": self.weights_path,
            "scaler_path": self.scaler_path,
            "auto_load": self.auto_load,
            "config": dict(self.config or {}),
            "settings_version": SETTINGS_VERSION,
            "persisted": bool(self.model_id),
        }


class AdviserSettingsStore:
    """SQLite-backed adviser settings. Thread-safe; degrades to defaults.

    A ``None``/unreachable settings DB means the caller gets the documented
    defaults and every write becomes a no-op that logs — the adviser's
    fail-closed behaviour never depends on a preference being readable.
    """

    def __init__(self, db: SettingsDatabase | None = None) -> None:
        self._db = db
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ reads

    def _get(self, key: str, default: str) -> str:
        if self._db is None:
            return default
        try:
            sv = self._db.get(key)
        except Exception as exc:  # a corrupt/unreadable settings DB is not fatal
            logger.warning("[ADVISER] settings read failed key=%s err=%s", key, exc)
            return default
        return str(sv.value) if sv is not None else default

    def load(self) -> AdviserSettings:
        """Read the persisted adviser settings, or the defaults when absent."""
        with self._lock:
            cfg_raw = self._get(_KEY_CONFIG, "")
            cfg: dict[str, Any] = dict(_DEFAULT_CONFIG)
            if cfg_raw:
                try:
                    loaded = json.loads(cfg_raw)
                    if isinstance(loaded, dict):
                        # Only accept keys we know; unknown keys are ignored
                        # rather than trusted (a future/older writer may have
                        # a different vocabulary).
                        cfg.update({k: v for k, v in loaded.items() if k in _DEFAULT_CONFIG})
                except json.JSONDecodeError as exc:
                    logger.warning("[ADVISER] settings config JSON unreadable err=%s", exc)
            auto = self._get(_KEY_AUTO_LOAD, "1").lower() in ("1", "true", "yes")
            return AdviserSettings(
                activation=self._get(_KEY_ACTIVATION, "DISABLED"),
                model_id=self._get(_KEY_MODEL_ID, ""),
                weights_path=self._get(_KEY_WEIGHTS, ""),
                scaler_path=self._get(_KEY_SCALER, ""),
                auto_load=auto,
                config=cfg,
            )

    # ----------------------------------------------------------------- writes

    def _set(self, key: str, value: Any, *, value_type: str | None = None) -> bool:
        if self._db is None:
            return False
        try:
            self._db.set(key, value, value_type=value_type, source="WEB_UI", actor="web")
            return True
        except Exception as exc:
            logger.warning("[ADVISER] settings write failed key=%s err=%s", key, exc)
            return False

    def save_selection(
        self,
        *,
        model_id: str,
        weights_path: str,
        scaler_path: str,
        config: dict[str, Any] | None = None,
    ) -> bool:
        """Persist the operator's model selection (the restart-recovery input).

        Storing the WEIGHTS + SCALER PATHS (not just the id) means a restart
        can re-load the exact artifact without re-enumerating the directory
        or guessing which file a friendly id maps to.
        """
        with self._lock:
            ok_id = self._set(_KEY_MODEL_ID, model_id)
            ok_w = self._set(_KEY_WEIGHTS, weights_path)
            ok_s = self._set(_KEY_SCALER, scaler_path)
            if config is not None:
                self._set(_KEY_CONFIG, json.dumps(config, default=str), value_type="json")
            self._set(_KEY_VERSION, str(SETTINGS_VERSION), value_type="int")
            return bool(ok_id and ok_w and ok_s)

    def save_activation(self, activation: str) -> bool:
        """Persist the activation rung (DISABLED by default)."""
        with self._lock:
            return self._set(_KEY_ACTIVATION, str(activation))

    def save_config(self, config: dict[str, Any]) -> bool:
        with self._lock:
            return self._set(_KEY_CONFIG, json.dumps(config, default=str), value_type="json")

    def save_auto_load(self, enabled: bool) -> bool:
        with self._lock:
            return self._set(_KEY_AUTO_LOAD, enabled, value_type="bool")

    def set_config_value(self, key: str, value: Any) -> bool:
        """Patch ONE documented config key, preserving the rest.

        The routes expose individual policy fields (stale gate, decision
        thresholds) rather than the whole blob, so a partial edit must not drop
        the keys the caller did not send.
        """
        if key not in _DEFAULT_CONFIG:
            logger.warning("[ADVISER] settings: unknown config key %r ignored", key)
            return False
        with self._lock:
            current = self.load().config
            current[key] = value
            return self.save_config(current)

    def clear_selection(self) -> bool:
        """Forget the selection (used by Unload & Disable)."""
        with self._lock:
            self.save_activation("DISABLED")
            ok = self._set(_KEY_MODEL_ID, "")
            self._set(_KEY_WEIGHTS, "")
            self._set(_KEY_SCALER, "")
            return ok


__all__ = [
    "SETTINGS_VERSION",
    "AdviserSettings",
    "AdviserSettingsStore",
]
