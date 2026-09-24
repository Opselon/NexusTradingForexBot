/**
 * Decision Trace — forensic derivation over the frozen v2 causal fields.
 *
 * Pure functions only (no React, no I/O): Node's TS-stripping test runner
 * imports this module directly so the derivation contract is regression-pinned
 * by tests/js/decision_trace_inspectors.test.js.
 *
 * TRUTH RULE (§60 / §73 / §Backend-guarantee): every value below is read from
 * a named backend payload field. Absence is rendered with the explicit words
 * UNKNOWN / NOT OBSERVED / PROVENANCE GAP / TERMINATED — never zero-filled,
 * never defaulted to a value that could read as runtime evidence, never a
 * frontend-computed verdict.
 */

import type {
  DecisionRow,
  Provenance,
  TraceBundle,
  TraceEvent,
  WhyReasons,
  WhyResponse,
} from "./types";

export const UNKNOWN = "UNKNOWN";
export const NOT_OBSERVED = "NOT OBSERVED";
export const PROVENANCE_GAP = "PROVENANCE GAP";
export const TERMINATED = "TERMINATED";
export const NO_REASON_OBSERVED = "NO REASON OBSERVED";
export const BACKEND_PENDING = "backend endpoint pending";

/* ---------------------------------------------------------------- §14
 * Payload metadata view — typed v2 fields FIRST, then safe detail keys.
 * Renders UNKNOWN / NOT AVAILABLE for absent fields, never a default.
 */
export interface PayloadField {
  key: string;
  value: string;
  /** origin of the rendered value (testable provenance, §60.1) */
  origin: "v2" | "detail" | "absent";
}

/** Sensitive key tokens mirrored from trace_contract.py:148 (`_is_sensitive`). */
const SENSITIVE_TOKENS = [
  "password",
  "passwd",
  "secret",
  "token",
  "api_key",
  "apikey",
  "api-key",
  "authorization",
  "credential",
  "private",
  "bearer",
  "cookie",
  "login",
  "passphrase",
  "auth_header",
  "access_key",
  "session_key",
  "dsn",
  "conn_str",
  "connection_string",
];

export function isSensitiveKey(key: string): boolean {
  const k = String(key).toLowerCase();
  return SENSITIVE_TOKENS.some((tok) => k.includes(tok));
}

function text(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return v.trim() ? v.trim() : null;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    const s = JSON.stringify(v);
    return s && s !== "null" ? s : null;
  } catch {
    return String(v);
  }
}

/**
 * §14 payload metadata for one event: v2 fields first (payload_summary, state,
 * mode, freshness, reason_code, model/provider, ids, latency), then safe
 * detail keys the runtime recorded. Sensitive keys are redacted (backend
 * already redacts — this is the defense-in-depth mirror of
 * trace_contract.py:183 `_is_sensitive`, §13/§48).
 */
export function payloadFields(ev: TraceEvent | null | undefined): PayloadField[] {
  if (!ev) return [];
  const out: PayloadField[] = [];
  const seen = new Set<string>();
  const push = (key: string, v: unknown, origin: PayloadField["origin"]) => {
    if (seen.has(key)) return;
    seen.add(key);
    if (origin === "absent") {
      out.push({ key, value: isSensitiveKey(key) ? "***REDACTED***" : UNKNOWN, origin });
      return;
    }
    const t = text(v);
    out.push({
      key,
      value: t === null ? (isSensitiveKey(key) ? "***REDACTED***" : UNKNOWN) : t,
      origin,
    });
  };
  // v2 typed fields first (CONTRACT §frozen v2 field order).
  push("payload_summary", ev.payload_summary, ev.payload_summary ? "v2" : "absent");
  push("state", ev.state, ev.state ? "v2" : "absent");
  push("mode", ev.mode, ev.mode ? "v2" : "absent");
  push("freshness", ev.freshness, ev.freshness === undefined || ev.freshness === null ? "absent" : "v2");
  push("reason_code", ev.reason_code, ev.reason_code ? "v2" : "absent");
  push("error_code", ev.error_code, ev.error_code ? "v2" : "absent");
  push("source", ev.source, ev.source ? "v2" : "absent");
  push("destination", ev.destination, ev.destination ? "v2" : "absent");
  push("model", ev.model, ev.model ? "v2" : "absent");
  push("provider", ev.provider, ev.provider ? "v2" : "absent");
  push("request_id", ev.request_id, ev.request_id ? "v2" : "absent");
  push("root_event_id", ev.root_event_id, ev.root_event_id ? "v2" : "absent");
  push("position_id", ev.position_id, ev.position_id ? "v2" : "absent");
  push("order_id", ev.order_id, ev.order_id ? "v2" : "absent");
  push("deal_id", ev.deal_id, ev.deal_id ? "v2" : "absent");
  push("execution_id", ev.execution_id, ev.execution_id ? "v2" : "absent");
  push("snapshot_id", ev.snapshot_id, ev.snapshot_id ? "v2" : "absent");
  push("duration_ms", ev.duration_ms, typeof ev.duration_ms === "number" ? "v2" : "absent");
  push("started_at", ev.started_at, ev.started_at ? "v2" : "absent");
  push("completed_at", ev.completed_at, ev.completed_at ? "v2" : "absent");
  // safe detail keys (backend-side sanitized already; this list never holds
  // credentials — sensitive names are caught by isSensitiveKey above).
  if (ev.detail) {
    for (const [k, v] of Object.entries(ev.detail)) {
      push(k, v, "detail");
    }
  }
  return out;
}

