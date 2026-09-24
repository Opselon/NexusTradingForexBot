/**
 * Decision Trace — trace queue buckets + compact live stream + state timeline
 * + replay/scrubber controls + follow modes (§29-§38, §55-§64).
 *
 * Observer-only: the scrubber/speed controls change the VIEW cadence only —
 * the runtime's timing is never touched. Counters are clickable and apply the
 * matching /events filter (§57). The stream rail is bounded and clicking a row
 * focuses the topology via onSelectStage / selectedTraceId (§58).
 *
 * OWNER: lane D, LIVE-CAUSAL-TOPOLOGY wave.
 */

import { memo, useCallback, type ReactNode } from "react";
import { NOT_OBSERVED, UNKNOWN, streamStateWords } from "../forensics";
import {
  groupTraces,
  stateTimeline,
  useDecisionTraceStore,
  type FollowMode,
  type ReplaySpeed,
  type StreamRow,
  type TraceQueue,
} from "../store";
import type { TraceEvent } from "../types";

/* --------------------------------------------------------------- §29/§30
 * Time scrubber + speed controls (LIVE / 0.5x / 1x / 2x / 5x / STEP / PAUSE).
 * UI OBSERVER ONLY: `frozen` gates the store's event arrays and `viewClock`
 * is a visualization cadence — no runtime timing is ever modified. */
const SPEEDS: ReplaySpeed[] = ["LIVE", 0.5, 1, 2, 5];

export const ScrubberBar = memo(function ScrubberBar({
  position,
  total,
  speed,
  paused,
  onSpeed,
  onTogglePause,
  onStep,
  onSeek,
}: {
  position: number;
  total: number;
  speed: ReplaySpeed;
  paused: boolean;
  onSpeed: (s: ReplaySpeed) => void;
  onTogglePause: () => void;
  onStep: (delta: number) => void;
  onSeek: (index: number) => void;
}) {
  const max = Math.max(0, total - 1);
  return (
    <section className="dti-section" aria-label="Time scrubber">
      <div className="dti-section-head">
        VIEW CLOCK
        <span className="dti-count">
          {total ? `${Math.min(position + 1, total)}/${total}` : "0 events"}
        </span>
      </div>
      <div className="dti-scrubber">
        <input
          type="range"
          min={0}
          max={max}
          value={Math.min(position, max)}
          disabled={max < 1}
          aria-label="Trace position"
          onChange={(e) => onSeek(Number(e.target.value))}
        />
        <div className="dti-speeds" role="group" aria-label="Speed">
          {SPEEDS.map((s) => (
            <button
              key={String(s)}
              type="button"
              className={`dti-speed ${speed === s ? "active" : ""}`}
              onClick={() => onSpeed(s)}
            >
              {String(s)}
            </button>
          ))}
        </div>
      </div>
      <div className="dti-bar" style={{ marginBlockStart: 6 }}>
        <button
          type="button"
          className={`dti-toggle ${paused ? "active" : ""}`}
          onClick={onTogglePause}
        >
          {paused ? "PAUSED" : "PAUSE"}
        </button>
        <button type="button" className="dti-toggle" onClick={() => onStep(-1)}>
          STEP −
        </button>
        <button type="button" className="dti-toggle" onClick={() => onStep(1)}>
          STEP +
        </button>
        <span className="spacer" />
        <span className="dti-note">
          {paused
            ? "View frozen (the observer keeps recording); last_seq still advances."
            : speed === "LIVE"
              ? "LIVE — the view follows the stream as it lands."
              : `${String(speed)}× visualization cadence (observer-only).`}
        </span>
      </div>
    </section>
  );
});

/** §55/§56 — PROVENANCE GAP wording is rendered for gap edges (render links,
 *  never new graph logic; the graph stays lane C's). */
export const ProvenanceLegend = memo(function ProvenanceLegend({
  gapCount,
}: {
  gapCount: number;
}) {
  return (
    <div className="dti-bar">
      <span className="dti-word tone-ok">observed</span>
      <span className="dti-word tone-warn">inferred</span>
      <span className="dti-word gap">PROVENANCE GAP</span>
      {gapCount ? (
        <span className="dti-note">
          {gapCount} edge(s) carry no observed runtime linkage — dashed and labelled.
        </span>
      ) : null}
    </div>
  );
});

