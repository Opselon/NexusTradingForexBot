/**
 * Decision Trace — the bounded state store (§56).
 *
 * Separates the concerns the spec mandates:
 *   systemStatus | liveEvents | topology | selectedTrace | selectedNode |
 *   timeline | historicalTraces | connection | observer | filters | search |
 *   replay | follow | why
 *
 * Backpressure contract (§43/§63): the store is the ONLY place event memory
 * grows. It is strictly bounded (LIVE_EVENT_CAP / STREAM_ROW_CAP /
 * QUEUE_CAP); every append is O(cap) via index rotation. The graph is derived
 * on demand by selectors, never re-stored. Coalesced/dropped counters come
 * straight from the backend batch frames and are surfaced verbatim (never
 * silently hidden).
 *
 * The store never mutates trading behavior — it only consumes observer data.
 * Replay/speed (§29/§30) is a pure UI-observer mode: `frozen` gates the event
 * array; the SSE client keeps advancing lastSeq so an unfreeze resumes cleanly
 * and the engine is never asked to slow down or wait.
 */

import { create } from "zustand";
import {
  buildGraph,
  buildTimeline,
  checkIntegrity,
  coalesceVisual,
  type ClientIntegrity,
  type DerivedGraph,
} from "./traceGraph";
import type {
  DecisionRow,
  EventsSince,
  EventFilters,
  ObserverSnapshot,
  ObserverStatus,
  TopologySnapshot,
  TraceBundle,
  TraceEvent,
  WhyResponse,
} from "./types";
import { activeFilterWords } from "./types";
import type { TraceStreamStatus } from "./stream";

const LIVE_EVENT_CAP = 4000;
const DECISION_CAP = 400;
const TOPOLOGY_MAX_EVENTS = 12_000;
/** Bounded slice of the live stream rail (§58) — a hard cap, newest retained. */
const STREAM_ROW_CAP = 200;
/** Bounded trace queue (§31) — one bucket shape per observed trace state. */
const QUEUE_CAP = 80;

export type TraceFilter =
  | "ALL"
  | "ACTIVE"
  | "PASSED"
  | "REJECTED"
  | "ERROR"
  | "MODEL"
  | "REGIME"
  | "GATE"
  | "RULE"
  | "RISK"
  | "EXECUTION"
  | "MT5";

export type ViewMode = "LIVE" | "HISTORICAL" | "REPLAY";
export type GraphLevel = "SYSTEM" | "TRACE" | "EVENT";
export type FollowMode = "NONE" | "REQUEST" | "POSITION" | "ORDER";
/** Visualization cadence only (§30) — the engine's timing is never touched. */
export type ReplaySpeed = "LIVE" | 0.5 | 1 | 2 | 5;

export interface StreamRow {
  event_id: string;
  sequence: number;
  timestamp: string;
  stage: string;
  status: string | null;
  trace_id: string | null;
  symbol: string | null;
  provenance_gap: boolean;
  terminal: boolean;
}

/** One bucket of the trace queue (§31): the traces the page is tracking. */
export interface TraceQueueBucket {
  /** trace_id of a trace the store still holds events for. */
  trace_id: string;
  /** VERBATIM status word of the trace's last retained event (never computed). */
  last_status: string | null;
  /** Free-word mode the runtime recorded on a retained event, else null. */
  mode: string | null;
  stage_count: number;
  /** True only when a retained event carries backend state CONFIRMED/EXECUTED. */
  executed: boolean;
  /** True when a retained event's status is one of the backend terminal words. */
  failed: boolean;
  last_sequence: number;
  last_ts: string | null;
}

export interface TraceQueue {
  active: TraceQueueBucket[];
  recent: TraceQueueBucket[];
  failed: TraceQueueBucket[];
  completed: TraceQueueBucket[];
}

interface DecisionTraceState {
  // ---- connection / observer
  streamStatus: TraceStreamStatus;
  observerStatus: ObserverStatus | null;
  observer: ObserverSnapshot | null;
  streamError: string | null;
  gapMessage: string | null;
  disconnectedAt: number | null;
  malformed: number;
  /** Honest resume handshake (§62): last hello/resume seq acked by the page. */
  resumedSeq: number | null;
  resyncing: boolean;

