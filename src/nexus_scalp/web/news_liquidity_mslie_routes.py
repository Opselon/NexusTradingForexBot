"""News / Liquidity / MSLIE — REST API routes (PHASE 12 + PHASE 18/20/22 blocks).

Extracted VERBATIM from the former monolith ``server.py`` (CHG-0032 Step 3C,
behavior-preserving; Agent-5 modularization pass). All routes are closures
over ``app.state.engine`` exactly as before. Nothing here mutates financial
truth or executes orders; news/liquidity/mslie are isolated advisory
subsystems whose routes return ``available=False`` when disabled.

Surface (paths unchanged): /api/news* (reads, toggles, auto-analysis,
refresh, self-heal, keywords, {article_id}), /api/liquidity/{state,features,
toggle}, /api/mslie/{status,features}.

BOUNDARY: closures over ``app.state`` only; no live-path imports;
follows the register(app) pattern of model_governance_routes (Step 3A) and
intelligence_routes (Step 3B).

USED BY: server.create_app.
DO-NOT-PUT-HERE: research/diagnostics/account routes (server.py slice),
news AI analysis routes (news_intelligence_routes.py).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.errors import log_web_error

logger = get_logger("nexus_scalp.web.news_liquidity_mslie_routes")

router = APIRouter()


def register_news_liquidity_mslie_routes(
    app: Any, _err: Any, serialize_enums: Any, time_mod: Any
) -> None:
    """Attach news/liquidity/mslie routes (closures over ``app``).

    ``_err``/``serialize_enums`` resolve from server.py exactly as the
    closures did inside create_app; ``time_mod`` is the ``time`` module
    (cool-down logic uses time.monotonic, unchanged).
    """
    # The verbatim block body references ``time`` (news refresh cooldown uses
    # time.monotonic). time_mod is accepted for call-site parity with the
    # registration call and intentionally unused.
    del time_mod
    import time as _time

    time = _time
    # =========================================================================
    # PHASE 12: NEWS INTELLIGENCE API (read + control, isolated subsystem)
    # -------------------------------------------------------------------------
    # Every route reads the dedicated news.db via the NewsEngine. When the
    # news subsystem is disabled/unavailable, routes return available=False;
    # they never fabricate data and never affect trading.
    # =========================================================================

    def _news() -> Any:
        engine = app.state.engine
        if not engine or not getattr(engine, "news_engine", None):
            return None
        return engine.news_engine

    @app.get("/api/news")
    def get_news(
        limit: int = 50, include_duplicates: bool = False, status: str | None = None
    ) -> dict[str, Any]:
        """Live news feed (canonical articles).

        `status` filters by article_status (ACTIVE / IRRELEVANT). When omitted,
        the default view excludes IRRELEVANT articles for operator focus while
        historical/irrelevant data remains reachable via status=ALL/IRRELEVANT.
        """
        news = _news()
        if news is None:
            return {"available": False}
        try:
            status_filter = status if status and status.upper() not in ("ALL", "NONE") else None
            rows = news.db.list_articles(
                limit=limit, include_duplicates=include_duplicates, status_filter=status_filter
            )
            from nexus_scalp.news.analysis.keywords import keyword_hits_for_article

            out = []
            for r in rows:
                analysis = news.db.get_analysis(r["article_id"])
                consensus = news.db.get_consensus(r["article_id"])
                ai = news.db.get_ai_analysis(r["article_id"])
                out.append(
                    {
                        "article_id": r["article_id"],
                        "title": r["title"],
                        "summary": r["summary"],
                        "source_id": r["source_id"],
                        "source_name": r["source_name"],
                        "published_at": r["published_at"],
                        "importance": r["importance"],
                        "importance_score": r["importance_score"],
                        "is_duplicate": bool(r["is_duplicate"]),
                        "article_status": str(r.get("article_status", "ACTIVE") or "ACTIVE"),
                        "evidence_sources": r["evidence_sources"],
                        "analysis": analysis,
                        "ai_analysis": ai,
                        "consensus": consensus,
                        "keyword_hits": keyword_hits_for_article(r),
                    }
                )
            status_counts = news.db.count_articles_by_status()
            return {"available": True, "articles": out, "status_counts": status_counts}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News feed failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/latest")
    def get_news_latest(limit: int = 10) -> dict[str, Any]:
        """Latest canonical news articles."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            rows = news.db.list_articles(limit=limit, include_duplicates=False)
            return {
                "available": True,
                "articles": [
                    {
                        "article_id": r["article_id"],
                        "title": r["title"],
                        "source_name": r["source_name"],
                        "published_at": r["published_at"],
                        "importance": r["importance"],
                        "importance_score": r["importance_score"],
                    }
                    for r in rows
                ],
            }
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/impact")
    def get_news_impact(asset: str = "XAUUSD", limit: int = 50) -> dict[str, Any]:
        """Recent impact records for an asset (XAUUSD default)."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            rows = news.db.list_recent_impacts(asset=asset, limit=limit)
            return {"available": True, "impacts": rows}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/timeline")
    def get_news_timeline(
        bucket_sec: int = 900, hours_back: int = 24, asset: str = "XAUUSD"
    ) -> dict[str, Any]:
        """Impact timeline aggregated into time buckets for the chart.

        bucket_sec map: 900 = 15m, 3600 = 1h, 14400 = 4h, 86400 = 1d.
        Returns buckets with bullish/bearish/neutral impact sums per bucket.
        """
        news = _news()
        if news is None:
            return {"available": False}
        try:
            buckets = news.db.impact_timeline(
                bucket_sec=bucket_sec, hours_back=hours_back, asset=asset
            )
            return {"available": True, "asset": asset, "buckets": buckets}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News timeline failed"})
            return _err("INTERNAL_ERROR")

    # =========================================================================
    # TASK-02-70D-INTEGRATION: LIQUIDITY INTELLIGENCE API
    # -------------------------------------------------------------------------
    # Real backend state only. The UI toggle routes through POST
    # /api/liquidity/toggle which persists via SettingsService
    # (model.liquidity_features_enabled, HOT_RESTRICTED) and hot-applies the
    # governor. Never UI-only, never fake values (brief 6/10/17/25).
    # =========================================================================
    def _liquidity_governor() -> Any:
        """Resolve the live engine's liquidity governor (standalone fallback)."""
        engine = app.state.engine
        gov = getattr(engine, "liquidity_governor", None) if engine is not None else None
        if gov is not None:
            return gov
        from nexus_scalp.features.liquidity_runtime import LiquidityGovernor

        return LiquidityGovernor(enabled=False)

    # =========================================================================
    # MSLIE: MARKET STRUCTURE & LIQUIDITY INTELLIGENCE API (read-only)
    # -------------------------------------------------------------------------
    # Market perception layer — regime/swings/liquidity map/sweeps/breakout/
    # smart-money. The UI is a pure renderer of backend state; nothing here
    # mutates execution, risk or the feature contract (INV-002/INV-009).
    # =========================================================================

    def _mslie_engine() -> Any:
        engine = app.state.engine
        ms = getattr(engine, "mslie_engine", None) if engine is not None else None
        if ms is not None:
            return ms
        from nexus_scalp.mslie import MarketStructureEngine

        return MarketStructureEngine(symbol="XAUUSD", timeframe="M1")

    @app.get("/api/mslie/status")
    def get_mslie_status() -> dict[str, Any]:
        """Canonical MSLIE status: engine status, market context, liquidity
        map, last sweep and the feature vector (real values only)."""
        try:
            ms = _mslie_engine()
            return {"success": True, **ms.get_debug_status()}
        except Exception as e:
            log_web_error(
                logger,
                "/api/mslie/status",
                None,
                e,
                context={"msg": "MSLIE status introspection failed"},
            )
            return {
                "success": False,
                "available": False,
                "status": "UNAVAILABLE",
                "reason": "MSLIE_STATE_ERROR",
            }

    @app.get("/api/mslie/features")
    def get_mslie_features() -> dict[str, Any]:
        """The full MarketIntelligenceFeatureVectorV1 (developer mode:
        inspect model input, copy JSON, export snapshot)."""
        try:
            ms = _mslie_engine()
            vector = ms.generate_feature_vector()
            if vector is None:
                return {
                    "success": True,
                    "available": False,
                    "reason": "NO_MSLIE_VECTOR",
                }
            return {"success": True, "available": True, "vector": vector.to_dict()}
        except Exception as e:
            log_web_error(
                logger,
                "/api/mslie/features",
                None,
                e,
                context={"msg": "MSLIE features introspection failed"},
            )
            return {"success": False, "available": False, "reason": "MSLIE_FEATURES_ERROR"}

    @app.get("/api/liquidity/state")
    def get_liquidity_state() -> dict[str, Any]:
        """Canonical liquidity status: enabled/available/status/source/latency/
        causal state + ten real values + model compatibility."""
        try:
            gov = _liquidity_governor()
            return {"success": True, **gov.report()}
        except Exception as e:
            log_web_error(
                logger,
                "/api/liquidity/state",
                None,
                e,
                context={"msg": "Liquidity state introspection failed"},
            )
            return {
                "success": False,
                "enabled": False,
                "available": False,
                "status": "UNAVAILABLE",
                "causal_state": "INVALID",
                "reason": "LIQUIDITY_STATE_ERROR",
            }

    @app.get("/api/liquidity/features")
    def get_liquidity_features() -> dict[str, Any]:
        """Ten individual liquidity values (real runtime snapshot; brief 17)."""
        try:
            gov = _liquidity_governor()
            return {"success": True, **gov.snapshot_payload()}
        except Exception as e:
            log_web_error(
                logger,
                "/api/liquidity/features",
                None,
                e,
                context={"msg": "Liquidity features introspection failed"},
            )
            return {
                "success": False,
                "schema_id": "scalp_v3",
                "dimension": 70,
                "timestamp": None,
                "source": "UNAVAILABLE",
                "features": {},
                "available": False,
                "reason": "LIQUIDITY_FEATURES_ERROR",
            }

    @app.post("/api/liquidity/toggle")
    def set_liquidity_toggle(payload: dict[str, Any]) -> dict[str, Any]:
        """Enable/disable Liquidity Intelligence (real backend config).

        Flow: UI -> POST -> backend validates -> governor persists via
        SettingsService -> runtime flag applied -> new status returned.
        NEVER restarts the engine; NEVER touches orders/risk/execution.
        """
        try:
            desired = bool(payload.get("enabled"))
            gov = _liquidity_governor()
            gov.set_enabled(desired, actor="web")
            return {"success": True, **gov.report()}
        except Exception as e:
            log_web_error(
                logger,
                "/api/liquidity/toggle",
                None,
                e,
                context={"msg": "Liquidity toggle failed"},
            )
            return {"success": False, "error": "LIQUIDITY_TOGGLE_FAILED"}

    @app.get("/api/news/toggle-state")
    def get_news_toggle_state() -> dict[str, Any]:
        """Current news toggle state (Pro Hot Reload — read side).

        Returns {enabled, runtime_version, source}. Enabled is authoritative:
        engine._news_enabled + snapshot truth (never UI-only).
        """
        engine = app.state.engine
        try:
            enabled = bool(getattr(engine, "_news_enabled", False)) if engine else False
            snap = None
            runtime_version = None
            if engine is not None and hasattr(engine, "runtime_config"):
                snap = engine.runtime_config.get_snapshot()
                runtime_version = snap.version
                # snapshot enabled is the validated persisted value
                enabled = bool(snap.news.enabled)
            return {
                "success": True,
                "enabled": enabled,
                "runtime_version": runtime_version,
                "source": getattr(snap, "source", "") if snap else "",
            }
        except Exception as e:
            log_web_error(logger, "/api/news/toggle-state", None, e)
            return {"success": False, "enabled": False, "error": "NEWS_TOGGLE_STATE_FAILED"}

    @app.post("/api/news/toggle")
    def set_news_toggle(payload: dict[str, Any]) -> dict[str, Any]:
        """Pro Hot Reload: enable/disable the News Intelligence engine live.

        Flow: UI toggle -> POST {enabled} -> runtime_config.apply(news.enabled)
        -> atomic snapshot swap -> _sync_runtime_config -> engine hot-swap
        (construct / tear down worker+gate) -> new toggle state returned.
        Never restarts the engine; never touches orders/risk/execution.
        News can still never force a trade (bounded gate invariant).
        """
        engine = app.state.engine
        if engine is None or not hasattr(engine, "runtime_config"):
            raise HTTPException(status_code=400, detail="Trading Engine offline.")
        raw = payload.get("enabled")
        if raw is None:
            raise HTTPException(status_code=422, detail="enabled (bool) required")
        desired = bool(raw)
        try:
            report = engine.apply_runtime_update(
                {"news.enabled": desired}, source="WEB_NEWS_TOGGLE", actor="web"
            )
            if not report.success:
                return {
                    "success": False,
                    "enabled": bool(getattr(engine, "_news_enabled", False)),
                    "error": report.reason or "NEWS_TOGGLE_REJECTED",
                    "runtime_version": engine.runtime_config.get_version(),
                }
            snap = engine.runtime_config.get_snapshot()
            # /api/news/health-style payload for the UI badge
            return {
                "success": True,
                "enabled": bool(snap.news.enabled),
                "runtime_version": snap.version,
                "source": snap.source,
                "worker_interval_sec": snap.news.worker_interval_sec,
            }
        except HTTPException:
            raise
        except Exception as e:
            log_web_error(logger, "/api/news/toggle", None, e)
            return {
                "success": False,
                "enabled": bool(getattr(engine, "_news_enabled", False)),
                "error": "NEWS_TOGGLE_FAILED",
            }

    # ------------------------------------------------------------------
    # News Auto Analysis (local deterministic, NO API key / NO endpoint).
    # UI can ENABLE or DISABLE it. OFF (default) = worker still ingests
    # and refreshes context, but skips automatic deterministic analysis
    # cycles. ON = every worker cycle analyzes recent unanalyzed articles
    # with the local rule-based engine for more accuracy downstream.
    # Manual POST /api/news/analyze/{id} always works regardless.
    # ------------------------------------------------------------------
    @app.get("/api/news/auto-analysis")
    def get_news_auto_analysis() -> dict[str, Any]:
        """Current News Auto Analysis toggle (read side)."""
        engine = app.state.engine
        try:
            enabled = False
            snap = None
            runtime_version = None
            if engine is not None and hasattr(engine, "runtime_config"):
                snap = engine.runtime_config.get_snapshot()
                runtime_version = snap.version
                enabled = bool(getattr(snap.news, "auto_analysis_enabled", False))
            else:
                enabled = (
                    bool(getattr(engine, "_news_auto_analysis_enabled", False)) if engine else False
                )
            # also surface worker gate truth when available
            worker_gate = None
            if engine is not None and getattr(engine, "news_worker", None) is not None:
                worker_gate = bool(getattr(engine.news_worker, "auto_analysis_enabled", enabled))
            return {
                "success": True,
                "enabled": enabled,
                "worker_enabled": worker_gate,
                "runtime_version": runtime_version,
                "source": getattr(snap, "source", "") if snap else "",
            }
        except Exception as e:
            log_web_error(logger, "/api/news/auto-analysis", None, e)
            return {"success": False, "enabled": False, "error": "NEWS_AUTO_ANALYSIS_STATE_FAILED"}

    @app.post("/api/news/auto-analysis")
    def set_news_auto_analysis(payload: dict[str, Any]) -> dict[str, Any]:
        """Enable/disable News Auto Analysis (hot-reload, persisted)."""
        engine = app.state.engine
        if engine is None or not hasattr(engine, "runtime_config"):
            raise HTTPException(status_code=400, detail="Trading Engine offline.")
        raw = payload.get("enabled") if isinstance(payload, dict) else None
        if raw is None:
            raise HTTPException(status_code=422, detail="enabled (bool) required")
        desired = bool(raw)
        try:
            report = engine.apply_runtime_update(
                {"news.auto_analysis_enabled": desired},
                source="WEB_NEWS_AUTO_ANALYSIS",
                actor="web",
            )
            if not report.success:
                cur = bool(getattr(engine, "_news_auto_analysis_enabled", False))
                return {
                    "success": False,
                    "enabled": cur,
                    "error": report.reason or "NEWS_AUTO_ANALYSIS_REJECTED",
                    "runtime_version": engine.runtime_config.get_version(),
                }
            snap = engine.runtime_config.get_snapshot()
            # _sync already propagated to worker; re-read worker truth
            worker_gate = None
            if getattr(engine, "news_worker", None) is not None:
                worker_gate = bool(getattr(engine.news_worker, "auto_analysis_enabled", desired))
            return {
                "success": True,
                "enabled": bool(snap.news.auto_analysis_enabled),
                "worker_enabled": worker_gate,
                "runtime_version": snap.version,
                "source": snap.source,
            }
        except HTTPException:
            raise
        except Exception as e:
            log_web_error(logger, "/api/news/auto-analysis", None, e)
            return {
                "success": False,
                "enabled": bool(getattr(engine, "_news_auto_analysis_enabled", False)),
                "error": "NEWS_AUTO_ANALYSIS_FAILED",
            }

    @app.get("/api/news/state")
    def get_news_state() -> dict[str, Any]:
        """Current news state (NORMAL/ELEVATED/HIGH_IMPACT/CONFLICTED/
        BREAKING/STALE) from the cached live context."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            ctx = news.current_context(force=True)
            return {
                "available": True,
                "state": ctx.state.value,
                "timestamp": ctx.timestamp.isoformat(),
                "bullish_score": ctx.bullish_score,
                "bearish_score": ctx.bearish_score,
                "confidence": ctx.confidence,
                "conflict_score": ctx.conflict_score,
                "freshness": ctx.freshness,
                "xauusd_relevance": ctx.xauusd_relevance,
                "usd_relevance": ctx.usd_relevance,
                "active_event_count": ctx.active_event_count,
                "stale": ctx.stale,
                "news_adjustment": ctx.news_adjustment,
                "active_high_impact": ctx.active_high_impact,
            }
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/sources")
    def get_news_sources(enabled_only: bool = False) -> dict[str, Any]:
        """Source registry + health."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            sources = news.db.list_sources(enabled_only=enabled_only)
            health = news.db.list_health()
            health_by_id = {h["source_id"]: h for h in health}
            for s in sources:
                s["health"] = health_by_id.get(s["source_id"])
            return {"available": True, "sources": sources}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/health")
    def get_news_health() -> dict[str, Any]:
        """News subsystem health + worker telemetry."""
        news = _news()
        if news is None:
            return {"available": False, "enabled": False}
        try:
            health = news.health()
            engine = app.state.engine
            worker_status = None
            if engine and getattr(engine, "news_worker", None) is not None:
                from nexus_scalp.news.worker import format_news_worker_status

                worker_status = format_news_worker_status(engine.news_worker)
            return {"available": True, "enabled": True, "health": health, "worker": worker_status}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/analysis/{article_id}")
    def get_news_analysis(article_id: str) -> dict[str, Any]:
        """Single article analysis."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            analysis = news.db.get_analysis(article_id)
            run = None
            if analysis:
                run = news.db.get_run(analysis["run_id"])
            return {"available": True, "analysis": analysis, "run": run}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/trades/{trade_id}")
    def get_news_trade_links(trade_id: str) -> dict[str, Any]:
        """News links for one trade."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            links = news.db.list_trade_links(trade_id=trade_id)
            return {"available": True, "trade_id": trade_id, "links": links}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.post("/api/news/analyze/{article_id}")
    def post_news_analyze(article_id: str, request: Request) -> dict[str, Any]:
        """AI Analyze: enqueue a background analysis job (never blocks).

        Idempotent: already-analyzed stories return SKIPPED_ALREADY_ANALYZED
        unless ?force=true is passed. Prevents re-analysis confusion.
        """
        news = _news()
        if news is None:
            return {"available": False}
        force = False
        try:
            force = bool((request.query_params.get("force", "false")).lower() == "true")
        except Exception:
            pass
        # Idempotent short-circuit: don't re-queue already-analyzed stories
        try:
            if not force:
                art = news.db.get_article(article_id)
                ah = str((art or {}).get("article_hash") or "")
                if art and ah and news.db.is_analyzed_hash(ah):
                    return {
                        "available": True,
                        "ok": True,
                        "status": "SKIPPED_ALREADY_ANALYZED",
                        "article_id": article_id,
                        "reason": "hash already analyzed",
                    }
                if news.db.get_analysis(article_id) is not None:
                    return {
                        "available": True,
                        "ok": True,
                        "status": "SKIPPED_ALREADY_ANALYZED",
                        "article_id": article_id,
                        "reason": "article already analyzed",
                    }
        except Exception:
            pass
        engine = app.state.engine
        try:
            if engine and getattr(engine, "news_worker", None) is not None:
                job = engine.news_worker.enqueue_analysis(article_id, priority=0.9)
                return {"available": True, **job}
            result = news.analyze_article_id(article_id, force=force)
            return {"available": True, **result}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News analyze failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/news/refresh")
    def post_news_refresh() -> dict[str, Any]:
        """Trigger one ingestion + analysis pass (bounded).

        BANDWIDTH GUARD (2026-08-18): rapid clicks on "Fetch News" used to
        trigger a full multi-source re-fetch EVERY time (the fetcher has no
        shared cooldown). A per-server minimum interval is enforced here so
        repeated clicks within 60s return the cached result instead of
        hammering the RSS endpoints.
        """
        news = _news()
        if news is None:
            return {"available": False}
        try:
            now = time.monotonic()
            with app.state.news_refresh_lock:
                last = app.state.news_refresh_ts
                if now - last < 60.0:
                    remaining = int(60.0 - (now - last))
                    return {
                        "available": True,
                        "cooldown": remaining,
                        "ingested": {"sources_polled": 0, "new": 0, "duplicate": 0, "merged": 0},
                        "analyzed_count": 0,
                        "skipped": f"refresh cooldown active ({remaining}s)",
                    }
                app.state.news_refresh_ts = now
            ingest = news.ingest_cycle(max_sources=8)
            analyzed = news.analysis_cycle(limit=10)
            return {
                "available": True,
                "ingested": ingest,
                "analyzed_count": len(analyzed),
                "cooldown": 0,
            }
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.post("/api/news/self-heal")
    def post_news_self_heal() -> dict[str, Any]:
        """Rebuild derived news state from raw articles."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            return {"available": True, **news.self_heal()}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/keywords")
    def get_news_keywords(top_n: int = 25, category: str = "", q: str = "") -> dict[str, Any]:
        """Keyword analysis dataset: full library + live corpus coverage.

        The dataset is the deterministic keyword backbone of the local news
        analysis pipeline (200+ keywords across currencies, assets,
        institutions, macro topics, XAUUSD drivers, directional phrases,
        geopolitics, energy and FX pairs). Returns:
            * dataset meta (version, total_keywords, categories),
            * corpus coverage (articles scanned, total mentions, active
              keywords, direction distribution),
            * top keyword coverage (hits, share, category, bias),
            * optional filterable full listing (category / q).
        """
        news = _news()
        if news is None:
            return {"available": False}
        try:
            from nexus_scalp.news.analysis.keywords import (
                analyze_keyword_coverage,
                categories,
                get_keyword_dataset,
                keyword_count,
            )

            articles = news.db.list_articles(limit=500, include_duplicates=False)
            coverage = analyze_keyword_coverage(articles, top_n=top_n)

            listing = []
            for k in get_keyword_dataset():
                if category and k.category != category:
                    continue
                if q and q.lower() not in k.keyword.lower():
                    continue
                listing.append(
                    {
                        "keyword": k.keyword,
                        "category": k.category,
                        "topics": [t.value for t in k.topics],
                        "direction_bias": k.direction_bias.value,
                        "weight": k.weight,
                        "aliases": list(k.aliases),
                    }
                )

            return {
                "available": True,
                "dataset": {
                    "version": coverage.dataset_version,
                    "total_keywords": keyword_count(),
                    "categories": categories(),
                },
                "coverage": {
                    "articles_scanned": coverage.total_articles_scanned,
                    "total_mentions": coverage.total_mentions,
                    "active_keywords": coverage.active_keywords,
                    "direction_distribution": coverage.direction_distribution,
                    "top_keywords": [
                        {
                            "keyword": c.keyword,
                            "category": c.category,
                            "direction_bias": c.direction_bias.value,
                            "weight": c.weight,
                            "article_hits": c.article_hits,
                            "mention_count": c.mention_count,
                            "share": c.share,
                        }
                        for c in coverage.top_keywords
                    ],
                },
                "keywords": listing,
            }
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News keywords failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/{article_id}")
    def get_news_detail(article_id: str) -> dict[str, Any]:
        """News detail view: article + analysis + impacts + consensus +
        related + trade links + post-event records."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            art = news.db.get_article(article_id)
            if not art:
                return {"available": False, "error": "ARTICLE_NOT_FOUND"}
            analysis = news.db.get_analysis(article_id)
            impacts = news.db.get_impacts(article_id)
            consensus = news.db.get_consensus(article_id)
            entities = news.db.get_entities(article_id)
            topics = news.db.get_topics(article_id)
            related = news.db.list_related(article_id, limit=10)
            trade_links = news.db.list_article_trade_links(article_id)
            versions = news.db.latest_version(article_id)
            post_events = news.validator.list_records(article_id=article_id, limit=10)
            return {
                "available": True,
                "article": art,
                "analysis": analysis,
                "impacts": impacts,
                "consensus": consensus,
                "entities": entities,
                "topics": topics,
                "related": related,
                "trade_links": trade_links,
                "versions": [versions] if versions else [],
                "post_event_validation": post_events,
            }
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News detail failed"})
            return _err("INTERNAL_ERROR")
