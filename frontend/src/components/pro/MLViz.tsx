/**
 * MLViz — ML / model pro-visualization suite (LANE ml-viz, ALT-UI-PRO 3/5).
 *
 * Four presentation-only views for the alternative console:
 *   FeatureContributionChart  diverging |value| bars + backend status word
 *   ProbTriple                P(NO_TRADE)/P(BUY)/P(SELL) verbatim display
 *   LatencySplitBar           feature / model-forward / e2e stage split
 *   Shadow70Strip             champion-vs-shadow actions + disagreement chips
 *
 * TRUTH RULES (identical to the rest of this console):
 *   - Props in, DOM out. No fetch, no query keys, no timers, no derived state
 *     decisions — every number arrives already produced by the backend.
 *   - All geometry/ranking math lives in lib/mlVizMath (pure, unit-tested).
 *   - A missing value renders UNKNOWN ("—"/"UNKNOWN"), never 0 and never an
 *     inferred residual: an absent latency stage becomes a visible GAP.
 *   - Status/class strings are the backend's own words; colours reuse the
 *     existing `.badge good|warn|bad|neutral|unknown` semantics from
 *     styles/theme.css. Unknown words land in the unknown bucket.
 */

import type { ModelMeta, Probabilities, Shadow70Observation } from "@/types/domain";
import { EmptyState } from "@/components/primitives";
import {
  UNKNOWN,
  agreementRateText,
  disagreementTally,
  featureStatusTally,
  formatAgeSec,
  latencySplit,
  msText,
  probTriple,
  reasonCopy,
  shadowRows,
  statusText,
  tallyFromCounts,
  topNContributions,
  type VizContribution,
  type VizStatusTone,
} from "@/lib/mlVizMath";
import "./pro-ml.css";

const NO_VALUE = "—";

/** Bar/badge tone for a backend feature status (theme colours only). */
const TONE_CLASS: Record<VizStatusTone, string> = {
  ok: "ok",
  warn: "warn",
  bad: "bad",
  unknown: "unk",
};

function valueText(v: number | null): string {
  return v === null ? UNKNOWN : String(v);
}

// ---------------------------------------------------------------------------
// 1. Feature contribution chart (diverging bars, ranked by |value|)
// ---------------------------------------------------------------------------

export interface FeatureContributionChartProps {
  /** `EngineSnapshot.features` — {index,name,value,status} verbatim. */
  features:
    | Array<{ index?: number | null; name?: string | null; value?: number | null; status?: string | null }>
    | null
    | undefined;
  /** How many top bars to draw (backend list stays untouched beyond that). */
  topN?: number;
  /** `V1FeaturesStatus.missing_features` — backend-reported gaps. */
  missingFeatures?: readonly string[] | null;
  /** `ModelMeta.feature_dimension` — schema width, shown as coverage context. */
  featureDimension?: number | null;
  /** `V1FeaturesStatus.warmup_state` — displayed as a badge, never re-decided. */
  warmupState?: string | null;
  /** `DiagnosticsSection.features_age_sec`. */
  ageSec?: number | null;
  title?: string;
}

function ContributionRow({ row }: { row: VizContribution }) {
  const pct = Math.round(row.fraction * 100);
  const tone = TONE_CLASS[row.statusTone];
  const tip =
    `${row.label} · value ${valueText(row.value)} · status ${row.status}` +
    (row.index !== null ? ` · index ${row.index}` : "") +
    (row.missing ? " · reported missing by backend" : "");
  return (
    <div className={`mlv-contrib-row ${tone}`} title={tip}>
      <span className="mlv-contrib-name" aria-hidden="true">
        {row.label}
        {row.missing && <span className="mlv-flag">MISSING</span>}
      </span>
      <span className="mlv-contrib-track" role="img" aria-label={tip}>
        <span className="mlv-contrib-axis" />
        <i
          className={`mlv-contrib-bar ${row.direction}`}
          style={
            row.direction === "neg"
              ? { right: "50%", width: `${pct / 2}%` }
              : { left: "50%", width: `${pct / 2}%` }
          }
        />
      </span>
      <span className="mlv-contrib-val num">{valueText(row.value)}</span>
      <span className={`badge ${row.statusTone === "ok" ? "neutral" : row.statusTone === "bad" ? "bad" : row.statusTone === "warn" ? "warn" : "unknown"}`}>
        {row.status}
      </span>
    </div>
  );
}

