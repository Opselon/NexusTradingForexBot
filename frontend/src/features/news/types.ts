/**
 * News bounded context — DTO contracts (exactly what the legacy routes return).
 *
 * Source of truth: src/nexus_scalp/web/news_liquidity_mslie_routes.py and
 * news_intelligence_routes.py (READ-ONLY). Every field is optional-tolerant:
 * the backend returns `available:false` when the subsystem is off, and rows
 * carry SQLite column values (numbers as numbers, JSON columns as strings when
 * the route did not decode them). Nothing here invents defaults.
 */

/** GET /api/news?limit&status&include_duplicates */
export interface NewsAnalysisRow {
  analysis_id?: string;
  article_id?: string;
  run_id?: string;
  status?: string;
  provider?: string;
  summary?: string;
  direction?: string | null;
  impact_strength?: number | null;
  confidence?: number | null;
  horizon?: string;
  importance?: string;
  importance_score?: number | null;
  relevance_to_xauusd?: number | null;
  relevance_to_usd?: number | null;
  surprise_assessment?: string;
  market_mechanism?: string;
  novelty?: string;
  analyzed_at?: string;
  entities?: unknown;
  topics?: unknown;
  impacts?: unknown;
  contradictory_factors?: unknown;
  risks?: unknown;
}

export interface NewsAiAnalysisRow {
  ai_analysis_id?: string;
  article_id?: string;
  provider?: string;
  model?: string;
  analysis_version?: string;
  analysis_status?: string;
  summary?: string;
  sentiment?: string;
  market_relevance?: string;
  xauusd_relevance?: string;
  potential_market_impact?: string;
  insufficient_evidence?: boolean;
  key_facts?: string[] | null;
  uncertainties?: string[] | null;
  analyzed_at?: string;
}

export interface NewsConsensusRow {
  article_id?: string;
  source_count?: number;
  independent_count?: number;
  agreement?: number | null;
  conflict?: number | null;
  weighted_direction?: string | null;
  confidence?: number | null;
  evaluated_at?: string;
}

export interface NewsKeywordHit {
  keyword: string;
  category?: string;
  direction_bias?: string;
  weight?: number;
  mentions?: number;
}

export interface NewsFeedArticle {
  article_id: string;
  title: string;
  summary?: string | null;
  source_id?: string;
  source_name?: string;
  published_at?: string | null;
  importance?: string | number | null;
  importance_score?: number | null;
  is_duplicate?: boolean;
  article_status?: string;
  evidence_sources?: unknown;
  analysis?: NewsAnalysisRow | null;
  ai_analysis?: NewsAiAnalysisRow | null;
  consensus?: NewsConsensusRow | null;
  keyword_hits?: NewsKeywordHit[];
}

export interface NewsFeedResponse {
  available: boolean;
  articles?: NewsFeedArticle[];
  status_counts?: Record<string, number>;
}

/** GET /api/news/state */
export interface NewsStateResponse {
  available: boolean;
  state?: string | null;
  timestamp?: string | null;
  bullish_score?: number | null;
  bearish_score?: number | null;
  confidence?: number | null;
  conflict_score?: number | null;
  freshness?: number | null;
  xauusd_relevance?: number | null;
  usd_relevance?: number | null;
  active_event_count?: number | null;
  stale?: boolean;
  news_adjustment?: number | null;
  active_high_impact?: unknown;
  reason?: string;
}

/** GET /api/news/sources — news_sources rows + joined health. */
export interface NewsSourceHealth {
  source_id?: string;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  last_status?: number | null;
  consecutive_failures?: number;
  rate_limited?: number | boolean;
  retry_after_sec?: number;
  backoff_until?: string | null;
  healthy?: number | boolean;
}

export interface NewsSourceRow {
  source_id: string;
  name?: string;
  kind?: string;
  tier?: string;
  url?: string;
  feed_url?: string;
  enabled?: number | boolean;
  poll_interval_sec?: number;
  language?: string;
  priority?: number;
  health?: NewsSourceHealth | null;
}

export interface NewsSourcesResponse {
  available: boolean;
  sources?: NewsSourceRow[];
}

/** GET /api/news/health */
export interface NewsSubsystemHealth {
  available: boolean;
  enabled?: boolean;
  health?: {
    available?: boolean;
    subsystem?: string;
    state?: string;
    stale?: boolean;
    db?: Record<string, unknown>;
    cycle_count?: number;
    last_error?: string | null;
    last_cycle_at?: string;
  };
  worker?: Record<string, unknown> | null;
  llm_budget?: Record<string, unknown>;
  calendar?: Record<string, unknown>;
  event_gate?: Record<string, unknown>;
}

