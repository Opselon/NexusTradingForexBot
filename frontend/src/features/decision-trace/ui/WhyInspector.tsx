/**
 * Decision Trace — WHY / NEXT inspector (§37 / §38).
 *
 * The PRIMARY view over GET /api/trace/why/{event_id}. The endpoint's own
 * states are this panel's states: TERMINATED / NOT OBSERVED / a 404's explicit
 * NOT_FOUND — never a fabricated continuation. Provenance is restated verbatim;
 * a NEXT with no observed edge renders PROVENANCE GAP wording (§56) and links
 * are rendered (never new graph logic — the graph stays lane C's).
 *
 * OWNER: lane D, LIVE-CAUSAL-TOPOLOGY wave.
 */

import { memo } from "react";
import {
  PROVENANCE_GAP,
  TERMINATED,
  UNKNOWN,
  gapWording,
  payloadFields,
  provenanceWord,
  renderWhy,
  whyHasReason,
  whyReasonsLines,
} from "../forensics";
import type { WhyResponse } from "../types";
import type { WhyViewState } from "../forensics";

const NOT_FOUND_WORD = "NOT FOUND";
const NO_REASON_WORD = "NO REASON OBSERVED";

function Word({
  tone,
  children,
  gap = false,
}: {
  tone: "ok" | "warn" | "fail" | "info" | "unknown";
  children: string;
  gap?: boolean;
}) {
  return (
    <span className={`dti-word ${gap ? "gap" : `tone-${tone}`}`}>
      {children}
    </span>
  );
}

function FieldRow({ k, v, absent }: { k: string; v: string; absent?: boolean }) {
  return (
    <>
      <div className={`dti-k ${absent ? "absent" : ""}`}>{k}</div>
      <div className={`dti-v ${absent ? "absent" : ""}`}>{v}</div>
    </>
  );
}

/**
 * §37 — the WHY reason block. Absent reason keys render UNKNOWN; an entirely
 * empty reasons object renders the explicit NO REASON OBSERVED line.
 */
export const WhyReasonBlock = memo(function WhyReasonBlock({
  why,
}: {
  why: WhyResponse["why"] | null | undefined;
}) {
  if (!whyHasReason(why)) {
    return (
      <div className="dti-row-empty" role="status">
        {NO_REASON_WORD} — the runtime recorded no reason fields for this event.
      </div>
    );
  }
  return (
    <div className="dti-fields">
      {whyReasonsLines(why).map((l) => (
        <FieldRow key={l.key} k={l.key} v={l.value} absent={l.value === UNKNOWN} />
      ))}
    </div>
  );
});

/**
 * §38 — NEXT DESTINATION panel from the same response. An observed successor
 * is clickable (focuses that event); TERMINATED / NOT OBSERVED show the honest
 * verdict and the gap wording (§56), with no clickable edge.
 */
export const NextDestination = memo(function NextDestination({
  res,
  onSelectEvent,
}: {
  res: WhyResponse | null;
  onSelectEvent?: (eventId: string | null) => void;
}) {
  if (!res) {
    return (
      <div className="dti-row-empty" role="status">
        NO NEXT OBSERVED — no WHY response loaded for this event.
      </div>
    );
  }
  const next = res.next;
  if (!next || (!next.observed && next.destination !== TERMINATED && next.destination !== "NOT OBSERVED")) {
    return (
      <div className="dti-row-empty" role="status">
        NO NEXT OBSERVED — the endpoint returned no next field.
      </div>
    );
  }

  const prov = provenanceWord(next.provenance);
  const wording = gapWording(next.provenance);

  if (!next.observed) {
    const terminated = next.destination === TERMINATED;
    const status = terminated ? (next.terminal_status ?? null) : null;
    return (
      <div>
        <div className="dti-bar" style={{ marginBlockEnd: 4 }}>
          <Word tone={terminated ? "fail" : "warn"} gap>
            {terminated ? TERMINATED : "NOT OBSERVED"}
          </Word>
          {status ? <Word tone="unknown">{status}</Word> : null}
        </div>
        <div className="dti-note">
          {terminated
            ? status
              ? `The path observably ended here (terminal status: ${status}). No successor is retained.`
              : "The path observably ended here; the runtime recorded no terminal status."
            : "No further event of this trace is retained — the observer ring may have evicted it."}
          <br />
          <span className="dti-cause">{wording}</span>
        </div>
      </div>
    );
  }

  const clickable = !!next.event_id && !!onSelectEvent;
  return (
    <div>
      <div className="dti-bar" style={{ marginBlockEnd: 4 }}>
        <Word tone={prov === "gap" ? "warn" : "ok"} gap={prov === "gap"}>
          {next.destination ?? UNKNOWN}
        </Word>
        {next.stage ? <Word tone="info">{next.stage}</Word> : null}
        {next.status ? <Word tone={next.status === "OK" ? "ok" : "unknown"}>{next.status}</Word> : null}
        {next.terminal ? <Word tone="fail">TERMINAL</Word> : null}
      </div>
      <div className="dti-fields">
        <FieldRow k="provenance" v={prov === "gap" ? PROVENANCE_GAP : prov} absent={prov === "gap"} />
        <FieldRow k="link" v={next.link ?? "none"} absent={!next.link} />
        <FieldRow k="event_id" v={next.event_id ?? UNKNOWN} absent={!next.event_id} />
        <FieldRow k="timestamp" v={next.timestamp ?? UNKNOWN} absent={!next.timestamp} />
      </div>
      <div className="dti-note" style={{ marginBlockStart: 4 }}>
        <span className="dti-cause">{wording}</span>
      </div>
      {clickable ? (
        <button
          type="button"
          className="dti-toggle"
          style={{ marginBlockStart: 6 }}
          onClick={() => onSelectEvent?.(next.event_id ?? null)}
        >
          Inspect {next.event_id}
        </button>
      ) : null}
    </div>
  );
});