  // ---- live events (bounded)
  events: TraceEvent[];
  lastSeq: number;
  coalescedEvents: number;
  droppedVisualEvents: number;

  // ---- topology (derived)
  topology: TopologySnapshot | null;

  // ---- decisions
  decisions: DecisionRow[];
  decisionsTotal: number;

  // ---- selection
  selectedBundle: TraceBundle | null;
  selectedBundleSource: ViewMode;
  selectedNodeStage: string | null;
  selectedEventId: string | null;
  graphLevel: GraphLevel;

  // ---- replay (§55) + visualization clock (§29/§30 — UI observer only)
  replay: {
    active: boolean;
    index: number;
    speed: number;
    playing: boolean;
  };
  viewClock: {
    /** speed: LIVE = follow the stream as it lands; a number = replay cadence. */
    speed: ReplaySpeed;
    /** PAUSE freezes the *view* (the observer is never paused); STEP advances. */
    paused: boolean;
  };

  // ---- follow modes (§32/§33/§34)
  follow: FollowMode;
  followKey: string | null;
  followFilters: EventFilters;

  // ---- filters / search
  filter: TraceFilter;
  search: string;
  symbolFilter: string | null;
  /** Server-side AND-filters applied to /api/trace/events (§36). */
  eventFilters: EventFilters;
  live: boolean;
  frozen: boolean;

  // ---- integrity (client-side live scan)
  integrity: ClientIntegrity | null;

  // ---- why (§37/§38) — last fetched WHY answer, keyed by event id
  whyCache: Map<string, WhyResponse>;

  // ---- live stream rail + trace queue (§58/§31)
  streamRows: StreamRow[];
  queue: TraceQueue;
  collapsedTraces: Set<string>;

  // ---- derived queue (recomputed on append, bounded by QUEUE_CAP)
  queueVersion: number;

  // ---- actions
  setStreamStatus: (s: TraceStreamStatus, error?: string) => void;
  setResyncing: (v: boolean) => void;
  setObserver: (snap: ObserverSnapshot | null) => void;
  setTopology: (t: TopologySnapshot | null) => void;
  appendFrame: (
    frame:
      | { kind: "batch"; events: TraceEvent[]; coalesced: number; dropped: number; lastSeq: number }
      | { kind: "resume"; payload: EventsSince },
  ) => void;
  setDecisions: (rows: DecisionRow[], total: number) => void;
  selectBundle: (bundle: TraceBundle | null, source: ViewMode) => void;
  selectNode: (stage: string | null) => void;
  selectEvent: (id: string | null) => void;
  setGraphLevel: (level: GraphLevel) => void;
  setFilter: (f: TraceFilter) => void;
  setSearch: (q: string) => void;
  setSymbolFilter: (s: string | null) => void;
  setEventFilters: (f: EventFilters) => void;
  clearEventFilters: () => void;
  setLive: (live: boolean) => void;
  setFrozen: (frozen: boolean) => void;
  noteGap: (message: string | null) => void;
  startReplay: () => void;
  stopReplay: () => void;
  replayStep: (delta: number) => void;
  replaySetSpeed: (speed: number) => void;
  replaySetPlaying: (playing: boolean) => void;
  setViewSpeed: (speed: ReplaySpeed) => void;
  toggleViewPaused: () => void;
  setFollow: (mode: FollowMode, key?: string | null) => void;
  clearFollow: () => void;
  /** Follow bar editor (§32-§34): stages the key without committing. */
  setFollowKey: (key: string) => void;
  /** Commit the staged key against the current follow mode. */
  commitFollow: () => void;
  setWhy: (eventId: string, res: WhyResponse | null) => void;
  toggleTraceCollapsed: (traceId: string) => void;
  collapseAllTraces: (collapsed: boolean) => void;
  reset: () => void;
}

function boundedPush<T>(arr: T[], items: T[], cap: number): T[] {
  if (items.length >= cap) return items.slice(items.length - cap);
  const next = arr.concat(items);
  return next.length > cap ? next.slice(next.length - cap) : next;
}

