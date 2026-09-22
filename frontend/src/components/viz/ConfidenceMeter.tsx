/**
 * ConfidenceMeter — horizontal confidence meter for ONE strategy (list view).
 *
 * The dense counterpart to ConfidenceGauge: same value/tier semantics, laid
 * out as a single row so many strategies stack without vertical scrolling.
 * Track + fill + ticks; the label, score and tier sit beside the meter.
 *
 * null/absent value renders a dashed UNKNOWN track — never a fabricated 0.
 */

import { clampRatio } from "./geometry";
import { tierFor, toneForRatio, type ConfidenceTone } from "./ConfidenceGauge";
import "./confidence-gauge.css";

export interface ConfidenceMeterProps {
  value: number | null | undefined;
  label?: string;
  alias?: string | null;
  tone?: ConfidenceTone;
}

export function ConfidenceMeter({ value, label, alias, tone }: ConfidenceMeterProps) {
  const ratio = clampRatio(value);
  const toneCls = tone ?? toneForRatio(ratio);
  const tier = tierFor(ratio);
  const readout = ratio === null ? "—" : ratio.toFixed(2);
  const name = alias ?? label ?? null;

  return (
    <div className="cm-row" title={label ? `${label}: ${readout}` : `confidence ${readout}`}>
      <div className="cm-name">{name}</div>
      <div className="cm-track-wrap">
        <div className="cm-track">
          {ratio !== null && (
            <div className={`cm-fill ${toneCls}`} style={{ width: `${ratio * 100}%` }} />
          )}
        </div>
      </div>
      <div className="cm-read">
        <span className={`cm-score ${ratio === null ? "neu" : toneCls}`}>{readout}</span>
        <span className="cm-tier">{tier}</span>
      </div>
    </div>
  );
}
