/**
 * Decision Trace — inspector shell (§14-§19, §29-§49, §74 desktop layout).
 *
 * Assembles the lane-D panels into the three-column desktop layout: the graph
 * lane (lane C's canvas) stays beside the inspector and the timeline so the
 * inspector never covers the active path. Compact/full mode collapses long
 * field lists. Every panel owns its own empty/partial state (§73).
 *
 * OWNER: lane D, LIVE-CAUSAL-TOPOLOGY wave.
 */

import { memo, useCallback, useMemo } from "react";
import { DecisionForensics, MetadataPanel } from "./DecisionForensics";
import {
  ErrorCascadePanel,
  FreshnessPanel,
  LatencyWaterfallPanel,
} from "./ForensicsPanels";
import { DeltaList, DeltaView } from "./DeltaView";
import {
  CounterButtons,
  FilterChips,
  LiveStreamPanel,
  ProvenanceLegend,
  ScrubberBar,
  StateTimelinePanel,
  StreamLifecycleBar,
  TraceQueuePanel,
  useViewClockBindings,
} from "./TracePanels";
import { WhyInspector, WhyPayloadBlock } from "./WhyInspector";
import { activeFilterWords } from "../types";
import type { TraceEvent } from "../types";
import { selectFilteredEvents, useDecisionTraceStore } from "../store";
import { useWhyQuery } from "../useCases";
import type { WhyViewState } from "../forensics";

const NOT_AVAILABLE = "NOT AVAILABLE";
/** Shared empty array so a missing bundle does not allocate per render. */
const NO_EVENTS: TraceEvent[] = [];
/**
 * The WHY view state is derived strictly from the query's own states
 * (§37): EMPTY before selection, PENDING while loading, NOT_FOUND on the
 * endpoint's 404, ERROR on a transport failure, READY otherwise.
 */
function useWhyViewState(): {
  ctx: WhyViewState;
  res: WhyViewState extends { res: infer R } ? R : null;
} {
  const selectedEventId = useDecisionTraceStore((s) => s.selectedEventId);
  const q = useWhyQuery(selectedEventId);
  if (!selectedEventId) return { ctx: { kind: "EMPTY" }, res: null };
  if (q.isPending) return { ctx: { kind: "PENDING" }, res: null };
  if (q.isError) return { ctx: { kind: "ERROR", message: String(q.error?.message ?? "query failed") }, res: null };
  if (q.data && q.data.found === false) {
    return { ctx: { kind: "NOT_FOUND", eventId: selectedEventId }, res: null };
  }
  return q.data
    ? { ctx: { kind: "READY", res: q.data }, res: q.data as never }
    : { ctx: { kind: "EMPTY" }, res: null };
}

