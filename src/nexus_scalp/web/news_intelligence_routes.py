"""News Intelligence API routes (PRODUCTION — News Intelligence 0100).

Thin handlers over the News AI service. Every route:
  * reads the dedicated news.db via the NewsEngine (isolated subsystem);
  * never exposes API keys / secrets to the frontend;
  * never raises into the caller (consistent error envelope);
  * reuses the Factory LLM provider as the single LLM source of truth.

Endpoints (§39):
  GET  /api/news/ai-status        — secret-free AI readiness (§5/§6)
  POST /api/news/analyze/{id}      — per-article AI analysis (§9/§10)
  POST /api/news/analyze/batch     — bounded-concurrency batch analysis (§22)
  POST /api/news/auto-prune        — recoverable IRRELEVANT classification (§27)
  POST /api/news/{id}/restore      — recover IRRELEVANT -> ACTIVE (§36)
  GET  /api/news?status=...        — existing feed gains status filtering (§35)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.server import serialize_enums

logger = get_logger("nexus_scalp.web.news_intelligence")

router = APIRouter(prefix="/api/news", tags=["news-intelligence"])


def _resolve_settings_svc(engine: Any | None) -> Any | None:
    cands = []
    if engine is not None:
        cands.append(engine)
        for attr in ("live_engine", "engine", "app_engine"):
            try:
                nxt = getattr(engine, attr, None)
                if nxt is not None:
                    cands.append(nxt)
            except Exception:
                pass
    for obj in cands:
        try:
            svc = getattr(obj, "settings_service", None)
            if svc is not None:
                return svc
        except Exception:
            continue
    try:
        from nexus_scalp.settings import load_settings_service

        return load_settings_service()
    except Exception:
        return None


def _engine(request: Request) -> Any | None:
    engine = request.app.state.engine
    if not engine or not getattr(engine, "news_engine", None):
        return None
    return engine


def _settings(request: Request) -> Any | None:
    engine = request.app.state.engine
    svc = getattr(engine, "settings_service", None) if engine else None
    if svc is not None:
        return svc
    try:
        from nexus_scalp.settings import load_settings_service

        return load_settings_service()
    except Exception:
        return None


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"available": True, **payload}


def _err(code: str, **kw: Any) -> dict[str, Any]:
    from nexus_scalp.web.server import new_request_id, safe_error_payload

    return safe_error_payload(code=code, request_id=new_request_id(), **kw)


# ---------------------------------------------------------------------------
# GET /api/news/ai-status  (§5 / §6) — secret-free AI readiness
# ---------------------------------------------------------------------------


@router.get("/ai-status")
def news_ai_status(request: Request) -> dict[str, Any]:
    """Lightweight AI readiness. Never performs an LLM completion and never
    returns the API key / encrypted secret / auth header.

    Does NOT require NewsEngine — the provider is the Factory LLM config
    on SettingsService, so status is available even when the news subsystem
    is idle/unavailable."""
    try:
        from nexus_scalp.news.ai_service import get_ai_status

        engine = request.app.state.engine
        svc = _settings(request)
        status = get_ai_status(engine=engine, settings_service=svc)
        return serialize_enums(_ok({"ai_status": status.to_dict()}))
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[NEWS_AI] status failed", error=str(e))
        return _err("INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# POST /api/news/analyze/{id}  (§9 / §10) — per-article AI analysis
# ---------------------------------------------------------------------------


@router.post("/analyze/{article_id}")
def news_analyze_article(article_id: str, request: Request) -> dict[str, Any]:
    """Analyze one article with the Factory LLM provider."""
    engine = _engine(request)
    if engine is None:
        return _err("NEWS_UNAVAILABLE", message="News subsystem not enabled")
    db = engine.news_engine.db
    svc = _settings(request)
    force = bool((request.query_params.get("force", "false")).lower() == "true")
    try:
        from nexus_scalp.news.ai_service import analyze_article_with_ai

        result = analyze_article_with_ai(
            db, article_id, engine=engine, settings_service=svc, force=force
        )
        payload = result.to_dict()
        if result.status == "failed":
            # Map known failure classes to truthful HTTP-friendly codes.
            if result.error_detail == "ARTICLE_NOT_FOUND":
                return _err("ARTICLE_NOT_FOUND")
            if "not configured" in result.error_detail:
                return _err(
                    "AI_NOT_CONFIGURED",
                    message="News AI analysis requires the Strategy Factory LLM configuration.",
                )
            return _err("AI_ANALYSIS_FAILED", message=result.error_detail)
        return serialize_enums(_ok(payload))
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[NEWS_AI] analyze failed", article_id=article_id, error=str(e))
        return _err("INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# POST /api/news/analyze/batch  (§22 / §51) — bounded-concurrency batch
# ---------------------------------------------------------------------------


@router.post("/analyze/batch")
def news_analyze_batch(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bounded-concurrency batch AI analysis. Per-item failures are isolated
    and reported individually; the whole batch does not fail."""
    engine = _engine(request)
    if engine is None:
        return _err("NEWS_UNAVAILABLE", message="News subsystem not enabled")
    payload = payload or {}
    ids = payload.get("article_ids") or []
    if not isinstance(ids, list) or not ids:
        return _err("INVALID_REQUEST", message="article_ids list required")
    # Safety cap (§51 — bounded, provider-aware).
    from nexus_scalp.news.ai_service import NEWS_AI_BATCH_CONCURRENCY

    ids = [str(i) for i in ids][:200]
    db = engine.news_engine.db
    svc = _settings(request)
    results: list[dict[str, Any]] = []
    completed = failed = skipped = 0
    from concurrent.futures import ThreadPoolExecutor

    def _one(aid: str) -> dict[str, Any]:
        from nexus_scalp.news.ai_service import analyze_article_with_ai

        try:
            r = analyze_article_with_ai(db, aid, engine=engine, settings_service=svc)
            return r.to_dict()
        except Exception as e:  # pragma: no cover - defensive
            return {
                "status": "failed",
                "article_id": aid,
                "analysis_status": "failed",
                "error_detail": f"batch worker error: {type(e).__name__}",
            }

    with ThreadPoolExecutor(max_workers=NEWS_AI_BATCH_CONCURRENCY) as ex:
        for r in ex.map(_one, ids):
            results.append(r)
            if r.get("status") == "completed":
                completed += 1
            elif r.get("status") == "skipped":
                skipped += 1
            else:
                failed += 1
    return serialize_enums(
        _ok(
            {
                "results": results,
                "total": len(ids),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
            }
        )
    )


