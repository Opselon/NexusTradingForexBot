/**
 * PURPOSE:  Hero state strip — glowing ENGINE/MODE/TICK pills + snapshot rail cells.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../model (OperatorSummaryDto + VO helpers), @/components/primitives
 *           (Skeleton), @/lib/format, ./StatusGlow, ./tones
 * PROVIDES: HeroStrip
 * INVARIANTS: every rail cell is a verbatim backend field (or NOT RECORDED / —);
 *             loading renders a skeleton, backend error renders a visible error
 *             note with retry — never a placeholder number.
 * EXTEND:   new rail cell = one <RailCell label value/> line fed by a direct
 *           model.ts field; never derive a cell the backend does not send.
 */
import { useMemo } from "react";
import { Skeleton } from "@/components/primitives";
import { formatAgeMs, formatDateTime } from "@/lib/format";
import { bool, notRecorded, num, obj, str, type OperatorSummaryDto } from "../model";
import { StatusGlow } from "./StatusGlow";
import { engineTone, modeTone, tickTone } from "./tones";

interface HeroStripProps {
  summary: OperatorSummaryDto | undefined;
  pending: boolean;
  error: boolean;
  errorMessage: string;
  onRetry: () => void;
}

/** One snapshot fact cell — dimmed when the backend recorded nothing. */
function RailCell({ label, value }: { label: string; value: string }) {
  const dim = value === "—" || value === "NOT RECORDED";
  return (
    <div className="ctl-rail-cell">
      <span className="ctl-rail-label">{label}</span>
      <span className={`ctl-rail-value${dim ? " ctl-dim" : ""}`} title={value}>
        {value}
      </span>
    </div>
  );
}

export function HeroStrip({ summary, pending, error, errorMessage, onRetry }: HeroStripProps) {
  // perf: the runtime object identity is stable across the 15s summary poll
  // unless the backend actually changed it — memo the tone derivations so a
  // re-render reuses the identical tone/text objects (cheap pure helpers, but
  // they run per render and feed memoized children below).
  const rt = useMemo(() => obj(summary?.runtime), [summary]);
  const eng = engineTone(bool(rt.engine_running));
  const tick = tickTone(rt);
  const mode = modeTone(String(rt.runtime_mode ?? rt.execution_mode ?? "UNKNOWN").toUpperCase());
  const tickAge = num(rt.tick_freshness_ms);

  return (
    <section className="ctl-hero" aria-label="Runtime state strip">
      <div className="ctl-hero-main">
        <div className="ctl-eyebrow">
          <span className="ctl-eyebrow-bar" aria-hidden="true" />
          OPERATOR CONSOLE · /api/operator/summary
        </div>

        {pending ? (
          <div className="ctl-glows">
            <span className="ctl-glow ctl-glow--unknown">
              <span className="ctl-dot" aria-hidden="true" />
              <span className="ctl-glow-label">STATE</span>
              <span className="ctl-glow-text">LOADING…</span>
            </span>
          </div>
        ) : error ? (
          <div className="ctl-glows">
            <span className="ctl-glow ctl-glow--unknown" title="operator/summary request failed">
              <span className="ctl-dot" aria-hidden="true" />
              <span className="ctl-glow-label">STATE</span>
              <span className="ctl-glow-text">UNAVAILABLE</span>
            </span>
            <button className="btn small" onClick={onRetry}>
              Retry
            </button>
          </div>
        ) : (
          <div className="ctl-glows">
            <StatusGlow label="ENGINE" tone={eng.tone} text={eng.text} title="engine_running (operator/summary)" />
            <StatusGlow label="MODE" tone={mode.tone} text={mode.text} title="runtime_mode / execution_mode" />
            <StatusGlow label="TICK" tone={tick.tone} text={tick.text} title="tick_stale / tick_freshness_ms" />
          </div>
        )}

        {error && (
          <div className="ctl-hero-error tiny" role="alert">
            summary unavailable: {errorMessage}
          </div>
        )}
      </div>

      <div className="ctl-rail">
        {pending ? (
          <div className="ctl-rail-loading">
            <Skeleton count={4} height={14} />
          </div>
        ) : (
          <>
            <RailCell label="SNAPSHOT" value={formatDateTime(str(rt.snapshot_timestamp))} />
            <RailCell label="STATE VER" value={notRecorded(num(rt.state_version))} />
            <RailCell label="TICK AGE" value={tickAge === null ? "NOT RECORDED" : formatAgeMs(tickAge)} />
            <RailCell label="LATEST DECISION" value={formatDateTime(summary?.ledger?.latest_decision_at)} />
          </>
        )}
      </div>
    </section>
  );
}
