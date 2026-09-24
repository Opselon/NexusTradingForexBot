/**
 * Decision Trace — domain types for the observer contract.
 *
 * MIRRORS src/nexus_scalp/observability/trace_contract.py 1:1
 * (trace_schema_version 1). Every field is optional because ABSENCE IS DATA:
 * the backend omits a key when the runtime produced no evidence for it, and
 * the UI must render UNKNOWN / NOT OBSERVED rather than a default that could
 * be mistaken for a real value (contract §02 truth rules).
 *
 * This file is PURE TYPES ONLY — zero runtime logic, zero imports — so it can
 * never perturb the hot path and Node's TS-stripping test runner can import
 * the derivation logic in traceGraph.ts without a bundler.
 */

export const TRACE_SCHEMA_VERSION = 1;

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
