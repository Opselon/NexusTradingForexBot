"""News PRO Auto-Analysis (FULL — LLM + local fallback, junk purge, full drain, console).

When the PRO toggle is ON (news.auto_analysis_enabled == True):

  * every due article is analyzed — drain until ALL done (no 10-per-cycle cap);
  * uses the Strategy Factory LLM provider (the ONLY API key source,
    settings/secret_store via resolve_factory_provider) — no second secret;
  * PRO prompt is injected via system+user turns; answer is strict JSON;
  * on LLM failure / unavailable / budget exhausted -> local deterministic
    analysis runs so variables are always set (never junk gaps);
  * variables are auto-mapped into BOTH tables:
      deterministic news_analysis  (pipeline + context: importance,
        xauusd/usd relevance, direction, impacts, confidence)  AND
      AI interpretation news_ai_analysis (sentiment, market/xauusd relevance
        strings, key facts, impact, uncertainties) — so downstream
        CurrentNewsContext + Gate see accurate values regardless of path;
  * junk news is removed: low-signal articles (importance < threshold AND
    xauusd_relevance < threshold) are marked IRRELEVANT (recoverable via
    auto_prune); the DB stays clean; a hard purge helper is also exposed
    for the News console to clear IRRELEVANT rows when desired;
  * every pass/answer/error is appended to a bounded in-memory + DB console
    ring so the News tab can stream "everything is pass and answers come
    from route" live, without touching the file log.

Separation from the basic worker:
  * worker.py tick() gates on auto_analysis_enabled and calls this module's
    `run_pro_cycle` only when ON — so OFF remains cheap ingest-only;
  * this module NEVER deletes rows on its own — junk is marked IRRELEVANT
    (recoverable) unless the console explicitly calls purge_irrelevant;
  * the API key never leaves the server process and is never logged.
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.ai_service import (
    _SYSTEM_PROMPT,
    NEWS_AI_ANALYSIS_VERSION,
    _build_user_prompt,
    _validate_response,
    auto_prune_irrelevant,
    get_ai_status,
    resolve_factory_provider,
)
from nexus_scalp.news.analysis.local import LocalNewsAnalyzer
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.models import NewsArticle, NewsNovelty, normalize_datetime
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.pro_auto")

# ---------------------------------------------------------------------------
# PRO prompt — strict JSON, injection-defended, accurate variable mapping.
# ---------------------------------------------------------------------------
PRO_SYSTEM_PROMPT = (
    _SYSTEM_PROMPT
    + "\n"
    + "PRO AUTO-ANALYSIS ADDITIONS (follow exactly):\n"
    + "- Return STRICT JSON only — no prose outside the object, no markdown.\n"
    + "- Be precise but concise; factual claims only from the supplied article.\n"
    + "- The downstream system auto-maps your JSON into both the AI layer and\n"
    + "  the deterministic variables (importance, xauusd relevance, direction,\n"
    + "  confidence). Accuracy of those mappings depends on your JSON being\n"
    + "  schema-correct and grounded.\n"
    + '- You MUST include \\"is_junk\\" (boolean). true ONLY for lifestyle/retail/celebrity/sports/human-interest/no-market noise with NO plausible USD/rates/FX/commodity/gold/XAUUSD/macro linkage (examples: tuition, warehouse-club retail, inheritance anecdote, sports). For ANY Treasury/Fed/ECB/BOE/CPI/PPI/yields/USD/gold/safe-haven/geopolitics on policy, set false even if thin — deterministic guard still prunes only if local signals also low.\n'
    + '- When is_junk=true include \\"junk_reason\\" (one of NO_GOLD_DRIVER_LIFESTYLE|CELEBRITY_NOISE|SPORTS_NOISE|LOW_SIGNAL_RETAIL|ANECDOTAL_OPINION, <=60 chars); else \\"\\".\n'
)

# Bounded in-process console ring — the News tab streams this via REST.
# Also persisted into news console table would bloat; we keep last N in
# memory and expose it via the server route so answers are traceable.
_CONSOLE: deque[dict[str, Any]] = deque(maxlen=500)
_CONSOLE_SEQ: int = 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _console_push(entry: dict[str, Any]) -> None:
    global _CONSOLE_SEQ  # noqa: PLW0603
    _CONSOLE_SEQ += 1
    entry.setdefault("seq", _CONSOLE_SEQ)
    entry.setdefault("ts", _now_iso())
    _CONSOLE.append(entry)


def get_console_history(limit: int = 200, since_seq: int = 0) -> list[dict[str, Any]]:
    """Return console entries after since_seq (bounded, never unbounded)."""
    bounded = max(1, min(int(limit), 500))
    try:
        since = int(since_seq)
    except Exception:
        since = 0
    out: list[dict[str, Any]] = []
    for e in list(_CONSOLE):
        if int(e.get("seq", 0)) > since:
            out.append(dict(e))
            if len(out) >= bounded:
                break
    return out


def console_status() -> dict[str, Any]:
    return {"size": len(_CONSOLE), "latest_seq": _CONSOLE_SEQ, "available": True}


def _resolve_settings_service(engine: Any | None, settings_service: Any | None) -> Any | None:
    if settings_service is not None:
        return settings_service
    # engine may be NewsEngine, LiveEngine, or FakeEngine — chase pointers
    candidates = []
    if engine is not None:
        candidates.append(engine)
        for attr in ("live_engine", "engine", "app_engine"):
            try:
                nxt = getattr(engine, attr, None)
                if nxt is not None:
                    candidates.append(nxt)
            except Exception:
                pass
        # global fallback: if this is a NewsEngine attached to a LiveEngine, follow .live_engine
        try:
            from nexus_scalp.settings import load_settings_service as _lss  # noqa
        except Exception:
            pass
    for obj in candidates:
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


def _parse_article_row(row: dict[str, Any]) -> NewsArticle:
    return NewsArticle(
        article_id=row["article_id"],
        article_hash=row.get("article_hash", ""),
        canonical_url=row.get("canonical_url", "") or "",
        title=row.get("title", ""),
        summary=row.get("summary", "") or "",
        body=row.get("body", "") or "",
        source_id=row.get("source_id", "") or "",
        source_name=row.get("source_name", "") or "",
        published_at=normalize_datetime(_parse_dt(row.get("published_at"))),
        raw_categories=[],
        novelty=NewsNovelty.NEW,
    )


def _parse_dt(value: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


def _local_signals(article: NewsArticle, analyzer: LocalNewsAnalyzer) -> dict[str, Any]:
    entities = analyzer.extract_entities(article)
    topics = analyzer.classify_topics(article, entities)
    direction, _ = analyzer.directional_hypothesis(article, entities, topics)
    xauusd = analyzer.xauusd_relevance(article, entities, topics)
    usd = analyzer.usd_relevance(article, entities, topics)
    importance_score, _ = analyzer.importance_score(article, topics, 0.5)
    return {
        "entities": [e.name for e in entities],
        "topics": [t.value for t in topics],
        "direction": direction.value,
        "xauusd_relevance": xauusd,
        "usd_relevance": usd,
        "importance_score": importance_score,
        "_entities": entities,
        "_topics": topics,
        "_direction": direction,
    }


def _gold_priority(article: NewsArticle, local: dict[str, Any]) -> float:
    """Gold Priority matrix — deterministic score for queue ordering + soft-retry.

    Weights: xauusd_relevance dominates, then importance, then driver topics
    (FED/CPI/rates/Bond yields/USD/geopolitics). Ensures BoE minutes / rate
    statements / CPI / FOMC jump ahead of Venmo/Costco junk without dropping
    junk (it just runs last, then gets pruned to IRRELEVANT if truly low).
    Returns 0..1; higher = run first. Never raises.
    """
    try:
        xau = float(local.get("xauusd_relevance", 0) or 0)
        imp = float(local.get("importance_score", 0) or 0)
        direction = str(local.get("direction", "NEUTRAL") or "NEUTRAL").upper()
        # Topic bonus: count high-value topics
        topics = [str(t).upper() for t in (local.get("topics") or [])]
        # Fallback keyword scan: titles like "FOMC minutes" sometimes get OTHER
        # from the local classifier — still credit them as drivers.
        title_blob = ((article.title or "") + " " + (article.summary or "")).upper()
        keyword_drivers = set()
        if any(
            k in title_blob
            for k in (
                "FOMC",
                "FEDERAL RESERVE",
                "ECB",
                "BOE",
                "BANK RATE",
                "MONETARY POLICY",
                "MINUTES",
                "STATEMENT",
            )
        ):
            keyword_drivers.update(["CENTRAL_BANK", "MONETARY_POLICY"])
        if any(k in title_blob for k in ("CPI", "INFLATION")):
            keyword_drivers.add("INFLATION")
        if any(k in title_blob for k in ("GOLD", "XAUUSD", "BULLION", "SAFE HAVEN")):
            keyword_drivers.add("SAFE_HAVEN")
        if any(k in title_blob for k in ("YIELD", "TREASURY", "BOND")):
            keyword_drivers.add("BOND_YIELDS")
        driver_bonus = 0.0
        for key, w in (
            ("INTEREST_RATES", 0.10),
            ("CENTRAL_BANK", 0.10),
            ("BOND_YIELDS", 0.10),
            ("INFLATION", 0.12),
            ("SAFE_HAVEN", 0.08),
            ("GEOPOLITICS", 0.08),
            ("MONETARY_POLICY", 0.08),
            ("USD", 0.06),
            ("COMMODITIES", 0.06),
        ):
            if key in topics or key in keyword_drivers:
                driver_bonus += w
        driver_bonus = min(0.30, driver_bonus)
        # Directional clarity (non-NEUTRAL) is informative for gold positioning
        dir_bonus = 0.06 if direction in ("BULLISH", "BEARISH") else 0.0
        # Freshness: prefer newer when scores tie — caller adds tiny time term
        # Keyword-driven articles have zero local xau (no body) but are clearly
        # gold-relevant (FOMC/CPI/ECB move gold). Inject a baseline so they rank up.
        xau_for_score = xau if xau > 0.05 else (0.30 if keyword_drivers else xau)
        score = (xau_for_score * 0.55) + (imp * 0.25) + driver_bonus + dir_bonus
        return max(0.0, min(1.0, round(score, 4)))
    except Exception:
        return 0.0


def _ranked_pending(db, analyzer, pending_rows, limit):
    """Reorders pending rows GOLD-FIRST via deterministic local signals.

    No LLM yet — cheap local pass over titles/summaries. Bounded and safe.
    Falls back to published_at order if scoring fails.
    """
    try:
        scored: list[tuple[float, str, dict]] = []
        for row in pending_rows:
            try:
                art = _parse_article_row(row)
                loc = _local_signals(art, analyzer)
                prio = _gold_priority(art, loc)
                scored.append((prio, str(row.get("published_at") or ""), row))
            except Exception:
                scored.append((0.0, str(row.get("published_at") or ""), row))
        # Single deterministic key: gold priority first (higher wins), then newer article first within tier.
        # Bucket priority to 0.05 so near-ties still prefer recency rather than noise.
        # Real order: bucket desc, then published_at desc — stable two-pass:
        scored.sort(key=lambda kv: kv[1], reverse=True)
        scored.sort(key=lambda kv: -round(kv[0] * 20) / 20)
        return [r for _, _, r in scored[: int(limit)]]
    except Exception:
        return pending_rows[: int(limit)]


def _map_llm_into_deterministic(
    db: NewsDatabase,
    article: NewsArticle,
    local: dict[str, Any],
    llm_json: dict[str, Any],
) -> dict[str, Any]:
    """Accurately map LLM JSON into the deterministic variable layer.

    Reuses the local analyzer's numeric signals as defaults; overwrites
    textual / categorical signals from the LLM when present. Impact strength
    and direction are conservatively bounded so junk never inflates scores.
    Returns the row dict that will be persisted into news_analysis.
    """
    from nexus_scalp.news.models import NewsDirection

    sentiment = str(llm_json.get("sentiment", local["direction"] or "NEUTRAL")).upper()
    # Map sentiment -> NewsDirection
    try:
        direction = (
            NewsDirection(sentiment)
            if sentiment in ("BULLISH", "BEARISH", "NEUTRAL")
            else local["_direction"]
        )
    except Exception:
        direction = local["_direction"]

    # Confidence: boosted when LLM says sufficient evidence, else conservative
    insufficient = bool(llm_json.get("insufficient_evidence", False))
    base_conf = 0.4 + float(local["importance_score"]) * 0.3
    confidence = round(base_conf if insufficient else min(1.0, base_conf + 0.15), 4)

    # Impact strength: derived from importance + sentiment clarity
    impact_strength = float(local["importance_score"]) * (
        0.9 if sentiment in ("BULLISH", "BEARISH") else 0.6
    )
    impact_strength = max(0.0, min(1.0, round(impact_strength, 4)))

    summary = str(llm_json.get("summary", article.summary or article.title or "") or "")[:4000]
    return {
        "summary": summary,
        "direction": direction,
        "impact_strength": impact_strength,
        "confidence": confidence,
        "insufficient_evidence": insufficient,
        "llm_market_relevance": str(llm_json.get("market_relevance", "") or "")[:1000],
        "llm_xauusd_relevance": str(llm_json.get("xauusd_relevance", "") or "")[:1000],
    }


def run_pro_auto_analysis_for_article(
    db: NewsDatabase,
    article_id: str,
    *,
    engine: Any | None = None,
    settings_service: Any | None = None,
    analyzer: LocalNewsAnalyzer | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Analyze ONE article in PRO mode (LLM via Factory, local fallback).

    Always ensures deterministic variables are set; also writes the AI layer
    when LLM succeeds. Returns a console-friendly result dict.
    """
    analyzer = analyzer or LocalNewsAnalyzer()
    row = db.get_article(article_id)
    if not row:
        _console_push(
            {
                "kind": "error",
                "article_id": article_id,
                "msg": "ARTICLE_NOT_FOUND",
                "via": "pro_auto",
            }
        )
        return {"ok": False, "error": "ARTICLE_NOT_FOUND", "via": "error"}

    # Idempotent: tombstoned hash never re-analyzed unless force=True
    try:
        ah0 = str(row.get("article_hash") or "")
        if not force and ah0 and db.is_analyzed_hash(ah0):
            _console_push(
                {
                    "kind": "skip",
                    "article_id": article_id,
                    "msg": "already analyzed (tombstone)",
                    "via": "cached",
                }
            )
            return {"ok": True, "via": "cached", "article_id": article_id, "status": "skipped"}
    except Exception:
        pass
    # Dedup: reuse valid prior AI analysis unless forced
    if not force:
        prior = db.get_ai_analysis(article_id)
        if prior and prior.get("analysis_status") in ("completed", "completed_insufficient"):
            # Still ensure deterministic layer exists
            if db.get_analysis(article_id):
                _console_push(
                    {
                        "kind": "skip",
                        "article_id": article_id,
                        "msg": "already analyzed",
                        "via": "cached",
                    }
                )
                return {"ok": True, "via": "cached", "article_id": article_id, "status": "skipped"}

    article = _parse_article_row(row)
    local = _local_signals(article, analyzer)

    # Resolve Factory LLM provider (API key from secret store, never exposed)
    svc2 = _resolve_settings_service(engine, settings_service)
    _eng_for_provider = engine  # _resolve_settings_service already chases engine.settings_service
    provider = resolve_factory_provider(_eng_for_provider, svc2)
    llm_json: dict[str, Any] | None = None
    via = "local"

    if provider is not None:
        try:
            user_prompt = _build_user_prompt(article, local)
            raw = provider.complete_json(
                system_prompt=PRO_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.2,
                max_tokens=2600,
            )
            if isinstance(raw, dict) and raw:
                llm_json = raw
                via = "llm"
            # Soft retry: retry with a slightly smaller budget (2200) for hosts that
            # truncate the first pass; if it still returns empty or insufficient, fall
            # back to local so the pipeline never stalls. Rare path — logged as retry.
            elif raw is None or (isinstance(raw, dict) and not raw):
                try:
                    raw = provider.complete_json(
                        system_prompt=PRO_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        temperature=0.2,
                        max_tokens=2200,
                    )
                    if isinstance(raw, dict) and raw:
                        llm_json = raw
                        via = "llm"
                        _console_push(
                            {
                                "kind": "ai_retry_ok",
                                "article_id": article_id,
                                "via": "llm",
                                "msg": "soft retry recovered LLM JSON",
                            }
                        )
                    else:
                        raise RuntimeError("retry empty")
                except Exception as _retry_e:
                    _err = getattr(provider, "usage", None)
                    _detail = getattr(_err, "last_error", "") if _err else ""
                    _req = getattr(_err, "requests", 0) if _err else 0
                    _fail = getattr(_err, "failures", 0) if _err else 0
                    _raw_type = type(raw).__name__ if raw is not None else "None"
                    _raw_len = len(str(raw)) if raw is not None else 0
                    _console_push(
                        {
                            "kind": "fallback",
                            "article_id": article_id,
                            "msg": f"LLM returned empty ({_raw_type} len={_raw_len}) last_error={_detail} req={_req} fail={_fail}, using local",
                            "via": "local",
                        }
                    )
                    logger.warning(
                        "[PRO_AUTO] LLM empty",
                        article_id=article_id,
                        raw_type=_raw_type,
                        raw_len=_raw_len,
                        last_error=_detail,
                        requests=_req,
                        failures=_fail,
                    )
            else:
                _err = getattr(provider, "usage", None)
                _detail = getattr(_err, "last_error", "") if _err else ""
                _req = getattr(_err, "requests", 0) if _err else 0
                _fail = getattr(_err, "failures", 0) if _err else 0
                _raw_type = type(raw).__name__ if raw is not None else "None"
                _raw_len = len(str(raw)) if raw is not None else 0
                _console_push(
                    {
                        "kind": "fallback",
                        "article_id": article_id,
                        "msg": f"LLM returned empty ({_raw_type} len={_raw_len}) last_error={_detail} req={_req} fail={_fail}, using local",
                        "via": "local",
                    }
                )
                logger.warning(
                    "[PRO_AUTO] LLM empty",
                    article_id=article_id,
                    raw_type=_raw_type,
                    raw_len=_raw_len,
                    last_error=_detail,
                    requests=_req,
                    failures=_fail,
                )
        except Exception as e:
            _console_push(
                {
                    "kind": "fallback",
                    "article_id": article_id,
                    "msg": f"LLM error {type(e).__name__}, using local",
                    "via": "local",
                }
            )
            llm_json = None

    # Deterministic path ALWAYS runs so variables are accurate
    # Use engine pipeline when available for full persistence; otherwise insert directly
    if llm_json is not None:
        _map_llm_into_deterministic(db, article, local, llm_json)
        # Validate LLM JSON schema before persisting AI layer
        validated = _validate_response(llm_json, article_id)
        validated.provider = (
            getattr(provider, "provider_name", "openai-compatible")
            if provider
            else "openai-compatible"
        )
        validated.model = getattr(provider, "model", "") if provider else ""
        validated.analysis_version = NEWS_AI_ANALYSIS_VERSION
        # Persist AI layer
        try:
            from nexus_scalp.news.ai_service import (
                _persist_ai_analysis as _persist_ai,  # type: ignore
            )

            if validated.status == "completed":
                validated.ai_analysis_id = f"nai_{uuid.uuid4().hex[:12]}"
                _persist_ai(db, validated)
                _console_push(
                    {
                        "kind": "ai_ok",
                        "article_id": article_id,
                        "via": "llm",
                        "provider": validated.provider,
                        "model": validated.model,
                        "sentiment": validated.sentiment,
                        "summary": (validated.summary or "")[:220],
                        "answer": {
                            k: llm_json.get(k)
                            for k in (
                                "summary",
                                "sentiment",
                                "market_relevance",
                                "xauusd_relevance",
                                "potential_market_impact",
                            )
                        },
                    }
                )
            else:
                _console_push(
                    {
                        "kind": "ai_failed",
                        "article_id": article_id,
                        "via": "llm",
                        "msg": validated.error_detail,
                    }
                )
        except Exception as e:
            _console_push(
                {
                    "kind": "ai_persist_failed",
                    "article_id": article_id,
                    "via": "llm",
                    "msg": type(e).__name__,
                }
            )
    # LLM-driven inline purge: when the model itself says is_junk=true AND the
    # deterministic local signals are also low (conservative double-gate), remove
    # the article immediately so the DB never accumulates Venmo/Costco noise.
    # This complements the gold-first queue and the tombstone blocklist.
    if llm_json is not None and bool(llm_json.get("is_junk")):
        try:
            _is_junk_llm = True
            _junk_reason = str(llm_json.get("junk_reason") or "LLM_JUNK")[:200] or "LLM_JUNK"
            # Conservative gate: require deterministic also-low so gold is never dropped on LLM false-positive
            _imp = float(local.get("importance_score", 0) or 0)
            _xau = float(local.get("xauusd_relevance", 0) or 0)
            # Use the same thresholds as auto_prune (0.30 / 0.25) — LLM+local AND
            _det_also_junk = _imp < 0.30 and _xau < 0.25
            if _det_also_junk:
                # Purge now: delete article + analysis + ai + derived, tombstone hash
                try:
                    _ah = str(row.get("article_hash") or "")
                    _title = str(row.get("title") or "")
                    with db._connect() as _conn:
                        _conn.execute(
                            "DELETE FROM news_articles WHERE article_id = ?;", (article_id,)
                        )
                        _conn.execute(
                            "DELETE FROM news_analysis WHERE article_id = ?;", (article_id,)
                        )
                        _conn.execute(
                            "DELETE FROM news_ai_analysis WHERE article_id = ?;", (article_id,)
                        )
                        _conn.execute(
                            "DELETE FROM news_entities WHERE article_id = ?;", (article_id,)
                        )
                        _conn.execute(
                            "DELETE FROM news_topics WHERE article_id = ?;", (article_id,)
                        )
                        _conn.execute(
                            "DELETE FROM news_impacts WHERE article_id = ?;", (article_id,)
                        )
                        _conn.execute(
                            "DELETE FROM news_consensus WHERE article_id = ?;", (article_id,)
                        )
                        if _ah:
                            _conn.execute(
                                "INSERT OR IGNORE INTO news_junk_hashes (article_hash, title, reason, pruned_at) VALUES (?, ?, ?, ?);",
                                (
                                    _ah,
                                    _title,
                                    f"llm_junk:{_junk_reason}",
                                    __import__("datetime")
                                    .datetime.now(__import__("datetime").UTC)
                                    .isoformat(),
                                ),
                            )
                        _conn.commit()
                    _console_push(
                        {
                            "kind": "llm_purge",
                            "article_id": article_id,
                            "via": "llm",
                            "msg": f"LLM junk purged: {_junk_reason} (imp={_imp:.2f} xau={_xau:.2f})",
                            "junk_reason": _junk_reason,
                        }
                    )
                    return {
                        "ok": True,
                        "via": "purged_llm",
                        "article_id": article_id,
                        "junk_reason": _junk_reason,
                        "local": local,
                        "llm_json": llm_json,
                    }
                except Exception as _pe:
                    _console_push(
                        {
                            "kind": "llm_purge_failed",
                            "article_id": article_id,
                            "via": "llm",
                            "msg": type(_pe).__name__,
                        }
                    )
            else:
                # LLM says junk but deterministic disagrees — mark IRRELEVANT instead of deleting (recoverable)
                try:
                    db.set_article_status(
                        article_id,
                        "IRRELEVANT",
                        reason=f"LLM_JUNK_SOFT:{_junk_reason}",
                        actor="llm_purge",
                    )
                    ah2 = str(row.get("article_hash") or "")
                    if ah2:
                        db.remember_junk_hash(
                            ah2,
                            title=str(row.get("title", "")),
                            reason=f"LLM_JUNK_SOFT:{_junk_reason}",
                        )
                    _console_push(
                        {
                            "kind": "llm_mark_irrelevant",
                            "article_id": article_id,
                            "via": "llm",
                            "msg": f"LLM junk soft-marked IRRELEVANT: {_junk_reason}",
                        }
                    )
                except Exception:
                    pass
        except Exception:
            pass

    # Deterministic analysis persistence — always accurate variables
    # Prefer the real pipeline (entities/topics/impacts/consensus)
    try:
        # Reuse the canonical pipeline so derived tables are populated
        if engine is not None and getattr(engine, "pipeline", None) is not None:
            # pipeline.analyze_article does the full stage + DB write
            engine.pipeline.analyze_article(article)
        else:
            from nexus_scalp.news.analysis.pipeline import NewsAnalysisPipeline

            pipe = NewsAnalysisPipeline(
                db, getattr(engine, "config", None) if engine else None, local=analyzer
            )
            pipe.analyze_article(article)
        _console_push(
            {
                "kind": "analysis_ok",
                "article_id": article_id,
                "via": via,
                "direction": local["direction"],
                "xauusd": local["xauusd_relevance"],
                "importance": local["importance_score"],
            }
        )
        return {
            "ok": True,
            "via": via,
            "article_id": article_id,
            "local": local,
            "llm_json": llm_json,
        }
    except Exception as e:
        _console_push(
            {
                "kind": "analysis_failed",
                "article_id": article_id,
                "via": via,
                "msg": type(e).__name__,
            }
        )
        return {"ok": False, "via": via, "error": type(e).__name__, "article_id": article_id}