export const TraceInspectors = memo(function TraceInspectors({
  compact = true,
  onToggleCompact,
}: {
  compact?: boolean;
  onToggleCompact?: () => void;
}) {
  const store = useDecisionTraceStore();
  const selectedBundle = store.selectedBundle;
  const events = selectedBundle?.events ?? NO_EVENTS;
  const { ctx, res } = useWhyViewState();
  const selectedEventId = store.selectedEventId;

  const selectedEvent = useMemo(
    () => events.find((e) => e.event_id === selectedEventId) ?? null,
    [events, selectedEventId],
  );
  const selectedEventIndex = useMemo(
    () => events.findIndex((e) => e.event_id === selectedEventId),
    [events, selectedEventId],
  );
  const prevEvent = selectedEventIndex > 0 ? events[selectedEventIndex - 1] ?? null : null;

  const selectEvent = useCallback((id: string | null) => {
    useDecisionTraceStore.getState().selectEvent(id);
  }, []);
  const applyCounter = useCallback((key: string, value: string) => {
    // §57: counters apply the matching events filter (stage/status words).
    const known = ["stage", "status", "mode", "position_id", "trace_id"];
    const k = known.includes(key) ? key : "stage";
    useDecisionTraceStore.getState().setEventFilters({ ...useDecisionTraceStore.getState().eventFilters, [k]: value });
  }, []);
  const removeFilter = useCallback((key: string) => {
    const next = { ...useDecisionTraceStore.getState().eventFilters };
    delete next[key as keyof typeof next];
    useDecisionTraceStore.getState().setEventFilters(next);
  }, []);
  const onSelectStreamRow = useCallback(
    (row: { event_id: string; trace_id: string | null; stage: string }) => {
      selectEvent(row.event_id);
      useDecisionTraceStore.getState().setSearch(row.trace_id ?? row.event_id);
    },
    [selectEvent],
  );
  /** Stable identities: the queue/stream panels are memoized and receive these. */
  const selectTrace = useCallback(
    (traceId: string) => {
      useDecisionTraceStore.getState().setEventFilters({ trace_id: traceId });
      selectEvent(null);
    },
    [selectEvent],
  );
  const toggleTraceCollapsed = useCallback((traceId: string) => {
    useDecisionTraceStore.getState().toggleTraceCollapsed(traceId);
  }, []);
  const liveRows = useMemo(
    () => store.streamRows.slice(-120),
    [store.streamRows],
  );
  const gapCount = useMemo(
    () => store.events.filter((e) => e.provenance_gap).length + liveRows.filter((r) => r.provenance_gap).length,
    [store.events, liveRows],
  );
  const clocks = useViewClockBindings();
  const counters = store.topology?.counters ?? store.observer?.counters ?? null;
  /** Rebuilt only when the filter set changes, not on every parent render. */
  const chips = useMemo(() => activeFilterWords(store.eventFilters), [store.eventFilters]);
  /** selectFilteredEvents allocates a new array — memoized so the state
   *  timeline keeps referential stability across unrelated re-renders. */
  const stateTimelineEvents = useMemo(() => selectFilteredEvents(store), [store]);

  return (
    <div className="dti-inspector dti-inspector-col" role="region" aria-label="Decision trace inspectors">
      <div className="dti-bar">
        <StreamLifecycleBar
          status={store.streamStatus}
          resyncing={store.resyncing}
          gapMessage={store.gapMessage}
          lastSeq={store.lastSeq}
        />
        <span className="spacer" />
        <button
          type="button"
          className={`dti-toggle ${compact ? "active" : ""}`}
          onClick={onToggleCompact}
        >
          {compact ? "COMPACT" : "FULL"}
        </button>
      </div>

      {!selectedBundle ? (
        <section className="dti-section" aria-label="No trace selected">
          <div className="dti-section-head">INSPECTOR</div>
          <div className="dti-row-empty" role="status">
            NO TRACE SELECTED {NOT_AVAILABLE} — pick a trace from the queue, the
            stream, or a decision row to open its causal forensics.
          </div>
          <div style={{ marginBlockStart: 8 }}>
            <FilterChips filters={chips} onRemove={removeFilter} />
            <CounterButtons
              counters={counters}
              active={null}
              onApply={applyCounter}
            />
          </div>
        </section>
      ) : (
        <>
          <ScrubberBar
            position={clocks.position}
            total={clocks.total}
            speed={clocks.speed}
            paused={clocks.paused}
            onSpeed={clocks.onSpeed}
            onTogglePause={clocks.onTogglePause}
            onStep={clocks.onStep}
            onSeek={clocks.onSeek}
          />
          <WhyInspector res={res} ctx={ctx} onSelectEvent={selectEvent} />
          <WhyPayloadBlock res={res} compact={compact} />
          <DecisionForensics
            row={selectedBundle.summary ?? null}
            events={events}
            compact={compact}
          />
          <DeltaView prev={prevEvent} cur={selectedEvent} />
          <DeltaList events={events} />
          <LatencyWaterfallPanel
            events={events}
            stats={null}
            compact={compact}
          />
          <FreshnessPanel events={events} />
          <ErrorCascadePanel events={events} />
          <MetadataPanel events={events} />
        </>
      )}

      <TraceQueuePanel
        queue={store.queue}
        selectedTraceId={selectedBundle?.trace_id ?? null}
        onSelectTrace={selectTrace}
      />
      <LiveStreamPanel
        rows={liveRows}
        selectedTraceId={selectedBundle?.trace_id ?? null}
        selectedEventId={selectedEventId}
        onSelect={onSelectStreamRow}
        collapsedTraces={store.collapsedTraces}
        onToggleCollapse={toggleTraceCollapsed}
      />
      <StateTimelinePanel events={stateTimelineEvents} onSelectEvent={selectEvent} />
      <ProvenanceLegend gapCount={gapCount} />
    </div>
  );
});

export default TraceInspectors;