/* --------------------------------------------------------------- §31/§64
 * Trace queue buckets (active / recent / failed / completed) with bounded
 * buffers. A bucket with no traces renders its explicit empty state (§73). */
function BucketColumn({
  title,
  rows,
  selectedTraceId,
  onSelect,
}: {
  title: string;
  rows: TraceQueue["active"];
  selectedTraceId: string | null;
  onSelect: (traceId: string) => void;
}) {
  return (
    <div className="dti-bucket">
      <div className="dti-bucket-head">
        {title}
        <span className="n">{rows.length}</span>
      </div>
      {rows.length ? (
        rows.map((b) => (
          <button
            type="button"
            className={`dti-bucket-row ${selectedTraceId === b.trace_id ? "active" : ""}`}
            key={b.trace_id}
            onClick={() => onSelect(b.trace_id)}
            title={`${b.trace_id} · ${b.stage_count} stage(s) · ${b.last_status ?? UNKNOWN}`}
          >
            <span className="tid">{b.trace_id}</span>
            <span className="st">{b.last_status ?? UNKNOWN}</span>
          </button>
        ))
      ) : (
        <div className="dti-row-empty">NONE OBSERVED</div>
      )}
    </div>
  );
}

export const TraceQueuePanel = memo(function TraceQueuePanel({
  queue,
  selectedTraceId,
  onSelectTrace,
}: {
  queue: TraceQueue;
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string) => void;
}) {
  return (
    <section className="dti-section" aria-label="Trace queue">
      <div className="dti-section-head">TRACE QUEUE</div>
      <div className="dti-queue">
        <BucketColumn
          title="active"
          rows={queue.active}
          selectedTraceId={selectedTraceId}
          onSelect={onSelectTrace}
        />
        <BucketColumn
          title="recent"
          rows={queue.recent}
          selectedTraceId={selectedTraceId}
          onSelect={onSelectTrace}
        />
        <BucketColumn
          title="failed"
          rows={queue.failed}
          selectedTraceId={selectedTraceId}
          onSelect={onSelectTrace}
        />
        <BucketColumn
          title="completed"
          rows={queue.completed}
          selectedTraceId={selectedTraceId}
          onSelect={onSelectTrace}
        />
      </div>
    </section>
  );
});

/* --------------------------------------------------------------- §58
 * Compact live event stream: bounded, sliced; clicking focuses the topology
 * via onSelectStage + selectedTraceId (never re-graphs). */
const STREAM_SLICE = 60;

export const LiveStreamPanel = memo(function LiveStreamPanel({
  rows,
  selectedTraceId,
  selectedEventId,
  onSelect,
  collapsedTraces,
  onToggleCollapse,
}: {
  rows: StreamRow[];
  selectedTraceId: string | null;
  selectedEventId: string | null;
  onSelect: (row: StreamRow) => void;
  collapsedTraces: Set<string>;
  onToggleCollapse: (traceId: string) => void;
}) {
  if (!rows.length) {
    return (
      <section className="dti-section" aria-label="Live stream">
        <div className="dti-section-head">LIVE STREAM</div>
        <div className="dti-row-empty" role="status">
          NO EVENTS OBSERVED — the stream has not delivered a batch yet.
        </div>
      </section>
    );
  }
  // §64: group by trace, newest first; a collapsed trace renders one row.
  const byTrace = new Map<string, StreamRow[]>();
  for (const r of rows) {
    const key = r.trace_id ?? UNKNOWN;
    const arr = byTrace.get(key);
    if (arr) arr.push(r);
    else byTrace.set(key, [r]);
  }
  const groups = [...byTrace.entries()].slice(0, STREAM_SLICE);
  return (
    <section className="dti-section" aria-label="Live stream">
      <div className="dti-section-head">
        LIVE STREAM
        <span className="dti-count">{rows.length} retained</span>
      </div>
      <div className="dti-stream">
        {groups.map(([traceId, group]) => {
          const collapsed = collapsedTraces.has(traceId);
          const newest = group[group.length - 1];
          return (
            <div key={traceId}>
              <button
                type="button"
                className={`dti-stream-row ${selectedTraceId === traceId ? "active" : ""}`}
                style={{ inlineSize: "100%" }}
                onClick={() => onToggleCollapse(traceId)}
                aria-expanded={!collapsed}
              >
                <span className="seq">{group.length}</span>
                <span className="stage">{collapsed ? (newest?.stage ?? UNKNOWN) : traceId}</span>
                <span className="tid">{collapsed ? "" : traceId}</span>
                <span>{newest?.status ?? UNKNOWN}</span>
              </button>
              {collapsed
                ? null
                : group
                    .slice()
                    .reverse()
                    .slice(0, 12)
                    .map((r) => (
                      <button
                        type="button"
                        className={`dti-stream-row ${selectedEventId === r.event_id ? "active" : ""}`}
                        key={r.event_id}
                        style={{ paddingInlineStart: 16 }}
                        onClick={() => onSelect(r)}
                      >
                        <span className="seq">#{r.sequence}</span>
                        <span className="stage">{r.stage}</span>
                        <span className="tid">{r.trace_id ?? UNKNOWN}</span>
                        <span>{r.status ?? UNKNOWN}</span>
                      </button>
                    ))}
            </div>
          );
        })}
      </div>
    </section>
  );
});