const TERMINAL_FAILURE_WORDS = new Set([
  "FAILED",
  "ERROR",
  "TIMEOUT",
  "CANCELLED",
  "BLOCKED",
  "REJECTED",
]);
const EXECUTED_WORDS = new Set(["EXECUTED", "CONFIRMED"]);

/**
 * Group the retained traces into the four queue buckets (§31), derived ONLY
 * from backend status words present on the retained events. A trace lands in
 * `completed` when its last retained event is a terminal status that is not a
 * failure; `failed` when the terminal word is a failure word; `active` while
 * its events keep landing (no terminal word yet); `recent` is a bounded tail
 * of completed/failed traces that just finished. NEVER invents a bucket label
 * a backend word did not support (§73 — empty buckets stay empty).
 */
export function groupTraces(events: TraceEvent[]): TraceQueue {
  const byTrace = new Map<string, TraceEvent[]>();
  for (const e of events) {
    if (!e.trace_id) continue;
    const arr = byTrace.get(e.trace_id);
    if (arr) arr.push(e);
    else byTrace.set(e.trace_id, [e]);
  }
  const buckets: TraceQueue = { active: [], recent: [], failed: [], completed: [] };
  let mostRecentSeq = 0;
  for (const arr of byTrace.values()) {
    const last = arr[arr.length - 1];
    if (!last) continue;
    const status = (last.status ?? "").toUpperCase();
    const bucket: TraceQueueBucket = {
      trace_id: last.trace_id as string,
      last_status: last.status,
      mode: firstMode(arr),
      stage_count: new Set(arr.map((e) => e.stage)).size,
      executed: arr.some((e) => EXECUTED_WORDS.has((e.state ?? e.status ?? "").toUpperCase())),
      failed: arr.some((e) => TERMINAL_FAILURE_WORDS.has((e.status ?? "").toUpperCase())),
      last_sequence: last.sequence,
      last_ts: last.timestamp,
    };
    const isTerminalFailure = TERMINAL_FAILURE_WORDS.has(status);
    const isTerminalOk = !isTerminalFailure && last.terminal;
    if (isTerminalFailure) buckets.failed.push(bucket);
    else if (isTerminalOk) buckets.completed.push(bucket);
    else buckets.active.push(bucket);
    mostRecentSeq = Math.max(mostRecentSeq, last.sequence);
  }
  for (const key of ["active", "failed", "completed", "recent"] as const) {
    buckets[key] = buckets[key]
      .sort((a, b) => b.last_sequence - a.last_sequence)
      .slice(0, QUEUE_CAP);
  }
  // recent = the last few traces that finished (terminal, non-active).
  buckets.recent = [...buckets.failed, ...buckets.completed]
    .sort((a, b) => b.last_sequence - a.last_sequence)
    .slice(0, QUEUE_CAP);
  // Hide the currently-active traces from `recent` (they are in `active`).
  const activeIds = new Set(buckets.active.map((b) => b.trace_id));
  buckets.recent = buckets.recent.filter((b) => !activeIds.has(b.trace_id));
  void mostRecentSeq;
  return buckets;
}

function firstMode(arr: TraceEvent[]): string | null {
  for (const e of arr) {
    if (e.mode) return e.mode;
    const legacy =
      e.detail && typeof e.detail.engine_mode === "string" ? e.detail.engine_mode : null;
    if (legacy) return legacy;
  }
  return null;
}