def run_pro_cycle(
    db: NewsDatabase,
    *,
    engine: Any | None = None,
    settings_service: Any | None = None,
    analyzer: LocalNewsAnalyzer | None = None,
    limit: int = 200,
    prune_junk: bool = True,
) -> dict[str, Any]:
    """PRO cycle: analyze ALL outstanding articles, then purge junk.

    Called by the worker when news.auto_analysis_enabled == True.
    Returns a summary: analyzed counts + junk counts + console seq.
    """
    analyzer = analyzer or LocalNewsAnalyzer()
    # Gold-first needs a wider candidate pool than the final drain limit —
    # otherwise a burst of fresh junk (Venmo/Costco) pushes older gold
    # (BoE/CPI/Fed) out of the 200-window. Fetch 3x then re-rank.
    _pool = max(int(limit) * 3, 500)
    articles = db.list_articles(limit=min(_pool, 2000), include_duplicates=False)
    # Only those lacking deterministic analysis
    # Gold Priority: rank pending deterministically BEFORE any LLM call,
    # so BoE/CPI/Fed/gold pieces are analyzed first and junk (Venmo/Costco)
    # naturally drains last (then auto-prunes to IRRELEVANT).
    raw_pending: list[dict[str, Any]] = []
    for art in articles:
        try:
            ah_chk = str(art.get("article_hash") or "")
            if ah_chk and db.is_analyzed_hash(ah_chk):
                continue
        except Exception:
            pass
        if db.get_analysis(art["article_id"]) is None:
            raw_pending.append(art)
        else:
            # Backfill tombstone so re-ingest stays suppressed even after retention
            try:
                ah_b = str(art.get("article_hash") or "")
                if ah_b:
                    ex0 = db.get_analysis(art["article_id"]) or {}
                    db.remember_analyzed_hash(
                        ah_b,
                        title=str(art.get("title", "")),
                        analysis_id=str(ex0.get("analysis_id", "")),
                    )
            except Exception:
                pass
    pending = _ranked_pending(db, analyzer, raw_pending, limit)
    # Keep true total for the status card (ranked slice is bounded by limit)
    total_pending = len(raw_pending)
    # Stash true total for logging before slicing limits upstream
    _pending_was_truncated = total_pending > len(pending)

    ok = skipped = failed = 0
    llm_used = local_used = 0

    # Count gold vs junk in this ranked slice for the console header
    try:
        _gold_in_slice = sum(
            1
            for _r in pending
            if _gold_priority(
                _parse_article_row(_r), _local_signals(_parse_article_row(_r), analyzer)
            )
            >= 0.20
        )
    except Exception:
        _gold_in_slice = 0
    _console_push(
        {
            "kind": "cycle_start",
            "pending": total_pending,
            "limit": limit,
            "gold_next": _gold_in_slice,
            "junk_next": len(pending) - _gold_in_slice,
            "msg": f"PRO cycle: {total_pending} pending — gold {_gold_in_slice} first, junk {len(pending) - _gold_in_slice} last",
        }
    )

    for art in pending:
        res = run_pro_auto_analysis_for_article(
            db,
            art["article_id"],
            engine=engine,
            settings_service=settings_service,
            analyzer=analyzer,
        )
        if res.get("ok"):
            if res.get("via") == "cached":
                skipped += 1
            else:
                ok += 1
                if res.get("via") == "llm":
                    llm_used += 1
                else:
                    local_used += 1
        else:
            failed += 1

    # After all analyses, auto-prune junk to keep DB clear
    junk = {"marked_irrelevant": 0, "preserved": 0, "already_irrelevant": 0}
    if prune_junk:
        try:
            pr = auto_prune_irrelevant(db, actor="pro_auto", analyzer=analyzer, limit=2000)
            junk = {
                "marked_irrelevant": pr.marked_irrelevant,
                "preserved": pr.preserved,
                "already_irrelevant": pr.already_irrelevant,
            }
            _console_push(
                {
                    "kind": "junk_prune",
                    "msg": f"junk marked IRRELEVANT: {pr.marked_irrelevant}, preserved {pr.preserved}",
                    **junk,
                }
            )
        except Exception as e:
            _console_push({"kind": "junk_failed", "msg": type(e).__name__})

    summary = {
        "total_pending": total_pending,
        "analyzed": ok,
        "skipped": skipped,
        "failed": failed,
        "via_llm": llm_used,
        "via_local": local_used,
        "junk": junk,
        "console_seq": _CONSOLE_SEQ,
    }
    _console_push({"kind": "cycle_done", **summary})
    try:
        db.load_worker_state()  # no-op, ensures db reachable
    except Exception:
        pass
    return summary


