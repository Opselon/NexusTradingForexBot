/**
 * Pro-console DTOs — /api/news/pro/* contracts
 * (src/nexus_scalp/web/news_intelligence_routes.py, READ-ONLY reference).
 *
 * Every field below was verified against the route handler body:
 *  GET  /api/news/pro/status          news_pro_status        — _ok({console, counts, latest_ai, provider})
 *  GET  /api/news/pro/console         news_pro_console       — _ok({entries, count})
 *  GET  /api/news/pro/latest-answers  news_pro_latest_answers— _ok({answers, count})
 *  POST /api/news/pro/analyze-all     news_pro_analyze_all   — _ok({summary})
 *  POST /api/news/pro/purge           news_pro_purge         — _ok(purge_irrelevant(...))
 * Error responses use the web/errors.py safe envelope ({available:false,
 * success:false, error:{code,message,request_id}}) — the SAME available flag
 * as success payloads, so it is optional-tolerant here and never ignored.
 */

import type { NewsAiStatusResponse, NewsAiAnalysisRow } from "./types";

export type NewsAiStatus = NonNullable<NewsAiStatusResponse["ai_status"]>;

/** news/pro_auto_console.console_status() — ring telemetry. */
export interface ProConsoleStatus {
  size?: number;
  latest_seq?: number;
  available?: boolean;
}

/** news_pro_status.counts — db.count_articles + count_pending_analysis + count_articles_by_status. */
export interface ProStatusCounts {
  total?: number;
  pending?: number;
  status_counts?: Record<string, number>;
}

/** provider_status_for_console() — secret-free provider view for the console. */
export interface ProProviderStatus {
  ai_status?: NewsAiStatus | null;
  provider_available?: boolean;
  provider_name?: string;
  model?: string;
  base_url?: string;
}

/** GET /api/news/pro/status */
export interface NewsProStatusResponse {
  available: boolean;
  success?: boolean;
  console?: ProConsoleStatus;
  counts?: ProStatusCounts;
  latest_ai?: NewsAiAnalysisRow | null;
  provider?: ProProviderStatus | null;
  error?: { code?: string; message?: string; request_id?: string } | string;
}

/**
 * One console entry from the pro_auto ring. Keys are OPTIONAL-TOLERANT because
 * the backend pushes a different key set per `kind` (verified in
 * news/pro_auto.py + purge_irrelevant). `seq`/`ts` are defaulted by
 * push_console. Nothing is ever synthesized for a missing key.
 *
 * Observed kinds: cycle_start, cycle_done, analysis_ok, analysis_failed,
 * ai_ok, ai_failed, ai_retry_ok, ai_persist_failed, fallback, skip, error,
 * deterministic_skip, budget_exhausted, junk_prune, junk_failed, llm_purge,
 * llm_purge_failed, llm_mark_irrelevant, purge.
 */
export interface ProConsoleEntry {
  seq?: number;
  ts?: string;
  kind?: string;
  msg?: string;
  summary?: string;
  /** ai_ok: the LLM answer fields echoed back verbatim by the backend. */
  answer?: Record<string, unknown> | null;
  via?: string;
  article_id?: string;
  sentiment?: string;
  provider?: string;
  model?: string;
  /* cycle_start / cycle_done + junk/purge aggregates (backend numbers only) */
  pending?: number;
  limit?: number;
  gold_next?: number;
  junk_next?: number;
  total_pending?: number;
  analyzed?: number;
  skipped?: number;
  failed?: number;
  via_llm?: number;
  via_local?: number;
  console_seq?: number;
  junk?: { marked_irrelevant?: number; preserved?: number; already_irrelevant?: number };
  marked_irrelevant?: number;
  preserved?: number;
  already_irrelevant?: number;
  deleted?: number;
  candidates?: number;
  total_irrelevant?: number;
  [k: string]: unknown;
}

