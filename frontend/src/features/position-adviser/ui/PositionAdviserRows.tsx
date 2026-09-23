/**
 * PURPOSE:  Memoized leaf components + the pure format helpers they share for
 *           the Position Adviser console, so the 3s status poll and train-form
 *           keystrokes stop re-running distSegments/belowBaseline over every
 *           model card, advisory row and trial row.
 * OWNER:    LANE-5 (perf wave) — features/position-adviser/** only.
 * CONSUMES: ../model (AdviserModelDto, AdviserAdvisoryDto, AdviserTrialDto,
 *           ActivationCheckDto), the feature's own ./position-adviser.css.
 * PROVIDES: classNames/fmt/fmtTime/belowBaseline/distSegments/distClass +
 *           MAJORITY_BASELINE_FALLBACK, ModelCard, AdvisoryRow, TrialRow,
 *           CheckItem (React.memo leaf components).
 * INVARANTS:Every rendered value is backend-authoritative (OOS metrics, status
 *           words, details); below-baseline models are flagged, never hidden;
 *           no component fetches.
 * EXTEND:   New position-adviser list rows go here with stable primitive props
 *           so memo stays effective.
 */

import { memo } from "react";
import type { ActivationCheckDto, AdviserAdvisoryDto, AdviserModelDto, AdviserTrialDto } from "../model";

/** Majority-class baseline is what any constant classifier scores; a model that
 *  cannot beat it is shown BELOW BASELINE, never hidden. The server reports it
 *  per-sweep; this is the fallback constant when the sweep value is absent. */
export const MAJORITY_BASELINE_FALLBACK = 0.682;

export function classNames(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** OOS accuracy vs the majority-class baseline: never silently green. */
export function belowBaseline(m: AdviserModelDto, baseline: number | null): boolean {
  const ref = baseline ?? MAJORITY_BASELINE_FALLBACK;
  return m.oos_accuracy != null && m.oos_accuracy < ref;
}

export type DistSeg = { key: string; pct: number };

/** OOS prediction distribution -> stacked meter segments (percent of total). */
export function distSegments(dist: Record<string, number>): DistSeg[] {
  const total = Object.values(dist).reduce((s, v) => s + (v || 0), 0);
  if (total <= 0) return [];
  return Object.entries(dist)
    .filter(([, v]) => (v || 0) > 0)
    .map(([k, v]) => ({ key: k.toUpperCase(), pct: ((v || 0) / total) * 100 }));
}

export function distClass(key: string): string {
  if (key === "KEEP") return "keep";
  if (key === "CLOSE") return "close";
  if (key === "REDUCE") return "reduce";
  return "other";
}

export function fmt(n: number | null | undefined, digits = 4): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "--";
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString();
}

/** One trained-checkpoint card with its guarded load command. */
export const ModelCard = memo(function ModelCard({
  m,
  isActive,
  baseline,
  busy,
  onLoad,
}: {
  m: AdviserModelDto;
  isActive: boolean;
  baseline: number | null;
  busy: boolean;
  onLoad: (m: AdviserModelDto) => void;
}) {
  const isBelow = belowBaseline(m, baseline);
  const segs = distSegments(m.oos_action_distribution || {});
  return (
    <div className={classNames("pa-model", isActive && "is-active")}>
      <div className="pa-model-top">
        <span className="pa-model-id">{m.model_id}</span>
        {isActive ? <span className="badge good">LOADED</span> : null}
        {isBelow ? <span className="badge bad">BELOW BASELINE</span> : null}
        {!m.has_scaler ? <span className="badge warn">NO SCALER</span> : null}
      </div>
      <div className="pa-model-facts">
        <span>
          OOS acc <b>{fmt(m.oos_accuracy)}</b>
        </span>
        <span>
          OOS loss <b>{fmt(m.oos_loss)}</b>
        </span>
        <span>
          val loss <b>{fmt(m.best_val_loss)}</b>
        </span>
        <span>
          train/oos <b>{m.train_rows ?? "--"}/{m.oos_rows ?? "--"}</b>
        </span>
        <span>
          epochs <b>{m.epochs ?? "--"}</b>
        </span>
        {m.created_at ? <span title={m.created_at}>{fmtTime(m.created_at)}</span> : null}
      </div>
      {segs.length > 0 ? (
        <div className="pa-dist">
          <span className="pa-dist-lab">OOS predictions</span>
          <span className="pa-dist-track">
            {segs.map((s) => (
              <span
                key={s.key}
                className={classNames("pa-dist-seg", distClass(s.key))}
                style={{ width: `${s.pct}%` }}
                title={`${s.key}: ${s.pct.toFixed(1)}%`}
              />
            ))}
          </span>
          <span className="pa-dist-legend">
            {segs.map((s) => (
              <span key={s.key}>
                <i className={distClass(s.key)} />
                {s.key} {s.pct.toFixed(0)}%
              </span>
            ))}
          </span>
        </div>
      ) : null}
      <div>
        <button
          type="button"
          disabled={busy || !m.has_scaler}
          onClick={() => onLoad(m)}
          className="pa-btn pa-btn-ghost"
        >
          {m.has_scaler ? "Load into live memory" : "No scaler sidecar — load refused"}
        </button>
      </div>
    </div>
  );
});