/* ---------------------------------------------------------------- §15/§39
 * What-changed delta between ADJACENT events of ONE trace, from REAL fields
 * only: v2 payload_summary keys + safe detail keys. A key only ever appears as
 * ADDED / REMOVED / CHANGED — never synthesized.
 */
export type DeltaKind = "ADDED" | "REMOVED" | "CHANGED";

export interface DeltaEntry {
  key: string;
  kind: DeltaKind;
  from: string;
  to: string;
}

export interface EventDelta {
  /** The delta compares prev -> cur; null when cur is the trace's first event. */
  prev: TraceEvent | null;
  cur: TraceEvent;
  entries: DeltaEntry[];
  /** Honest verdict word for the delta view (never a computed outcome). */
  status: string;
}

function evidenceMap(ev: TraceEvent): Map<string, unknown> {
  const m = new Map<string, unknown>();
  if (ev.payload_summary) {
    for (const [k, v] of Object.entries(ev.payload_summary)) m.set(k, v);
  }
  if (ev.detail) {
    for (const [k, v] of Object.entries(ev.detail)) {
      if (!isSensitiveKey(k)) m.set(k, v);
    }
  }
  return m;
}

export function deltaBetween(prev: TraceEvent | null, cur: TraceEvent): EventDelta {
  const entries: DeltaEntry[] = [];
  if (prev) {
    const a = evidenceMap(prev);
    const b = evidenceMap(cur);
    for (const [k, bv] of b) {
      if (!a.has(k)) {
        const t = text(bv);
        entries.push({ key: k, kind: "ADDED", from: UNKNOWN, to: t ?? UNKNOWN });
      } else {
        const av = a.get(k);
        if (JSON.stringify(av) !== JSON.stringify(bv)) {
          entries.push({
            key: k,
            kind: "CHANGED",
            from: text(av) ?? UNKNOWN,
            to: text(bv) ?? UNKNOWN,
          });
        }
      }
    }
    for (const [k, av] of a) {
      if (!b.has(k)) {
        entries.push({ key: k, kind: "REMOVED", from: text(av) ?? UNKNOWN, to: UNKNOWN });
      }
    }
  }
  return {
    prev,
    cur,
    entries,
    status: cur.status ?? UNKNOWN,
  };
}

/**
 * Deltas for every adjacent pair in a trace (§39 INPUT/OUTPUT/DELTA). Derived
 * from the observed, sequence-ordered events only — no stage stitching.
 */
export function traceDeltas(events: TraceEvent[]): EventDelta[] {
  const sorted = events.slice().sort((a, b) => a.sequence - b.sequence);
  const out: EventDelta[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const cur = sorted[i];
    if (!cur) continue;
    out.push(deltaBetween(i > 0 ? sorted[i - 1] ?? null : null, cur));
  }
  return out;
}

/* ---------------------------------------------------------------- §40
 * Freshness display: the runtime's own freshness word, verbatim.
 */
