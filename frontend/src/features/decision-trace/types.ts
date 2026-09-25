/**
 * Decision Trace — domain types for the observer contract.
 *
 * MIRRORS src/nexus_scalp/observability/trace_contract.py 1:1
 * (trace_schema_version 1). Every field is optional because ABSENCE IS DATA:
 * the backend omits a key when the runtime produced no evidence for it, and
 * the UI must render UNKNOWN / NOT OBSERVED rather than a default that could
 * be mistaken for a real value (contract §02 truth rules).
 *
 * Frozen causal contract v2 (additive-only, wave LIVE-CAUSAL-TOPOLOGY): the
 * 21 optional fields below and the TraceState/TraceMode vocabularies mirror
 * trace_contract.py:TraceState / :MODE (absence => UNKNOWN, never guessed).
 *
 * This file is PURE TYPES ONLY — zero runtime logic, zero imports — so it can
 * never perturb the hot path and Node's TS-stripping test runner can import
 * the derivation logic in traceGraph.ts without a bundler.
 */

export const TRACE_SCHEMA_VERSION = 1;

/**
 * Frozen TraceState vocabulary (trace_contract.py:TraceState). Free strings
 * from future runtime releases render as-is; the UI never computes a state.
 */
export const TraceState = {
  IDLE: "IDLE",
  RECEIVED: "RECEIVED",
  PROCESSING: "PROCESSING",
  WAITING: "WAITING",
  COMPLETED: "COMPLETED",
  PASSED: "PASSED",
  REJECTED: "REJECTED",
  FAILED: "FAILED",
  BLOCKED: "BLOCKED",
  SKIPPED: "SKIPPED",
  TIMEOUT: "TIMEOUT",
  STALE: "STALE",
  CANCELLED: "CANCELLED",
  EXECUTING: "EXECUTING",
  CONFIRMED: "CONFIRMED",
} as const;
export type TraceStateWord = (typeof TraceState)[keyof typeof TraceState] | (string & {});

/**
 * Frozen mode vocabulary (trace_contract.py mode words). Absent mode is
 * rendered UNKNOWN — the UI never infers LIVE/PAPER/SHADOW from anything else.
 */
export const TraceMode = {
  LIVE: "LIVE",
  PAPER: "PAPER",
  SHADOW: "SHADOW",
  REPLAY: "REPLAY",
  BACKTEST: "BACKTEST",
  TRAINING: "TRAINING",
} as const;
export type TraceModeWord = (typeof TraceMode)[keyof typeof TraceMode] | (string & {});

/** Stages the runtime actually emits (TraceStage). */
export type TraceStage =
  | "MARKET"
  | "FEATURES"
  | "REGIME"
  | "INFERENCE"
  | "POLICY"
  | "POST_POLICY"
  | "DECISION"
  | "RISK"
  | "EXECUTION"
  | "MT5"
  | "GATEWAY"
  | "ORDER";

/** Unknown stages from future runtime releases arrive as free strings. */
export type StageName = TraceStage | (string & {});

/**
 * Provenance of a causal link: `observed` (runtime evidence) or `inferred`
 * (timestamp-fallback correlation). A link with neither stays absent and the
 * UI renders PROVENANCE GAP (§56).
 */
export type Provenance = "observed" | "inferred";

export interface TraceEvent {
  event_id: string;
  trace_id: string | null;
  parent_event_id: string | null;
  sequence: number;
  timestamp: string; // wall clock ISO — display
  monotonic_ns: number | null; // duration math (timeline)
  stage: StageName;
  component: string | null;
  event_type: string | null;
  status: string | null;
  symbol: string | null;
  decision_id: string | null;
  latency_us: number | null;
  terminal: boolean;
  unmapped: boolean;
  provenance_gap: boolean;
  detail: Record<string, unknown> | null;
  trace_schema_version: number;
  /* ------------------------------------------------------------- v2 causal
   * Additive optional fields (frozen contract v2). ABSENCE IS DATA: the
   * backend omits a key when the emit site recorded nothing, so every UI
   * reads these with `?? null` and renders UNKNOWN / NOT OBSERVED. */
  /** Same-trace correlation across components (request lifecycle §9). */
  request_id?: string | null;
  /** First event id of this trace (root marker). */
  root_event_id?: string | null;
  /** Where the request came from (§10, e.g. MARKET_TICK/CLI/API). */
  source?: string | null;
  /** Where it went next (§11). */
  destination?: string | null;
  /** Frozen state word the emit site recorded (TraceState). */
  state?: string | null;
  /** Stage start clock (wall ISO). */
  started_at?: string | null;
  /** Stage completion clock (wall ISO). */
  completed_at?: string | null;
  /** Backend-measured stage duration. */
  duration_ms?: number | null;
  /** Why this event happened / rejected (§16), verbatim runtime code. */
  reason_code?: string | null;
  /** Error class the runtime reported (§42 cascade). */
  error_code?: string | null;
  /** Execution mode the runtime knew (§44). */
  mode?: string | null;
  /** External provider name when one participated (§24). */
  provider?: string | null;
  /** Model identity string when known (§49). */
  model?: string | null;
  /** Position/ticket this event targets (§33). */
  position_id?: string | null;
  /** Broker order id this event targets (§34). */
  order_id?: string | null;
  /** Broker deal id (rarely emitted — adapter seam). */
  deal_id?: string | null;
  /** Execution/attempt id grouping an order attempt (§20). */
  execution_id?: string | null;
  /** Account/input snapshot id (§28 lineage). */
  snapshot_id?: string | null;
  /** Safe payload summary the emit site reported (§14). */
  payload_summary?: Record<string, unknown> | null;
  /** Data freshness verdict the runtime measured (§40, VALID/STALE/INVALID). */
  freshness?: string | number | null;
  /** observed | inferred for this record's linkage. */
  provenance?: string | null;
}

