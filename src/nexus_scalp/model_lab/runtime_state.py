"""Neural Studio runtime state machine (Phase 3 / 35 / 52).

WHY THIS EXISTS
---------------
"Model unavailable", "Active Runtime Champion train_studio_1789951009_70d" and
"ENGINE: STOPPED" can be simultaneously true, and the UI had no vocabulary for
it. The old single-field derivation made LOADED, READY, INFERENCE AVAILABLE and
ENGINE RUNNING four readings of one ``activeModel`` object.

These are independent facts:

    ENGINE       — is the live engine process running and healthy?
    MODEL        — is a model loaded into the inference slot?
    INFERENCE    — can that model actually produce a decision right now?

Each has its own probe and its own vocabulary. A model can be LOADED while
INFERENCE is BLOCKED (engine stopped, or the loaded model is a canary with no
scaler). A champion in the registry is not the runtime model. This module is the
single place that resolves the three, so the header, the registry panel and the
API all read the same verdict.

Model lifecycle states (Phase 3) are deliberately NOT overloaded onto these:
    DISCOVERED → TRAINING → TRAINED → VERIFIED → SERVABLE →
    LOADED → WARMING → READY → ACTIVE → STANDBY → FAILED → RETIRED
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Model lifecycle states (Phase 3) — one meaning per state, no field carries two.
MODEL_LIFECYCLE_STATES: tuple[str, ...] = (
    "DISCOVERED",   # checkpoint seen on disk, not yet validated
    "TRAINING",     # a fit is in progress
    "TRAINED",      # fit finished, artifact written
    "VERIFIED",     # passed the verify battery
    "SERVABLE",     # artifact + scaler + manifest all validated
    "NOT_LOADED",   # no model resident in the inference slot
    "LOADED",       # weights resident in the inference slot
    "WARMING",      # warmup forward in flight
    "READY",        # warmup passed; inference is possible
    "ACTIVE",       # the runtime's current serving model
    "STANDBY",      # loaded but not serving (e.g. hot-load previous model)
    "FAILED",       # load/train/verify failed
    "RETIRED",      # operator retired; not eligible to serve
)

ENGINE_STATES: tuple[str, ...] = (
    "RUNNING",      # process up and healthy
    "DEGRADED",     # up but a subsystem reports unhealthy
    "STOPPED",      # no engine process
    "UNKNOWN",      # could not probe
)

INFERENCE_STATES: tuple[str, ...] = (
    "READY",        # a loaded, warm model can produce a decision
    "BLOCKED",      # model loaded but inference cannot run (engine stopped)
    "WARMING",      # model loaded, warmup not yet confirmed
    "NOT_LOADED",   # no model in the slot
    "FAILED",       # the loaded model failed its smoke inference
    "UNAVAILABLE",  # probe itself failed — never silently "ready"
)


@dataclass(frozen=True)
class RuntimeState:
    """The three independent runtime facts plus their evidence."""

    engine_state: str
    model_state: str
    inference_state: str
    # Evidence — every non-RUNNING/READY verdict carries the reason it was set.
    engine_detail: str
    model_detail: str
    inference_detail: str
    runtime_model_id: str
    runtime_dimension: int | None
    runtime_schema_id: str
    selected_model_id: str
    selected_dimension: int | None
    selected_schema_id: str
    device: str
    inference_available: bool
    engine_running: bool
    model_loaded: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["model_lifecycle_states"] = list(MODEL_LIFECYCLE_STATES)
        d["engine_states"] = list(ENGINE_STATES)
        d["inference_states"] = list(INFERENCE_STATES)
        return d


def _schema_id_for_dimension(dimension: int | None) -> str:
    if dimension == 50:
        return "scalp_v1"
    if dimension == 70:
        return "scalp_v3"
    return ""


def _safe_int(value: Any) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out in (50, 70) else None


def resolve_runtime_state(
    engine: Any = None,
    selected_model_id: str = "",
    selected_model_record: Any = None,
    hot_loaded_bundle: Any = None,
) -> RuntimeState:
    """Resolve the three independent runtime facts.

    Parameters are the owners of each fact, passed in — never a container that
    merely holds someone else's attribute:

    * ``engine``                — the live engine (or None)
    * ``selected_model_record`` — the registry row the operator selected
    * ``hot_loaded_bundle``     — the in-memory inference slot

    The runtime model identity is read from the SLOT and from the registry's
    current champion, never from a container attribute (the wiring-lesson class:
    reading ``getattr(bundle.model, "model_id")`` returns "" because a torch
    module carries no model_id).
    """
    # ---------------------------------------------------------------- engine
    if engine is None:
        engine_state = "STOPPED"
        engine_detail = "no live engine process is attached to the web app"
    else:
        healthy = _probe_engine_health(engine)
        if healthy is None:
            engine_state = "UNKNOWN"
            engine_detail = "engine health probe raised; treating as unknown"
        elif healthy:
            engine_state = "RUNNING"
            engine_detail = "engine process is up and reports healthy"
        else:
            engine_state = "DEGRADED"
            engine_detail = "engine process is up but reports unhealthy"

    # ---------------------------------------------------------------- model
    runtime_model_id = ""
    runtime_dimension: int | None = None
    device = "cpu"

    if hot_loaded_bundle is not None:
        runtime_model_id = str(getattr(hot_loaded_bundle, "model_id", "") or "")
        runtime_dimension = _safe_int(getattr(hot_loaded_bundle, "dimension", None))
        model = getattr(hot_loaded_bundle, "model", None)
        if model is not None:
            try:
                device = str(next(model.parameters()).device)
            except (StopIteration, RuntimeError):
                device = "cpu"
        model_state = "LOADED"
        model_detail = f"model {runtime_model_id or 'unnamed'} is resident in the inference slot"
    elif selected_model_record is not None:
        # Nothing in the slot; the registry champion is the closest truth.
        runtime_model_id = str(getattr(selected_model_record, "id", "") or "")
        runtime_dimension = _safe_int(getattr(selected_model_record, "dimension", None))
        model_state = "STANDBY"
        model_detail = (
            f"registry champion {runtime_model_id or 'none'} is not resident in the "
            "inference slot"
        )
    else:
        model_state = "NOT_LOADED"
        model_detail = "no model is resident in the inference slot and no champion is set"

    runtime_schema_id = _schema_id_for_dimension(runtime_dimension)

    # ------------------------------------------------------------ inference
    # A loaded model can still be unable to infer: the engine is stopped, or the
    # scaler is absent. These are separate verdicts from "is it loaded".
    #
    # ``engine is None`` means the web layer is not attached to a live engine at
    # all (the studio running against a bare web process). That is NOT an
    # inference-permitting state — a studio that reports READY while nothing can
    # actually produce a live decision is the exact conflation this module
    # exists to end. Only a RUNNING (or absent-but-explicitly-standalone) engine
    # permits inference.
    if hot_loaded_bundle is None:
        inference_state = "NOT_LOADED"
        inference_detail = model_detail
    elif engine is None:
        inference_state = "BLOCKED"
        inference_detail = (
            f"model {runtime_model_id} is loaded but no live engine is attached, "
            "so no live decision can be produced"
        )
    elif engine_state == "STOPPED":
        inference_state = "BLOCKED"
        inference_detail = (
            f"model {runtime_model_id} is loaded but the engine is stopped, so "
            "no live decision can be produced"
        )
    elif engine_state == "DEGRADED":
        inference_state = "BLOCKED"
        inference_detail = (
            f"model {runtime_model_id} is loaded but the engine is degraded; "
            "inference is withheld"
        )
    elif engine_state == "UNKNOWN":
        inference_state = "BLOCKED"
        inference_detail = (
            f"model {runtime_model_id} is loaded but the engine health probe "
            "failed; inference is withheld until the engine state is known"
        )
    else:
        scaler = getattr(hot_loaded_bundle, "scaler", None)
        scaler_ready = bool(getattr(scaler, "is_ready", lambda: False)())
        if not scaler_ready:
            inference_state = "BLOCKED"
            inference_detail = (
                f"model {runtime_model_id} is loaded but its scaler is not ready; "
                "inference would run on unnormalized features"
            )
        else:
            inference_state = "READY"
            inference_detail = (
                f"model {runtime_model_id} ({runtime_dimension}D) is loaded, warm "
                "and scaler-attached; inference can produce a decision"
            )

    # ---------------------------------------------------------- selection
    sel_id = str(selected_model_id or "").strip()
    sel_dimension: int | None = None
    if selected_model_record is not None:
        sel_dimension = _safe_int(getattr(selected_model_record, "dimension", None))
    if sel_id and sel_dimension is None and hot_loaded_bundle is not None:
        # A selection that matches the runtime model inherits its dimension.
        if sel_id == runtime_model_id:
            sel_dimension = runtime_dimension

    selected_schema_id = _schema_id_for_dimension(sel_dimension)

    return RuntimeState(
        engine_state=engine_state,
        model_state=model_state,
        inference_state=inference_state,
        engine_detail=engine_detail,
        model_detail=model_detail,
        inference_detail=inference_detail,
        runtime_model_id=runtime_model_id,
        runtime_dimension=runtime_dimension,
        runtime_schema_id=runtime_schema_id,
        selected_model_id=sel_id,
        selected_dimension=sel_dimension,
        selected_schema_id=selected_schema_id,
        device=device,
        inference_available=inference_state == "READY",
        engine_running=engine_state == "RUNNING",
        model_loaded=model_state in ("LOADED", "WARMING", "STANDBY", "ACTIVE"),
    )


def _probe_engine_health(engine: Any) -> bool | None:
    """Return True/False for health, or None if the probe itself failed."""
    probe = getattr(engine, "is_healthy", None)
    if callable(probe):
        try:
            return bool(probe())
        except Exception:
            return None
    return None