export function FeatureContributionChart(props: FeatureContributionChartProps) {
  const topN = props.topN ?? 12;
  const rows = topNContributions(props.features, topN, { missingFeatures: props.missingFeatures });
  const total = Array.isArray(props.features) ? props.features.length : 0;
  const tally = featureStatusTally(props.features);
  const shown = rows.filter((r) => r.renderable).length;
  const dim = props.featureDimension ?? null;

  return (
    <div className="mlv mlv-contrib">
      <div className="mlv-head">
        <span className="mlv-title">{props.title ?? "Top feature contributions (|value|)"}</span>
        <span className="mlv-meta">
          <span title="features_age_sec (backend diagnostics)">age {formatAgeSec(props.ageSec ?? null)}</span>
          <span className={`badge ${props.warmupState ? "neutral" : "unknown"}`}>
            {statusText(props.warmupState)}
          </span>
        </span>
      </div>

      {total === 0 ? (
        <EmptyState
          message="No feature vector in the snapshot."
          hint="UNKNOWN is rendered on purpose — the console never draws a contribution chart it has no backend values for."
        />
      ) : (
        <>
          <div className="mlv-subline">
            <span>
              {shown} of {topN} ranked · {total} backend entries
              {dim !== null ? ` · schema ${dim}D` : ""}
            </span>
            <span className="mlv-tally">
              {Object.entries(tally)
                .sort((a, b) => b[1] - a[1])
                .map(([status, count]) => (
                  <span key={status} className="mlv-tally-item" title={`backend status ${status}`}>
                    {status} <b className="num">{count}</b>
                  </span>
                ))}
            </span>
          </div>
          <div className="mlv-contrib-list">
            {rows.map((r, i) => (
              <ContributionRow key={`${r.label}#${r.index ?? i}`} row={r} />
            ))}
          </div>
          <div className="mlv-note">
            Raw pipeline values (backend `features` payload) — a model input, not a
            gradient contribution; bars are |value| scaled to the largest shown value.
            NaN/Inf/UNAVAILABLE entries keep their backend status word and never render as 0.
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. Probability triple
// ---------------------------------------------------------------------------

export interface ProbTripleProps {
  /**
   * `EngineSnapshot.probs` (available flag + no_trade/buy/sell), or a
   * `PredictionRow["probabilities"]` triple via `variant="prediction"` — that
   * payload has no `available` flag, so the component derives availability as
   * "the backend sent at least one number" and nothing more.
   */
  probs: Partial<Probabilities> | null | undefined;
  /** Row variant: "prediction" derives availability from value presence. */
  variant?: "engine" | "prediction";
  /** Backend `inference_timestamp` / decision extras. */
  inferenceTimestamp?: string | null;
  /** `EngineSnapshot.ai_decision` verbatim. */
  decision?: string | null;
  /** `EngineSnapshot.ai_reason` verbatim (wording comes from lib/signal only). */
  reason?: string | null;
  /** `EngineSnapshot.ai_confidence` verbatim. */
  confidence?: number | null;
  ageSec?: number | null;
  title?: string;
}

export function ProbTriple(props: ProbTripleProps) {
  const payload: Probabilities =
    props.variant === "prediction"
      ? {
          available:
            props.probs !== null &&
            props.probs !== undefined &&
            (props.probs.no_trade !== null || props.probs.buy !== null || props.probs.sell !== null),
          no_trade: props.probs?.no_trade ?? null,
          buy: props.probs?.buy ?? null,
          sell: props.probs?.sell ?? null,
          inference_timestamp: props.probs?.inference_timestamp ?? null,
        }
      : {
          available: props.probs?.available === true,
          no_trade: props.probs?.no_trade ?? null,
          buy: props.probs?.buy ?? null,
          sell: props.probs?.sell ?? null,
          inference_timestamp: props.probs?.inference_timestamp ?? null,
        };
  const triple = probTriple(payload);
  const ts = props.inferenceTimestamp ?? triple.inferenceTimestamp ?? null;

  return (
    <div className="mlv mlv-prob">
      <div className="mlv-head">
        <span className="mlv-title">{props.title ?? "Probability triple (inference output)"}</span>
        <span className="mlv-meta">
          <span
            className={`badge ${payload.available ? "good" : "unknown"}`}
            title="probs.available — the backend's own availability flag"
          >
            {payload.available ? "AVAILABLE" : "UNKNOWN"}
          </span>
          <span title="diagnostics.inference_age_sec">age {formatAgeSec(props.ageSec ?? null)}</span>
        </span>
      </div>

      {!payload.available ? (
        <EmptyState
          message="probs.available = false — no live inference output."
          hint="Values would be fabricated, so they are withheld: warming up, guardian-blocked or engine stopped are backend states, not 0%."
        />
      ) : (
        <div className="probbar">
          {triple.rows.map((r) => (
            <div className="row" key={r.key} title={`${r.label} = ${r.valueText} (backend value verbatim)`}>
              <span className="lab">{r.label}</span>
              <span className="track" role="img" aria-label={`${r.label} ${r.valueText}`}>
                <i className={r.tone} style={{ width: `${r.fraction * 100}%` }} />
              </span>
              <span className="val">{r.pctText}</span>
              <span className="mlv-prob-raw num">{r.valueText}</span>
            </div>
          ))}
        </div>
      )}

      <dl className="kv mlv-kv">
        <dt>Σ probs</dt>
        <dd>
          {triple.sum === null
            ? NO_VALUE
            : `${triple.sum.toFixed(3)}${triple.sum > 1.0001 || triple.sum < 0.999 ? " · not renormalized" : ""}`}
        </dd>
        <dt>decision</dt>
        <dd>{statusText(props.decision)}</dd>
        <dt>confidence</dt>
        <dd>{props.confidence === null || props.confidence === undefined ? NO_VALUE : props.confidence}</dd>
        {props.reason !== null && props.reason !== undefined && (() => {
          // Wording is REUSED from lib/signal (exact-match REASONS); an unknown
          // code shows the code itself and produces no prose.
          const copy = reasonCopy(props.reason);
          return (
            <>
              <dt>reason</dt>
              <dd className="inline-mono" title={copy.detail}>
                {props.reason === "" ? NO_VALUE : copy.code}
              </dd>
              {copy.simple !== null && (
                <>
                  <dt>reason (plain)</dt>
                  <dd style={{ textAlign: "left" }} className="muted">{copy.simple}</dd>
                </>
              )}
            </>
          );
        })()}
        <dt>inference ts</dt>
        <dd>{ts ?? NO_VALUE}</dd>
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3. Inference latency split bar
// ---------------------------------------------------------------------------

export interface LatencySplitBarProps {
  /** `EngineSnapshot.model` (feature_ms / model_forward_ms / e2e_ms / latency_breakdown). */
  model: ModelMeta | null | undefined;
  title?: string;
}

/** Ordered headline stages: label + the mlVizMath key it reads. */
const HEADLINE_STAGES = [
  { key: "feature_ms", label: "features", short: "FEAT", stage: "T1→T2" },
  { key: "model_forward_ms", label: "model forward", short: "FWD", stage: "T5→T6" },
] as const;

export function LatencySplitBar(props: LatencySplitBarProps) {
  const split = latencySplit(props.model);
  const measured = HEADLINE_STAGES.map((s) => ({ ...s, value: split[s.key] }));
  const e2e = split.e2e_ms;
  const sumMeasured = measured.every((s) => s.value !== null)
    ? measured.reduce((a, s) => a + (s.value ?? 0), 0)
    : null;
  const unattributed = e2e !== null && sumMeasured !== null ? Math.max(0, e2e - sumMeasured) : null;

  const segWidth = (v: number | null) => (v === null || e2e === null || e2e <= 0 ? 0 : (v / e2e) * 100);

  return (
    <div className="mlv mlv-lat">
      <div className="mlv-head">
        <span className="mlv-title">{props.title ?? "Inference latency split (backend-measured stages)"}</span>
        <span className="mlv-meta">
          <span className="num mlv-lat-total" title="e2e_ms (T0→T10)">
            {e2e === null ? NO_VALUE : msText(e2e)}
          </span>
        </span>
      </div>

      {!split.hasData ? (
        <EmptyState
          message="No latency breakdown in model meta."
          hint="The tracer only emits a stage when both of its marks were recorded — a missing number is an unmeasured stage, not a 0 ms stage."
        />
      ) : (
        <>
          <div className="mlv-lat-track" role="img" aria-label={`e2e ${msText(e2e)}`}>
            {e2e === null ? (
              <span className="mlv-lat-gap" style={{ width: "100%" }} title="e2e_ms not reported by the backend">
                NO E2E BASIS
              </span>
            ) : (
              <>
                {measured.map((s) =>
                  s.value === null ? (
                    <span
                      key={s.key}
                      className="mlv-lat-gap"
                      style={{ width: "12%" }}
                      title={`${s.label}: ${s.stage} not reported by the backend — drawn as a gap, never as 0`}
                    >
                      {s.short} —
                    </span>
                  ) : (
                    <span
                      key={s.key}
                      className={`mlv-lat-seg ${s.key}`}
                      style={{ width: `${segWidth(s.value)}%` }}
                      title={`${s.label} (${s.stage}) = ${msText(s.value)}`}
                    >
                      {s.short} {msText(s.value)}
                    </span>
                  ),
                )}
                {unattributed !== null && unattributed > 0 && (
                  <span
                    className="mlv-lat-seg unattributed"
                    style={{ width: `${segWidth(unattributed)}%` }}
                    title={`e2e_ms minus reported stages (scaling/tensor/post/decision/queue not individually reported) = ${msText(unattributed)} — arithmetic residual, not a measured stage`}
                  >
                    other {msText(unattributed)}
                  </span>
                )}
              </>
            )}
          </div>

          <dl className="kv mlv-kv">
            {measured.map((s) => (
              <div key={s.key} style={{ display: "contents" }}>
                <dt title={`${s.stage}`}>
                  {s.label} <span className="muted small">{s.stage}</span>
                </dt>
                <dd className={s.value === null ? "mlv-missing" : undefined}>{msText(s.value)}</dd>
              </div>
            ))}
            <div style={{ display: "contents" }}>
              <dt title="T0→T10">end-to-end</dt>
              <dd>{msText(e2e)}</dd>
            </div>
            {split.latency_ms !== null && (
              <div style={{ display: "contents" }}>
                <dt>latency_ms (flattened)</dt>
                <dd>{msText(split.latency_ms)}</dd>
              </div>
            )}
            {split.stages.map((st) => (
              <div key={st.key} style={{ display: "contents" }}>
                <dt>{st.key.replace(/_ms$/, "")}</dt>
                <dd>{msText(st.value)}</dd>
              </div>
            ))}
          </dl>

          {split.partial && (
            <div className="mlv-note">
              Partial breakdown: at least one stage was not reported, so the bar shows a gap
              instead of guessing where the missing time went.
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 4. 70D shadow-vs-champion disagreement strip
// ---------------------------------------------------------------------------

export interface Shadow70StripProps {
  /** `Shadow70State.store.recent_observations`. */
  observations: readonly Shadow70Observation[] | null | undefined;
  /** `Shadow70State.store.disagreement_counts` (backend histogram). */
  counts?: Record<string, number> | null;
  /** `Shadow70State.runtime.state` verbatim (lifecycle badge). */
  runtimeState?: string | null;
  /** `Shadow70State.available` — false renders the UNKNOWN block. */
  available?: boolean;
  /** Max rows drawn (newest-first as sent by the backend). */
  limit?: number;
  title?: string;
}

export function Shadow70Strip(props: Shadow70StripProps) {
  const list = Array.isArray(props.observations) ? props.observations : [];
  const rows = shadowRows(list, props.limit ?? 12);
  const observed = disagreementTally(list);
  const backendTally = props.counts ? tallyFromCounts(props.counts) : null;
  const headline = backendTally ?? observed;

  if (props.available === false || (list.length === 0 && !backendTally)) {
    return (
      <div className="mlv mlv-s70">
        <div className="mlv-head">
          <span className="mlv-title">{props.title ?? "70D shadow vs champion"}</span>
          <span className="mlv-meta">
            <span className={`badge ${props.runtimeState ? "neutral" : "unknown"}`}>
              {statusText(props.runtimeState)}
            </span>
          </span>
        </div>
        <EmptyState
          message="No 70D shadow observations from the backend."
          hint="UNKNOWN — the 70D model is never claimed healthy, agreeing, or disagreeing without real shadow70_observations rows."
        />
      </div>
    );
  }

  return (
    <div className="mlv mlv-s70">
      <div className="mlv-head">
        <span className="mlv-title">{props.title ?? "70D shadow vs champion"}</span>
        <span className="mlv-meta">
          <span className={`badge ${props.runtimeState ? "neutral" : "unknown"}`} title="Shadow70 runtime state (backend)">
            {statusText(props.runtimeState)}
          </span>
          <span className="num" title="valid observations in this payload">
            {headline.total} rows · agreement {agreementRateText(headline)}
          </span>
        </span>
      </div>

      <div className="mlv-chips">
        {headline.buckets.map((b) => (
          <span
            key={b.class}
            className={`badge ${b.level} mlv-chip`}
            title={`backend disagreement class ${b.class} — ${b.count} row(s)`}
          >
            {b.class} <b className="num">{b.count}</b>
          </span>
        ))}
        {headline.buckets.length === 0 && <span className="badge unknown mlv-chip">UNKNOWN</span>}
      </div>

      {rows.length === 0 ? (
        <div className="mlv-note">Backend histogram present, no recent rows in this payload.</div>
      ) : (
        <div className="mlv-strip">
          {rows.map((r) => (
            <div
              key={r.observationId}
              className={`mlv-strip-row ${r.level} ${r.actionDiffers ? "differs" : ""}`}
              title={[
                `observation ${r.observationId}`,
                `champion ${r.championAction} @ ${r.championConfidence ?? UNKNOWN}`,
                `shadow ${r.shadowAction} @ ${r.shadowConfidence ?? UNKNOWN}`,
                `disagreement ${r.disagreement}`,
                `regime ${r.regime} · news ${r.newsState} · liquidity ${r.liquidityState} · outcome ${r.outcome}`,
              ].join("\n")}
            >
              <span className="mlv-strip-time num">{r.timeText}</span>
              <span className="mlv-strip-pair">
                <span className="mlv-act champ" title="champion_action (backend)">
                  {r.championAction}
                  <em className="num">{r.championConfidence ?? UNKNOWN}</em>
                </span>
                <span className="mlv-strip-arrow" aria-hidden="true">
                  {r.actionDiffers ? "≠" : "="}
                </span>
                <span className="mlv-act shad" title="shadow_action (backend)">
                  {r.shadowAction}
                  <em className="num">{r.shadowConfidence ?? UNKNOWN}</em>
                </span>
              </span>
              <span className={`badge ${r.level}`} title="disagreement class (backend verbatim)">
                {r.disagreement}
              </span>
              <span className="mlv-strip-ctx" title="regime / news / liquidity / outcome (backend fields)">
                {r.regime} · {r.newsState} · {r.liquidityState} · {r.outcome}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="mlv-note">
        Chip colours reuse the console&apos;s badge semantics; class names are the backend&apos;s
        DisagreementClass strings verbatim. Rows shown: {rows.length} of {list.length} in payload —
        agreement rate counts only what the backend classified.
      </div>
    </div>
  );
}