export interface TraceNode {
  stage: StageName;
  count: number;
  first_seen_seq: number;
  last_ts: string;
  /** True when this stage was seen with no parent and is not MARKET. */
  unmapped?: boolean;
  in_degree?: number;
  out_degree?: number;
}

export interface TraceEdge {
  source: StageName;
  target: StageName;
  count: number;
  first_seen_seq: number;
}

export interface TopologySnapshot {
  generated_at: string;
  observer_status: ObserverStatus;
  nodes: TraceNode[];
  edges: TraceEdge[];
  counters: Record<string, number> | null;
  trace_schema_version: number;
}

export type ObserverStatus = "OFF" | "STARTING" | "ACTIVE" | "STOPPING" | "ERROR";

export interface ObserverSession {
  observer_id: string;
  session_id: string;
  started_at: string;
  stopped_at: string | null;
  status: ObserverStatus;
}

export interface SubscriberInfo {
  subscriber_id: string;
  connected_at: string;
  buffered: number;
  dropped_visual_events: number;
  coalesced_events: number;
}

export interface ObserverSnapshot {
  observer_id: string;
  status: ObserverStatus;
  session: ObserverSession | null;
  recent_sessions: ObserverSession[];
  counters: Record<string, number> | null;
  events_retained: number;
  decisions_retained: number;
  events_ring_capacity: number;
  decisions_ring_capacity: number;
  trace_schema_version: number;
  subscribers: SubscriberInfo[];
}

/** A decision row in the live feed. */
export interface DecisionRow {
  decision_id: string | null;
  trace_id: string | null;
  status: string | null;
  action: string | null;
  symbol: string | null;
  regime: string | null;
  reason_code: string | null;
  rejection_reason: string | null;
  reason: string | null;
  confidence: number | null;
  model_id: string | null;
  model_version: string | null;
  contract: string | null;
  latency_us: number | null;
  recorded_at: string | null;
  started_at?: string | null;
  latency_ms?: number | null;
  policy?: string | null;
  risk_state?: string | null;
  free_margin?: string | number | null;
  exposure?: string | number | null;
  [key: string]: unknown;
}

export interface DecisionsList {
  total: number;
  offset: number;
  rows: DecisionRow[];
  last_seq: number;
}

export interface EventsSince {
  events: TraceEvent[];
  gap: boolean;
  last_seq: number;
  /** Echo of the AND-filters the server applied (empty = unfiltered tail). */
  filters?: Record<string, string>;
}

/**
 * AND-filter bag for GET /api/trace/events (trace_routes.py:344). Every value
 * is a VERBATIM runtime word/id (trace_id, stage, status, mode, position_id).
 * Null/undefined/empty mean "filter not set" on both sides, so a filter that
 * matches nothing yields an empty page and the honest empty state (§73).
 */
export interface EventFilters {
  trace_id?: string | null;
  stage?: string | null;
  status?: string | null;
  mode?: string | null;
  position_id?: string | null;
}

export interface EventsQuery extends EventFilters {
  last_seq?: number;
  limit?: number;
}

/**
 * Build the /events query string from a sparse filter bag. Empty/absent words
 * are dropped (server-side they mean "not set" — trace_routes.py:61
 * _as_text). NEVER interpolates null-ish values, so an empty filter set
 * reproduces the legacy unfiltered tail byte-for-byte.
 */
