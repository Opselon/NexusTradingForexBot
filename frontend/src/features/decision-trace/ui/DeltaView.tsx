/**
 * Decision Trace — what-changed delta view (§15 / §39).
 *
 * Deltas are computed between ADJACENT events of ONE trace, from real fields
 * only (payload_summary + safe detail keys). No evidence => no delta row; the
 * first event of a trace has no predecessor and says so.
 *
 * OWNER: lane D, LIVE-CAUSAL-TOPOLOGY wave.
 */

import { memo } from "react";
import { UNKNOWN, deltaBetween, traceDeltas } from "../forensics";
import type { TraceEvent } from "../types";

function kindTone(kind: "ADDED" | "CHANGED" | "REMOVED"): string {
  return kind === "ADDED" ? "added" : kind === "CHANGED" ? "changed" : "removed";
}

export const DeltaRows = memo(function DeltaRows({
  entries,
}: {
  entries: ReturnType<typeof deltaBetween>["entries"];
}) {
  if (!entries.length) {
    return (
      <div className="dti-row-empty" role="status">
        NO CHANGES OBSERVED — the retained evidence is identical for both events.
      </div>
    );
  }
  return (
    <div className="dti-delta">
      {entries.map((e) => (
        <div className="dti-delta-row" key={`${e.key}-${e.kind}`} style={{ display: "contents" }}>
          <span className="key">{e.key}</span>
          <span className={`kind ${kindTone(e.kind)}`}>{e.kind}</span>
          <span className="from">{e.from}</span>
          <span className="arrow">→</span>
          <span className="to">{e.to}</span>
        </div>
      ))}
    </div>
  );
});

/**
 * §15 — the delta between two adjacent events. `prev` is null when the current
 * event is the trace's first (explicit, never an invented baseline).
 */
export const DeltaView = memo(function DeltaView({
  prev,
  cur,
}: {
  prev: TraceEvent | null;
  cur: TraceEvent | null;
}) {
  if (!cur) {
    return (
      <section className="dti-section" aria-label="Delta">
        <div className="dti-section-head">DELTA — what changed</div>
        <div className="dti-row-empty" role="status">
          DELTA {NOT_AVAILABLE} — no event selected.
        </div>
      </section>
    );
  }
  if (!prev) {
    return (
      <section className="dti-section" aria-label="Delta">
        <div className="dti-section-head">DELTA — what changed</div>
        <div className="dti-bar" style={{ marginBlockEnd: 6 }}>
          <span className="dti-word tone-info">{cur.stage}</span>
          <span className="dti-word tone-unknown">FIRST OBSERVED EVENT</span>
        </div>
        <div className="dti-note">
          This event is the first retained event of its trace — there is no
          predecessor to compare against (no baseline is invented).
        </div>
      </section>
    );
  }
  const d = deltaBetween(prev, cur);
  return (
    <section className="dti-section" aria-label="Delta">
      <div className="dti-section-head">
        DELTA — what changed
        <span className="dti-count">
          {prev.stage} → {cur.stage}
        </span>
      </div>
      <DeltaRows entries={d.entries} />
    </section>
  );
});

const NOT_AVAILABLE = "NOT AVAILABLE";

/**
 * §39 — every adjacent pair of the trace, oldest first, as a delta list.
 * The caller passes the trace's sequence-ordered events.
 */
export const DeltaList = memo(function DeltaList({ events }: { events: TraceEvent[] }) {
  const deltas = traceDeltas(events);
  if (!deltas.length) {
    return (
      <section className="dti-section" aria-label="Deltas">
        <div className="dti-section-head">DELTAS — per step</div>
        <div className="dti-row-empty" role="status">
          NO DELTAS {NOT_AVAILABLE} — the trace holds no events.
        </div>
      </section>
    );
  }
  return (
    <section className="dti-section" aria-label="Deltas">
      <div className="dti-section-head">
        DELTAS — per step
        <span className="dti-count">{deltas.length} step(s)</span>
      </div>
      <div style={{ display: "grid", gap: 10 }}>
        {deltas.map((d, i) => (
          <div key={`${d.cur.event_id}-${i}`}>
            <div className="dti-bar" style={{ marginBlockEnd: 4 }}>
              <span className="dti-word tone-info">
                {d.prev ? `${d.prev.stage} → ${d.cur.stage}` : `${d.cur.stage} (first)`}
              </span>
              <span className="dti-word tone-unknown">{d.cur.status ?? UNKNOWN}</span>
            </div>
            {d.prev ? <DeltaRows entries={d.entries} /> : (
              <div className="dti-note">No predecessor retained — nothing to compare.</div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
});
