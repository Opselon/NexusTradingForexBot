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
import type { ShellPageProps } from "@/app/featureModule";
import { ReplayControls } from "./ReplayControls";
import { TraceCompare } from "./TraceCompare";
import { TraceDecisions } from "./TraceDecisions";
import { TraceEventList } from "./TraceEventList";
import { TraceGraphCanvas } from "./TraceGraphCanvas";
import { TraceHeader } from "./TraceHeader";
import { TraceInspector } from "./TraceInspector";
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

type Panel = "canvas" | "events" | "decisions";

export default function DecisionTracePage({ snapshot }: ShellPageProps) {
  const [panel, setPanel] = useState<Panel>("canvas");
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
  useDecisionsQuery(100);
  useResumeHydration();
  const { status: streamStatus, error: streamError } = useTraceStream(live);

  const graph = useMemo(() => selectGraph(store), [store]);
  const timeline = useMemo(() => selectTimeline(store), [store]);
  const integrity = useMemo(() => selectIntegrity(store), [store]);
  const decisions = useFilteredDecisions();

  const bundleKey = selectedBundle?.decision_id ?? selectedBundle?.trace_id ?? null;
  const bundleQ = useBundleQuery(
    bundleKey,
    selectedBundleSource === "HISTORICAL" && !!bundleKey,
  );
  const activeBundle =
    selectedBundleSource === "HISTORICAL" && bundleQ.data ? bundleQ.data : selectedBundle;

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

  const integrityItems = useMemo(() => {
    if (!integrity || integrity.total === 0) return [];
    const items: string[] = [];
    if (integrity.duplicateEvents) items.push(`duplicate event ids: ${integrity.duplicateEvents}`);
    if (integrity.missingSequence) items.push(`sequence gaps: ${integrity.missingSequence}`);
    if (integrity.missingParent) items.push(`missing parent refs: ${integrity.missingParent}`);
    if (integrity.executionWithoutDecision) items.push(`execution without decision: ${integrity.executionWithoutDecision}`);
    if (integrity.mt5WithoutOrder) items.push(`MT5 without order: ${integrity.mt5WithoutOrder}`);
    return items;
  }, [integrity]);

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
              ? "OBSERVABILITY OFFLINE — stream failed; polling continues."
              : "Reconnecting to the observer stream..."}
            {streamError ? ` (${streamError})` : ""}
          </span>
        </div>
      )}
      {gapMessage ? (
        <div className="dt-banner warn" role="alert">
          <span className="dt-banner-glyph">~</span>
          <span>TRACE GAP — the observer ring evicted data older than the resume point; continuity is not asserted.</span>
        </div>
      ) : null}
      {integrityItems.length ? (
        <div className="dt-banner warn" role="status">
          <span className="dt-banner-glyph">i</span>
          <span>
            INTEGRITY ({integrityItems.length}): {integrityItems.slice(0, 4).join(" · ")}
            {integrityItems.length > 4 ? ` +${integrityItems.length - 4} more` : ""}
          </span>
        </div>
      ) : null}

      <div className="dt-body">
        <nav className="dt-panel-tabs" aria-label="View">
          <button className={panel === "canvas" ? "active" : ""} onClick={() => setPanel("canvas")}>
            Topology
          </button>
          <button className={panel === "events" ? "active" : ""} onClick={() => setPanel("events")}>
            Events <span className="dt-tab-count">{events.length}</span>
          </button>
          <button className={panel === "decisions" ? "active" : ""} onClick={() => setPanel("decisions")}>
            Decisions <span className="dt-tab-count">{decisionsTotal}</span>
          </button>
          <span className="dt-tab-note">
            topology {topoQ.data ? `${topoQ.data.nodes.length} nodes / ${topoQ.data.edges.length} edges` : "—"}
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
                    <span>Latency timeline — {activeBundle.trace_id ?? activeBundle.decision_id ?? "trace"}</span>
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
                  <TraceTimeline entries={timeline} onSelectStage={selectStage} selectedStage={selectedNodeStage} />
                </div>
              ) : null}
            </div>
          ) : panel === "events" ? (
            <TraceEventList events={events} selectedId={selectedEventId} onSelect={selectEvent} />
          ) : (
            <TraceDecisions
              rows={decisions}
              total={decisionsTotal}
              loading={observerQ.isPending}
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
            onClose={() => useDecisionTraceStore.getState().selectBundle(null, "LIVE")}
          />
          {selectedNodeStage ? (
            <NodeStagePanel
              stage={selectedNodeStage}
              count={graph.nodes.find((n) => n.stage === selectedNodeStage)?.count ?? 0}
              events={events.filter((e) => e.stage === selectedNodeStage)}
              onClose={() => selectStage(null)}
            />
          ) : null}
        </aside>
      </div>

      <footer className="dt-footer">
        <span>
          observer {observer?.status ?? "unknown"} · stream {streamStatus} · events {events.length} ·
          coalesced {coalescedEvents} · dropped {droppedVisualEvents} · malformed {malformed} ·
          last_seq {lastSeq}
        </span>
        <span className="dt-footer-note">Every figure is observed runtime data.</span>
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
  return (
    <section className="dt-stage-panel" aria-label={`Stage ${stage} evidence`}>
      <header className="dt-insp-sec-title">
        Stage evidence — {stage}{" "}
        <button className="dt-insp-close" onClick={onClose} aria-label="Close">x</button>
      </header>
      <div className="dt-insp-sec-body">
        <div className="dt-stage-meta">{count} observed event(s) across all traces</div>
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
