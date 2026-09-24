/**
 * Decision Trace — decision + rejection forensics (§17 / §18 / §50).
 *
 * Everything is runtime-derived from the decision row and the trace's own
 * events: RECOMMENDED never renders as EXECUTED, and value/threshold pairs
 * render ONLY where the detail carries real numbers (else explicit UNKNOWN).
 *
 * OWNER: lane D, LIVE-CAUSAL-TOPOLOGY wave.
 */

import { memo } from "react";
import {
  UNKNOWN,
  decisionCard,
  executionLine,
  modelIdentity,
  originLine,
  rejectionForensics,
  safeDbMetadata,
} from "../forensics";
import { useDecisionTraceStore } from "../store";
import { useWhyQuery } from "../useCases";
import type { DecisionRow, TraceEvent, WhyResponse } from "../types";

function Row({ k, v, absent }: { k: string; v: string; absent?: boolean }) {
  return (
    <>
      <div className={`dti-k ${absent ? "absent" : ""}`}>{k}</div>
      <div className={`dti-v ${absent ? "absent" : ""}`}>{v}</div>
    </>
  );
}

/**
 * §17/§50 — the decision card: a compact summary of the real decision row
 * (ids, timestamps, model/mode/freshness, policy/risk summary, deliberation
 * keys), plus the observed execution line.
 */
export const DecisionCardPanel = memo(function DecisionCardPanel({
  row,
  events,
  compact = true,
}: {
  row: DecisionRow | null;
  events: TraceEvent[];
  compact?: boolean;
}) {
  const card = decisionCard(row, events);
  const identity = modelIdentity(events);
  const origin = originLine(events);
  return (
    <section className={`dti-section ${compact ? "dti-compact" : ""}`} aria-label="Decision">
      <div className="dti-section-head">
        DECISION <span style={{ marginInlineStart: 4 }}>— runtime summary</span>
        <span className="dti-count">{card.decision_id ?? UNKNOWN}</span>
      </div>
      <div className="dti-bar" style={{ marginBlockEnd: 6 }}>
        <span className="dti-word tone-info">{card.action}</span>
        <span className="dti-word tone-unknown">{card.mode}</span>
        <span className="dti-word tone-warn">{card.execution}</span>
        <span className="dti-word tone-unknown">{card.gateway}</span>
      </div>
      <div className="dti-fields">
        <Row k="trace_id" v={card.trace_id ?? UNKNOWN} absent={!card.trace_id} />
        <Row k="decision_id" v={card.decision_id ?? UNKNOWN} absent={!card.decision_id} />
        <Row k="position_id" v={card.position_id ?? UNKNOWN} absent={!card.position_id} />
        <Row k="model" v={card.model} absent={card.model === UNKNOWN} />
        <Row k="model_version" v={identity.model_version} absent={identity.model_version === UNKNOWN} />
        <Row k="provider" v={card.provider} absent={card.provider === UNKNOWN} />
        <Row k="policy" v={card.policy} absent={card.policy === UNKNOWN} />
        <Row k="risk" v={card.risk} absent={card.risk === UNKNOWN} />
        <Row
          k="total_latency_us"
          v={card.total_latency_us === null ? "NOT OBSERVED" : String(card.total_latency_us)}
          absent={card.total_latency_us === null}
        />
        {compact ? null : (
          <>
            <Row k="artifact" v={identity.artifact} absent={identity.artifact === UNKNOWN} />
            <Row k="snapshot_id" v={identity.snapshot_id} absent={identity.snapshot_id === UNKNOWN} />
            <Row k="source" v={origin.source} absent={origin.source === UNKNOWN} />
            <Row k="destination" v={origin.destination} absent={origin.destination === UNKNOWN} />
            <Row k="request_id" v={origin.request_id} absent={origin.request_id === UNKNOWN} />
          </>
        )}
      </div>
    </section>
  );
});

/**
 * §18 — rejection forensics. rejection_code/summary present => show them, plus
 * the value/threshold pairs ONLY where the detail carries real numbers; every
 * absent field renders UNKNOWN explicitly.
 */