export function buildEventsQuery(q: EventsQuery | null | undefined): string {
  if (!q) return "";
  const params: Record<string, string | number | undefined> = {
    last_seq: q.last_seq ? Math.max(0, Math.floor(q.last_seq)) : undefined,
    limit: q.limit ? Math.max(1, Math.min(Math.floor(q.limit), 5000)) : undefined,
    trace_id: q.trace_id ? q.trace_id.trim() : undefined,
    stage: q.stage ? q.stage.trim().toUpperCase() : undefined,
    status: q.status ? q.status.trim().toUpperCase() : undefined,
    mode: q.mode ? q.mode.trim().toUpperCase() : undefined,
    position_id: q.position_id ? q.position_id.trim() : undefined,
  };
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    s.set(k, String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : "";
}

/** Which active filters the server echo-back would carry (UI chip rendering). */
export function activeFilterWords(
  filters: EventFilters | null | undefined,
): Array<{ key: string; value: string }> {
  if (!filters) return [];
  const out: Array<{ key: string; value: string }> = [];
  for (const key of ["trace_id", "stage", "status", "mode", "position_id"] as const) {
    const v = filters[key];
    if (v && String(v).trim()) out.push({ key, value: String(v).trim() });
  }
  return out;
}

export interface LatencyStageEntry {
  n: number;
  p50_us?: number;
  p95_us?: number;
  p99_us?: number;
  max_us?: number;
  mean_us?: number;
  insufficient_data?: boolean;
}

export interface LatencyStats {
  generated_at: string;
  min_sample_n: number;
  stages: Record<string, LatencyStageEntry>;
}

export interface IntegrityWarning {
  code: string;
  [key: string]: unknown;
}

export interface IntegrityReport {
  generated_at: string;
  traces_scanned: number;
  warnings: IntegrityWarning[];
  truncated: boolean;
}

export interface TraceBundle {
  query: string;
  trace_id: string | null;
  decision_id: string | null;
  events: TraceEvent[];
  summary: DecisionRow | null;
  found: boolean;
}

/* --------------------------------------------------------- WHY / NEXT (§37/§38)
 * Mirror of GET /api/trace/why/{event_id} (web/trace_routes.py
 * _why_payload): every field is runtime-observed; TERMINATED / NOT OBSERVED /
 * 404 are rendered as explicit states, never fabricated continuations. */

/** Reason-bearing evidence the runtime recorded for one event. */
export interface WhyReasons {
  reason_code?: string;
  error_code?: string;
  state?: string;
  rejection_reason?: string;
  blocked_by?: string;
  decision_stage?: string;
}

export interface WhyNextObserved {
  observed: true;
  /** Destination stage, or the literal TERMINATED/NOT OBSERVED word. */
  destination: string;
  event_id: string;
  stage: string;
  event_type: string | null;
  status: string | null;
  timestamp: string | null;
  terminal: boolean;
  /** observed (parent_event_id link) | inferred (same-trace successor). */
  provenance: "observed" | "inferred";
  link: "parent_event_id" | "trace_sequence";
  why?: WhyReasons;
}

export interface WhyNextTerminated {
  observed: true;
  destination: "TERMINATED";
  terminal_status: string | null;
  why?: WhyReasons;
}

/**
 * Shared NEXT fields the panel reads. TERMINATED/NOT OBSERVED carry no edge, so
 * `provenance`/`link` are absent there and the panel renders PROVENANCE GAP.
 */
export interface WhyNextBase {
  /** Destination stage, or the literal TERMINATED/NOT OBSERVED word. */
  destination: string;
  /** observed (parent_event_id link) | inferred (same-trace successor). */
  provenance?: "observed" | "inferred";
  link?: "parent_event_id" | "trace_sequence";
  event_id?: string;
  stage?: string;
  event_type?: string | null;
  status?: string | null;
  timestamp?: string | null;
  terminal?: boolean;
  terminal_status?: string | null;
  why?: WhyReasons;
}

export type WhyNext = WhyNextBase & {
  observed: boolean;
};

export interface WhyResponse {
  found: boolean;
  trace_schema_version?: number;
  event_id: string | null;
  trace_id: string | null;
  sequence: number | null;
  timestamp: string | null;
  stage: string | null;
  component: string | null;
  event_type: string | null;
  status: string | null;
  terminal: boolean;
  /** Runtime reason evidence (absent keys = NO REASON OBSERVED). */
  why: WhyReasons;
  /** Redacted detail (secrets stripped by the backend). */
  detail: Record<string, unknown> | null;
  next: WhyNext;
  provenance?: string | null;
  /* Optional v2 fields echoed only when the emit site recorded them. */
  symbol?: string | null;
  decision_id?: string | null;
  latency_us?: number | null;
  state?: string | null;
  reason_code?: string | null;
  error_code?: string | null;
  mode?: string | null;
  position_id?: string | null;
  order_id?: string | null;
  deal_id?: string | null;
  model?: string | null;
  provider?: string | null;
  freshness?: string | number | null;
  parent_event_id?: string | null;
}

/** SSE named events (§76). */
export interface HelloFrame {
  event: "hello";
  observer_id: string;
  session_id: string;
  started_at: string;
  trace_schema_version: number;
  last_seq: number;
  resumed: boolean;
  sent_at: string;
}

export interface BatchFrame {
  event: "batch";
  count: number;
  last_seq: number;
  dropped_visual_events: number;
  coalesced_events: number;
  events: TraceEvent[];
  sent_at: string;
}

export interface HeartbeatFrame {
  event: "heartbeat";
  last_seq: number;
  observer_status: ObserverStatus;
  sent_at: string;
}

export interface GapFrame {
  event: "gap";
  last_seq: number;
  message: string;
}

export interface ErrorFrame {
  event: "error";
  code?: string;
  message?: string;
  sent_at: string;
}

export type TraceStreamFrame =
  | HelloFrame
  | BatchFrame
  | HeartbeatFrame
  | GapFrame
  | ErrorFrame;