/** One advisory in the live feed (computes its probability meter internally). */
export const AdvisoryRow = memo(function AdvisoryRow({ a }: { a: AdviserAdvisoryDto }) {
  const dist = distSegments(a.probabilities || {});
  return (
    <div className="pa-feed-item">
      <div className="pa-feed-top">
        <span className="pa-feed-ticket">#{a.ticket}</span>
        <span
          className={classNames(
            "badge",
            a.action === "CLOSE" ? "bad" : a.action === "REDUCE" ? "warn" : "good",
          )}
        >
          {a.action}
        </span>
        <span className="pa-conf" title="adviser confidence">
          <span className="pa-conf-track">
            <i style={{ width: `${Math.max(0, Math.min(1, a.confidence)) * 100}%` }} />
          </span>
          <span className="pa-conf-val">{a.confidence.toFixed(3)}</span>
        </span>
        <span className={a.hold_score_adjustment < 0 ? "pa-hold-neg" : "pa-hold-zero"}>
          hold adj {a.hold_score_adjustment.toFixed(2)}
        </span>
        <span className={classNames("badge", a.applied ? "good" : "neutral")}>
          {a.applied ? "APPLIED" : "LOGGED ONLY"}
        </span>
        <span className="pa-feed-time">
          {a.latency_ms.toFixed(2)} ms · {fmtTime(a.evaluated_at)}
        </span>
      </div>
      {dist.length > 0 ? (
        <span className="pa-dist-track" style={{ height: 4 }}>
          {dist.map((s) => (
            <span
              key={s.key}
              className={classNames("pa-dist-seg", distClass(s.key))}
              style={{ width: `${s.pct}%` }}
              title={`${s.key}: ${s.pct.toFixed(1)}%`}
            />
          ))}
        </span>
      ) : null}
      {a.not_applied_reason ? <div className="pa-feed-reason">{a.not_applied_reason}</div> : null}
    </div>
  );
});

/** One auto-tune trial row; the winner/failed flags come from the sweep DTO. */
export const TrialRow = memo(function TrialRow({
  t,
  bestModelId,
}: {
  t: AdviserTrialDto;
  bestModelId: string;
}) {
  const isWinner = t.model_id === bestModelId;
  return (
    <tr className={classNames(isWinner && "is-winner", t.failed && "is-failed")}>
      <td className="mono-id">
        {t.failed ? "✗ " : isWinner ? "★ " : "· "}
        {t.model_id}
      </td>
      <td>{t.learning_rate}</td>
      <td>{t.batch_size}</td>
      <td>{t.seed}</td>
      <td>{t.failed ? "failed" : fmt(t.oos_loss)}</td>
      <td>{t.failed ? "—" : fmt(t.oos_accuracy)}</td>
    </tr>
  );
});

/** One activation-prerequisite check line. */
export const CheckItem = memo(function CheckItem({ c }: { c: ActivationCheckDto }) {
  return (
    <li className={classNames("pa-check", c.passed ? "ok" : "fail")}>
      <span className="pa-check-mark" aria-hidden="true">
        {c.passed ? "✓" : "✕"}
      </span>
      <span>
        <span className="pa-check-name">{c.name}</span>
        <span className="pa-check-detail"> — {c.detail}</span>
      </span>
    </li>
  );
});
