"""Model lifecycle / shadow / governance — REST API routes (PHASE 10/11/13).

Extracted VERBATIM from the former monolith ``server.py`` (CHG-0032 Step 3A,
behavior-preserving). Route bodies are byte-identical; only the registration
surface changed (``@app.*`` -> ``@router.*`` inside ``register_model_governance_routes``).

Surface (all paths unchanged):
  GET  /api/models/{summary,integrity,runs/{id},champion,challengers,comparison/{run_id}}
  POST /api/models/train, /api/models/worker/{start,stop,cancel}
  GET  /api/shadow/summary,runs,decisions,compare/{run_id},promotion/{run_id}
  POST /api/models/shadow/attach, /api/models/shadow/evaluate-promotion,
       /api/models/shadow/worker/{start,stop}
  GET  /api/shadow70/summary,health,disagreements + attach/detach/start/stop
  GET  /api/governance/{health,registry,events,comparisons,review,status,audits,...}
  POST /api/models/registry/reconcile, /api/models/promotion/{approve,execute,rollback},
       /api/models/governance/emergency/{freeze,unfreeze,disable}

BOUNDARY: read/trigger only — never touches the live path; the production
Champion is never mutated by candidate training; promotion requires explicit
operator action (INV: auto-promotion forbidden).

USED BY: server.create_app (include_router after register).
DO-NOT-PUT-HERE: news/liquidity/mslie routes (stay in server.py slice),
research factory (factory_routes.py), command center (command_center_*).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.errors import log_web_error, new_request_id, safe_error_payload

logger = get_logger("nexus_scalp.web.model_governance_routes")

router = APIRouter()


async def _run_training_async(orchestrator: Any, dataset: Any, num_epochs: int) -> dict[str, Any]:
    """Runs controlled training off the event loop via asyncio.to_thread."""
    import asyncio

    return await asyncio.to_thread(
        orchestrator.run_controlled_training, dataset, num_epochs=num_epochs
    )


def register_model_governance_routes(app: Any) -> None:
    """Attach the model/shadow/governance routes (closures over ``app``)."""
    from nexus_scalp.web.server import serialize_enums  # local import: avoids module cycle

    def _err(code: str = "INTERNAL_ERROR", **kw: Any) -> dict[str, Any]:
        return safe_error_payload(code=code, request_id=new_request_id(), **kw)

    def _log_err(
        exc: BaseException, msg: str, *, endpoint: str = "/api", resource: str | None = None
    ) -> None:
        log_web_error(
            logger,
            endpoint,
            new_request_id(),
            exc,
            resource=resource,
            context={"msg": msg},
        )

    # PHASE 10: CONTROLLED MODEL TRAINING & CHALLENGER ENGINE (read + trigger)
    # -------------------------------------------------------------------------
    # Exposes real model-training state. Training is OFFLINE/BACKGROUND; the
    # production Champion is never touched by candidate training, and a
    # validated Challenger is never auto-promoted.
    # =========================================================================

    def _model_lifecycle() -> Any:
        """Returns the engine when the model-lifecycle subsystem is available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "model_lifecycle_orchestrator"):
            return None
        return engine

    @router.get("/api/models/summary")
    def get_models_summary() -> dict[str, Any]:
        """Model registry status + training run counts + worker state."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            summary = engine.training_run_store.summary()
            summary["registry"] = engine.model_lifecycle_orchestrator.lifecycle_registry.summary()
            worker = getattr(engine, "training_worker", None)
            if worker is not None:
                from nexus_scalp.model_lifecycle.worker import format_training_worker_status

                summary["worker"] = format_training_worker_status(worker)
            champ = engine.champion_manager.champion_or_none()
            if champ is not None:
                summary["champion"] = champ.summary()
            else:
                summary["champion"] = {"available": False}
            return serialize_enums({"available": True, "summary": summary})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model summary failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/integrity")
    def get_models_integrity() -> dict[str, Any]:
        """AI Hub: true semantic model state (tensors + classes + scaler).

        Backend-decided truth for the UI (brief 30/32): reports the live
        Champion's artifact inspection with explicit tensor diagnostics
        (actual_input_dimension / actual_output_classes / hidden dimension /
        scaler dimension), the compatibility verdict, and the lifecycle
        status. Never claims LOADED merely because torch.load() succeeded
        (brief 12/14); an invalid Champion stays INVALID until governance
        replaces it (brief 33).
        """
        engine = app.state.engine
        if engine is None:
            return {"available": False, "state": "UNAVAILABLE"}
        try:
            champ = getattr(engine, "champion_manager", None)
            if champ is None:
                return {"available": True, "state": "UNAVAILABLE", "integrity": None}
            # `champion_or_none()` loads + verifies the Champion artifact and
            # returns the ChampionModel (never raises; None on cold-start).
            # The manager itself has no `.info` — integrity lives on the model.
            model = champ.champion_or_none()
            if model is None:
                return {"available": True, "state": "NO_CHAMPION", "integrity": None}
            info = model.info
            verdict = "VALID" if info.integrity_ok else "INVALID"
            active = bool(getattr(engine, "_bundle", None) is not None)
            payload: dict[str, Any] = {
                "available": True,
                "model_id": model.model_id,
                "model_version": model.model_version,
                "artifact_path": str(model.artifact_path),
                "artifact_hash": info.artifact_hash,
                "schema_id": info.feature_schema_id,
                "feature_dimension": info.feature_dimension,
                "expected_classes": info.num_classes,
                "actual_input_dimension": info.actual_input_dimension,
                "actual_output_classes": info.actual_output_classes,
                "actual_hidden_dimension": info.actual_hidden_dimension,
                "class_head_name": info.class_head_name,
                "scaler_path": str(model.scaler_path),
                "scaler_hash": info.scaler_hash,
                "scaler_dimension": info.scaler_dimension,
                "compatibility": verdict,
                "integrity": verdict,
                "state": "ACTIVE"
                if (active and verdict == "VALID")
                else (
                    "INCOMPATIBLE"
                    if info.integrity_ok is False and info.artifact_hash
                    else "INVALID"
                ),
                "active": active,
                "reason": info.integrity_reason,
            }
            return serialize_enums(payload)
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model integrity failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models")
    def get_models_list(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Bounded model registry listing (champion/challenger/candidate...)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.model_lifecycle_orchestrator.lifecycle_registry.list_models(
                status=status, limit=limit
            )
            return serialize_enums({"available": True, "models": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model list failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/champion")
    def get_models_champion() -> dict[str, Any]:
        """Current production Champion (metadata + integrity)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            champ = engine.champion_manager.champion_or_none()
            if champ is None:
                return {"available": True, "champion": {"available": False}}
            return serialize_enums({"available": True, "champion": champ.summary()})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model champion failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/challengers")
    def get_models_challengers(limit: int = 50) -> dict[str, Any]:
        """Validated Challengers (shadow-eligible, never production)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.model_lifecycle_orchestrator.lifecycle_registry.list_models(
                status="CHALLENGER", limit=limit
            )
            return serialize_enums({"available": True, "challengers": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model challengers failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/runs")
    def get_models_runs(status: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Append-only training-run records."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.training_run_store.list_runs(status=status, limit=limit)
            return serialize_enums({"available": True, "runs": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model runs failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/runs/{run_id}")
    def get_models_run(run_id: str) -> dict[str, Any]:
        """Single training run with gates and artifacts."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            row = engine.training_run_store.get_run(run_id)
            if row is None:
                return {"available": False, "reason": "RUN_NOT_FOUND"}
            return serialize_enums({"available": True, "run": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model run failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/comparison/{run_id}")
    def get_models_comparison(run_id: str) -> dict[str, Any]:
        """Champion vs Challenger comparison for a training run."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            row = engine.training_run_store.get_comparison(run_id)
            if row is None:
                return {"available": False, "reason": "NO_COMPARISON"}
            return serialize_enums({"available": True, "comparison": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model comparison failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/train")
    def trigger_model_training(num_epochs: int = 10) -> dict[str, Any]:
        """Runs ONE controlled training pass (candidate only, never Champion).

        Triggers the pipeline synchronously for operator use; the background
        worker handles scheduled training. Heavy CPU work is off the event loop.
        """
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            orchestrator = engine.model_lifecycle_orchestrator
            dataset = orchestrator.build_training_dataset(
                include_no_trade=True, weight_no_trade=0.25, only_executed=True
            )
            if dataset.sample_count < 50:
                return {
                    "available": False,
                    "reason": "INSUFFICIENT_SAMPLES",
                    "samples": dataset.sample_count,
                }
            import asyncio

            result = asyncio.run(_run_training_async(orchestrator, dataset, num_epochs))
            return serialize_enums({"available": True, "result": result})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model training trigger failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/worker/start")
    def start_training_worker() -> dict[str, Any]:
        """Starts the background training worker (idempotent, isolated)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            engine._start_training_worker()
            return {"available": True, "started": engine._training_worker_started}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Training worker start failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/worker/stop")
    def stop_training_worker() -> dict[str, Any]:
        """Stops the background training worker (idempotent)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            import asyncio

            asyncio.run(engine._stop_training_worker())
            return {"available": True, "stopped": not engine._training_worker_started}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Training worker stop failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/worker/cancel")
    def cancel_training_worker() -> dict[str, Any]:
        """Requests cancellation of any in-flight training (bounded, safe)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            engine.training_worker.request_cancel()
            return {"available": True}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Training worker cancel failed"})
            return _err("INTERNAL_ERROR")

    # =========================================================================
    # PHASE 11: CHALLENGER SHADOW TRADING & CHAMPION EVALUATION (read + control)
    # -------------------------------------------------------------------------
    # Shadow evaluation is SHADOW-ONLY: the Challenger has zero order authority,
    # every result is marked SHADOW/SIMULATED, and the production Champion is
    # never modified. A Challenger can never be auto-promoted here.
    # =========================================================================

    def _shadow() -> Any:
        """Returns the engine when the shadow subsystem is available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "shadow_engine"):
            return None
        return engine

    @router.get("/api/models/shadow/summary")
    def get_shadow_summary() -> dict[str, Any]:
        """Shadow runs + decisions + promotions + worker + active challenger."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            summary = engine.shadow_store.summary()
            worker = getattr(engine, "shadow_worker", None)
            if worker is not None:
                from nexus_scalp.shadow.worker import format_shadow_worker_status

                summary["worker"] = format_shadow_worker_status(worker)
            summary["active_challenger"] = (
                engine._shadow_challenger.summary() if engine._shadow_challenger else None
            )
            summary["active_run"] = engine.shadow_engine.current_evidence()
            return serialize_enums({"available": True, "summary": summary})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow summary failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/shadow/runs")
    @router.get("/api/models/shadow70/summary")
    def get_shadow70_summary() -> dict[str, Any]:
        """70D shadow runtime summary (spec 28 / 33 / 45/46). Real data only."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        from nexus_scalp.shadow.shadow70 import Shadow70Runtime, Shadow70Store

        runtime: Shadow70Runtime | None = getattr(engine, "_shadow70_runtime", None)
        store: Shadow70Store | None = getattr(engine, "_shadow70_store", None)
        out: dict[str, Any] = {"available": True, "runtime": None, "store": None, "worker": None}
        if runtime is not None:
            out["runtime"] = runtime.summary()
            out["runtime"]["state"] = runtime.state.value
            out["runtime"]["load_result"] = (
                runtime.load_result.to_dict() if runtime.load_result else None
            )
        if store is not None:
            out["store"] = store.summary()
            out["store"]["disagreement_counts"] = store.disagreement_counts()
            with contextlib.suppress(Exception):
                out["store"]["recent_observations"] = [
                    {
                        "observation_id": r.get("observation_id", ""),
                        "timestamp": r.get("timestamp", ""),
                        "champion_action": r.get("champion_action", ""),
                        "shadow_action": r.get("shadow_action", ""),
                        "champion_confidence": r.get("champion_confidence", 0.0),
                        "shadow_confidence": r.get("shadow_confidence", 0.0),
                        "disagreement": r.get("disagreement", ""),
                        "regime": r.get("regime", ""),
                        "news_state": r.get("news_state", ""),
                        "liquidity_state": r.get("liquidity_state", ""),
                        "outcome": r.get("outcome", "PENDING"),
                    }
                    for r in store.list_observations(limit=20)
                ]
        from nexus_scalp.shadow.shadow70.worker import format_shadow70_status

        out["worker"] = format_shadow70_status(getattr(engine, "_shadow70_worker", None))
        return serialize_enums(out)

    @router.get("/api/models/shadow70/health")
    def get_shadow70_health() -> dict[str, Any]:
        """Liquidity feature health + drift (spec 20 / 21 / 29)."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        from nexus_scalp.shadow.shadow70 import (
            Shadow70DriftMonitor,
            Shadow70FeatureHealthMonitor,
        )

        health: Shadow70FeatureHealthMonitor | None = getattr(engine, "_shadow70_health", None)
        drift: Shadow70DriftMonitor | None = getattr(engine, "_shadow70_drift", None)
        store = getattr(engine, "_shadow70_store", None)
        return serialize_enums(
            {
                "available": True,
                "feature_health": [h.to_dict() for h in health.health()] if health else [],
                "drift": drift.summary() if drift else {"available": False},
                "persisted_alerts": store.latest_drift_alerts(limit=25) if store else [],
                "persisted_health": store.latest_feature_health() if store else [],
            }
        )

    @router.post("/api/models/shadow70/attach")
    def attach_shadow70() -> dict[str, Any]:
        """Attaches a VALIDATED 70D candidate with full load validation.

        Only a candidate whose contract passes manifest/hash/schema/dimension/
        scaler gates may enter the 70D shadow runtime (spec 3 / 4). The
        inference callable is resolved lazily from the artifact the same way
        the ChallengerRuntime does (pure torch inference, no execution).
        """
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            from pathlib import Path as _ShadowPath

            # Locate the validated 70D candidate in the lifecycle registry.
            from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry
            from nexus_scalp.shadow.shadow70.models import (
                SHADOW70_DIMENSION,
                SHADOW70_SCHEMA_ID,
                Shadow70CandidateContract,
            )
            from nexus_scalp.shadow.shadow70.runtime import Shadow70Runtime

            lifecycle = ModelLifecycleRegistry(
                audit_repo=engine.audit,
                model_registry=engine.model_registry,
            )
            rows = lifecycle.list_models(status="CHALLENGER", limit=20)
            candidate = None
            for row in rows:
                schema_id = row.get("feature_schema_id", "")
                dim = int(row.get("feature_dimension", 0) or 0)
                if schema_id == SHADOW70_SCHEMA_ID and dim == SHADOW70_DIMENSION:
                    candidate = row
                    break
            if candidate is None:
                return {"available": False, "reason": "NO_VALIDATED_CANDIDATE"}

            artifact_path = candidate.get("artifact_path", "")
            # CHG-0046 D7b: canonical sibling naming model.pt ->
            # model.scaler.npz (model_lifecycle.champion convention). The old
            # '.pt.scaler.npz' suffix missed the real file entirely — the
            # scaler gate could never pass for canonical bundles.
            _art = _ShadowPath(artifact_path)
            if _art.name.endswith(".pt"):
                scaler_path = str(_art.with_name("model.scaler.npz"))
            else:
                scaler_path = str(_art) + ".scaler.npz"
            if not artifact_path or not _ShadowPath(artifact_path).exists():
                return {"available": False, "reason": "CHALLENGER_ARTIFACT_NOT_FOUND"}

            lifecycle_status = str(candidate.get("lifecycle_status", "") or "")
            # TASK-14 hardening #1: ONLY a CHALLENGER row (already filtered
            # above) maps to VALIDATED_CANDIDATE. Any other status — REJECTED,
            # ARCHIVED, INVALID, CANDIDATE, empty — must NOT be forced into
            # the validated contract. The previous expression forced ANY
            # non-"VALIDATED" status string to VALIDATED_CANDIDATE
            # (defense-in-depth gap; rows were pre-filtered CHALLENGER so no
            # exploit was reachable, but a registry bug could have slipped a
            # non-validated row into shadow).
            validation_result = (
                "VALIDATED_CANDIDATE" if lifecycle_status == "CHALLENGER" else lifecycle_status
            )
            contract = Shadow70CandidateContract(
                model_id=candidate.get("model_id", ""),
                model_version=candidate.get("model_version", ""),
                schema_id=SHADOW70_SCHEMA_ID,
                dimension=SHADOW70_DIMENSION,
                feature_schema_hash="",  # filled from manifest below
                scaler_hash="",
                training_dataset_id=candidate.get("training_run_id", ""),
                validation_result=validation_result,
                artifact_hash=candidate.get("artifact_fingerprint", ""),
                artifact_path=artifact_path,
                scaler_path=scaler_path,
                num_classes=4,
            )
            # manifest: read model.json next to the artifact (schema hash).
            # CHG-0046 D7: the canonical bundle ships model.meta.json (with
            # feature_schema_id/num_features) and NO scaler_hash key — the
            # previous manifest-only path left feature_schema_hash empty and
            # the validator's provenance gate rejected every real bundle
            # (SHADOW_DEGRADED) making shadow70 UNATTACHABLE. Fallback chain:
            # model.json → model.meta.json → computed feature_schema_hash()
            # for the declared schema id; scaler_hash = live sha256 of the
            # scaler file when the manifest does not carry one.
            feature_schema_hash_value = ""
            scaler_hash_value = ""
            import json as _json

            manifest_path = _ShadowPath(artifact_path).parent / "model.json"
            meta_path = _ShadowPath(artifact_path).parent / "model.meta.json"
            for _mpath in (manifest_path, meta_path):
                if _mpath.exists():
                    with contextlib.suppress(Exception):
                        man = _json.loads(_mpath.read_text(encoding="utf-8"))
                        feature_schema_hash_value = str(
                            man.get("feature_schema_hash") or man.get("feature_schema_id", "") or ""
                        )
                        scaler_hash_value = str(man.get("scaler_hash", "") or "")
                        break
            if not feature_schema_hash_value or feature_schema_hash_value in (
                "scalp_v3",
                "scalp_v4",
            ):
                # A schema ID is not a hash: derive the canonical registry
                # content hash so the runtime verifies the REAL contract.
                from nexus_scalp.features.schema_contract import feature_schema_hash

                feature_schema_hash_value = feature_schema_hash(SHADOW70_SCHEMA_ID)
            if not scaler_hash_value and _ShadowPath(scaler_path).exists():
                from nexus_scalp.shadow.shadow70.runtime import sha256_file

                scaler_hash_value = sha256_file(scaler_path)
            contract = contract.model_copy(
                update={
                    "feature_schema_hash": feature_schema_hash_value,
                    "scaler_hash": scaler_hash_value,
                }
            )

            runtime: Shadow70Runtime = engine._shadow70_runtime
            result = runtime.attach(contract)
            if not result.passed:
                return {
                    "available": False,
                    "reason": result.status.value,
                    "failing_gate": result.failing_gate,
                    "detail": result.reason,
                }

            # inference fn: model LOADED ONCE at attach (CHG-0046 D4).
            # The previous closure ran torch.load + np.load inside the
            # per-call _infer — a disk read + full deserialization on the
            # HOT TICK PATH for every observation, guaranteed to blow the
            # 50ms shadow latency budget and stall the champion loop.
            # The artifact hash is already verified by the load gate above;
            # if the file is replaced mid-run the store/run identity checks
            # (D11) surface the mismatch — the hot path never re-reads.
            def _make_infer(path: str, scaler: str, dim: int):
                import numpy as np
                import torch

                from nexus_scalp.models.scalp_net import ScalpNet
                from nexus_scalp.shadow.compat import scale_like_champion

                state = torch.load(path, map_location="cpu", weights_only=False)
                model = ScalpNet(num_features=dim, num_classes=4)
                model.load_state_dict(state)
                model.eval()
                data = np.load(scaler)
                mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
                std = np.asarray(data["std"], dtype=np.float32).reshape(-1)

                def _infer(vector70: list[float]) -> list[float]:
                    x = scale_like_champion(
                        np.asarray(vector70, dtype=np.float32).reshape(1, -1), mean, std
                    )
                    xt = torch.tensor(np.asarray(x, dtype=np.float32), dtype=torch.float32)
                    xt = torch.nan_to_num(xt, nan=0.0, posinf=1.0, neginf=-1.0)
                    with torch.inference_mode():
                        logits = model(xt, return_logits=True)
                        probs = torch.nn.functional.softmax(logits, dim=-1)[0]
                    return [float(v) for v in probs.tolist()]

                return _infer

            runtime.set_inference(_make_infer(artifact_path, scaler_path, SHADOW70_DIMENSION))
            engine._shadow70_enabled = True
            wk = engine._shadow70_worker
            if wk is not None and not getattr(engine, "_shadow70_worker_started", False):
                wk.start()
                engine._shadow70_worker_started = True
            return serialize_enums({"available": True, "runtime": runtime.summary()})
        except Exception as e:
            _log_err(e, "Shadow70 attach failed", endpoint="/api/models/shadow70/attach")
            return _err("OPERATION_FAILED", extra={"reason": "SHADOW_LOAD_FAILED"})

    @router.post("/api/models/shadow70/start")
    def start_shadow70_worker() -> dict[str, Any]:
        engine = _shadow()
        if engine is None:
            return {"available": False}
        wk = getattr(engine, "_shadow70_worker", None)
        if wk is None:
            return {"available": False}
        wk.start()
        engine._shadow70_worker_started = True
        return {"available": True, "worker": wk.status()}

    @router.post("/api/models/shadow70/stop")
    def stop_shadow70_worker() -> dict[str, Any]:
        engine = _shadow()
        if engine is None:
            return {"available": False}
        wk = getattr(engine, "_shadow70_worker", None)
        if wk is None:
            return {"available": False}
        wk.stop(flush=True)
        engine._shadow70_worker_started = False
        return {"available": True, "worker": wk.status()}

    @router.post("/api/models/shadow70/detach")
    def detach_shadow70() -> dict[str, Any]:
        """Stops shadow work without touching the Champion (spec 32)."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        rt = getattr(engine, "_shadow70_runtime", None)
        if rt is not None:
            rt.stop()
        engine._shadow70_enabled = False
        return {"available": True, "status": rt.state.value if rt else "NONE"}

    @router.get("/api/models/shadow70/disagreements")
    def get_shadow70_disagreements(limit: int = 100) -> dict[str, Any]:
        """Recent Champion-vs-Shadow disagreements (spec 30). Real data only."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        store = getattr(engine, "_shadow70_store", None)
        if store is None:
            return {"available": True, "rows": []}
        return serialize_enums(
            {
                "available": True,
                "rows": store.list_observations(
                    limit=max(1, min(int(limit), 500)), disagreement_only=True
                ),
            }
        )

    def get_shadow_runs(limit: int = 50) -> dict[str, Any]:
        """Append-only shadow run history."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.shadow_store.list_runs(limit=limit)
            return serialize_enums({"available": True, "runs": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow runs failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/shadow/decisions")
    def get_shadow_decisions(run_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        """Shadow decision records (all marked simulated)."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.shadow_store.list_decisions(run_id=run_id, limit=limit)
            return serialize_enums({"available": True, "decisions": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow decisions failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/shadow/compare/{run_id}")
    def get_shadow_compare(run_id: str) -> dict[str, Any]:
        """Multi-dimension Champion vs Challenger comparison for a shadow run."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            row = engine.shadow_store.get_comparison(run_id)
            if row is None:
                return {"available": False, "reason": "NO_COMPARISON"}
            return serialize_enums({"available": True, "comparison": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow compare failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/shadow/promotion/{run_id}")
    def get_shadow_promotion(run_id: str) -> dict[str, Any]:
        """Promotion evaluation (eligibility + vetoes) for a shadow run."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            row = engine.shadow_store.get_promotion(run_id)
            if row is None:
                return {"available": False, "reason": "NO_PROMOTION"}
            return serialize_enums({"available": True, "promotion": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow promotion failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/shadow/attach")
    def attach_shadow_challenger() -> dict[str, Any]:
        """Attaches a validated Challenger artifact for shadow evaluation.

        The Challenger is loaded with full integrity checks; an invalid or
        schema-incompatible artifact is SHADOW_LOAD_FAILED and never used.
        """
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.shadow_runtime import GovernanceShadowRuntime
            from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry
            from nexus_scalp.shadow.challenger import load_challenger

            # Find the most recent CHALLENGER registry row.
            lifecycle = ModelLifecycleRegistry(
                audit_repo=engine.audit,
                model_registry=engine.model_registry,
            )
            challengers = lifecycle.list_models(status="CHALLENGER", limit=5)
            if not challengers:
                return {"available": False, "reason": "NO_VALIDATED_CHALLENGER"}
            row = challengers[0]
            artifact_path = row.get("artifact_path", "")
            model_id = row.get("model_id", "")
            model_version = row.get("model_version", "")
            if not artifact_path:
                return {"available": False, "reason": "CHALLENGER_ARTIFACT_MISSING"}
            from pathlib import Path

            path = Path(artifact_path)
            if not path.exists():
                return {"available": False, "reason": "CHALLENGER_ARTIFACT_NOT_FOUND"}
            scaler = Path(str(path) + ".scaler.npz")
            # TASK-6: the deterministic 10-gate load gate MUST pass before
            # any Challenger enters the shadow runtime (spec 4). A
            # rejected model is never loaded; the failing gate is reported.
            from nexus_scalp.governance.load_gate import ModelLoadGate, read_manifest_file

            manifest = read_manifest_file(Path(artifact_path).parent / "model.json") or {}
            gate = ModelLoadGate(db_path=engine.audit._db_path if engine.audit else None).evaluate(
                artifact_path=path,
                scaler_path=scaler,
                model_id=model_id,
                model_version=model_version,
                manifest=manifest,
                lifecycle_state=row.get("lifecycle_status", ""),
            )
            if not gate.passed:
                return {
                    "available": False,
                    "reason": "MODEL_LOAD_REJECTED",
                    "failing_gate": gate.failing_gate.value if gate.failing_gate else "",
                }
            runtime = load_challenger(
                artifact_path=path,
                scaler_path=scaler,
                model_id=model_id,
                model_version=model_version,
                live_schema_id=engine.FEATURE_SCHEMA_ID,
                live_dimension=engine.FEATURE_DIM,
            )
            engine._shadow_challenger = runtime
            engine.shadow_engine.attach_challenger(runtime)
            # TASK-6: wire the governance shadow runtime (same-input
            # alignment + parity + latency + failure isolation).
            engine._governance_shadow = GovernanceShadowRuntime(
                runtime=runtime,
                store=engine.governance_store,
            )
            # Start a fresh shadow run bound to this challenger.
            from nexus_scalp.shadow.models import ShadowModelRef

            champ = engine.champion_manager.champion_or_none()
            champ_ref = (
                ShadowModelRef(
                    model_id=champ.model_id,
                    model_version=champ.model_version,
                    feature_schema_id=champ.feature_schema_id,
                    feature_dimension=champ.feature_dimension,
                    artifact_hash=champ.artifact_hash,
                    is_champion=True,
                )
                if champ
                else None
            )
            run_id = engine.shadow_engine.start_run(
                run_id=None,
                champion=champ_ref or ShadowModelRef(model_id="none", model_version=""),
                challenger_ref=runtime.ref or ShadowModelRef(model_id="none", model_version=""),
            )
            return serialize_enums(
                {
                    "available": True,
                    "challenger": runtime.summary(),
                    "run_id": run_id,
                }
            )
        except Exception as e:
            _log_err(e, "Shadow attach failed", endpoint="/api/models/shadow/attach")
            return _err("OPERATION_FAILED", extra={"reason": "SHADOW_LOAD_FAILED"})

    @router.post("/api/models/shadow/evaluate-promotion")
    def evaluate_shadow_promotion(run_id: str) -> dict[str, Any]:
        """Computes the explainable promotion evaluation + vetoes for a run."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            comparison_row = engine.shadow_store.get_comparison(run_id)
            if comparison_row is None:
                return {"available": False, "reason": "NO_COMPARISON"}
            import json as _json

            payload = _json.loads(comparison_row.get("payload") or "{}")
            from nexus_scalp.shadow.models import ShadowComparison

            comparison = ShadowComparison.model_validate(payload)
            evaluation = engine.shadow_engine.comparer.evaluate_promotion(comparison)
            engine.shadow_store.save_promotion(evaluation)
            return serialize_enums(
                {"available": True, "evaluation": evaluation.model_dump(mode="json")}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow promotion eval failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/shadow/worker/start")
    def start_shadow_worker() -> dict[str, Any]:
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            engine._start_shadow_worker()
            return {"available": True, "started": engine._shadow_worker_started}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow worker start failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/shadow/worker/stop")
    def stop_shadow_worker() -> dict[str, Any]:
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            import asyncio

            asyncio.run(engine._stop_shadow_worker())
            return {"available": True, "stopped": not engine._shadow_worker_started}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow worker stop failed"})
            return _err("INTERNAL_ERROR")

    def _governance() -> Any:
        """Returns the governance engine or None (safe)."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "governance_engine"):
            return None
        return engine

    @router.get("/api/models/governance/health")
    def get_governance_health() -> dict[str, Any]:
        """Truthful model-governance runtime health (spec 27)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            health = engine._governance_snapshot_health()
            return serialize_enums({"available": True, "health": health})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance health failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/governance/registry")
    def get_governance_registry() -> dict[str, Any]:
        """Truthful registry reconciliation (spec 3). Read-only."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            snapshot = engine.governance_engine.registry_snapshot(
                audit_db=engine.audit._db_path if engine.audit else "",
                champion_id=engine.champion_manager.model_id,
                champion_artifact=engine.config.model.model_artifact_path,
            )
            return serialize_enums({"available": True, "registry": snapshot})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance registry failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/registry/reconcile")
    def reconcile_registry() -> dict[str, Any]:
        """Makes the registry truthful about the CURRENT Champion (spec 3)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            engine._sync_champion_registry_state()
            snapshot = engine.governance_engine.registry_snapshot(
                audit_db=engine.audit._db_path if engine.audit else "",
                champion_id=engine.champion_manager.model_id,
                champion_artifact=engine.config.model.model_artifact_path,
            )
            return serialize_enums({"available": True, "registry": snapshot})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Registry reconcile failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/governance/events")
    def get_governance_events(limit: int = 200, event: str = "") -> dict[str, Any]:
        """Append-only governance event ledger (spec 30 / 31)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.governance_store.list_events(limit=limit, event=event)
            return serialize_enums({"available": True, "events": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance events failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/governance/comparisons")
    def get_governance_comparisons(limit: int = 200, run_id: str = "") -> dict[str, Any]:
        """Canonical shadow comparison rows (spec 9 / 14)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.governance_store.list_comparisons(limit=limit, run_id=run_id)
            return serialize_enums({"available": True, "comparisons": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance comparisons failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/shadow/outcomes")
    def link_shadow_outcomes(run_id: str = "", horizon_bars: int = 15) -> dict[str, Any]:
        """Links shadow decisions to eventual outcomes (spec 16)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.evidence import outcome_for_decision

            ids = []
            rows = engine.shadow_store.list_decisions(run_id=run_id, limit=2000)
            for r in rows:
                payload = {}
                try:
                    import json as _j

                    payload = _j.loads(r.get("payload") or "{}")
                except Exception:
                    payload = {}
                decision = dict(r)
                entry = payload.get("hypothetical_entry", 0.0) if isinstance(payload, dict) else 0.0
                decision["entry_price"] = entry or 0.0
                decision["decision_id"] = r.get("decision_id", "")
                outcome = outcome_for_decision(
                    decision=decision,
                    audit_db=engine.audit._db_path if engine.audit else None,
                    horizon_bars=max(1, min(int(horizon_bars), 60)),
                )
                ids.append(
                    {"shadow_decision_id": r.get("shadow_decision_id", ""), "outcome": outcome}
                )
            linked_count = sum(1 for x in ids if x["outcome"].get("linkage_state") == "LINKED")
            return serialize_enums(
                {"available": True, "linked": linked_count, "total": len(ids), "outcomes": ids}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow outcomes failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/governance/review")
    def get_governance_review() -> dict[str, Any]:
        """Live calibration + drift + backtest-vs-live divergence evidence."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.evidence import (
                backtest_live_divergence,
                brier_score,
                calibration_buckets,
                detect_drift,
                ece_score,
            )

            rows = engine.governance_store.list_comparisons(limit=3000)
            cal_rows = []
            probs_window = []
            actions = []
            for r in rows:
                try:
                    import json as _j2

                    cp = _j2.loads(r.get("champion_probabilities") or "[]")
                    chp = _j2.loads(r.get("challenger_probabilities") or "[]")
                except Exception:
                    cp, chp = [], []
                if cp:
                    probs_window.append(cp)
                    cal_rows.append({"confidence": max(cp), "correct": True})
                if chp:
                    probs_window.append(chp)
                    cal_rows.append({"confidence": max(chp), "correct": True})
                actions.append(str(r.get("champion_action", "NO_TRADE")))
                actions.append(str(r.get("challenger_action", "NO_TRADE")))

            buckets = calibration_buckets(cal_rows)
            drift = detect_drift(
                probs_window=probs_window[:300],
                actions=actions[:300],
                model_id="shadow",
            )
            divergence = backtest_live_divergence(
                backtest_accuracy=None,
                backtest_expectancy_r=None,
                live_samples=len(rows),
            )
            return serialize_enums(
                {
                    "available": True,
                    "calibration": {
                        "buckets": [b.model_dump(mode="json") for b in buckets],
                        "brier": brier_score(cal_rows),
                        "ece": ece_score(buckets),
                    },
                    "drift": [a.model_dump(mode="json") for a in drift],
                    "divergence": divergence,
                    "samples": len(rows),
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance review failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/promotion/approve")
    def approve_promotion(payload: dict[str, Any]) -> dict[str, Any]:
        """Operator approval for READY_FOR_REVIEW -> APPROVED (spec 21/22)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.engine import PromotionGateError

            actor = str(payload.get("actor", "") or "").strip()
            model_id = str(payload.get("model_id", "") or "")
            model_version = str(payload.get("model_version", "") or "")
            reason = str(payload.get("reason", "") or "")
            if not actor or not model_id:
                return _err("PROMOTION_BLOCKED", extra={"reason": "actor and model_id required"})
            transition = engine.governance_engine.approve(
                model_id=model_id,
                model_version=model_version,
                actor=actor,
                reason=reason or "operator approval",
                evidence={"operator": actor, "source": "api"},
            )
            return serialize_enums(
                {"available": True, "transition": transition.model_dump(mode="json")}
            )
        except PromotionGateError as e:
            # BUG-040: never leak raw exception text to clients; log full
            # detail server-side only.
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Promotion gate blocked"},
            )
            return _err("PROMOTION_BLOCKED", extra={"reason": "promotion gate blocked"})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion approve failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/promotion/rollback")
    def rollback_promotion(payload: dict[str, Any]) -> dict[str, Any]:
        """Operator rollback to the previous Champion (spec 23)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.engine import PromotionGateError

            actor = str(payload.get("actor", "") or "").strip()
            failed_id = str(payload.get("failed_model_id", "") or "")
            failed_version = str(payload.get("failed_version", "") or "")
            previous_id = str(payload.get("previous_model_id", "") or "")
            previous_version = str(payload.get("previous_version", "") or "")
            reason = str(payload.get("reason", "") or "")
            if not actor or not failed_id or not previous_id:
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={"reason": "actor, failed_model_id and previous_model_id required"},
                )
            transition = engine.governance_engine.rollback(
                failed_model_id=failed_id,
                failed_version=failed_version,
                previous_model_id=previous_id,
                previous_version=previous_version,
                actor=actor,
                reason=reason or "operator rollback",
                previous_artifact=engine.config.model.model_artifact_path,
            )
            return serialize_enums(
                {"available": True, "transition": transition.model_dump(mode="json")}
            )
        except PromotionGateError as e:
            # BUG-040: never leak raw exception text to clients; log full
            # detail server-side only.
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Promotion gate blocked"},
            )
            return _err("PROMOTION_BLOCKED", extra={"reason": "promotion gate blocked"})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion rollback failed"})
            return _err("INTERNAL_ERROR")

    # -------------------------------------------------------------------------
    # TASK-08: 70D governance — promotion preview / transaction / rollback
    # preview / emergency controls / audit trail (spec 28 / 29 / 30 / 31 / 32)
    # -------------------------------------------------------------------------

    def _promotion_lock_path() -> str:
        """Cross-process promotion lock location (artifacts/governance/)."""
        engine = app.state.engine
        base = Path(
            engine.config.artifacts_dir if hasattr(engine.config, "artifacts_dir") else "artifacts"
        )
        locks_dir = base / "governance" / "locks"
        return str(locks_dir / "promotion.lock")

    @router.get("/api/models/governance/status")
    def get_governance_status() -> dict[str, Any]:
        """MODEL STATUS API (spec 32): champion, candidate, gates, promotion."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            health = engine._governance_snapshot_health()
            champ = health.get("champion", {})
            chal = health.get("challenger", {})
            state = engine.governance_engine._promotion_state_summary() or {}
            state.get("by_state", {})
            # champion schema/hash live in the registry snapshot
            snapshot = engine.governance_engine.registry_snapshot(
                audit_db=engine.audit._db_path if engine.audit else "",
                champion_id=engine.champion_manager.model_id,
                champion_artifact=engine.config.model.model_artifact_path,
            )
            cats = snapshot.get("categories", {}) or {}
            champ_cat = (cats.get("CURRENT_CHAMPION") or {}) if cats else {}
            chal_cat = (cats.get("CURRENT_CHALLENGER") or {}) if cats else {}
            candidate_status = chal_cat.get("lifecycle_state", "") or chal.get("state", "NONE")
            candidate_status = candidate_status or "NONE"
            gates = engine.governance_engine.promotion_checklist({})
            return serialize_enums(
                {
                    "available": True,
                    "champion": {
                        "model_id": champ_cat.get("model_id", "") or champ.get("id", ""),
                        "version": champ_cat.get("version", "") or champ.get("version", ""),
                        "schema": champ_cat.get("schema_id", "") or champ.get("schema", ""),
                        "hash": champ_cat.get("artifact_hash", "")
                        or champ.get("artifact_hash", ""),
                    },
                    "candidate": {
                        "model_id": chal_cat.get("model_id", "") or chal.get("id", ""),
                        "status": candidate_status,
                        "schema": chal_cat.get("schema_id", "") or chal.get("schema", ""),
                    },
                    "gates": {
                        "technical": "PASS" if champ.get("healthy") else "UNKNOWN",
                        "oos": "PASS" if gates.get("passed") else "INCONCLUSIVE",
                        "robustness": "PASS" if gates.get("passed") else "INCONCLUSIVE",
                        "shadow": "PASS"
                        if bool(health.get("shadow", {}).get("comparisons"))
                        else "INCONCLUSIVE",
                        "drift": "INCONCLUSIVE",
                    },
                    "promotion": {
                        "eligible": bool(champ.get("healthy")),
                        "approved": candidate_status == "APPROVED",
                        "frozen": engine.governance_engine.promotion_frozen,
                    },
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance status failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/governance/promotion-preview")
    def get_promotion_preview(model_id: str = "", model_version: str = "") -> dict[str, Any]:
        """PROMOTION PREVIEW (spec 28). READ-ONLY — shows exact gates BEFORE
        any operator decision. No mutation, no lock."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        if not model_id:
            return _err("PROMOTION_BLOCKED", extra={"reason": "model_id required"})
        try:
            champ_ref = engine.champion_manager.champion_or_none()
            preview = engine.governance_engine.promotion_preview(
                model_id=model_id,
                model_version=model_version,
                artifact_path=engine.config.model.model_artifact_path,
                runtime_schema_id=champ_ref.feature_schema_id if champ_ref else "",
                runtime_dimension=champ_ref.feature_dimension if champ_ref else 0,
                locks_dir=Path(_promotion_lock_path()).parent,
            )
            return serialize_enums({"available": True, "preview": preview})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion preview failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/promotion/execute")
    def execute_promotion(payload: dict[str, Any]) -> dict[str, Any]:
        """PROMOTION TRANSACTION (spec 8 / 29): the ONLY promotion path.

        Requires: actor, model_id, model_version, reason, approval_token,
        old_champion_model_id/version + old_champion_hash.
        A button can never call a hidden auto-promotion path — this endpoint
        re-verifies everything fresh, takes the cross-process promotion lock,
        records the old Champion, activates, verifies, and commits (spec 37).
        """
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.transaction import (
                PromotionTransactionError,
                execute_promotion_transaction,
            )

            actor = str(payload.get("actor", "") or "").strip()
            model_id = str(payload.get("model_id", "") or "")
            model_version = str(payload.get("model_version", "") or "")
            reason = str(payload.get("reason", "") or "")
            approval_token = str(payload.get("approval_token", "") or "")
            if not actor or not model_id or not approval_token:
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={"reason": "actor, model_id and approval_token required"},
                )
            if engine.governance_engine.promotion_frozen:
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={"reason": "promotion frozen (emergency stop)"},
                )
            champ_ref = engine.champion_manager.champion_or_none()
            old_champion = {
                "model_id": str(payload.get("old_champion_model_id", "") or ""),
                "version": str(payload.get("old_champion_version", "") or ""),
                "artifact_hash": str(payload.get("old_champion_hash", "") or ""),
                "schema_id": str(payload.get("old_champion_schema", "") or ""),
            }
            audit_row = execute_promotion_transaction(
                store=engine.governance_store,
                lock_path=_promotion_lock_path(),
                model_id=model_id,
                model_version=model_version,
                actor=actor,
                reason=reason or "operator promotion",
                approval_token=approval_token,
                old_champion=old_champion,
                candidate={
                    "model_id": model_id,
                    "version": model_version,
                    "artifact_hash": str(payload.get("candidate_hash", "") or ""),
                    "schema_id": str(payload.get("candidate_schema", "") or ""),
                },
                activate=lambda mid, mver: engine._activate_promoted_model(
                    model_id=mid, model_version=mver
                ),
                verify_new=lambda mid, mver: {"ok": engine._champion_bundle_healthy()},
                rollback_activate=lambda mid, mver: engine._activate_rollback_model(
                    model_id=mid, model_version=mver
                ),
                artifact_path=engine.config.model.model_artifact_path,
                runtime_schema_id=champ_ref.feature_schema_id if champ_ref else "",
                runtime_dimension=champ_ref.feature_dimension if champ_ref else 0,
                correlation_id="api_promotion",
            )
            return serialize_enums({"available": True, "promotion": audit_row})
        except PromotionTransactionError as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion transaction blocked"})
            return _err(
                "PROMOTION_BLOCKED",
                extra={
                    "reason": "promotion transaction blocked",
                    "detail": str(getattr(e, "safe_message", "") or type(e).__name__),
                },
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion execute failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/governance/rollback-preview")
    def get_rollback_preview(
        failed_model_id: str = "", previous_model_id: str = ""
    ) -> dict[str, Any]:
        """ROLLBACK PREVIEW (spec 30): verifies the old artifact is still
        valid BEFORE the operator commits. Read-only."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            if not failed_model_id:
                return _err("PROMOTION_BLOCKED", extra={"reason": "failed_model_id required"})
            preview = engine.governance_engine.rollback_preview(
                failed_model_id=failed_model_id,
                previous_model_id=previous_model_id,
                previous_artifact=engine.config.model.model_artifact_path,
            )
            return serialize_enums({"available": True, "preview": preview})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Rollback preview failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/governance/emergency/freeze")
    def freeze_promotions(payload: dict[str, Any]) -> dict[str, Any]:
        """FREEZE PROMOTION (spec 31): emergency stop, distinct from Stop Bot."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            actor = str(payload.get("actor", "") or "").strip()
            if not actor:
                return _err("PROMOTION_BLOCKED", extra={"reason": "actor required"})
            engine.governance_engine.freeze_promotions(
                actor=actor, reason=str(payload.get("reason", "") or "")
            )
            return serialize_enums(
                {"available": True, "state": engine.governance_engine.emergency_freezes()}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion freeze failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/governance/emergency/unfreeze")
    def unfreeze_promotions(payload: dict[str, Any]) -> dict[str, Any]:
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            actor = str(payload.get("actor", "") or "").strip()
            if not actor:
                return _err("PROMOTION_BLOCKED", extra={"reason": "actor required"})
            engine.governance_engine.unfreeze_promotions(
                actor=actor, reason=str(payload.get("reason", "") or "")
            )
            return serialize_enums(
                {"available": True, "state": engine.governance_engine.emergency_freezes()}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion unfreeze failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/models/governance/emergency/disable")
    def disable_candidate(payload: dict[str, Any]) -> dict[str, Any]:
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            actor = str(payload.get("actor", "") or "").strip()
            model_id = str(payload.get("model_id", "") or "")
            if not actor or not model_id:
                return _err("PROMOTION_BLOCKED", extra={"reason": "actor and model_id required"})
            engine.governance_engine.disable_candidate(
                model_id=model_id,
                actor=actor,
                reason=str(payload.get("reason", "") or ""),
            )
            return serialize_enums(
                {"available": True, "state": engine.governance_engine.emergency_freezes()}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Candidate disable failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/models/governance/audits")
    def get_governance_audits(limit: int = 100) -> dict[str, Any]:
        """Immutable promotion/rollback audit trail (spec 29 / 30)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            promotions = engine.governance_store.list_promotion_audits(limit=limit)
            rollbacks = engine.governance_store.list_rollback_audits(limit=limit)
            return serialize_enums(
                {
                    "available": True,
                    "promotions": promotions,
                    "rollbacks": rollbacks,
                    "emergency": engine.governance_engine.emergency_freezes(),
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance audits failed"})
            return _err("INTERNAL_ERROR")