export function freshnessWord(ev: TraceEvent | null | undefined): string {
  if (!ev) return UNKNOWN;
  if (ev.freshness === undefined || ev.freshness === null) return NOT_OBSERVED;
  return String(ev.freshness);
}

/** Freshness tone class (for the CSS token mapping) — restates the word only. */
export function freshnessTone(word: string): "fresh" | "stale" | "invalid" | "unknown" {
  const w = String(word).toUpperCase();
  if (w === "VALID" || w === "FRESH") return "fresh";
  if (w === "STALE" || w === "AGING") return "stale";
  if (w === "INVALID") return "invalid";
  return "unknown";
}

/* ---------------------------------------------------------------- §41
 * Latency waterfall: per-event latency_us / duration_ms only, labelled as a
 * UI-observed sum when the backend carries no total.
 */
export interface WaterfallBar {
  stage: string;
  event_id: string;
  us: number;
  /** NOT OBSERVED when the runtime recorded no latency for this event. */
  observed: boolean;
}

export interface LatencyWaterfall {
  bars: WaterfallBar[];
  /** Sum of the observed per-event latencies — labelled UI-observed sum. */
  ui_sum_us: number | null;
  /** Backend-provided per-stage p50/p95/p99 totals, when present. */
  backend_stages: Record<string, { p50_us: number | null; p95_us: number | null; n: number }>;
  /** Stages the backend timed but which have no event in this trace. */
  backend_only_stages: string[];
  /** Total claimed by the backend (duration_ms on a terminal event), else null. */
  backend_total_us: number | null;
}

export function latencyWaterfall(
  events: TraceEvent[],
  stats?: {
    stages?: Record<string, { p50_us?: number; p95_us?: number; n?: number }> | null;
  } | null,
): LatencyWaterfall {
  const bars: WaterfallBar[] = events
    .slice()
    .sort((a, b) => a.sequence - b.sequence)
    .map((e) => {
      const us = e.duration_ms != null ? e.duration_ms * 1000 : e.latency_us;
      return {
        stage: e.stage,
        event_id: e.event_id,
        us: typeof us === "number" ? us : 0,
        observed: typeof us === "number",
      };
    });
  const uiSum = bars.length ? bars.reduce((a, b) => a + b.us, 0) : null;
  const backendStages: LatencyWaterfall["backend_stages"] = {};
  const statsStages = stats?.stages ?? null;
  if (statsStages) {
    for (const [stage, entry] of Object.entries(statsStages)) {
      const p50 = entry?.p50_us;
      const p95 = entry?.p95_us;
      backendStages[stage] = {
        p50_us: typeof p50 === "number" ? p50 : null,
        p95_us: typeof p95 === "number" ? p95 : null,
        n: typeof entry?.n === "number" ? entry.n : 0,
      };
    }
  }
  const observedStageNames = new Set(bars.map((b) => b.stage));
  const backendOnlyStages = Object.keys(backendStages).filter(
    (s) => !observedStageNames.has(s),
  );
  const backendTotal = events.reduce<number | null>((acc, e) => {
    if (typeof e.duration_ms !== "number") return acc;
    return acc === null ? e.duration_ms * 1000 : Math.max(acc, e.duration_ms * 1000);
  }, null);
  return {
    bars,
    ui_sum_us: uiSum,
    backend_stages: backendStages,
    backend_only_stages: backendOnlyStages,
    backend_total_us: backendTotal,
  };
}

/* ---------------------------------------------------------------- §42
 * Error cascade: chains of real error_code values across one trace, in
 * observation order. No error_code anywhere => NO ERROR OBSERVED.
 */
export interface CascadeNode {
  stage: string;
  event_id: string;
  timestamp: string | null;
  error_code: string;
  reason_code: string | null;
  state: string | null;
}

export interface ErrorCascade {
  nodes: CascadeNode[];
  /** "NO ERROR OBSERVED" when the trace carries no backend error_code. */
  summary: string;
}

export function errorCascade(events: TraceEvent[]): ErrorCascade {
  const nodes: CascadeNode[] = [];
  for (const e of events.slice().sort((a, b) => a.sequence - b.sequence)) {
    const code = e.error_code ?? (e.detail?.error_code as string | null) ?? null;
    if (!code || !String(code).trim()) continue;
    nodes.push({
      stage: e.stage,
      event_id: e.event_id,
      timestamp: e.timestamp,
      error_code: String(code),
      reason_code: e.reason_code ?? (e.detail?.reason_code as string | null) ?? null,
      state: e.state ?? null,
    });
  }
  return {
    nodes,
    summary: nodes.length ? `${nodes.length} ERROR EVENT(S)` : "NO ERROR OBSERVED",
  };
}

