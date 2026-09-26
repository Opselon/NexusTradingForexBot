/**
 * Decision Trace — latency waterfall + freshness + error cascade + health
 * (§40 / §41 / §42 / §43).
 *
 * Every number traces to a named backend field (per-event latency_us /
 * duration_ms, /latency stage stats, /observer + /topology state words). Sums
 * are labelled UI-observed or backend-total; absent evidence renders the
 * explicit words. Health never invents a status: a subsystem without a
 * dedicated endpoint reads "backend endpoint pending" (CONTRACT §43).
 *
 * OWNER: lane D, LIVE-CAUSAL-TOPOLOGY wave.
 */

import { memo, useMemo } from "react";
import {
  BACKEND_PENDING,
  NOT_OBSERVED,
  UNKNOWN,
  errorCascade,
  freshnessTone,
  freshnessWord,
  latencyWaterfall,
  stateTone,
  systemHealth,
} from "../forensics";
import type {
  LatencyStats,
  ObserverSnapshot,
  TopologySnapshot,
  TraceEvent,
} from "../types";

function micros(us: number): string {
  if (us >= 1000) return `${(us / 1000).toFixed(2)} ms`;
  return `${Math.round(us)} µs`;
}

/**
 * §41 — latency waterfall. Bars come from per-event latency_us / duration_ms;
 * per-stage p50/p95 are the backend's own /latency samples. The sum is shown
 * as a UI-observed sum (or the backend total when the runtime recorded one) —
 * never an invented number.
 */
export const LatencyWaterfallPanel = memo(function LatencyWaterfallPanel({
  events,
  stats,
  compact = true,
}: {
  events: TraceEvent[];
  stats: LatencyStats | null;
  compact?: boolean;
}) {
  /** Waterfall is a filter/aggregate chain — memoized so a parent re-render
   *  with unchanged props costs nothing (R1 §5.1). */
  const w = useMemo(() => latencyWaterfall(events, stats ?? null), [events, stats]);
  if (!w.bars.length) {
    return (
      <section className="dti-section" aria-label="Latency waterfall">
        <div className="dti-section-head">LATENCY — waterfall</div>
        <div className="dti-row-empty" role="status">
          {NOT_OBSERVED} — no events retained for this trace.
        </div>
      </section>
    );
  }
  const peak = Math.max(1, ...w.bars.map((b) => b.us));
  const shown = compact ? w.bars.slice(-14) : w.bars;
  return (
    <section className={`dti-section ${compact ? "dti-compact" : ""}`} aria-label="Latency waterfall">
      <div className="dti-section-head">
        LATENCY — waterfall
        <span className="dti-count">{w.bars.length} event(s)</span>
      </div>
      <div className="dti-waterfall">
        {shown.map((b) => (
          <div className={`dti-wf-row ${b.observed ? "" : "missing"}`} key={b.event_id}>
            <span className="stage">{b.stage}</span>
            <span className="dti-wf-bar" role="img" aria-label={`${b.stage} ${b.observed ? micros(b.us) : NOT_OBSERVED}`}>
              {b.observed ? <i style={{ inlineSize: `${(b.us / peak) * 100}%` }} /> : null}
            </span>
            <span className="us">{b.observed ? micros(b.us) : NOT_OBSERVED}</span>
          </div>
        ))}
      </div>
      <div className="dti-wf-total">
        <span>
          UI-observed sum: <b>{w.ui_sum_us === null ? NOT_OBSERVED : micros(w.ui_sum_us)}</b>
        </span>
        {w.backend_total_us !== null ? (
          <span>
            backend total: <b>{micros(w.backend_total_us)}</b>
          </span>
        ) : null}
        {compact && w.bars.length > shown.length ? (
          <span>+ {w.bars.length - shown.length} earlier (toggle full)</span>
        ) : null}
      </div>
      {Object.keys(w.backend_stages).length ? (
        <details className="dti-section" style={{ marginBlockStart: 8 }}>
          <summary>backend stage stats (/latency)</summary>
          <div className="dti-fields">
            {Object.entries(w.backend_stages).map(([stage, s]) => (
              <span key={stage} style={{ gridColumn: "1 / -1", display: "grid", gridTemplateColumns: "subgrid" }}>
                <span className="dti-k">{stage}</span>
                <span className="dti-v">
                  p50 {s.p50_us === null ? UNKNOWN : micros(s.p50_us)} · p95{" "}
                  {s.p95_us === null ? UNKNOWN : micros(s.p95_us)} · n={s.n}
                </span>
              </span>
            ))}
          </div>
          {w.backend_only_stages.length ? (
            <div className="dti-row-empty" style={{ marginBlockStart: 6 }}>
              Stages the backend timed but this trace never visited:{" "}
              {w.backend_only_stages.join(", ")}
            </div>
          ) : null}
        </details>
      ) : (
        <div className="dti-row-empty" style={{ marginBlockStart: 6 }} role="status">
          BACKEND STAGE STATS {NOT_OBSERVED} — /latency returned no samples.
        </div>
      )}
    </section>
  );
});

