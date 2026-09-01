"""
Strategy Factory — REST API Routes
===================================
STRATEGY FACTORY (2026-08-20).

REST surface for the Strategy Factory control room:

  GET    /api/factory/status          — loop + worker + generations telemetry
  GET    /api/factory/generations     — generation list (bounded)
  GET    /api/factory/generations/{id}— one generation (with candidates)
  GET    /api/factory/candidates      — candidate rows (filter by generation)
  GET    /api/factory/events          — event stream (bounded)
  GET    /api/factory/failures        — structured failure reasons
  GET    /api/factory/ranking         — ranked registry survivors by dimension
  GET    /api/factory/memory          — evolution memory (research summary)
  POST   /api/factory/generate        — create+generate+validate one generation
  POST   /api/factory/evaluate/{id}   — evaluate a candidate through the pipeline
  POST   /api/factory/loop/start      — start autonomous loop
  POST   /api/factory/loop/pause      — pause autonomous loop
  POST   /api/factory/loop/resume     — resume autonomous loop
  POST   /api/factory/loop/stop       — kill switch

Every route is wrapped (never raises), uses serialize_enums and mirrors the
research API conventions. The factory NEVER touches the live path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.factory_routes")

router = APIRouter(prefix="/api/factory", tags=["strategy-factory"])


def _factory(request: Request) -> Any | None:
    engine = request.app.state.engine
    if not engine or not hasattr(engine, "strategy_factory"):
        return None
    return engine.strategy_factory


def _worker(request: Request) -> Any | None:
    engine = request.app.state.engine
    if not engine or not hasattr(engine, "strategy_factory_worker"):
        return None
    return engine.strategy_factory_worker


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"available": True, **payload}


def _factory_enabled_safe(svc: Any) -> bool:
    """CHG-0034: enabled_getter tolerant of older settings services."""
    try:
        return bool(svc.factory_effective_enabled())
    except Exception:
        return True


def _err(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


@router.get("/status")
def factory_status(request: Request) -> dict[str, Any]:
    """Loop state, worker telemetry, provider usage, generations count."""
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.strategies.factory.store import (
            list_generations,
            provider_usage_total,
        )

        worker = _worker(request)
        provider = getattr(factory, "provider", None)
        payload = {
            "loop": factory.loop_status(),
            "generations": list_generations(factory._research_backend, limit=20),
            "provider_usage": provider_usage_total(factory._research_backend),
            "config": factory.config.model_dump(),
            "provider": (
                {
                    "available": bool(provider.available()),
                    "model": provider.model,
                    "base_url": provider.api_base_url,
                    "prompt_version": provider.prompt_version,
                    "usage": provider.usage.snapshot(),
                }
                if provider is not None
                else {"available": False}
            ),
        }
        if worker is not None:
            payload["worker"] = worker.status()
        from nexus_scalp.web.server import serialize_enums

        return serialize_enums(_ok(payload))
    except Exception as e:
        log_factory_error("/api/factory/status", e)
        return _err("INTERNAL_ERROR")


@router.get("/generations")
def factory_generations(request: Request, limit: int = 20) -> dict[str, Any]:
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.strategies.factory.store import list_generations
        from nexus_scalp.web.server import serialize_enums

        return serialize_enums(
            _ok({"generations": list_generations(factory._research_backend, limit=limit)})
        )
    except Exception as e:
        log_factory_error("/api/factory/generations", e)
        return _err("INTERNAL_ERROR")


@router.get("/generations/{generation_id}")
def factory_generation(request: Request, generation_id: str) -> dict[str, Any]:
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.strategies.factory.store import (
            get_generation,
            list_candidates,
            list_failures,
        )
        from nexus_scalp.web.server import serialize_enums

        gen = get_generation(factory._research_backend, generation_id)
        if gen is None:
            return _err("GENERATION_NOT_FOUND")
        return serialize_enums(
            _ok(
                {
                    "generation": gen,
                    "candidates": list_candidates(
                        factory._research_backend, generation_id=generation_id, limit=2000
                    ),
                    "failures": list_failures(
                        factory._research_backend, generation_id=generation_id, limit=200
                    ),
                }
            )
        )
    except Exception as e:
        log_factory_error(f"/api/factory/generations/{generation_id}", e)
        return _err("INTERNAL_ERROR")


@router.get("/candidates")
def factory_candidates(
    request: Request,
    generation_id: str | None = None,
    lifecycle: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.strategies.factory.store import list_candidates
        from nexus_scalp.web.server import serialize_enums

        rows = list_candidates(
            factory._research_backend, generation_id=generation_id, lifecycle=lifecycle, limit=limit
        )
        return serialize_enums(_ok({"candidates": rows}))
    except Exception as e:
        log_factory_error("/api/factory/candidates", e)
        return _err("INTERNAL_ERROR")


@router.get("/benchmarks")
def factory_benchmarks(
    request: Request,
    generation_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Strategy-aware benchmarks (per-candidate backtests) for AI decisions.

    Returns the factory_runs rows that carry the `benchmark` artifact
    (coverage + walk-forward/OOS/robustness explainability + decision label)
    produced by the 2026-08-21 fix. Each benchmark is strategy-specific
    (filtered dataset), not the shared ledger-average that caused the
    07:15 systemic 40-failure collapse.
    """
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.strategies.factory.store import list_runs
        from nexus_scalp.web.server import serialize_enums

        rows = list_runs(factory._research_backend, limit=limit)
        if generation_id:
            rows = [r for r in rows if r.get("generation_id") == generation_id]
        # Decode benchmark payloads for the response (stored as JSON text in result_summary)
        import json as _json

        benchmarks: list[dict[str, Any]] = []
        for r in rows:
            raw = r.get("result_summary")
            bm: Any = None
            if isinstance(raw, str):
                try:
                    parsed = _json.loads(raw)
                    bm = parsed.get("benchmark") if isinstance(parsed, dict) else None
                    if bm is None and isinstance(parsed, dict):
                        bm = parsed
                except Exception:
                    bm = None
            elif isinstance(raw, dict):
                bm = raw.get("benchmark") or raw
            if bm and isinstance(bm, dict) and bm.get("candidate_id"):
                benchmarks.append(bm)
        # Also surface candidates that lacked a factory_runs row (legacy generations)
        # via on-demand coverage computation — kept bounded.
        if not benchmarks and generation_id:
            from nexus_scalp.strategies.factory.store import list_candidates

            cands = list_candidates(
                factory._research_backend, generation_id=generation_id, limit=limit
            )
            for c in cands[:20]:
                try:
                    row = factory._candidate_from_row(c)  # type: ignore[attr-defined]
                    if row is None:
                        continue
                    from nexus_scalp.strategies.factory.benchmark import candidate_coverage_stats

                    cov = candidate_coverage_stats(row, factory._ledger_snapshot_for_filter())
                    benchmarks.append(cov)
                except Exception:
                    continue
        return serialize_enums(_ok({"benchmarks": benchmarks[:limit]}))
    except Exception as e:
        log_factory_error("/api/factory/benchmarks", e)
        return _err("INTERNAL_ERROR")


