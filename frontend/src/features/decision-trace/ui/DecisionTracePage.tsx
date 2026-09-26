/**
 * DecisionTracePage — the observability window (§01-§80).
 *
 * Composition only. Owns: page lifecycle (mount => observer start + SSE
 * open), panel tabs, node focus, replay wiring. Data derivation lives in
 * traceGraph (pure), fetching in useCases. Never hardcodes a trading
 * topology; unknowns render UNKNOWN / NOT OBSERVED / PROVENANCE GAP.
 *
 * Styling: ./decision-trace.css (namespaced `dt-`).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { errorRequestId, errorText } from "@/pages/_shared/SectionState";
import type { ShellPageProps } from "@/app/featureModule";
import { ReplayControls } from "./ReplayControls";
import { TraceCompare } from "./TraceCompare";
import { TraceDecisions } from "./TraceDecisions";
import { TraceEventList } from "./TraceEventList";
import { TraceGraphCanvas } from "./TraceGraphCanvas";
import { TraceHeader } from "./TraceHeader";
import { TraceInspector } from "./TraceInspector";
import { TraceInspectors } from "./TraceInspectors";
import { TraceTimeline } from "./TraceTimeline";
import { TraceToolbar } from "./TraceToolbar";
import {
  selectGraph,
  selectIntegrity,
  selectTimeline,
  useDecisionTraceStore,
} from "../store";
import {
  useBundleQuery,
  useDecisionsQuery,
  useFilteredDecisions,
  useIntegrityQuery,
  useObserverQuery,
  useResumeHydration,
  useTopologyQuery,
  useTraceStream,
} from "../useCases";

import "../decision-trace.css";
import "../decision-trace-hero.css";
import "../decision-trace-lists.css";
import "../decision-trace-side.css";
import "../decision-trace-graph.css";
import "../traceInspectors.css";

type Panel = "canvas" | "events" | "decisions";

type PageT = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** Observer status token → display label (token stays raw in store/comparisons). */
function observerLabel(t: PageT, status: string | null | undefined): string {
  switch (status) {
    case "off": return t("trace.observer.off", "OFF");
    case "starting": return t("trace.observer.starting", "STARTING");
    case "active": return t("trace.observer.active", "ACTIVE");
    case "stopping": return t("trace.observer.stopping", "STOPPING");
    case "error": return t("trace.observer.error", "ERROR");
    case "unknown": return t("trace.status.unknown", "UNKNOWN");
    default: return status ?? t("trace.status.unknown", "UNKNOWN");
  }
}

/** TraceStreamStatus token → display label (token stays raw in logic). */
function streamStatusLabel(t: PageT, status: string): string {
  switch (status) {
    case "connecting": return t("trace.stream.connecting", "CONNECTING");
    case "connected": return t("trace.stream.connected", "CONNECTED");
    case "reconnecting": return t("trace.stream.reconnecting", "RECONNECTING");
    case "disconnected": return t("trace.stream.disconnected", "DISCONNECTED");
    case "failed": return t("trace.stream.failed", "FAILED");
    default: return status;
  }
}