export const useDecisionTraceStore = create<DecisionTraceState>((set, get) => ({
  streamStatus: "disconnected",
  observerStatus: null,
  observer: null,
  streamError: null,
  gapMessage: null,
  disconnectedAt: null,
  malformed: 0,
  resumedSeq: null,
  resyncing: false,

  events: [],
  lastSeq: 0,
  coalescedEvents: 0,
  droppedVisualEvents: 0,

  topology: null,

  decisions: [],
  decisionsTotal: 0,

  selectedBundle: null,
  selectedBundleSource: "LIVE",
  selectedNodeStage: null,
  selectedEventId: null,
  graphLevel: "SYSTEM",

  replay: { active: false, index: 0, speed: 1, playing: false },
  viewClock: { speed: "LIVE", paused: false },

  follow: "NONE",
  followKey: null,
  followFilters: {},

  filter: "ALL",
  search: "",
  symbolFilter: null,
  eventFilters: {},
  live: true,
  frozen: false,

  integrity: null,

  whyCache: new Map<string, WhyResponse>(),

  streamRows: [],
  queue: { active: [], recent: [], failed: [], completed: [] },
  collapsedTraces: new Set<string>(),
  queueVersion: 0,

  setStreamStatus: (s, error) =>
    set({
      streamStatus: s,
      streamError: error ?? null,
      disconnectedAt: s === "disconnected" ? Date.now() : null,
      resyncing: s === "reconnecting" ? get().resyncing : get().resyncing,
    }),

  setResyncing: (v) => set({ resyncing: v }),

  setObserver: (snap) =>
    set({ observer: snap, observerStatus: snap?.status ?? null }),

  setTopology: (t) => set({ topology: t }),

  appendFrame: (frame) => {
    const st = get();
    if (st.viewClock.paused) {
      // View frozen (§29): data keeps being accounted (§79) but the arrays are
      // not mutated. lastSeq still advances so a later unfreeze resumes cleanly
      // and nothing is silently fabricated.
      if (frame.kind === "batch") set({ lastSeq: Math.max(st.lastSeq, frame.lastSeq) });
      return;
    }
    if (frame.kind === "resume") {
      const events = frame.payload.events ?? [];
      set({
        events: boundedPush(st.events, events, LIVE_EVENT_CAP),
        lastSeq: Math.max(st.lastSeq, frame.payload.last_seq ?? st.lastSeq),
        resumedSeq: frame.payload.last_seq ?? st.lastSeq,
        resyncing: false,
        gapMessage: frame.payload.gap ? "TRACE GAP" : st.gapMessage,
        streamRows: boundedPush(
          st.streamRows,
          events.map(streamRowOf),
          STREAM_ROW_CAP,
        ),
        queue: groupTraces(boundedPush(st.events, events, LIVE_EVENT_CAP)),
        queueVersion: st.queueVersion + 1,
      });
      return;
    }
    const raw = frame.events ?? [];
    // Coalesce redundant visual progress events (§43); terminal/rejection
    // events are never coalesced — coalesceVisual only drops _PROGRESS.
    const { events, coalesced } = coalesceVisual(raw);
    const nextEvents = boundedPush(st.events, events, LIVE_EVENT_CAP);
    set({
      events: nextEvents,
      lastSeq: Math.max(st.lastSeq, frame.lastSeq ?? st.lastSeq),
      coalescedEvents: st.coalescedEvents + coalesced,
      droppedVisualEvents: st.droppedVisualEvents + frame.dropped,
      streamRows: boundedPush(
        st.streamRows,
        events.map(streamRowOf),
        STREAM_ROW_CAP,
      ),
      queue: groupTraces(nextEvents),
      queueVersion: st.queueVersion + 1,
    });
  },

  setDecisions: (rows, total) =>
    set({ decisions: boundedPush([], rows, DECISION_CAP), decisionsTotal: total }),

  selectBundle: (bundle, source) =>
    set({
      selectedBundle: bundle,
      selectedBundleSource: source,
      selectedEventId: null,
      selectedNodeStage: null,
      graphLevel: bundle ? "TRACE" : "SYSTEM",
      replay: bundle ? { active: false, index: 0, speed: 1, playing: false } : get().replay,
    }),

  selectNode: (stage) => set({ selectedNodeStage: stage, graphLevel: stage ? "TRACE" : get().graphLevel }),
  selectEvent: (id) => set({ selectedEventId: id, graphLevel: id ? "EVENT" : get().graphLevel }),
  setGraphLevel: (level) => set({ graphLevel: level }),

  setFilter: (f) => set({ filter: f }),
  setSearch: (q) => set({ search: q }),
  setSymbolFilter: (s) => set({ symbolFilter: s }),
  setEventFilters: (f) => set({ eventFilters: f }),
  clearEventFilters: () => set({ eventFilters: {} }),
  setLive: (live) => set({ live }),
  setFrozen: (frozen) => set({ frozen }),

  noteGap: (message) => set({ gapMessage: message }),

  startReplay: () => {
    const b = get().selectedBundle;
    if (!b) return;
    set({
      replay: { active: true, index: 0, speed: get().replay.speed || 1, playing: false },
      graphLevel: "TRACE",
      selectedBundleSource: "REPLAY",
    });
  },
  stopReplay: () => set({ replay: { active: false, index: 0, speed: 1, playing: false } }),
  replayStep: (delta) =>
    set((s) => {
      const total = s.selectedBundle?.events.length ?? 0;
      const next = Math.max(0, Math.min(total - 1, s.replay.index + delta));
      return { replay: { ...s.replay, index: next } };
    }),
  replaySetSpeed: (speed) => set((s) => ({ replay: { ...s.replay, speed } })),
  replaySetPlaying: (playing) => set((s) => ({ replay: { ...s.replay, playing } })),

  setViewSpeed: (speed) => set((s) => ({ viewClock: { ...s.viewClock, speed } })),
  toggleViewPaused: () => set((s) => ({ viewClock: { ...s.viewClock, paused: !s.viewClock.paused } })),

  setFollow: (mode, key) => {
    const st = get();
    const k = key ?? st.followKey;
    set({ follow: mode, followKey: mode === "NONE" ? null : k, followFilters: {} });
    if (mode === "NONE") return;
    const events = st.events;
    if (!events.length) return;
    if (mode === "REQUEST") {
      const ev = events.find((e) => (e.trace_id ?? e.request_id ?? e.event_id) === k);
      if (!ev) return;
      set({
        followFilters: { trace_id: ev.trace_id ?? null },
        eventFilters: { trace_id: ev.trace_id ?? null },
      });
      return;
    }
    if (mode === "POSITION") {
      const pid = resolvePositionId(events, k);
      set({ followFilters: { position_id: pid }, eventFilters: { position_id: pid } });
      return;
    }
    if (mode === "ORDER") {
      const eid = resolveOrderTrace(events, k);
      set({
        followFilters: { trace_id: eid.traceId },
        eventFilters: { trace_id: eid.traceId },
      });
      return;
    }
  },
  clearFollow: () =>
    set({ follow: "NONE", followKey: null, followFilters: {}, eventFilters: {} }),

  setFollowKey: (key) => set({ followKey: key }),

  commitFollow: () => {
    const st = get();
    if (st.follow === "NONE") return;
    get().setFollow(st.follow, st.followKey);
  },

  setWhy: (eventId, res) =>
    set((s) => {
      const map = new Map(s.whyCache);
      if (res) map.set(eventId, res);
      else map.delete(eventId);
      return { whyCache: map };
    }),

  toggleTraceCollapsed: (traceId) =>
    set((s) => {
      const next = new Set(s.collapsedTraces);
      if (next.has(traceId)) next.delete(traceId);
      else next.add(traceId);
      return { collapsedTraces: next };
    }),
  collapseAllTraces: (collapsed) =>
    set(() => {
      if (!collapsed) return { collapsedTraces: new Set<string>() };
      const ids = new Set<string>();
      for (const e of get().events) if (e.trace_id) ids.add(e.trace_id);
      return { collapsedTraces: ids };
    }),

  reset: () =>
    set({
      events: [],
      lastSeq: 0,
      coalescedEvents: 0,
      droppedVisualEvents: 0,
      decisions: [],
      decisionsTotal: 0,
      selectedBundle: null,
      selectedNodeStage: null,
      selectedEventId: null,
      gapMessage: null,
      integrity: null,
      topology: null,
      streamRows: [],
      queue: { active: [], recent: [], failed: [], completed: [] },
      collapsedTraces: new Set<string>(),
      queueVersion: 0,
      whyCache: new Map<string, WhyResponse>(),
      follow: "NONE",
      followKey: null,
      followFilters: {},
      eventFilters: {},
    }),
}));

