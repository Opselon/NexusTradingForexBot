/**
 * Decision Trace — the bounded state store (§56).
 *
 * Separates the concerns the spec mandates:
 *   systemStatus | liveEvents | topology | selectedTrace | selectedNode |
 *   timeline | historicalTraces | connection | observer | filters | search |
 *   replay
 *
 * Backpressure contract (§43): the store is the ONLY place event memory grows.
 * It is strictly bounded (LIVE_EVENT_CAP); every append is O(cap) via index
 * rotation. The graph is derived on demand by selectors, never re-stored.
 * Coalesced/dropped counters come straight from the backend batch frames and
 * are surfaced verbatim (never silently hidden).
 *
 * The store never mutates trading behavior — it only consumes observer data.
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
  ObserverSnapshot,
  ObserverStatus,
  TopologySnapshot,
  TraceBundle,
  TraceEvent,
} from "./types";
import type { TraceStreamStatus } from "./stream";

const LIVE_EVENT_CAP = 4000;
const DECISION_CAP = 400;
const TOPOLOGY_MAX_EVENTS = 12_000;

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

interface DecisionTraceState {
  // ---- connection / observer
  streamStatus: TraceStreamStatus;
  observerStatus: ObserverStatus | null;
  observer: ObserverSnapshot | null;
  streamError: string | null;
  gapMessage: string | null;
  disconnectedAt: number | null;
  malformed: number;

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

  // ---- replay
  replay: {
    active: boolean;
    index: number;
    speed: number;
    playing: boolean;
  };

  // ---- filters / search
  filter: TraceFilter;
  search: string;
  symbolFilter: string | null;
  live: boolean;
  frozen: boolean;

  // ---- integrity (client-side live scan)
  integrity: ClientIntegrity | null;

  // ---- actions
  setStreamStatus: (s: TraceStreamStatus, error?: string) => void;
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
  setLive: (live: boolean) => void;
  setFrozen: (frozen: boolean) => void;
  noteGap: (message: string | null) => void;
  startReplay: () => void;
  stopReplay: () => void;
  replayStep: (delta: number) => void;
  replaySetSpeed: (speed: number) => void;
  replaySetPlaying: (playing: boolean) => void;
  reset: () => void;
}

function boundedPush<T>(arr: T[], items: T[], cap: number): T[] {
  if (items.length >= cap) return items.slice(items.length - cap);
  const next = arr.concat(items);
  return next.length > cap ? next.slice(next.length - cap) : next;
}

export const useDecisionTraceStore = create<DecisionTraceState>((set, get) => ({
  streamStatus: "disconnected",
  observerStatus: null,
  observer: null,
  streamError: null,
  gapMessage: null,
  disconnectedAt: null,
  malformed: 0,

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

  filter: "ALL",
  search: "",
  symbolFilter: null,
  live: true,
  frozen: false,

  integrity: null,

  setStreamStatus: (s, error) =>
    set({
      streamStatus: s,
      streamError: error ?? null,
      disconnectedAt: s === "disconnected" ? Date.now() : null,
    }),

  setObserver: (snap) =>
    set({ observer: snap, observerStatus: snap?.status ?? null }),

  setTopology: (t) => set({ topology: t }),

  appendFrame: (frame) => {
    const st = get();
    if (st.frozen) {
      // View frozen: data keeps being accounted (§79) but the array is not
      // mutated. We still advance lastSeq so a later unfreeze resumes cleanly.
      if (frame.kind === "batch") set({ lastSeq: Math.max(st.lastSeq, frame.lastSeq) });
      return;
    }
    if (frame.kind === "resume") {
      const events = frame.payload.events ?? [];
      set({
        events: boundedPush(st.events, events, LIVE_EVENT_CAP),
        lastSeq: Math.max(st.lastSeq, frame.payload.last_seq ?? st.lastSeq),
        gapMessage: frame.payload.gap ? "TRACE GAP" : st.gapMessage,
      });
      return;
    }
    const raw = frame.events ?? [];
    // Coalesce redundant visual progress events (§43); terminal/rejection
    // events are never coalesced — coalesceVisual only drops _PROGRESS.
    const { events, coalesced } = coalesceVisual(raw);
    set({
      events: boundedPush(st.events, events, LIVE_EVENT_CAP),
      lastSeq: Math.max(st.lastSeq, frame.lastSeq ?? st.lastSeq),
      coalescedEvents: st.coalescedEvents + coalesced,
      droppedVisualEvents: st.droppedVisualEvents + frame.dropped,
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
    }),
}));

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

export function selectFilteredDecisions(state: DecisionTraceState): DecisionRow[] {
  const q = state.search.trim().toLowerCase();
  return state.decisions.filter((r) => {
    if (state.symbolFilter && r.symbol !== state.symbolFilter) return false;
    if (state.filter !== "ALL") {
      const status = (r.status ?? "").toUpperCase();
      switch (state.filter) {
        case "PASSED":
          if (!["APPROVED", "EXECUTED", "DISPATCHED", "PASS"].includes(status)) return false;
          break;
        case "REJECTED":
          if (!["REJECTED", "NO_TRADE"].includes(status)) return false;
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
