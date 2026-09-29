"""ML Position Controller — runtime lifecycle state machine + persistence.

TASK-ML-CTRL §14/§15/§16/§25: activation is persisted in the authoritative
settings store (SettingsService / app_settings.db), survives restart, and
drives a real DISABLED → LOADING → ACTIVE / UNLOADING / FAILED /
RESTART_REQUIRED lifecycle. The React UI is never the source of truth.

Persistence keys (settings DB, HOT_RESTRICTED where hot-applicable):
    position_ml.enabled                  bool
    position_ml.model_id                 str
    position_ml.model_path               str
    position_ml.scaler_path              str
    position_ml.schema_version           str
    position_ml.activation_timestamp     float
    position_ml.controller_mode          str  ("ML" | "LEGACY")
    position_ml.previous_legacy_state    json (previous controller config, §19)

Restore-on-startup contract: if ``enabled`` is true, the exact persisted
model/scaler paths are reloaded, the schema version is validated against
:const:`POSITION_FEATURE_SCHEMA_VERSION`, and the ML controller + ownership
gate are restored BEFORE the first position-management tick. If validation
fails, the lifecycle enters FAILED with an explicit reason — legacy is NOT
silently reactivated (§17/§30).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.ownership import Controller, PositionOwnershipGate

logger = get_logger("nexus_scalp.position_adviser.ml_lifecycle")

_SETTINGS_PREFIX = "position_ml"


class MLLifecycleState(StrEnum):
    """Real model-runtime lifecycle (spec §16). DISABLED is the safe floor."""

    DISABLED = "DISABLED"
    LOADING = "LOADING"
    ACTIVE = "ACTIVE"
    UNLOADING = "UNLOADING"
    FAILED = "FAILED"
    RESTART_REQUIRED = "RESTART_REQUIRED"


@dataclass
class MLLifecycleRecord:
    """The authoritative persisted state (mirrors the settings DB keys)."""

    enabled: bool = False
    model_id: str = ""
    model_path: str = ""
    scaler_path: str = ""
    schema_version: str = ""
    controller_mode: str = Controller.LEGACY.value
    activation_timestamp: float = 0.0
    previous_legacy_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model_id": self.model_id,
            "model_path": self.model_path,
            "scaler_path": self.scaler_path,
            "schema_version": self.schema_version,
            "controller_mode": self.controller_mode,
            "activation_timestamp": self.activation_timestamp,
            "previous_legacy_state": dict(self.previous_legacy_state),
        }


class MLPositionControllerLifecycle:
    """Owns the ML runtime lifecycle. One instance per engine process."""

    def __init__(
        self,
        settings_service: Any,
        gate: PositionOwnershipGate,
        schema_version: str,
    ) -> None:
        self._settings = settings_service
        self._gate = gate
        self._schema_version = schema_version
        self._lock = threading.RLock()
        self.state = MLLifecycleState.DISABLED
        self.last_error: str = ""
        self.loaded_model_id: str = ""
        self.load_timestamp: float = 0.0
        self.last_inference_timestamp: float = 0.0
        #: Callbacks wired by the engine: loader returns a truthy handle on
        #: success or raises; unloader must release resources synchronously.
        self._model_loader = None
        self._model_unloader = None
        self.restore_from_persistence()

    # ------------------------------------------------------------ wiring

    def wire_model_runtime(self, loader: Any, unloader: Any) -> None:
        """Inject the artifact loader/unloader (avoids a torch import here)."""
        self._model_loader = loader
        self._model_unloader = unloader

    # -------------------------------------------------------- persistence

    def _persist(self, rec: MLLifecycleRecord) -> None:
        """Persist through the injected store (AdviserSettingsStore-compatible).

        The store's own keys (position_adviser.*) remain the single
        authoritative record — no parallel key family (§25).
        """
        store = self._settings
        # AdviserSettingsStore-shaped API (load/save_selection/save_activation)
        if hasattr(store, "save_selection") and hasattr(store, "save_activation"):
            store.save_selection(
                model_id=rec.model_id,
                weights_path=rec.model_path,
                scaler_path=rec.scaler_path,
            )
            store.save_activation(
                MLLifecycleState.ACTIVE.value if rec.enabled else MLLifecycleState.DISABLED.value
            )
            return
        # test/fake settings service: generic get/set
        p = _SETTINGS_PREFIX
        s = self._settings
        s.set(f"{p}.enabled", rec.enabled, value_type="bool", actor="ml_lifecycle")
        s.set(f"{p}.model_id", rec.model_id, actor="ml_lifecycle")
        s.set(f"{p}.model_path", rec.model_path, actor="ml_lifecycle")
        s.set(f"{p}.scaler_path", rec.scaler_path, actor="ml_lifecycle")
        s.set(f"{p}.schema_version", rec.schema_version, actor="ml_lifecycle")
        s.set(f"{p}.controller_mode", rec.controller_mode, actor="ml_lifecycle")
        s.set(
            f"{p}.activation_timestamp",
            rec.activation_timestamp,
            value_type="float",
            actor="ml_lifecycle",
        )
        import json

        s.set(
            f"{p}.previous_legacy_state",
            json.dumps(rec.previous_legacy_state, ensure_ascii=False),
            value_type="json",
            actor="ml_lifecycle",
        )

    def read_persisted(self) -> MLLifecycleRecord:
        store = self._settings
        rec = MLLifecycleRecord()
        if hasattr(store, "save_selection") and hasattr(store, "load"):
            s = store.load()
            rec.enabled = str(s.activation).upper() in ("PAPER", "LIVE")
            rec.model_id = s.model_id
            rec.model_path = s.weights_path
            rec.scaler_path = s.scaler_path
            rec.schema_version = self._schema_version
            rec.controller_mode = Controller.ML.value if rec.enabled else Controller.LEGACY.value
            return rec
        # generic get/set fallback (tests)
        p = _SETTINGS_PREFIX
        s = self._settings
        import json

        raw_prev = s.get(f"{p}.previous_legacy_state")
        try:
            prev = json.loads(raw_prev.value) if raw_prev and raw_prev.value else {}
        except Exception:
            prev = {}
        if (v := s.get(f"{p}.enabled")) is not None:
            rec.enabled = bool(v.value)
        if (v := s.get(f"{p}.model_id")) is not None:
            rec.model_id = str(v.value)
        if (v := s.get(f"{p}.model_path")) is not None:
            rec.model_path = str(v.value)
        if (v := s.get(f"{p}.scaler_path")) is not None:
            rec.scaler_path = str(v.value)
        if (v := s.get(f"{p}.schema_version")) is not None:
            rec.schema_version = str(v.value)
        if (v := s.get(f"{p}.controller_mode")) is not None:
            rec.controller_mode = str(v.value)
        if (v := s.get(f"{p}.activation_timestamp")) is not None:
            rec.activation_timestamp = float(v.value)
        rec.previous_legacy_state = prev if isinstance(prev, dict) else {}
        return rec

    # ------------------------------------------------------------- lifecycle

    def activate(self, *, model_id: str, model_path: str, scaler_path: str) -> dict[str, Any]:
        """Enable ML control: persist intent, load, validate, flip ownership."""
        with self._lock:
            if self.state is MLLifecycleState.ACTIVE:
                return {"status": "OK", "state": str(self.state), "message": "already active"}
            self.state = MLLifecycleState.LOADING
            self.last_error = ""
            rec = self.read_persisted()
            # §19: preserve the PREVIOUS legacy config before first takeover
            # only (never overwrite the preserved snapshot on re-activation).
            if not rec.previous_legacy_state:
                rec.previous_legacy_state = {
                    "controller": Controller.LEGACY.value,
                    "preserved_at": time.time(),
                }
            rec.enabled = True
            rec.model_id = model_id
            rec.model_path = model_path
            rec.scaler_path = scaler_path
            rec.schema_version = self._schema_version
            rec.controller_mode = Controller.ML.value
            rec.activation_timestamp = time.time()
            self._persist(rec)

            try:
                if self._model_loader is None:
                    raise RuntimeError("model runtime not wired (loader missing)")
                self._model_loader(model_path, scaler_path)
            except Exception as exc:
                self.state = MLLifecycleState.FAILED
                self.last_error = f"MODEL_LOAD_FAILED: {exc}"
                logger.error("[ML_CTRL] event=LOAD_FAILED err=%s", exc)
                return {"status": "FAILED", "reason": self.last_error}

            if rec.schema_version != self._schema_version:
                # §30: explicit rejection, no silent compatibility.
                self.state = MLLifecycleState.FAILED
                self.last_error = (
                    f"MODEL_LOAD_REJECTED: feature schema mismatch "
                    f"expected={self._schema_version} artifact={rec.schema_version}"
                )
                logger.error("[ML_CTRL] event=SCHEMA_MISMATCH %s", self.last_error)
                return {"status": "FAILED", "reason": self.last_error}

            self.loaded_model_id = model_id
            self.load_timestamp = time.time()
            self.state = MLLifecycleState.ACTIVE
            self._gate.set_ml_active(True)
            logger.info(
                "[ML_CTRL] event=ACTIVATED model=%s schema=%s", model_id, rec.schema_version
            )
            return {"status": "OK", "state": str(self.state), "model_id": model_id}

    def disable(self) -> dict[str, Any]:
        """Real disable: persist off, unload resources, restore LEGACY (§16)."""
        with self._lock:
            if self.state is MLLifecycleState.DISABLED:
                return {"status": "OK", "state": str(self.state), "message": "already disabled"}
            self.state = MLLifecycleState.UNLOADING
            try:
                if self._model_unloader is not None:
                    self._model_unloader()
            except Exception as exc:
                logger.warning("[ML_CTRL] event=UNLOAD_ERROR err=%s", exc)
            rec = self.read_persisted()
            rec.enabled = False
            rec.controller_mode = Controller.LEGACY.value
            self._persist(rec)
            self._gate.set_ml_active(False)
            self.loaded_model_id = ""
            self.state = MLLifecycleState.DISABLED
            logger.info("[ML_CTRL] event=DEACTIVATED legacy_restored=true")
            return {"status": "OK", "state": str(self.state)}

    def mark_restart_required(self, reason: str = "") -> dict[str, Any]:
        with self._lock:
            self.state = MLLifecycleState.RESTART_REQUIRED
            self.last_error = reason
            return {"status": "OK", "state": str(self.state)}

    def restore_from_persistence(self) -> dict[str, Any]:
        """Startup recovery: re-enable ML if (and only if) persisted enabled.

        Validation failure => FAILED state with the explicit reason; legacy is
        NOT silently restored (§17/§30) — the operator sees the failure.
        """
        with self._lock:
            rec = self.read_persisted()
            if not rec.enabled:
                self.state = MLLifecycleState.DISABLED
                self._gate.set_ml_active(False)
                return {"status": "OK", "state": str(self.state), "restored": False}
            if rec.schema_version != self._schema_version:
                self.state = MLLifecycleState.FAILED
                self.last_error = (
                    f"MODEL_LOAD_REJECTED on restart: schema expected="
                    f"{self._schema_version} artifact={rec.schema_version}"
                )
                # Ownership stays ML-persisted; legacy stays blocked (§17).
                self._gate.set_ml_active(True)
                self._gate.set_ml_health(False, self.last_error)
                return {"status": "FAILED", "reason": self.last_error}
            try:
                if self._model_loader is not None:
                    self._model_loader(rec.model_path, rec.scaler_path)
            except Exception as exc:
                self.state = MLLifecycleState.FAILED
                self.last_error = f"MODEL_LOAD_FAILED on restart: {exc}"
                self._gate.set_ml_active(True)
                self._gate.set_ml_health(False, self.last_error)
                return {"status": "FAILED", "reason": self.last_error}
            self.loaded_model_id = rec.model_id
            self.load_timestamp = time.time()
            self.state = MLLifecycleState.ACTIVE
            self._gate.set_ml_active(True)
            logger.info(
                "[ML_CTRL] event=RESTORED model=%s schema=%s", rec.model_id, rec.schema_version
            )
            return {"status": "OK", "state": str(self.state), "restored": True}

    def mark_inference(self) -> None:
        with self._lock:
            self.last_inference_timestamp = time.time()

    def status(self) -> dict[str, Any]:
        with self._lock:
            rec = self.read_persisted()
            return {
                "lifecycle_state": str(self.state),
                "persisted": rec.to_dict(),
                "loaded_model_id": self.loaded_model_id,
                "load_timestamp": self.load_timestamp,
                "last_inference_timestamp": self.last_inference_timestamp,
                "last_error": self.last_error,
                "restart_required": self.state is MLLifecycleState.RESTART_REQUIRED,
                "controller_mode": rec.controller_mode
                if self.state is MLLifecycleState.ACTIVE
                else Controller.LEGACY.value,
            }