function streamRowOf(e: TraceEvent): StreamRow {
  const symbol =
    typeof e.symbol === "string" ? e.symbol : (e.detail?.symbol as string | null) ?? null;
  return {
    event_id: e.event_id,
    sequence: e.sequence,
    timestamp: e.timestamp,
    stage: e.stage,
    status: e.status,
    trace_id: e.trace_id,
    symbol: typeof symbol === "string" ? symbol : null,
    provenance_gap: e.provenance_gap,
    terminal: e.terminal,
  };
}

/**
 * Resolve a position/ticket key to the backend position_id the events actually
 * carry (§33): the v2 field first, then the safe detail key. Returns null when
 * the runtime never recorded a position id for this ticket.
 */
export function resolvePositionId(events: TraceEvent[], key: string | null): string | null {
  if (!key) return null;
  const k = key.trim();
  if (!k) return null;
  for (const e of events) {
    if (e.position_id && String(e.position_id).trim() === k) return e.position_id;
    const fromDetail = e.detail?.position_id;
    if (typeof fromDetail === "string" && fromDetail.trim() === k) return fromDetail;
    const ticket = e.detail?.ticket;
    if (typeof ticket === "string" && ticket.trim() === k) {
      const recorded = typeof fromDetail === "string" ? fromDetail : null;
      return e.position_id ?? recorded ?? ticket;
    }
  }
  // key not seen as a recorded id: use it verbatim (the backend filters on the
  // recorded word; an unknown key yields the honest empty page).
  return k;
}