export default function DecisionTracePage({ snapshot }: ShellPageProps) {
  const t = useI18n((s) => s.t);
  const [panel, setPanel] = useState<Panel>("canvas");
  const [inspectorCompact, setInspectorCompact] = useState(true);
  const [comparePair, setComparePair] = useState<
    [import("../types").DecisionRow, import("../types").DecisionRow] | null
  >(null);

  const store = useDecisionTraceStore();
  const {
    filter,
    search,
    selectedEventId,
    selectedNodeStage,
    selectedBundle,
    selectedBundleSource,
    replay,
    live,
    events,
    lastSeq,
    coalescedEvents,
    droppedVisualEvents,
    malformed,
    decisionsTotal,
    gapMessage,
    observer,
  } = store;

  const observerQ = useObserverQuery(!live);
  const topoQ = useTopologyQuery(!live);
  useIntegrityQuery(!live);
  const decisionsQ = useDecisionsQuery(100);
  useResumeHydration();
  const { status: streamStatus, error: streamError } = useTraceStream(live);

  const graph = useMemo(() => selectGraph(store), [store]);
  const timeline = useMemo(() => selectTimeline(store), [store]);
  const integrity = useMemo(() => selectIntegrity(store), [store]);
  const decisions = useFilteredDecisions();

  const bundleKey = selectedBundle?.decision_id ?? selectedBundle?.trace_id ?? null;
  const bundleEnabled = selectedBundleSource === "HISTORICAL" && !!bundleKey;
  const bundleQ = useBundleQuery(bundleKey, bundleEnabled);
  const activeBundle =
    selectedBundleSource === "HISTORICAL" && bundleQ.data ? bundleQ.data : selectedBundle;

  /**
   * Presentation-only state signals (CONTRACT §9): each keys off a query or
   * stream state that already exists — no fetch, refetch or interval is
   * touched. An error is only surfaced when the payload never arrived, so a
   * stale-but-real value keeps rendering (SectionState order).
   */
  const eventsPending = events.length === 0 && streamStatus === "connecting";
  const eventsError =
    events.length === 0 && streamStatus === "failed"
      ? (streamError ??
        t("trace.banner.offline", "OBSERVABILITY OFFLINE — stream failed; polling continues."))
      : null;
  const endpointFallback = t("shell.section.error_fallback", "Endpoint unavailable.");
  const decisionsError =
    decisionsQ.isError && decisionsQ.data === undefined
      ? errorText(decisionsQ.error, endpointFallback)
      : null;
  const bundlePending = bundleEnabled && bundleQ.isPending;
  const bundleError =
    bundleEnabled && bundleQ.isError && bundleQ.data === undefined
      ? errorText(bundleQ.error, endpointFallback)
      : null;

  const replayEvent =
    replay.active && activeBundle ? (activeBundle.events[replay.index] ?? null) : null;

  const selectStage = useCallback((stage: string | null) => {
    useDecisionTraceStore.getState().selectNode(stage);
  }, []);

  const selectEvent = useCallback((id: string | null) => {
    const st = useDecisionTraceStore.getState();
    st.selectEvent(id);
    const ev = st.events.find((e) => e.event_id === id);
    if (ev) {
      st.selectBundle(
        {
          query: ev.trace_id ?? ev.event_id,
          trace_id: ev.trace_id,
          decision_id: ev.decision_id,
          events: st.events.filter((e) => e.trace_id === ev.trace_id),
          summary: null,
          found: true,
        },
        "LIVE",
      );
    }
  }, []);

  const openRow = useCallback((row: import("../types").DecisionRow) => {
    const st = useDecisionTraceStore.getState();
    if (row.trace_id) {
      st.selectBundle(
        {
          query: row.decision_id ?? row.trace_id,
          trace_id: row.trace_id,
          decision_id: row.decision_id,
          events: st.events.filter((e) => e.trace_id === row.trace_id),
          summary: row,
          found: true,
        },
        "LIVE",
      );
    } else {
      st.selectBundle(
        { query: row.decision_id ?? "", trace_id: null, decision_id: row.decision_id, events: [], summary: row, found: false },
        "HISTORICAL",
      );
    }
  }, []);

  /** Stage events for the selected-node panel — one filter per store change,
   *  not per page render (the page re-renders on every SSE batch). */
  const stageEvents = useMemo(
    () => (selectedNodeStage ? events.filter((e) => e.stage === selectedNodeStage) : []),
    [events, selectedNodeStage],
  );
  const closeBundle = useCallback(() => {
    useDecisionTraceStore.getState().selectBundle(null, "LIVE");
  }, []);

  const integrityItems = useMemo(() => {
    if (!integrity || integrity.total === 0) return [];
    const items: string[] = [];
    if (integrity.duplicateEvents) items.push(t("trace.integrity.duplicate", "duplicate event ids: {n}", { n: integrity.duplicateEvents }));
    if (integrity.missingSequence) items.push(t("trace.integrity.seq", "sequence gaps: {n}", { n: integrity.missingSequence }));
    if (integrity.missingParent) items.push(t("trace.integrity.parent", "missing parent refs: {n}", { n: integrity.missingParent }));
    if (integrity.executionWithoutDecision) items.push(t("trace.integrity.exec_no_dec", "execution without decision: {n}", { n: integrity.executionWithoutDecision }));
    if (integrity.mt5WithoutOrder) items.push(t("trace.integrity.mt5_no_order", "MT5 without order: {n}", { n: integrity.mt5WithoutOrder }));
    return items;
  }, [integrity, t]);

  const toggleLive = useCallback(() => {
    const next = !useDecisionTraceStore.getState().live;
    useDecisionTraceStore.getState().setLive(next);
    if (next) useDecisionTraceStore.getState().setFrozen(false);
  }, []);

  useEffect(
    () => () => {
      useDecisionTraceStore.getState().stopReplay();
    },
    [],
  );

  return (
    <div className="dt-page">
      <TraceHeader
        snapshot={snapshot}
        schemaVersion={observerQ.data?.trace_schema_version}
        observerStatus={observer?.status ?? "unknown"}
      />

      <TraceToolbar paused={!live} onTogglePause={toggleLive} />

      {(streamStatus === "reconnecting" || streamStatus === "failed" || streamError) && (
        <div className={`dt-banner ${streamStatus === "failed" ? "fail" : "warn"}`} role="alert">
          <span className="dt-banner-glyph">!</span>
          <span>
            {streamStatus === "failed"
              ? t("trace.banner.offline", "OBSERVABILITY OFFLINE — stream failed; polling continues.")
              : t("trace.banner.reconnecting", "Reconnecting to the observer stream…")}
            {streamError ? t("trace.banner.cause", " ({cause})", { cause: streamError }) : ""}
          </span>
        </div>
      )}
      {gapMessage ? (
        <div className="dt-banner warn" role="alert">
          <span className="dt-banner-glyph">~</span>
          <span>{t("trace.banner.gap", "TRACE GAP — the observer ring evicted data older than the resume point; continuity is not asserted.")}</span>
        </div>
      ) : null}
      {integrityItems.length ? (
        <div className="dt-banner warn" role="status">
          <span className="dt-banner-glyph">i</span>
          <span>
            {t("trace.banner.integrity", "INTEGRITY ({n}):", { n: integrityItems.length })} {integrityItems.slice(0, 4).join(" · ")}
            {integrityItems.length > 4 ? t("trace.banner.more", " +{n} more", { n: integrityItems.length - 4 }) : ""}
          </span>
        </div>
      ) : null}

      <div className="dt-body">
        <nav className="dt-panel-tabs" aria-label={t("trace.tab.aria", "View")}>
          <button className={panel === "canvas" ? "active" : ""} onClick={() => setPanel("canvas")}>
            {t("trace.tab.topology", "Topology")}
          </button>
          <button className={panel === "events" ? "active" : ""} onClick={() => setPanel("events")}>
            {t("trace.tab.events", "Events")} <span className="dt-tab-count">{events.length}</span>
          </button>
          <button className={panel === "decisions" ? "active" : ""} onClick={() => setPanel("decisions")}>
            {t("trace.tab.decisions", "Decisions")} <span className="dt-tab-count">{decisionsTotal}</span>
          </button>
          <span className="dt-tab-note">
            {topoQ.data
              ? t("trace.tab.topo_note", "topology {nodes} nodes / {edges} edges", { nodes: topoQ.data.nodes.length, edges: topoQ.data.edges.length })
              : t("trace.tab.topo_pending", "topology —")}
          </span>
        </nav>

        <main className="dt-main">
          {panel === "canvas" ? (
            <div className="dt-canvas-wrap">
              <TraceGraphCanvas
                graph={graph}
                selectedTraceId={activeBundle?.trace_id ?? null}
                activeEvents={activeBundle && activeBundle.events.length ? activeBundle.events : null}
                onSelectStage={selectStage}
                selectedStage={selectedNodeStage}
              />
              {activeBundle ? (
                <div className="dt-timeline-wrap">
                  <div className="dt-timeline-head">
                    <span>{t("trace.timeline.head", "Latency timeline — {id}", { id: activeBundle.trace_id ?? activeBundle.decision_id ?? t("trace.timeline.fallback_id", "trace") })}</span>
                    <ReplayControls
                      active={replay.active}
                      index={replay.index}
                      total={activeBundle.events.length}
                      speed={replay.speed}
                      playing={replay.playing}
                      onStart={store.startReplay}
                      onStop={store.stopReplay}
                      onStep={store.replayStep}
                      onSpeed={store.replaySetSpeed}
                      onPlaying={store.replaySetPlaying}
                    />
                  </div>
                  <TraceTimeline
                    entries={timeline}
                    onSelectStage={selectStage}
                    selectedStage={selectedNodeStage}
                    pending={bundlePending}
                    error={bundleError}
                  />
                </div>
              ) : null}
            </div>
          ) : panel === "events" ? (
            <TraceEventList
              events={events}
              selectedId={selectedEventId}
              onSelect={selectEvent}
              pending={eventsPending}
              error={eventsError}
            />
          ) : (
            <TraceDecisions
              rows={decisions}
              total={decisionsTotal}
              loading={observerQ.isPending || decisionsQ.isPending}
              error={decisionsError}
              requestId={decisionsError ? errorRequestId(decisionsQ.error) : null}
              filter={filter}
              search={search}
              onFilter={store.setFilter}
              onSearch={store.setSearch}
              onSelect={openRow}
              selectedId={activeBundle?.decision_id ?? null}
              onComparePair={setComparePair}
            />
          )}
        </main>

        <aside className="dt-side">
          {comparePair ? (
            <TraceCompare a={comparePair[0]} b={comparePair[1]} onClose={() => setComparePair(null)} />
          ) : null}
          <TraceInspector
            bundle={activeBundle}
            source={replay.active ? "REPLAY" : selectedBundleSource}
            replayEvent={replayEvent}
            onClose={closeBundle}
          />
          {/* §74 desktop layout: lane-D forensics column sits beside the graph
           * (never over it), compact by default. */}
          <TraceInspectors compact={inspectorCompact} onToggleCompact={() => setInspectorCompact((v) => !v)} />
          {selectedNodeStage ? (
            <NodeStagePanel
              stage={selectedNodeStage}
              count={graph.nodes.find((n) => n.stage === selectedNodeStage)?.count ?? 0}
              events={stageEvents}
              onClose={() => selectStage(null)}
            />
          ) : null}
        </aside>
      </div>

      <footer className="dt-footer">
        <span>
          {t("trace.footer", "observer {observer} · stream {stream} · events {events} · coalesced {coalesced} · dropped {dropped} · malformed {malformed} · last_seq {lastSeq}", {
            observer: observerLabel(t, observer?.status),
            stream: streamStatusLabel(t, streamStatus),
            events: events.length,
            coalesced: coalescedEvents,
            dropped: droppedVisualEvents,
            malformed,
            lastSeq,
          })}
        </span>
        <span className="dt-footer-note">{t("trace.footer_note", "Every figure is observed runtime data.")}</span>
      </footer>
    </div>
  );
}

function NodeStagePanel({
  stage,
  count,
  events,
  onClose,
}: {
  stage: string;
  count: number;
  events: Array<{ event_id: string; sequence: number; timestamp: string }>;
  onClose: () => void;
}) {
  const t = useI18n((s) => s.t);
  return (
    <section className="dt-stage-panel" aria-label={t("trace.stage.aria", "Stage {stage} evidence", { stage })}>
      <header className="dt-insp-sec-title">
        {t("trace.stage.title", "Stage evidence — {stage}", { stage })}{" "}
        <button className="dt-insp-close" onClick={onClose} aria-label={t("trace.stage.close", "Close")}>x</button>
      </header>
      <div className="dt-insp-sec-body">
        <div className="dt-stage-meta">{t("trace.stage.meta", "{count} observed event(s) across all traces", { count })}</div>
        <ul className="dt-stage-list">
          {events.slice(-12).map((e) => (
            <li key={e.event_id}>
              <code>#{e.sequence}</code> <span>{e.timestamp}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