@router.get("/events")
def factory_events(
    request: Request, generation_id: str | None = None, limit: int = 200
) -> dict[str, Any]:
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.strategies.factory.store import list_events
        from nexus_scalp.web.server import serialize_enums

        rows = list_events(factory._research_backend, generation_id=generation_id, limit=limit)
        return serialize_enums(_ok({"events": rows}))
    except Exception as e:
        log_factory_error("/api/factory/events", e)
        return _err("INTERNAL_ERROR")


@router.get("/failures")
def factory_failures(
    request: Request, generation_id: str | None = None, limit: int = 200
) -> dict[str, Any]:
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.strategies.factory.store import list_failures
        from nexus_scalp.web.server import serialize_enums

        rows = list_failures(factory._research_backend, generation_id=generation_id, limit=limit)
        return serialize_enums(_ok({"failures": rows}))
    except Exception as e:
        log_factory_error("/api/factory/failures", e)
        return _err("INTERNAL_ERROR")


@router.get("/ranking")
def factory_ranking(
    request: Request, dimension: str = "OVERALL", limit: int = 50
) -> dict[str, Any]:
    """Ranked registry survivors by dimension (spec 53)."""
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.research.store import list_registry
        from nexus_scalp.strategies.factory.ranking import RankDimension, rank_strategies
        from nexus_scalp.web.server import serialize_enums

        rows = list_registry(factory.audit_repo, limit=500)
        try:
            dim = RankDimension(dimension.upper())
        except ValueError:
            dim = RankDimension.OVERALL
        ranked = rank_strategies(rows, dimension=dim, limit=limit)
        return serialize_enums(_ok({"dimension": dim.value, "ranked": ranked}))
    except Exception as e:
        log_factory_error("/api/factory/ranking", e)
        return _err("INTERNAL_ERROR")