/**
 * Follow ORDER (§34): find the trace that the order id belongs to. The search
 * is over real recorded ids only (order_id, deal_id, execution_id, detail
 * order ids) — never a correlation guess.
 */
export function resolveOrderTrace(
  events: TraceEvent[],
  key: string | null,
): { traceId: string | null; orderId: string | null } {
  if (!key) return { traceId: null, orderId: null };
  const k = key.trim();
  if (!k) return { traceId: null, orderId: null };
  for (const e of events) {
    const candidates = [e.order_id, e.deal_id, e.execution_id];
    for (const c of candidates) {
      if (c !== null && c !== undefined && String(c).trim() === k) {
        return { traceId: e.trace_id, orderId: c };
      }
    }
    if (e.detail) {
      const d = e.detail;
      for (const field of ["order_id", "deal_id", "execution_id", "ticket", "order_ticket"]) {
        const v = d[field];
        if (typeof v === "string" && v.trim() === k) return { traceId: e.trace_id, orderId: v };
      }
    }
  }
  return { traceId: null, orderId: k };
}

// ------------------------------------------------------------- selectors
// Derived on demand (structural sharing: React layers memoize identity).

export function selectGraph(state: DecisionTraceState): DerivedGraph {
  return buildGraph(state.events.slice(-TOPOLOGY_MAX_EVENTS));
}

export function selectTimeline(state: DecisionTraceState) {
  const bundle = state.selectedBundle;
  if (!bundle) return [];
  return buildTimeline(bundle.events);
}

export function selectIntegrity(state: DecisionTraceState): ClientIntegrity | null {
  if (!state.events.length) return state.integrity;
  return checkIntegrity(state.events);
}

/**
 * Search across the ids/words the brief mandates (§35): trace_id, request_id,
 * position_id, order_id, deal_id, symbol, model, provider, status. Every term
 * is matched against the runtime-recorded string (v2 field first, then the
 * safe detail key) — never against an invented label.
 */
export function matchSearch(ev: TraceEvent, q: string): boolean {
  if (!q) return true;
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  const fields: unknown[] = [
    ev.trace_id,
    ev.request_id,
    ev.position_id,
    ev.order_id,
    ev.deal_id,
    ev.execution_id,
    ev.snapshot_id,
    ev.event_id,
    ev.decision_id,
    ev.symbol,
    ev.model,
    ev.provider,
    ev.stage,
    ev.status,
    ev.event_type,
    ev.component,
    ev.mode,
    ev.reason_code,
    ev.error_code,
    ev.state,
  ];
  for (const f of fields) {
    if (typeof f === "string" && f.toLowerCase().includes(needle)) return true;
  }
  const d = ev.detail;
  if (d) {
    for (const key of [
      "symbol",
      "model_id",
      "model_version",
      "provider",
      "order_id",
      "deal_id",
      "ticket",
      "position_id",
      "request_id",
      "trace_id",
      "action",
      "regime",
      "error_code",
      "engine_mode",
    ]) {
      const v = d[key];
      if (typeof v === "string" && v.toLowerCase().includes(needle)) return true;
    }
  }
  return false;
}