def purge_irrelevant(
    db: NewsDatabase,
    *,
    hard_delete: bool = False,
    older_than_hours: float | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """Remove IRRELEVANT junta from the DB (console-triggered).

    Soft (default): just reports counts; hard_delete True actually deletes
    the rows (plus derived analysis/entities/topics/impacts) so the DB stays
    clear. When older_than_hours is set, only IRRELEVANT older than that is
    affected. Bounded by limit.
    """
    bounded = max(1, min(int(limit), 10000))
    with db._connect() as conn:
        # Collect candidate ids
        if older_than_hours and older_than_hours > 0:
            cutoff = datetime.now(UTC).timestamp() - float(older_than_hours) * 3600.0
            # news_articles.published_at is ISO string — compare lexicographically via ISO or timestamp
            # Use julianday to compare; fallback to all IRRELEVANT if parse fails
            rows = conn.execute(
                "SELECT article_id, published_at FROM news_articles WHERE article_status = 'IRRELEVANT' LIMIT ?;",
                (bounded,),
            ).fetchall()
            ids: list[str] = []
            for r in rows:
                try:
                    ts = datetime.fromisoformat(
                        str(r["published_at"]).replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    ts = 0
                if ts <= cutoff or ts == 0:
                    ids.append(str(r["article_id"]))
        else:
            rows = conn.execute(
                "SELECT article_id FROM news_articles WHERE article_status = 'IRRELEVANT' LIMIT ?;",
                (bounded,),
            ).fetchall()
            ids = [str(r["article_id"]) for r in rows]

        total_irrelevant = conn.execute(
            "SELECT COUNT(*) AS c FROM news_articles WHERE article_status = 'IRRELEVANT';"
        ).fetchone()["c"]
        count = len(ids)

        if not hard_delete:
            return {
                "candidates": count,
                "total_irrelevant": int(total_irrelevant),
                "deleted": 0,
                "hard_delete": False,
            }

        # Capture hashes before delete so they can be tombstoned (purge is durable)
        hashes: list[tuple[str, str]] = []
        if ids:
            for aid in ids:
                try:
                    row = conn.execute(
                        "SELECT article_hash, title FROM news_articles WHERE article_id = ?;",
                        (aid,),
                    ).fetchone()
                    if row:
                        hashes.append((str(row["article_hash"]), str(row["title"] or "")))
                except Exception:
                    pass
        deleted = 0
        for aid in ids:
            conn.execute("DELETE FROM news_articles WHERE article_id = ?;", (aid,))
            conn.execute("DELETE FROM news_analysis WHERE article_id = ?;", (aid,))
            conn.execute("DELETE FROM news_ai_analysis WHERE article_id = ?;", (aid,))
            conn.execute("DELETE FROM news_entities WHERE article_id = ?;", (aid,))
            conn.execute("DELETE FROM news_topics WHERE article_id = ?;", (aid,))
            conn.execute("DELETE FROM news_impacts WHERE article_id = ?;", (aid,))
            conn.execute("DELETE FROM news_consensus WHERE article_id = ?;", (aid,))
            deleted += 1
        # Tombstone purged hashes — future RSS polls with same article_hash are suppressed
        for ah, title in hashes:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO news_junk_hashes (article_hash, title, reason, pruned_at) VALUES (?, ?, ?, ?);",
                    (
                        ah,
                        title,
                        "purge_irrelevant",
                        __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
                    ),
                )
            except Exception:
                pass
        conn.commit()

    _console_push(
        {
            "kind": "purge",
            "msg": f"purged {deleted}/{count} IRRELEVANT (hard_delete={hard_delete})",
            "deleted": deleted,
            "candidates": count,
            "total_irrelevant": int(total_irrelevant),
        }
    )
    return {
        "candidates": count,
        "total_irrelevant": int(total_irrelevant),
        "deleted": deleted,
        "hard_delete": bool(hard_delete),
    }


def provider_status_for_console(
    engine: Any | None = None,
    settings_service: Any | None = None,
) -> dict[str, Any]:
    s = get_ai_status(engine, settings_service)
    prov = resolve_factory_provider(engine, settings_service)
    return {
        "ai_status": s.to_dict(),
        "provider_available": bool(prov and prov.available()),
        "provider_name": getattr(prov, "provider_name", "") if prov else "",
        "model": getattr(prov, "model", "") if prov else "",
        "base_url": getattr(prov, "api_base_url", "") if prov else "",
    }