/* ---------------------------------------------------------------- §37/§38
 * WHY / NEXT rendering over GET /api/trace/why/{event_id}. The endpoint's own
 * states are the UI's states: TERMINATED / NOT OBSERVED / 404 / pending.
 */
export type WhyViewState =
  | { kind: "EMPTY" }
  | { kind: "PENDING" }
  | { kind: "NOT_FOUND"; eventId: string }
  | { kind: "ERROR"; message: string }
  | { kind: "READY"; res: WhyResponse };
export interface WhyPanel {
  state: WhyViewState;
  whyLines: Array<{ key: string; value: string }>;
  next: {
    destination: string;
    provenance: string;
    link: string;
    event_id: string | null;
    stage: string | null;
    status: string | null;
    timestamp: string | null;
    terminal: boolean;
    note: string;
    whyLines: Array<{ key: string; value: string }>;
  } | null;
  provenance: string;
}

export function whyReasonsLines(why: WhyReasons | null | undefined): Array<{ key: string; value: string }> {
  if (!why) return [];
  const keys = ["reason_code", "error_code", "state", "rejection_reason", "blocked_by", "decision_stage"];
  return keys.map((k) => ({ key: k, value: text((why as Record<string, unknown>)[k]) ?? UNKNOWN }));
}

export function whyHasReason(why: WhyReasons | null | undefined): boolean {
  if (!why) return false;
  return Object.values(why).some((v) => text(v) !== null);
}

/**
 * The WHY panel's PRIMARY view (§37). Provenance is taken verbatim: an
 * observed edge restates `observed`; an inferred one restates `inferred`;
 * TERMINATED / NOT OBSERVED carry no edge, so the panel says PROVENANCE GAP.
 */
export function renderWhy(res: WhyResponse | null, ctx: WhyViewState): WhyPanel {
  if (ctx.kind === "EMPTY") {
    return { state: ctx, whyLines: [], next: null, provenance: UNKNOWN };
  }
  if (ctx.kind === "PENDING" || ctx.kind === "NOT_FOUND" || ctx.kind === "ERROR") {
    return { state: ctx, whyLines: [], next: null, provenance: UNKNOWN };
  }
  const r = res;
  if (!r) return { state: { kind: "EMPTY" }, whyLines: [], next: null, provenance: UNKNOWN };
  const whyLines = whyReasonsLines(r.why);
  const next = r.next;
  let nextPanel: WhyPanel["next"] = null;
  if (next && next.observed && next.destination !== "TERMINATED") {
    nextPanel = {
      destination: next.destination ?? UNKNOWN,
      provenance: next.provenance === "inferred" ? "inferred" : "observed",
      link: next.link === "trace_sequence" ? "trace_sequence" : "parent_event_id",
      event_id: next.event_id ?? null,
      stage: next.stage ?? null,
      status: next.status ?? null,
      timestamp: next.timestamp ?? null,
      terminal: next.terminal ?? false,
      note: next.provenance === "inferred"
        ? "Next event of the same trace, linked by sequence only — no observed parent edge (PROVENANCE GAP risk)."
        : "Next event linked by the runtime's own parent_event_id.",
      whyLines: whyReasonsLines(next.why),
    };
  } else if (next && next.observed && next.destination === "TERMINATED") {
    nextPanel = {
      destination: TERMINATED,
      provenance: PROVENANCE_GAP,
      link: "none",
      event_id: null,
      stage: null,
      status: next.terminal_status ?? null,
      timestamp: null,
      terminal: true,
      note: next.terminal_status
        ? `The path observably ended here (terminal status: ${next.terminal_status}).`
        : "The path observably ended here; the runtime recorded no terminal status.",
      whyLines: whyReasonsLines(next.why),
    };
  } else if (next && !next.observed) {
    nextPanel = {
      destination: NOT_OBSERVED,
      provenance: PROVENANCE_GAP,
      link: "none",
      event_id: null,
      stage: null,
      status: null,
      timestamp: null,
      terminal: false,
      note: "No further event of this trace is retained — the observer ring may have evicted it (PROVENANCE GAP).",
      whyLines: [],
    };
  }
  return {
    state: ctx,
    whyLines,
    next: nextPanel,
    provenance: r.provenance === "inferred" ? "inferred" : (r.provenance ?? PROVENANCE_GAP),
  };
}