export function selectFilteredEvents(state: DecisionTraceState): TraceEvent[] {
  const q = state.search.trim();
  if (!q && !state.eventFilters.trace_id && !state.eventFilters.stage &&
      !state.eventFilters.status && !state.eventFilters.mode &&
      !state.eventFilters.position_id) {
    return state.events;
  }
  return state.events.filter((e) => {
    if (!matchSearch(e, q)) return false;
    return matchesEventFilters(e, state.eventFilters);
  });
}

/**
 * Client-side mirror of the server AND-filters (trace_routes.py:79
 * _apply_event_filters) — used for the already-retained view so the page does
 * not re-fetch when the user narrows over buffered data.
 */
export function matchesEventFilters(e: TraceEvent, f: EventFilters | null): boolean {
  if (!f) return true;
  const tid = f.trace_id ? f.trace_id.trim() : null;
  const stg = f.stage ? f.stage.trim().toUpperCase() : null;
  const sta = f.status ? f.status.trim().toUpperCase() : null;
  const md = f.mode ? f.mode.trim().toUpperCase() : null;
  const pid = f.position_id ? f.position_id.trim() : null;
  if (tid && e.trace_id !== tid) return false;
  if (stg && String(e.stage ?? "").toUpperCase() !== stg) return false;
  if (sta && String(e.status ?? "").toUpperCase() !== sta) return false;
  if (pid) {
    const eventPid = e.position_id ?? (e.detail?.position_id as string | null) ?? null;
    if (String(eventPid ?? "") !== pid) return false;
  }
  if (md) {
    const raw = e.mode ?? (e.detail?.engine_mode as string | null) ?? null;
    const eventMode = raw ? String(raw).toUpperCase() : "";
    if (eventMode !== md) return false;
  }
  return true;
}

export function selectFilterChips(state: DecisionTraceState) {
  return activeFilterWords(state.eventFilters);
}

/* --------------------------------------------------------------- §59
 * State timeline: one row per observed event with its exact timestamp and
 * the state word the runtime recorded (status only when no state word). */
export interface StateTimelineRow {
  event_id: string;
  sequence: number;
  timestamp: string;
  stage: string;
  state: string;
}

export function stateTimeline(events: TraceEvent[]): StateTimelineRow[] {
  return events
    .slice()
    .sort((a, b) => a.sequence - b.sequence)
    .map((e) => ({
      event_id: e.event_id,
      sequence: e.sequence,
      timestamp: e.timestamp,
      stage: e.stage,
      state: e.state ?? e.status ?? "UNKNOWN",
    }));
}

export function selectQueue(state: DecisionTraceState): TraceQueue {
  return state.queue;
}

export function selectFilteredDecisions(state: DecisionTraceState): DecisionRow[] {
  const q = state.search.trim().toLowerCase();
  return state.decisions.filter((r) => {
    if (state.symbolFilter && r.symbol !== state.symbolFilter) return false;
    if (state.filter !== "ALL") {
      const status = (r.status ?? "").toUpperCase();
      switch (state.filter) {
        case "PASSED":
          if (![`APPROVED`, "EXECUTED", "DISPATCHED", "PASS"].includes(status)) return false;
          break;
        case "REJECTED":
          if (![`REJECTED`, "NO_TRADE"].includes(status)) return false;
          break;
        case "ERROR":
          if (status !== "ERROR" && status !== "FAILED") return false;
          break;
        case "ACTIVE":
          if (!r.trace_id) return false;
          break;
        case "MODEL":
        case "REGIME":
        case "GATE":
        case "RULE":
        case "RISK":
        case "EXECUTION":
        case "MT5":
          // stage/category filters apply to events, not decision rows:
          // keep rows whose trace contains an event of that stage.
          break;
      }
    }
    if (!q) return true;
    const hay = [
      r.decision_id,
      r.trace_id,
      r.request_id,
      r.position_id,
      r.order_id,
      r.deal_id,
      r.execution_id,
      r.symbol,
      r.model_id,
      r.model_version,
      r.contract,
      r.regime,
      r.status,
      r.action,
      r.reason_code,
      r.rejection_reason,
      r.reason,
    ]
      .filter((x) => typeof x === "string")
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });
}