/** §40 — freshness display: event.freshness verbatim, with a restating tone. */
export const FreshnessPanel = memo(function FreshnessPanel({ events }: { events: TraceEvent[] }) {
  /** filter + sort + slice chain memoized (R1 §5.1): newest 6 carrying a
   *  runtime freshness word. `.filter` already yields a fresh array, so the
   *  sort never touches the store's event list. */
  const newest = useMemo(
    () =>
      events
        .filter((e) => e.freshness !== undefined && e.freshness !== null)
        .sort((a, b) => b.sequence - a.sequence)
        .slice(0, 6),
    [events],
  );
  if (!newest.length) {
    return (
      <section className="dti-section" aria-label="Freshness">
        <div className="dti-section-head">FRESHNESS — data age verdict</div>
        <div className="dti-row-empty" role="status">
          FRESHNESS {NOT_OBSERVED} — no event carries a runtime freshness word.
        </div>
      </section>
    );
  }
  return (
    <section className="dti-section" aria-label="Freshness">
      <div className="dti-section-head">FRESHNESS — data age verdict</div>
      <div className="dti-stream">
        {newest.map((e) => {
          const word = freshnessWord(e);
          return (
            <div className="dti-stream-row" key={e.event_id} style={{ cursor: "default" }}>
              <span className="stage">{e.stage}</span>
              <span className={`dti-word tone-${freshnessTone(word) === "fresh" ? "ok" : freshnessTone(word) === "stale" ? "warn" : freshnessTone(word) === "invalid" ? "fail" : "unknown"}`}>
                {word}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
});

/**
 * §42 — error cascade from real error_code chains only. No error_code anywhere
 * in the trace renders NO ERROR OBSERVED (never an invented root cause).
 */
export const ErrorCascadePanel = memo(function ErrorCascadePanel({ events }: { events: TraceEvent[] }) {
  /** Error-chain walk memoized — same inputs, no re-walk per render (R1 §5.1). */
  const cascade = useMemo(() => errorCascade(events), [events]);
  if (!cascade.nodes.length) {
    return (
      <section className="dti-section" aria-label="Error cascade">
        <div className="dti-section-head">ERROR — cascade</div>
        <div className="dti-bar" style={{ marginBlockEnd: 4 }}>
          <span className="dti-word tone-ok">NO ERROR OBSERVED</span>
        </div>
        <div className="dti-note">No event of this trace carries a backend error_code.</div>
      </section>
    );
  }
  return (
    <section className="dti-section" aria-label="Error cascade">
      <div className="dti-section-head">
        ERROR — cascade
        <span className="dti-count">{cascade.summary}</span>
      </div>
      <div className="dti-cascade">
        {cascade.nodes.map((n) => (
          <div className="dti-cascade-row" key={n.event_id}>
            <span className="stage">{n.stage}</span>
            <span className="code">{n.error_code}</span>
            <span className="reason">{n.reason_code ?? n.state}</span>
          </div>
        ))}
      </div>
    </section>
  );
});

/**
 * §43 — system health overlay, only from real backend observer/topology state
 * words. A subsystem with no dedicated endpoint shows
 * "backend endpoint pending", never a faked healthy/unhealthy verdict.
 */
export const SystemHealthPanel = memo(function SystemHealthPanel({
  observer,
  topology,
  stats,
}: {
  observer: ObserverSnapshot | null;
  topology: TopologySnapshot | null;
  stats: LatencyStats | null;
}) {
  const items = systemHealth(observer, topology, stats);
  return (
    <section className="dti-section" aria-label="System health">
      <div className="dti-section-head">HEALTH — backend state words</div>
      <div className="dti-health">
        {items.map((i) => (
          <div className="dti-health-row" key={i.subsystem}>
            <span className={`dti-word tone-${i.tone === "unknown" ? "unknown" : i.tone === "ok" ? "ok" : i.tone === "warn" ? "warn" : "fail"}`}>
              {i.state === BACKEND_PENDING ? `${BACKEND_PENDING}` : i.state}
            </span>
            <span>
              <span className="sub">{i.subsystem}</span>
              <span className="src">{i.source}</span>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
});

/** tone helper kept local so the panels stay declarative */
export function healthToneClass(tone: "ok" | "warn" | "fail" | "unknown"): string {
  return `tone-${stateTone(tone === "unknown" ? "" : tone.toUpperCase()) === "unknown" ? "unknown" : tone}`;
}