/**
 * The full WHY panel (§37 PRIMARY VIEW). `ctx` is the connection-state view of
 * the query: EMPTY before selection, PENDING while fetching, NOT_FOUND on a
 * 404, ERROR on a transport failure, READY when the endpoint answered.
 */
export const WhyInspector = memo(function WhyInspector({
  res,
  ctx,
  onSelectEvent,
}: {
  res: WhyResponse | null;
  ctx: WhyViewState;
  onSelectEvent?: (eventId: string | null) => void;
}) {
  const panel = renderWhy(res, ctx);
  const compact = false;

  if (panel.state.kind !== "READY") {
    const word =
      panel.state.kind === "PENDING"
        ? "QUERYING"
        : panel.state.kind === "NOT_FOUND"
          ? NOT_FOUND_WORD
          : panel.state.kind === "ERROR"
            ? "QUERY FAILED"
            : "NO EVENT SELECTED";
    const tone = panel.state.kind === "NOT_FOUND" || panel.state.kind === "ERROR" ? "fail" : "unknown";
    const cause =
      panel.state.kind === "PENDING"
        ? "Asking the observer for the reason this event was recorded."
        : panel.state.kind === "NOT_FOUND"
          ? "The observer has no retained event with this id — it was never observed or the ring evicted it (NOT OBSERVED)."
          : panel.state.kind === "ERROR"
            ? (panel.state as { message?: string }).message ?? "The WHY endpoint did not answer."
            : "Select an event to ask the observer why it happened (§37).";
    return (
      <section className="dti-section" aria-label="WHY inspector">
        <div className="dti-section-head">
          WHY? <span style={{ marginInlineStart: 4 }}>— reason evidence</span>
        </div>
        <div className="dti-bar" style={{ marginBlockEnd: 6 }}>
          <Word tone={tone as "ok" | "warn" | "fail" | "info" | "unknown"}>{word}</Word>
        </div>
        <div className="dti-note">{cause}</div>
      </section>
    );
  }

  const r = panel.state.res;
  return (
    <section className={`dti-section ${compact ? "dti-compact" : ""}`} aria-label="WHY inspector">
      <div className="dti-section-head">
        WHY? <span style={{ marginInlineStart: 4 }}>— reason evidence</span>
        <span className="dti-count">{r.event_id ?? UNKNOWN}</span>
      </div>
      <div className="dti-bar" style={{ marginBlockEnd: 6 }}>
        <Word tone="info">{r.stage ?? UNKNOWN}</Word>
        {r.status ? <Word tone={r.status === "OK" ? "ok" : "warn"}>{r.status}</Word> : null}
        {r.terminal ? <Word tone="fail">TERMINAL</Word> : null}
        <span className="spacer" />
        <Word tone={r.provenance === "inferred" ? "warn" : "ok"} gap={r.provenance !== "observed" && r.provenance !== "inferred"}>
          {r.provenance ?? PROVENANCE_GAP}
        </Word>
      </div>
      <WhyReasonBlock why={r.why} />
      {r.detail ? (
        <details className="dti-section" style={{ marginBlockStart: 8 }}>
          <summary>detail (redacted payload)</summary>
          <pre className="dti-json">{JSON.stringify(r.detail, null, 2)}</pre>
        </details>
      ) : (
        <div className="dti-row-empty" style={{ marginBlockStart: 6 }} role="status">
          DETAIL NOT OBSERVED — the runtime recorded no detail for this event.
        </div>
      )}

      <div className="dti-section-head" style={{ marginBlockStart: 10 }}>
        NEXT DESTINATION <span className="dti-count">§38</span>
      </div>
      <NextDestination res={r} onSelectEvent={onSelectEvent} />
    </section>
  );
});

/**
 * §14 — payload metadata view for the WHY event: typed v2 fields first
 * (payload_summary / state / mode / freshness / reason_code), then the safe
 * detail keys the runtime recorded.
 */
export const WhyPayloadBlock = memo(function WhyPayloadBlock({
  res,
  compact = true,
}: {
  res: WhyResponse | null;
  compact?: boolean;
}) {
  if (!res) {
    return (
      <div className="dti-row-empty" role="status">
        PAYLOAD NOT OBSERVED — no event selected.
      </div>
    );
  }
  const rows = payloadFields({
    detail: res.detail,
    state: res.state,
    mode: res.mode,
    reason_code: res.reason_code,
    error_code: res.error_code,
    model: res.model,
    provider: res.provider,
    position_id: res.position_id,
    order_id: res.order_id,
    freshness: res.freshness,
  } as never);
  const shown = compact ? rows.slice(0, 12) : rows;
  return (
    <div className="dti-payload">
      {shown.map((row) => (
        <div className="dti-payload-row" key={row.key}>
          <span className="key">{row.key}</span>
          <span className={row.origin === "absent" ? "val nested" : "val"}>{row.value}</span>
        </div>
      ))}
      {!compact && rows.length > shown.length ? (
        <div className="dti-row-empty">{rows.length - shown.length} more keys (compact mode)</div>
      ) : null}
      {compact && rows.length > shown.length ? (
        <div className="dti-row-empty">+ {rows.length - shown.length} more (toggle full)</div>
      ) : null}
    </div>
  );
});