/* ---------------------------------------------------------------- §62
 * Stream lifecycle state words (§62): STREAM DISCONNECTED / RECONNECTING /
 * RESYNCING, derived strictly from the client's real connection state.
 */
export function streamStateWords(status: string, resyncing: boolean): {
  label: string;
  tone: "ok" | "warn" | "fail" | "info";
  note: string;
} {
  if (status === "connected") {
    return {
      label: resyncing ? "RESYNCING" : "CONNECTED",
      tone: "ok",
      note: resyncing
        ? "Reconnected — reconciling the event position from the resume point."
        : "Live observer stream connected.",
    };
  }
  if (status === "reconnecting") {
    return {
      label: "RECONNECTING",
      tone: "warn",
      note: "Stream disconnected; the client keeps its last_seq and will resume without duplicating events.",
    };
  }
  if (status === "connecting") {
    return {
      label: resyncing ? "RESYNCING" : "CONNECTING",
      tone: "info",
      note: "Opening the observer stream with the last observed sequence as the resume point.",
    };
  }
  if (status === "failed") {
    return {
      label: "STREAM DISCONNECTED",
      tone: "fail",
      note: "Stream failed; polling continues and the last observed state stays on screen.",
    };
  }
  return {
    label: "STREAM DISCONNECTED",
    tone: "fail",
    note: "No live stream — the page shows the last observed data only.",
  };
}

/* ---------------------------------------------------------------- §43
 * System health overlay: state words only from real backend observer /
 * topology payloads. A subsystem with no endpoint => "backend endpoint
 * pending" (CONTRACT §43: never a faked status).
 */
export interface HealthItem {
  subsystem: string;
  state: string;
  source: string;
  tone: "ok" | "warn" | "fail" | "unknown";
}

/** Map a backend state word to a tone class (restate only, never compute). */
export function stateTone(word: string | null | undefined): HealthItem["tone"] {
  const w = String(word ?? "").toUpperCase();
  if (!w) return "unknown";
  if (["ACTIVE", "CONNECTED", "HEALTHY", "SERVING", "READY", "ARMED", "RUNNING", "ON"].includes(w)) {
    return "ok";
  }
  if (["OFFLINE", "ERROR", "FAILED", "DOWN", "STOPPED", "OFF"].includes(w)) return "fail";
  if (["DEGRADED", "STALE", "STOPPING", "STARTING", "SHADOW", "PENDING"].includes(w)) return "warn";
  return "unknown";
}

export function systemHealth(
  observer: {
    status?: string | null;
    session?: { status?: string | null } | null;
    subscribers?: unknown[] | null;
  } | null,
  topology: { nodes?: Array<{ stage: string; count: number }> } | null,
  latency: { stages?: Record<string, unknown> | null } | null,
): HealthItem[] {
  const items: HealthItem[] = [];
  const obsStatus = observer?.status ?? null;
  items.push({
    subsystem: "OBSERVER",
    state: obsStatus ?? "UNKNOWN",
    source: "GET /api/trace/observer .status",
    tone: stateTone(obsStatus),
  });
  // Subsystems without a dedicated health endpoint: their evidence is the
  // presence of observed stage events / latency samples. Absence is stated as
  // "backend endpoint pending", never as a healthy/unhealthy verdict.
  const stageNames = (topology?.nodes ?? []).map((n) => n.stage);
  const timedStages = latency?.stages ? Object.keys(latency.stages) : [];
  const subs: Array<[string, string, string]> = [
    ["RISK", "RISK", "GET /api/trace/topology .nodes[]"],
    ["EXECUTION", "EXECUTION", "GET /api/trace/topology .nodes[]"],
    ["GATEWAY", "GATEWAY", "GET /api/trace/topology .nodes[]"],
    ["MODEL", "INFERENCE", "GET /api/trace/latency .stages"],
  ];
  for (const [label, evidenceName, source] of subs) {
    const observed = stageNames.includes(evidenceName) || timedStages.includes(evidenceName);
    items.push({
      subsystem: label,
      state: observed ? "OBSERVED" : BACKEND_PENDING,
      source,
      tone: observed ? "ok" : "unknown",
    });
  }
  void observer?.session?.status;
  void observer?.subscribers;
  return items;
}