export const RejectionForensicsPanel = memo(function RejectionForensicsPanel({
  row,
  events,
  why,
  compact = true,
}: {
  row: DecisionRow | null;
  events: TraceEvent[];
  why?: WhyResponse | null;
  compact?: boolean;
}) {
  const forensics = rejectionForensics(row, events, why ?? null);
  if (!forensics.rejected) {
    return (
      <section className="dti-section" aria-label="Rejection forensics">
        <div className="dti-section-head">REJECTION — forensics</div>
        <div className="dti-bar" style={{ marginBlockEnd: 6 }}>
          <span className="dti-word tone-ok">NOT REJECTED</span>
          <span className="dti-word tone-unknown">{executionLine(events)}</span>
        </div>
        <div className="dti-note">
          No backend status/state word asserts rejection for this trace
          (reason_code {forensics.reason_code}).
        </div>
        {!compact ? (
          <div className="dti-fields" style={{ marginBlockStart: 6 }}>
            <Row k="reason_code" v={forensics.reason_code} absent={forensics.reason_code === UNKNOWN} />
            <Row k="next" v={forensics.next} absent={forensics.next === UNKNOWN} />
          </div>
        ) : null}
      </section>
    );
  }

  const pairs = forensics.pairs;
  return (
    <section className={`dti-section ${compact ? "dti-compact" : ""}`} aria-label="Rejection forensics">
      <div className="dti-section-head">
        REJECTION — forensics
        <span className="dti-count">{pairs.length ? `${pairs.length} gate(s)` : "no gates"}</span>
      </div>
      <div className="dti-bar" style={{ marginBlockEnd: 6 }}>
        <span className="dti-word tone-fail">REJECTED</span>
        {forensics.rejecting_stage ? (
          <span className="dti-word tone-warn">{forensics.rejecting_stage}</span>
        ) : null}
        <span className="dti-word tone-unknown">{forensics.state}</span>
      </div>
      <div className="dti-fields">
        <Row k="reason_code" v={forensics.reason_code} absent={forensics.reason_code === UNKNOWN} />
        <Row
          k="rejection_reason"
          v={forensics.rejection_reason}
          absent={forensics.rejection_reason === UNKNOWN}
        />
        <Row k="blocked_by" v={forensics.blocked_by} absent={forensics.blocked_by === UNKNOWN} />
        <Row k="state" v={forensics.state} absent={forensics.state === UNKNOWN} />
        <Row k="next" v={forensics.next} absent={forensics.next === UNKNOWN} />
      </div>
      {pairs.length ? (
        <div style={{ marginBlockStart: 8 }}>
          <div className="dti-section-head" style={{ marginBlockEnd: 4 }}>
            GATES — value vs threshold
          </div>
          <div className="dti-pairs">
            {pairs.map((p) => (
              <div className="dti-pair" key={p.key}>
                <span className="key">{p.key}</span>
                <span className="actual">{p.actual}</span>
                <span className="op">{p.operator}</span>
                <span className="required">{p.required}</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="dti-row-empty" style={{ marginBlockStart: 6 }} role="status">
          NO GATE NUMBERS OBSERVED — the runtime recorded no value/threshold
          pair for this rejection (rejection summary only).
        </div>
      )}
    </section>
  );
});

/** §45-§49 — safe DB metadata + model identity, only when present. */
export const MetadataPanel = memo(function MetadataPanel({ events }: { events: TraceEvent[] }) {
  const dbEvents = events.filter((e) => {
    const d = safeDbMetadata(e);
    return Object.keys(d).length > 0;
  });
  const identity = modelIdentity(events);
  const showIdentity =
    identity.model !== UNKNOWN ||
    identity.provider !== UNKNOWN ||
    identity.model_version !== UNKNOWN ||
    identity.snapshot_id !== UNKNOWN;
  if (!dbEvents.length && !showIdentity) {
    return (
      <section className="dti-section" aria-label="Metadata">
        <div className="dti-section-head">METADATA — DB / model</div>
        <div className="dti-row-empty" role="status">
          DB METADATA NOT OBSERVED — no event carried a safe DB record.
        </div>
      </section>
    );
  }
  return (
    <section className="dti-section" aria-label="Metadata">
      <div className="dti-section-head">METADATA — DB / model</div>
      {showIdentity ? (
        <div className="dti-fields" style={{ marginBlockEnd: 8 }}>
          <Row k="model" v={identity.model} absent={identity.model === UNKNOWN} />
          <Row k="provider" v={identity.provider} absent={identity.provider === UNKNOWN} />
          <Row
            k="model_version"
            v={identity.model_version}
            absent={identity.model_version === UNKNOWN}
          />
          <Row k="snapshot_id" v={identity.snapshot_id} absent={identity.snapshot_id === UNKNOWN} />
        </div>
      ) : (
        <div className="dti-row-empty" style={{ marginBlockEnd: 6 }} role="status">
          MODEL IDENTITY NOT OBSERVED
        </div>
      )}
      {dbEvents.map((e) => (
        <div key={e.event_id} style={{ marginBlockEnd: 6 }}>
          <div className="dti-k">{e.stage}</div>
          <div className="dti-payload">
            {Object.entries(safeDbMetadata(e)).map(([k, v]) => (
              <div className="dti-payload-row" key={k}>
                <span className="key">{k}</span>
                <span className="val">{v}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
});

/**
 * Wired wrapper: reads the selected trace from the store and fetches its WHY
 * answer so the rejection panel can restate the endpoint's own destination.
 */
export const DecisionForensics = memo(function DecisionForensics({
  row,
  events,
  compact,
}: {
  row: DecisionRow | null;
  events: TraceEvent[];
  compact?: boolean;
}) {
  const selectedEventId = useDecisionTraceStore((s) => s.selectedEventId);
  const whyQ = useWhyQuery(selectedEventId, false);
  const why = whyQ.data ?? null;
  return (
    <>
      <DecisionCardPanel row={row} events={events} compact={compact} />
      <RejectionForensicsPanel row={row} events={events} why={why} compact={compact} />
    </>
  );
});