/* --------------------------------------------------------------- §59
 * State timeline with exact timestamps (§59). */
export const StateTimelinePanel = memo(function StateTimelinePanel({
  events,
  onSelectEvent,
}: {
  events: TraceEvent[];
  onSelectEvent?: (eventId: string) => void;
}) {
  const rows = stateTimeline(events);
  if (!rows.length) {
    return (
      <section className="dti-section" aria-label="State timeline">
        <div className="dti-section-head">STATE TIMELINE</div>
        <div className="dti-row-empty" role="status">
          NO TIMELINE OBSERVED — this trace holds no events.
        </div>
      </section>
    );
  }
  return (
    <section className="dti-section" aria-label="State timeline">
      <div className="dti-section-head">
        STATE TIMELINE
        <span className="dti-count">{rows.length} state(s)</span>
      </div>
      <div className="dti-timeline">
        {rows.map((r) => {
          const cls =
            r.state === "REJECTED" || r.state === "BLOCKED"
              ? "rejected"
              : r.state === "CONFIRMED" || r.state === "COMPLETED"
                ? "ok"
                : r.state === "FAILED" || r.state === "ERROR"
                  ? "terminal"
                  : "";
          return (
            <button
              type="button"
              className={`dti-timeline-row ${cls}`}
              key={r.event_id}
              style={{ textAlign: "start", cursor: onSelectEvent ? "pointer" : "default" }}
              onClick={() => onSelectEvent?.(r.event_id)}
            >
              <span className="ts">{r.timestamp}</span>
              <span className="stage">{r.stage}</span>
              <span>{r.state}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
});

/* ------------------------------------------------------- §32/§33/§34/§36
 * FOLLOW REQUEST / POSITION / ORDER + the filter chips that echo the server. */
const FOLLOW_MODES: Array<{ id: FollowMode; label: string; placeholder: string }> = [
  { id: "NONE", label: "off", placeholder: "" },
  { id: "REQUEST", label: "request", placeholder: "trace_id / request_id" },
  { id: "POSITION", label: "position", placeholder: "position_id / ticket" },
  { id: "ORDER", label: "order", placeholder: "order_id / deal_id / ticket" },
];

export const FollowBar = memo(function FollowBar({
  mode,
  keyValue,
  onMode,
  onKey,
}: {
  mode: FollowMode;
  keyValue: string;
  onMode: (m: FollowMode) => void;
  onKey: (k: string) => void;
}) {
  const active = FOLLOW_MODES.find((m) => m.id === mode) ?? FOLLOW_MODES[1];
  if (!active) return null;
  return (
    <section className="dti-section" aria-label="Follow">
      <div className="dti-section-head">FOLLOW</div>
      <div className="dti-bar" style={{ marginBlockEnd: 6 }}>
        <div className="dti-tabs" role="tablist" aria-label="Follow mode">
          {FOLLOW_MODES.filter((m) => m.id !== "NONE").map((m) => (
            <button
              key={m.id}
              type="button"
              className={`dti-tab ${mode === m.id ? "active" : ""}`}
              onClick={() => onMode(m.id)}
            >
              {m.label}
            </button>
          ))}
        </div>
        {mode !== "NONE" ? (
          <button type="button" className="dti-toggle active" onClick={() => onMode("NONE")}>
            clear
          </button>
        ) : null}
      </div>
      {mode !== "NONE" ? (
        <div className="dti-follow">
          <label className="dti-k" htmlFor="dti-follow-key">
            {active.label}
          </label>
          <input
            id="dti-follow-key"
            className=""
            placeholder={active.placeholder}
            value={keyValue}
            spellCheck={false}
            onChange={(e) => onKey(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onMode(mode);
            }}
          />
        </div>
      ) : (
        <div className="dti-note">
          No follow filter — the events query runs unfiltered (legacy tail).
        </div>
      )}
    </section>
  );
});

/** §57 — counters are clickable and apply the matching /events filter. */
export function CounterButtons({
  counters,
  active,
  onApply,
}: {
  counters: Record<string, number> | null;
  active: string | null;
  onApply: (key: string, value: string) => void;
}) {
  const entries = Object.entries(counters ?? {});
  if (!entries.length) {
    return (
      <div className="dti-row-empty" role="status">
        COUNTERS {NOT_OBSERVED} — the observer reported no counters.
      </div>
    );
  }
  return (
    <div className="dti-bar">
      {entries.slice(0, 12).map(([k, v]) => (
        <button
          type="button"
          key={k}
          className={`dti-counter ${active === k ? "active" : ""}`}
          onClick={() => onApply(k, String(v))}
          title={`Filter events by ${k}`}
        >
          {k} <b>{v}</b>
        </button>
      ))}
    </div>
  );
}

/** §62 — the stream lifecycle word + honest gap surfacing. */
export const StreamLifecycleBar = memo(function StreamLifecycleBar({
  status,
  resyncing,
  gapMessage,
  lastSeq,
}: {
  status: string;
  resyncing: boolean;
  gapMessage: string | null;
  lastSeq: number;
}) {
  const w = streamStateWords(status, resyncing);
  return (
    <div className="dti-bar">
      <span className={`dti-word tone-${w.tone}`}>{w.label}</span>
      <span className="dti-note">{w.note}</span>
      {gapMessage ? <span className="dti-word gap">TRACE GAP</span> : null}
      <span className="spacer" />
      <span className="dti-k">last_seq</span>
      <span className="dti-v">{lastSeq}</span>
    </div>
  );
});

/** Bounded helper used by the page wiring (kept here so the panels stay pure). */
export function useStreamSlice(rows: StreamRow[]): StreamRow[] {
  return rows.slice(-STREAM_SLICE * 2);
}

/* Small local helper: boxes optional children under a section title. */
export function PanelBox({
  title,
  count,
  children,
}: {
  title: string;
  count?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="dti-section" aria-label={title}>
      <div className="dti-section-head">
        {title}
        {count !== undefined ? <span className="dti-count">{count}</span> : null}
      </div>
      {children}
    </section>
  );
}

/* Re-export for the page (single import surface). */
export { groupTraces };

/** §36 — filter chips: the server's own echo, removable in place. */
export const FilterChips = memo(function FilterChips({
  filters,
  onRemove,
}: {
  filters: Array<{ key: string; value: string }>;
  onRemove: (key: string) => void;
}) {
  if (!filters.length) return null;
  return (
    <div className="dti-bar">
      {filters.map((f) => (
        <span className="dti-chip" key={f.key}>
          {f.key}: {f.value}
          <button type="button" aria-label={`Clear ${f.key}`} onClick={() => onRemove(f.key)}>
            ×
          </button>
        </span>
      ))}
    </div>
  );
});

/** Wired store bindings for the scrubber (observer-only view clock). */
export function useViewClockBindings() {
  const viewClock = useDecisionTraceStore((s) => s.viewClock);
  const setViewSpeed = useDecisionTraceStore((s) => s.setViewSpeed);
  const toggleViewPaused = useDecisionTraceStore((s) => s.toggleViewPaused);
  const replayStep = useDecisionTraceStore((s) => s.replayStep);
  const replay = useDecisionTraceStore((s) => s.replay);
  const total = useDecisionTraceStore((s) => s.selectedBundle?.events.length ?? 0);
  const onSeek = useCallback(
    (index: number) => {
      const st = useDecisionTraceStore.getState();
      if (!st.selectedBundle) return;
      st.replaySetPlaying(false);
      st.replayStep(index - st.replay.index);
    },
    [],
  );
  return {
    position: replay.index,
    total,
    speed: viewClock.speed,
    paused: viewClock.paused,
    onSpeed: setViewSpeed,
    onTogglePause: toggleViewPaused,
    onStep: replayStep,
    onSeek,
  };
}