/* ---------------------------------------------------------------- §50
 * Decision card — compact summary from the real decision row + the trace's
 * observed events. RECOMMENDED is never rendered as EXECUTED; the execution
 * line restates a backend CONFIRMED/EXECUTED state word only.
 */
export interface DecisionCard {
  trace_id: string | null;
  decision_id: string | null;
  position_id: string | null;
  action: string;
  mode: string;
  model: string;
  provider: string;
  policy: string;
  risk: string;
  execution: string;
  gateway: string;
  total_latency_us: number | null;
}

const EXECUTED_WORDS = new Set(["EXECUTED", "CONFIRMED"]);
const REJECT_WORDS = new Set(["REJECTED", "NO_TRADE", "FAILED", "BLOCKED", "ERROR"]);

/**
 * Execution line for the decision card (§50/§66): only a backend
 * CONFIRMED/EXECUTED state/status word may read as executed; everything else
 * restates the observed word or NOT REACHED.
 */
export function executionLine(events: TraceEvent[]): string {
  const gatewayEvents = events.filter((e) => e.stage === "GATEWAY" || e.stage === "MT5");
  const executedEvent = gatewayEvents.find((e) =>
    EXECUTED_WORDS.has((e.state ?? e.status ?? "").toUpperCase()),
  );
  if (executedEvent) {
    return (executedEvent.state ?? executedEvent.status ?? UNKNOWN).toUpperCase();
  }
  if (gatewayEvents.length) {
    const last = gatewayEvents[gatewayEvents.length - 1];
    const w = (last?.state ?? last?.status ?? null);
    return w ? String(w).toUpperCase() : "GATEWAY REACHED (NO CONFIRMATION OBSERVED)";
  }
  const execEvents = events.filter((e) => e.stage === "EXECUTION" || e.stage === "ORDER");
  if (execEvents.length) {
    const last = execEvents[execEvents.length - 1];
    const w = last?.state ?? last?.status ?? null;
    return w ? String(w).toUpperCase() : "EXECUTION OBSERVED (NO GATEWAY RESPONSE)";
  }
  return "NOT REACHED";
}

export function riskLine(row: DecisionRow | null, events: TraceEvent[]): string {
  const riskEvents = events.filter((e) => e.stage === "RISK");
  for (let i = riskEvents.length - 1; i >= 0; i--) {
    const e = riskEvents[i];
    if (!e) continue;
    const w = e.state ?? e.status ?? (e.detail?.verdict as string | null) ?? null;
    if (w) return String(w).toUpperCase();
  }
  const detailVerdict = riskEvents
    .map((e) => e.detail?.reason as string | null)
    .find((v) => typeof v === "string" && v.trim());
  if (detailVerdict) return detailVerdict;
  if (row?.risk_state) return String(row.risk_state).toUpperCase();
  if (riskEvents.length) return "RISK OBSERVED (NO VERDICT WORD)";
  return "NOT REACHED";
}

export function decisionCard(
  row: DecisionRow | null,
  events: TraceEvent[],
): DecisionCard {
  const evMode = events.find((e) => e.mode)?.mode ?? null;
  return {
    trace_id: row?.trace_id ?? events.find((e) => e.trace_id)?.trace_id ?? null,
    decision_id: row?.decision_id ?? events.find((e) => e.decision_id)?.decision_id ?? null,
    position_id:
      events.find((e) => e.position_id)?.position_id ??
      text(events.find((e) => e.detail?.position_id)?.detail?.position_id) ??
      null,
    action: text(row?.action) ?? text(events.find((e) => e.detail?.action)?.detail?.action) ?? UNKNOWN,
    mode: text(evMode) ?? text(row?.execution_path) ?? UNKNOWN,
    model: text(events.find((e) => e.model)?.model) ?? text(row?.model_id) ?? UNKNOWN,
    provider: text(events.find((e) => e.provider)?.provider) ?? UNKNOWN,
    policy: text(row?.policy) ?? UNKNOWN,
    risk: riskLine(row, events),
    execution: executionLine(events),
    gateway: (() => {
      const gw = events.find((e) => e.stage === "GATEWAY" || e.stage === "MT5");
      const name = gw?.detail?.gateway;
      return text(name) ?? (gw ? "GATEWAY" : "NOT REACHED");
    })(),
    total_latency_us: latencyWaterfall(events).ui_sum_us,
  };
}