/** GET /api/news/impact */
export interface NewsImpactRow {
  id?: number;
  article_id?: string;
  asset?: string;
  direction?: string;
  strength?: number | null;
  relevance?: number | null;
  confidence?: number | null;
  horizon?: string;
  evaluated_at?: string;
}

export interface NewsImpactResponse {
  available: boolean;
  impacts?: NewsImpactRow[];
}

/** GET /api/news/timeline */
export interface NewsTimelineBucket {
  bucket_start: string;
  bucket_ts: number;
  bullish: number;
  bearish: number;
  neutral: number;
  article_count: number;
  top_title?: string;
}

export interface NewsTimelineResponse {
  available: boolean;
  asset?: string;
  buckets?: NewsTimelineBucket[];
}

/** GET /api/news/keywords */
export interface NewsKeywordsResponse {
  available: boolean;
  dataset?: {
    version?: string;
    total_keywords?: number;
    categories?: Record<string, number>;
  };
  coverage?: {
    articles_scanned?: number;
    total_mentions?: number;
    active_keywords?: number;
    direction_distribution?: Record<string, number>;
    top_keywords?: Array<{
      keyword: string;
      category?: string;
      direction_bias?: string;
      weight?: number;
      article_hits?: number;
      mention_count?: number;
      share?: number;
    }>;
  };
  keywords?: Array<{
    keyword: string;
    category?: string;
    topics?: string[];
    direction_bias?: string;
    weight?: number;
    aliases?: string[];
  }>;
}

/** GET /api/news/analysis/{id} */
export interface NewsArticleAnalysisResponse {
  available: boolean;
  analysis?: NewsAnalysisRow | null;
  run?: { run_id?: string; started_at?: string; finished_at?: string; status?: string; provider?: string; error?: string } | null;
}

/** GET /api/news/{article_id} */
export interface NewsArticleDetailResponse {
  available: boolean;
  error?: string;
  article?: Record<string, unknown> & { article_id?: string; title?: string; summary?: string; published_at?: string };
  analysis?: NewsAnalysisRow | null;
  impacts?: NewsImpactRow[];
  consensus?: NewsConsensusRow | null;
  entities?: Array<Record<string, unknown>>;
  topics?: Array<Record<string, unknown>>;
  related?: Array<Record<string, unknown>>;
  trade_links?: Array<Record<string, unknown>>;
  versions?: Array<Record<string, unknown>>;
  post_event_validation?: Array<Record<string, unknown>>;
}

/** GET /api/news/trades/{trade_id} */
export interface NewsTradeLinksResponse {
  available: boolean;
  trade_id?: string;
  links?: Array<Record<string, unknown>>;
}

/** GET /api/news/toggle-state · POST /api/news/toggle */
export interface NewsToggleState {
  success?: boolean;
  enabled: boolean;
  runtime_version?: number | string | null;
  source?: string;
  error?: string;
  worker_interval_sec?: number | null;
}

/** GET /api/news/auto-analysis · POST /api/news/auto-analysis */
export interface NewsAutoAnalysisState {
  success?: boolean;
  enabled: boolean;
  worker_enabled?: boolean | null;
  runtime_version?: number | string | null;
  source?: string;
  error?: string;
}

/** POST /api/news/refresh */
export interface NewsRefreshResult {
  available: boolean;
  cooldown?: number;
  skipped?: string;
  ingested?: { sources_polled?: number; new?: number; duplicate?: number; merged?: number };
  analyzed_count?: number;
  error?: string;
}

/** POST /api/news/self-heal */
export interface NewsSelfHealResult {
  available: boolean;
  status?: string;
  rebuilt?: Record<string, number>;
  error?: string;
}

/** POST /api/news/analyze/{id} */
export interface NewsAnalyzeResult {
  available?: boolean;
  ok?: boolean;
  status?: string;
  article_id?: string;
  reason?: string;
  job_id?: string;
  error?: string;
}

/** GET /api/news/ai-status (secret-free AI readiness). */
export interface NewsAiStatusResponse {
  ok?: boolean;
  available?: boolean;
  ai_status?: {
    state?: string;
    provider?: string;
    model?: string;
    detail?: string;
  } | null;
  error?: { code?: string; message?: string; request_id?: string } | string;
}

export type NewsFilter = "ACTIVE" | "ALL" | "IRRELEVANT";

/** POST /api/news/analyze/batch */
export interface BatchAnalyzeResult {
  available?: boolean;
  completed?: number;
  failed?: number;
  skipped?: number;
  error?: string;
}

/** POST /api/news/auto-prune */
export interface PruneResult {
  marked_irrelevant?: number;
  preserved?: number;
  already_irrelevant?: number;
  error?: string;
}