@router.get("/memory")
def factory_memory(request: Request) -> dict[str, Any]:
    """Structured evolution memory (what the next generation consumes)."""
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        memory = factory.build_memory()
        from nexus_scalp.web.server import serialize_enums

        return serialize_enums(_ok({"memory": memory}))
    except Exception as e:
        log_factory_error("/api/factory/memory", e)
        return _err("INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# LLM provider configuration (UI-controllable, hot-swappable, 2026-08-20)
# ---------------------------------------------------------------------------


@router.get("/llm-config")
def factory_llm_config_status(request: Request) -> dict[str, Any]:
    """Safe status of the LLM provider config (never the raw API key)."""
    try:
        from nexus_scalp.settings import load_settings_service
        from nexus_scalp.web.server import serialize_enums

        engine = request.app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        svc = svc or load_settings_service()
        status = svc.factory_llm_config_status()
        factory = _factory(request)
        provider_status = {}
        if factory is not None and factory.provider is not None:
            provider_status = {
                "available": factory.provider.available(),
                "model": factory.provider.model,
                "base_url": factory.provider.api_base_url,
                "prompt_version": factory.provider.prompt_version,
                "usage": factory.provider.usage.snapshot(),
            }
        return serialize_enums(_ok({"status": status, "provider": provider_status}))
    except Exception as e:
        log_factory_error("/api/factory/llm-config", e)
        return _err("INTERNAL_ERROR")


@router.post("/llm-config")
def factory_llm_config_save(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist the LLM provider config (encrypted key) + hot-rebuild the
    in-process factory provider. Never returns the API key."""
    payload = payload or {}
    try:
        from nexus_scalp.settings import load_settings_service
        from nexus_scalp.web.server import serialize_enums

        engine = request.app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        svc = svc or load_settings_service()
        result = svc.set_factory_llm_config(
            api_key=str(payload.get("api_key") or "").strip() or None,
            base_url=str(payload.get("base_url") or "").strip() or None,
            model=str(payload.get("model") or "").strip() or None,
            temperature=payload.get("temperature"),
            request_timeout_sec=payload.get("request_timeout_sec"),
            max_requests_per_generation=payload.get("max_requests_per_generation"),
            clear_api_key=bool(payload.get("clear_api_key", False)),
            actor="web",
        )
        # Hot-rebuild the running factory provider so changes apply without
        # restart (mirrors the telegram notifier rebuild pattern).
        factory = _factory(request)
        if engine is not None and factory is not None:
            from nexus_scalp.strategies.factory.provider import LLMGenerationProvider

            cfg = svc.get_factory_llm_config()
            new_provider = LLMGenerationProvider(
                api_base_url=cfg["api_base_url"],
                model=cfg["model"],
                api_key=cfg["api_key"],
                temperature=cfg["temperature"],
                secret_store=svc.secrets,
                request_timeout_sec=cfg.get("request_timeout_sec", 300.0),
                max_requests_per_generation=cfg.get("max_requests_per_generation", 60),
                enabled_getter=lambda: _factory_enabled_safe(svc),
            )
            # CHG-0034: config changed -> the gate re-validates WITHOUT a
            # network probe (steer 66: hot reload must not disrupt the engine).
            new_provider._gate.reconfigure()
            factory.provider = new_provider
            logger.info(
                "[STRATEGY_FACTORY] LLM provider hot-rebuilt",
                configured=new_provider.available(),
                model=new_provider.model,
            )
        return serialize_enums(_ok(result))
    except Exception as e:
        log_factory_error("/api/factory/llm-config save", e)
        return _err("INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# Generation control
# ---------------------------------------------------------------------------


@router.post("/generate")
def factory_generate(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Creates, generates and structurally validates one generation.

    Runs synchronously by design (bounded population); evaluation proceeds
    via the worker/queue. Autonomous loop must be STOPPED for a manual run.
    """
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    payload = payload or {}
    size = payload.get("size")
    mode = str(payload.get("mode", "MANUAL")).upper()
    try:
        gen = factory.create_generation(size=int(size) if size else None, mode=mode)
        population = factory.generate_population(
            gen["generation_id"], size=int(size) if size else None
        )
        validation = factory.validate_population(population)
        provider = getattr(factory, "provider", None)
        source = "LLM" if provider is not None and provider.available() else "DETERMINISTIC"
        logger.info(
            "[STRATEGY_FACTORY] event=GENERATE_OK generation_id=%s population=%s "
            "passed=%s failed=%s source=%s request_id=%s",
            gen["generation_id"],
            len(population),
            len(validation["passed"]),
            len(validation["failed"]),
            source,
            request.headers.get("x-request-id", "-"),
        )
        # BUG-135: a MANUAL generate must ALSO EVALUATE + COMPLETE the generation.
        # Previously the route stopped after structural validation, stranding every
        # generation RUNNING with 0 evaluated (13 in a row). Full cycle is expensive
        # -> background thread; results stream into the factory store/events.
        import threading as _threading

        def _run_full_cycle() -> None:
            try:
                factory.run_generation_cycle(size=int(size) if size else None)
            except Exception as _cyc_err:  # failure-isolated
                logger.error(
                    "[STRATEGY_FACTORY] event=GENERATE_CYCLE_FAILED generation_id=%s error=%s",
                    gen["generation_id"],
                    _cyc_err,
                )

        _threading.Thread(
            target=_run_full_cycle, name=f"factory-cycle-{gen['generation_id']}", daemon=True
        ).start()
        from nexus_scalp.web.server import serialize_enums

        return serialize_enums(
            _ok(
                {
                    "generation": gen,
                    "population": len(population),
                    "passed": len(validation["passed"]),
                    "failed": len(validation["failed"]),
                    "status": "GENERATED",
                    "source": source,
                }
            )
        )
    except Exception as e:
        log_factory_error("/api/factory/generate", e)
        logger.error(
            "[STRATEGY_FACTORY] event=GENERATE_FAILED generation_id=error=%s request_id=%s",
            str(e),
            request.headers.get("x-request-id", "-"),
        )
        return _err("INTERNAL_ERROR")


@router.post("/evaluate/{candidate_id}")
def factory_evaluate(request: Request, candidate_id: str) -> dict[str, Any]:
    """Evaluates one persisted candidate through the authoritative pipeline."""
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        from nexus_scalp.strategies.factory.store import list_candidates

        rows = list_candidates(factory._research_backend, limit=500)
        row = next((c for c in rows if c.get("candidate_id") == candidate_id), None)
        if row is None:
            return _err("CANDIDATE_NOT_FOUND")
        candidate = factory._candidate_from_row(row)
        if candidate is None:
            return _err("CANDIDATE_REHYDRATE_FAILED")
        dataset = factory._build_dataset()
        result = factory.evaluate_candidate(candidate, dataset)
        from nexus_scalp.web.server import serialize_enums

        return serialize_enums(_ok({"result": result or {}}))
    except Exception as e:
        log_factory_error(f"/api/factory/evaluate/{candidate_id}", e)
        return _err("INTERNAL_ERROR")


@router.post("/complete/{generation_id}")
def factory_complete(request: Request, generation_id: str) -> dict[str, Any]:
    """Finalizes a generation: ranking + elite + summary."""
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        completion = factory.complete_generation(generation_id)
        from nexus_scalp.web.server import serialize_enums

        return serialize_enums(_ok(completion))
    except Exception as e:
        log_factory_error(f"/api/factory/complete/{generation_id}", e)
        return _err("INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# Autonomous loop control (spec 73 / 106)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CHG-0034: provider health gate + Strategy Factory user toggle (steer 3/16/58)
# ---------------------------------------------------------------------------


def _settings_svc(request: Request) -> Any:
    """Resolves the settings service (engine-bound first, standalone fallback)."""
    engine = request.app.state.engine
    svc = getattr(engine, "settings_service", None) if engine else None
    return svc


@router.get("/provider-health")
def factory_provider_health(request: Request) -> dict[str, Any]:
    """Secret-free combined health payload (steer sections 16, 58).

    One call answers: user intent, runtime auto-disable + reason, gate
    circuit state, credentials presence, provider runtime state, gate
    metrics. The API key value is NEVER included.
    """
    try:
        from nexus_scalp.settings import load_settings_service
        from nexus_scalp.web.server import serialize_enums

        svc = _settings_svc(request) or load_settings_service()
        snap = svc.factory_health_snapshot()
        factory = _factory(request)
        gate_state: dict[str, Any] = {}
        if factory is not None and getattr(factory, "provider", None) is not None:
            try:
                gate_state = factory.provider._gate.health_snapshot()
            except Exception as gate_err:
                gate_state = {"error": type(gate_err).__name__}
        worker = _worker(request)
        snap["gate"] = gate_state
        snap["worker_running"] = bool(worker.running) if worker is not None else False
        snap["trading_engine"] = "UNAFFECTED"
        return serialize_enums(_ok(snap))
    except Exception as e:
        log_factory_error("/api/factory/provider-health", e)
        return _err("INTERNAL_ERROR")


@router.post("/provider-toggle")
def factory_provider_toggle(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """THE single user control for the Strategy Factory external feature.

    Enabling re-validates configuration WITHOUT any network probe; when
    credentials are missing the response is still success=false-to-enable
    with an actionable reason (steer section 13 — no hammering).
    Disabling stops new provider requests; in-flight bounded work may
    finish; the trading engine is NEVER affected (steer 14, 60).
    """
    payload = payload or {}
    try:
        from nexus_scalp.settings import load_settings_service
        from nexus_scalp.strategies.factory.provider_gate import get_provider_gate
        from nexus_scalp.web.server import serialize_enums

        svc = _settings_svc(request) or load_settings_service()
        enabled = bool(payload.get("enabled"))
        # Config pre-check on ENABLE (no network — steer section 9/13).
        config_block = None
        if enabled:
            cfg_status = svc.factory_llm_config_status()
            if not (
                cfg_status.get("api_key_present")
                and cfg_status.get("base_url")
                and cfg_status.get("model")
            ):
                missing = (
                    "API key"
                    if not cfg_status.get("api_key_present")
                    else ("base URL" if not cfg_status.get("base_url") else "model")
                )
                config_block = {
                    "code": "CANNOT_ENABLE_CONFIG_INCOMPLETE",
                    "missing": missing,
                    "message": f"Cannot enable: provider {missing} is not configured.",
                }
        snap = svc.set_factory_enabled(enabled, actor="web")
        # Keep the global gate coherent with the user action (steer 59/60).
        if enabled and config_block is None:
            gate = get_provider_gate()
            gate.reconfigure()  # re-validate WITHOUT network
        result: dict[str, Any] = {
            "enabled": enabled,
            "state": snap,
            "trading_engine": "UNAFFECTED",
        }
        if config_block is not None:
            result["enable_blocked"] = config_block
        return serialize_enums(_ok(result))
    except Exception as e:
        log_factory_error("/api/factory/provider-toggle", e)
        return _err("INTERNAL_ERROR")


@router.post("/provider-test")
def factory_provider_test(request: Request) -> dict[str, Any]:
    """ONE controlled provider probe (steer section 15) — user-invoked only.

    Sends a tiny chat completion (max_tokens=8); the gate chain (rate
    limit, circuit, single-flight) fully applies. Never retries on the
    caller side. Result: READY with latency, or UNAVAILABLE with a
    normalized reason. Never returns secret values.
    """
    try:
        from nexus_scalp.settings import load_settings_service
        from nexus_scalp.strategies.factory.provider_gate import FailureCategory
        from nexus_scalp.web.server import serialize_enums

        svc = _settings_svc(request) or load_settings_service()
        if not svc.factory_effective_enabled():
            return _err("STRATEGY_FACTORY_DISABLED")
        cfg_status = svc.factory_llm_config_status()
        if not (
            cfg_status.get("api_key_present")
            and cfg_status.get("base_url")
            and cfg_status.get("model")
        ):
            return _err("PROVIDER_NOT_CONFIGURED")
        factory = _factory(request)
        provider = getattr(factory, "provider", None) if factory is not None else None
        if provider is None or not getattr(provider, "api_base_url", ""):
            # Build a THROWAWAY provider for the probe (never stored).
            from nexus_scalp.strategies.factory.provider import LLMGenerationProvider

            cfg = svc.get_factory_llm_config()
            provider = LLMGenerationProvider(
                api_base_url=cfg["api_base_url"],
                model=cfg["model"],
                api_key=cfg["api_key"],
                secret_store=getattr(svc, "secrets", None),
                request_timeout_sec=min(30.0, float(cfg.get("request_timeout_sec", 30.0))),
                enabled_getter=lambda: True,
            )
        result = provider._gate.execute(
            "probe:test-provider",
            lambda: _test_probe_request(provider),
            single_flight=True,
        )
        if result.ok:
            payload = {
                "provider_state": "READY",
                "latency_ms": round(result.duration_ms, 1),
                "model": provider.model,
            }
            return serialize_enums(_ok(payload))
        reason_map = {
            FailureCategory.RATE_LIMITED: "Provider rate-limited (HTTP 429). Strategy Factory paces automatically; trading engine unaffected.",
            FailureCategory.AUTH_ERROR: "Provider rejected the credentials (authentication failure). Configure a valid API key.",
            FailureCategory.NETWORK_ERROR: "Provider unreachable (network error).",
            FailureCategory.TIMEOUT: "Provider timed out.",
            FailureCategory.SERVER_ERROR: "Provider server error (transient).",
            FailureCategory.CONFIG_ERROR: "Provider configuration invalid.",
        }
        return serialize_enums(
            _ok(
                {
                    "provider_state": "UNAVAILABLE",
                    "reason": reason_map.get(result.category, result.reason or "unknown failure"),
                    "category": result.category.value,
                    "latency_ms": round(result.duration_ms, 1),
                }
            )
        )
    except Exception as e:
        log_factory_error("/api/factory/provider-test", e)
        return _err("INTERNAL_ERROR")


def _test_probe_request(provider: Any) -> Any:
    """Builds the minimal probe send() callable for provider-test."""
    from nexus_scalp.strategies.factory.provider_gate import execute_http_post

    payload = {
        "model": provider.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    url = f"{provider.api_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider._api_key}",
        "Content-Type": "application/json",
    }
    return execute_http_post(
        provider._gate,
        url,
        payload=payload,
        headers=headers,
        timeout=min(30.0, provider.request_timeout_sec),
        request_key="probe:test-provider",
        single_flight=False,  # probe must actually fire
    )


@router.post("/loop/start")
def factory_loop_start(request: Request) -> dict[str, Any]:
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        ok = factory.start_loop("AUTONOMOUS")
        return _ok({"started": ok, "loop": factory.loop_status()})
    except Exception as e:
        log_factory_error("/api/factory/loop/start", e)
        return _err("INTERNAL_ERROR")


@router.post("/loop/pause")
def factory_loop_pause(request: Request) -> dict[str, Any]:
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        ok = factory.pause_loop()
        return _ok({"paused": ok, "loop": factory.loop_status()})
    except Exception as e:
        log_factory_error("/api/factory/loop/pause", e)
        return _err("INTERNAL_ERROR")


@router.post("/loop/resume")
def factory_loop_resume(request: Request) -> dict[str, Any]:
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        ok = factory.resume_loop()
        return _ok({"resumed": ok, "loop": factory.loop_status()})
    except Exception as e:
        log_factory_error("/api/factory/loop/resume", e)
        return _err("INTERNAL_ERROR")


@router.post("/loop/stop")
def factory_loop_stop(request: Request) -> dict[str, Any]:
    """Kill switch (spec 106): stop new generations / LLM requests instantly."""
    factory = _factory(request)
    if factory is None:
        return _err("FACTORY_UNAVAILABLE")
    try:
        ok = factory.stop_loop()
        worker = _worker(request)
        if worker is not None:
            worker.stop()
        return _ok({"stopped": ok, "loop": factory.loop_status()})
    except Exception as e:
        log_factory_error("/api/factory/loop/stop", e)
        return _err("INTERNAL_ERROR")


def log_factory_error(route: str, error: Exception) -> None:
    try:
        from nexus_scalp.web.server import log_web_error

        log_web_error(logger, route, None, error, context={"msg": "Strategy Factory route failed"})
    except Exception:
        logger.error("[STRATEGY_FACTORY] route failed", route=route, error=str(error))


__all__ = ["router"]