/* ---------------------------------------------------------------- §55/§56
 * Provenance-gap wording for the replay/inspector edges (render layer only —
 * no graph logic; the graph stays lane C's).
 */
export function gapWording(provenance: string | null | undefined): string {
  if (provenance === "observed") return "observed";
  if (provenance === "inferred") {
    return "inferred — linked by trace sequence only, no observed parent edge (PROVENANCE GAP)";
  }
  return "PROVENANCE GAP — no observed runtime evidence connecting these stages";
}

export function provenanceWord(p: string | null | undefined): Provenance | "gap" {
  if (p === "observed" || p === "inferred") return p;
  return "gap";
}

/* ---------------------------------------------------------------- §45-§49
 * Safe DB metadata + model identity + origin: every field read from the
 * backend payload, UNKNOWN when absent.
 */
export function safeDbMetadata(ev: TraceEvent | null): Record<string, string> {
  if (!ev || !ev.detail) return {};
  const d = ev.detail;
  const keys = ["operation", "table", "rows", "row_count", "duration_ms", "success", "result", "driver"];
  const out: Record<string, string> = {};
  for (const k of keys) {
    const v = d[k];
    const t = text(v);
    if (t !== null) out[k] = t;
  }
  return out;
}

export function modelIdentity(events: TraceEvent[]): {
  model: string;
  provider: string;
  model_version: string;
  artifact: string;
  snapshot_id: string;
} {
  const inf = events.find((e) => e.stage === "INFERENCE") ?? null;
  const pick = (f: (e: TraceEvent) => unknown): string => {
    const direct = events.map(f).find((v) => text(v) !== null) ?? null;
    return text(direct) ?? UNKNOWN;
  };
  return {
    model: pick((e) => e.model) ?? text(inf?.detail?.model_id) ?? UNKNOWN,
    provider: pick((e) => e.provider) ?? UNKNOWN,
    model_version: pick((e) => e.detail?.model_version) ?? UNKNOWN,
    artifact: pick((e) => e.detail?.artifact_path ?? e.detail?.artifact) ?? UNKNOWN,
    snapshot_id: pick((e) => e.snapshot_id) ?? UNKNOWN,
  };
}

export function originLine(events: TraceEvent[]): {
  source: string;
  destination: string;
  request_id: string;
} {
  const first = events[0] ?? null;
  const last = events[events.length - 1] ?? null;
  return {
    source: text(first?.source) ?? text(first?.detail?.source) ?? UNKNOWN,
    destination: text(last?.destination) ?? text(last?.detail?.destination) ?? UNKNOWN,
    request_id: text(events.find((e) => e.request_id)?.request_id) ?? UNKNOWN,
  };
}

/* ---------------------------------------------------------------- §17/§18
 * Decision + rejection forensics from the real decision row / events. The
 * value/threshold pairs render ONLY when the detail carries real numbers.
 */
export interface RejectionPair {
  key: string;
  actual: string;
  required: string;
  operator: string;
}

const PAIR_SUFFIXES = ["", "_actual", "_value", "_measured", "_observed"];