# ---------------------------------------------------------------------------
# POST /api/news/auto-prune  (§27 / §28 / §29) — recoverable IRRELEVANT
# ---------------------------------------------------------------------------


@router.post("/auto-prune")
def news_auto_prune(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pro Mode: mark low-signal, non-XAUUSD-relevant articles IRRELEVANT.

    Original records are preserved; only a recoverable status transitions.
    Idempotent: a second call produces zero new changes.
    """
    engine = _engine(request)
    if engine is None:
        return _err("NEWS_UNAVAILABLE", message="News subsystem not enabled")
    # Pro Mode gate: the news subsystem must be enabled (backend-enforced, §31).
    payload = payload or {}
    actor = str(payload.get("actor", "pro_user") or "pro_user")
    db = engine.news_engine.db
    try:
        from nexus_scalp.news.ai_service import auto_prune_irrelevant

        result = auto_prune_irrelevant(db, actor=actor)
        return serialize_enums(_ok(result.to_dict()))
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[NEWS_PRUNE] failed", error=str(e))
        return _err("INTERNAL_ERROR")


@router.post("/{article_id}/restore")
def news_restore_article(article_id: str, request: Request) -> dict[str, Any]:
    """Recoverably restore an IRRELEVANT article to ACTIVE (§36)."""
    engine = _engine(request)
    if engine is None:
        return _err("NEWS_UNAVAILABLE", message="News subsystem not enabled")
    db = engine.news_engine.db
    try:
        from nexus_scalp.news.ai_service import restore_article

        result = restore_article(db, article_id, actor="pro_user")
        if not result.get("ok"):
            return _err(result.get("error", "RESTORE_FAILED"))
        return serialize_enums(_ok(result))
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[NEWS_RESTORE] failed", article_id=article_id, error=str(e))
        return _err("INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# PRO AUTO — live console, full drain, junk purge (News Tab console)
# ---------------------------------------------------------------------------


@router.get("/pro/status")
def news_pro_status(request: Request) -> dict[str, Any]:
    """PRO status: auto toggle + Factory provider + analysis progress.
    Falls back to the on-disk DB when the news subsystem is idle so the
    console never hides provider readiness (Fix: your Factory key was
    configured but /pro/status returned NEWS_UNAVAILABLE when NewsEngine
    had not started)."""
    engine = _engine(request)
    svc = _resolve_settings_svc(engine) if engine else _settings(request)
    try:
        from nexus_scalp.news.pro_auto import console_status, provider_status_for_console

        if engine is not None:
            db = engine.news_engine.db
        else:
            from nexus_scalp.news.config import NewsConfig
            from nexus_scalp.news.database import NewsDatabase

            db = NewsDatabase(NewsConfig().db_path)
            db.initialize_schema()
        total = db.count_articles()
        # Unbounded pending (not a 500/2000-limited window scan) so the badge
        # stays accurate when total exceeds the window (2139 total -> 316 vs 1039).
        try:
            pending = int(db.count_pending_analysis())
        except Exception:
            pending = sum(
                1
                for r in db.list_articles(limit=500, include_duplicates=False)
                if db.get_analysis(r["article_id"]) is None
            )
        status_counts = db.count_articles_by_status()
        last = db.list_ai_analysis(limit=1)
        latest_ai = dict(last[0]) if last else None
        prov = provider_status_for_console(engine=engine, settings_service=svc)
        return serialize_enums(
            _ok(
                {
                    "console": console_status(),
                    "counts": {
                        "total": int(total),
                        "pending": int(pending),
                        "status_counts": status_counts,
                    },
                    "latest_ai": latest_ai,
                    "provider": prov,
                }
            )
        )
    except Exception as e:
        logger.warning("[NEWS_PRO_STATUS] failed", error=str(e))
        return _err("INTERNAL_ERROR")


@router.get("/pro/console")
def news_pro_console(request: Request, limit: int = 200, since_seq: int = 0) -> dict[str, Any]:
    """Live console feed: every pass/answer/error, ordered by seq.
    Always available (in-memory ring survives even when the news subsystem
    is idle) so the News tab never shows an empty console due to NEWS_UNAVAILABLE.
    Query params: limit (1..500), since_seq (poll from last seen seq - 0 for all).
    Frontend polls this every 1-2s when the News tab is active.
    """
    try:
        from nexus_scalp.news.pro_auto import get_console_history

        entries = get_console_history(limit=limit, since_seq=since_seq)
        return _ok({"entries": entries, "count": len(entries)})
    except Exception as e:  # pragma: no cover
        logger.warning("[NEWS_CONSOLE] failed", error=str(e))
        return _err("INTERNAL_ERROR")


@router.post("/pro/analyze-all")
def news_pro_analyze_all(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """PRO manual trigger: drain ALL unanalyzed articles via Factory LLM + junk purge.

    Uses the same run_pro_cycle the worker uses when the auto toggle is ON,
    so the "everything passes" trace goes to the same console. Rate-limited
    by a 15s in-memory cooldown to avoid accidental double-drain hammering.
    """
    engine = _engine(request)
    if engine is None:
        return _err("NEWS_UNAVAILABLE", message="News subsystem not enabled")
    import time as _time

    last = float(getattr(request.app.state, "_news_pro_last_trigger", 0.0) or 0.0)
    if _time.time() - last < 15.0 and not bool((payload or {}).get("force")):
        return _err(
            "COOLDOWN",
            message=f"Analyze ALL cooldown — retry in {int(15 - (_time.time() - last))}s",
        )
    request.app.state._news_pro_last_trigger = _time.time()
    payload = payload or {}
    limit = max(10, min(int(payload.get("limit", 200) or 200), 2000))
    svc = _resolve_settings_svc(engine)
    try:
        from nexus_scalp.news.pro_auto import run_pro_cycle

        db = engine.news_engine.db
        summary = run_pro_cycle(
            db, engine=engine, settings_service=svc, limit=limit, prune_junk=True
        )
        return serialize_enums(_ok({"summary": summary}))
    except Exception as e:  # pragma: no cover
        logger.warning("[NEWS_PRO_ANALYZE_ALL] failed", error=str(e))
        return _err("INTERNAL_ERROR")


@router.post("/pro/purge")
def news_pro_purge(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """News console purge: reports IRRELEVANT counts or hard-deletes them.

    Body: { hard_delete: bool, older_than_hours: float|null, limit: int }
    Default is soft (counts only). hard_delete=true actually DELETEs rows so
    the DB stays clear. Answers are traced to the console.
    """
    engine = _engine(request)
    if engine is None:
        return _err("NEWS_UNAVAILABLE", message="News subsystem not enabled")
    payload = payload or {}
    try:
        from nexus_scalp.news.pro_auto import purge_irrelevant

        db = engine.news_engine.db
        res = purge_irrelevant(
            db,
            hard_delete=bool(payload.get("hard_delete", False)),
            older_than_hours=payload.get("older_than_hours"),
            limit=int(payload.get("limit", 5000) or 5000),
        )
        return serialize_enums(_ok(res))
    except Exception as e:  # pragma: no cover
        logger.warning("[NEWS_PRO_PURGE] failed", error=str(e))
        return _err("INTERNAL_ERROR")


@router.get("/pro/latest-answers")
def news_pro_latest_answers(request: Request, limit: int = 20) -> dict[str, Any]:
    """Latest PRO answers from the AI layer (full trace via console for pass logs).
    Falls back to opening the on-disk news DB when the news subsystem is idle
    so answers remain visible."""
    engine = _engine(request)
    try:
        if engine is not None:
            rows = engine.news_engine.db.list_ai_analysis(limit=max(1, min(int(limit), 100)))
        else:
            from nexus_scalp.news.config import NewsConfig
            from nexus_scalp.news.database import NewsDatabase

            db = NewsDatabase(NewsConfig().db_path)
            db.initialize_schema()
            rows = db.list_ai_analysis(limit=max(1, min(int(limit), 100)))
        return _ok({"answers": rows, "count": len(rows)})
    except Exception as e:  # pragma: no cover
        logger.warning("[NEWS_PRO_ANSWERS] failed", error=str(e))
        return _err("INTERNAL_ERROR")