/** GET /api/news/pro/console?limit&since_seq (limit clamped 1..500 server-side). */
export interface NewsProConsoleResponse {
  available: boolean;
  success?: boolean;
  entries?: ProConsoleEntry[];
  count?: number;
  error?: { code?: string; message?: string; request_id?: string } | string;
}

/** GET /api/news/pro/latest-answers?limit (rows = news_ai_analysis, cap 100). */
export interface NewsProAnswersResponse {
  available: boolean;
  success?: boolean;
  answers?: NewsAiAnalysisRow[];
  count?: number;
  error?: { code?: string; message?: string; request_id?: string } | string;
}

/** run_pro_cycle summary (POST /api/news/pro/analyze-all -> {summary}). */
export interface ProAnalyzeAllSummary {
  total_pending?: number;
  analyzed?: number;
  skipped?: number;
  failed?: number;
  via_llm?: number;
  via_local?: number;
  junk?: { marked_irrelevant?: number; preserved?: number; already_irrelevant?: number };
  console_seq?: number;
}

export interface NewsProAnalyzeAllResponse {
  available: boolean;
  success?: boolean;
  summary?: ProAnalyzeAllSummary | null;
  error?: { code?: string; message?: string; request_id?: string } | string;
}

/** purge_irrelevant() return — soft reports counts, hard deletes (candidates bounded 1..10000). */
export interface ProPurgeResult {
  candidates?: number;
  total_irrelevant?: number;
  deleted?: number;
  hard_delete?: boolean;
}

export interface NewsProPurgeResponse extends ProPurgeResult {
  available: boolean;
  success?: boolean;
  error?: { code?: string; message?: string; request_id?: string } | string;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Round-2 additions — the non-pro news AI commands (same safe envelope law:
 * success -> {available:true, ...payload}; refusal -> {available:false,
 * success:false, error:{code,message,request_id}} at HTTP 200, so callers
 * must inspect `available` and never assume success).
 *
 *  POST /api/news/analyze/batch   news_analyze_batch   — _ok({results,total,completed,failed,skipped})
 *  POST /api/news/auto-prune      news_auto_prune      — _ok(PruneResult.to_dict())
 *
 * NOTE (route order, verified in debug_research_routes.register ~L2156/2177):
 * `register_news_liquidity_mslie_routes` runs BEFORE `include_router(news_intel_router)`,
 * so POST /api/news/analyze/{id} is served by the INLINE worker-enqueue handler
 * (returns the NewsAnalyzeResult job shape from ./types), not the sync AI handler.
 * Only the batch variant of the intelligence router is reachable as typed here.
 * ────────────────────────────────────────────────────────────────────────── */

/** one item of the batch results array (per-item NewsAIAnalysisResult.to_dict()). */
export interface NewsAiAnalyzeResultRow {
  status?: string;
  article_id?: string;
  ai_analysis_id?: string;
  provider?: string;
  model?: string;
  analysis_version?: string;
  summary?: string;
  market_relevance?: string;
  xauusd_relevance?: string;
  sentiment?: string;
  importance_assessment?: string;
  key_facts?: string[] | null;
  potential_market_impact?: string;
  uncertainties?: string[] | null;
  insufficient_evidence?: boolean;
  analysis_status?: string;
  error_detail?: string;
  prompt_version?: string;
}

/** POST /api/news/analyze/batch (ids capped at 200 server-side, §51). */
export interface NewsBatchAnalyzeResponse {
  available: boolean;
  success?: boolean;
  results?: NewsAiAnalyzeResultRow[];
  total?: number;
  completed?: number;
  failed?: number;
  skipped?: number;
  error?: { code?: string; message?: string; request_id?: string } | string;
}

/** POST /api/news/auto-prune — PruneResult.to_dict() (recoverable IRRELEVANT). */
export interface NewsAutoPruneResponse {
  available: boolean;
  success?: boolean;
  processed?: number;
  marked_irrelevant?: number;
  already_irrelevant?: number;
  preserved?: number;
  failed?: number;
  actor?: string;
  rule_version?: string;
  error?: { code?: string; message?: string; request_id?: string } | string;
}