/** Value/threshold pairs from the runtime's own gate record (risk_checks). */
export function rejectionPairs(
  row: DecisionRow | null,
  events: TraceEvent[],
): RejectionPair[] {
  const checks: Record<string, unknown> = (() => {
    const riskEvent = events.find((e) => e.stage === "RISK" || e.stage === "POLICY");
    const fromEvent = riskEvent?.detail?.risk_checks;
    if (fromEvent && typeof fromEvent === "object") {
      return fromEvent as Record<string, unknown>;
    }
    const fromRow = row ? (row as Record<string, unknown>).risk_checks : null;
    if (fromRow && typeof fromRow === "object") return fromRow as Record<string, unknown>;
    return {};
  })();
  const pairs: RejectionPair[] = [];
  const seen = new Set<string>();
  const find = (
    base: string,
  ): { actual: unknown; required: unknown; thresholdKey: string } | null => {
    for (const suffix of PAIR_SUFFIXES) {
      const actual = checks[`${base}${suffix}`];
      if (actual === undefined || actual === null) continue;
      const thresholdKey = ["min_", "max_"]
        .map((p) => `${p}${base}`)
        .concat([`${base}_threshold`, `${base}_limit`])
        .find((k) => checks[k] !== undefined && checks[k] !== null);
      if (!thresholdKey) continue;
      return { actual, required: checks[thresholdKey], thresholdKey };
    }
    return null;
  };
  for (const key of Object.keys(checks)) {
    const stripped = key.replace(/^(min_|max_)/, "");
    const pair = find(stripped);
    if (pair && !seen.has(stripped)) {
      seen.add(stripped);
      pairs.push({
        key: stripped,
        actual: text(pair.actual) ?? UNKNOWN,
        required: text(pair.required) ?? UNKNOWN,
        // operator from the REAL threshold key the runtime recorded
        operator: pair.thresholdKey.startsWith("min_")
          ? ">="
          : pair.thresholdKey.startsWith("max_")
            ? "<="
            : "vs",
      });
    }
  }
  return pairs;
}

export interface RejectionForensics {
  rejected: boolean;
  reason_code: string;
  rejection_reason: string;
  blocked_by: string;
  rejecting_stage: string;
  state: string;
  pairs: RejectionPair[];
  next: string;
}

/**
 * §18 rejection forensics. `rejected` is true ONLY when a backend status/state
 * word asserts rejection (never inferred from latency or absence); the value /
 * threshold pairs render only where the detail carries real numbers, else the
 * explicit word UNKNOWN.
 */
export function rejectionForensics(
  row: DecisionRow | null,
  events: TraceEvent[],
  why: WhyResponse | null = null,
): RejectionForensics {
  const statusWord = String(row?.status ?? "").toUpperCase();
  const stateWords = events
    .map((e) => (e.state ?? e.status ?? "").toUpperCase())
    .filter(Boolean);
  const rejected =
    REJECT_WORDS.has(statusWord) ||
    stateWords.some((w) => REJECT_WORDS.has(w)) ||
    (why?.why.rejection_reason ? true : false);
  const rejectEvent = events
    .slice()
    .reverse()
    .find(
      (e) =>
        REJECT_WORDS.has(String(e.state ?? "").toUpperCase()) ||
        REJECT_WORDS.has(String(e.status ?? "").toUpperCase()) ||
        Boolean(e.reason_code),
    );
  // Next-state honesty (§18): when WHY is loaded, restate the endpoint's own
  // destination word (TERMINATED / NOT OBSERVED / stage); otherwise only a
  // verified CONFIRMED/EXECUTED state word may read as continued execution.
  const nextLine = (() => {
    if (why?.next && why.next.destination === "TERMINATED") {
      return why.next.terminal_status
        ? `TERMINATED — ${why.next.terminal_status}`
        : "TERMINATED";
    }
    if (why?.next && why.next.observed) return why.next.destination;
    return rejected ? "NOT OBSERVED (no continuation evidence)" : "CONTINUES TO OBSERVED NEXT STAGE";
  })();
  return {
    rejected,
    reason_code:
      text(why?.why.reason_code) ??
      text(rejectEvent?.reason_code) ??
      text(row?.reason_code) ??
      UNKNOWN,
    rejection_reason:
      text(why?.why.rejection_reason) ??
      text(row?.rejection_reason) ??
      text(rejectEvent?.detail?.rejection_reason) ??
      UNKNOWN,
    blocked_by: text(rejectEvent?.detail?.blocked_by) ?? UNKNOWN,
    rejecting_stage: rejectEvent?.stage ?? (rejected ? UNKNOWN : "NOT REACHED"),
    state: text(rejectEvent?.state) ?? text(rejectEvent?.status) ?? UNKNOWN,
    pairs: rejectionPairs(row, events),
    next: nextLine,
  };
}

/* ---------------------------------------------------------------- helpers
 * Bundle-event helpers shared with the old inspector surface.
 */
export function bundleEvents(bundle: TraceBundle | null): TraceEvent[] {
  return bundle ? bundle.events : [];
}
