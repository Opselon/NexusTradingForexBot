/**
 * ML — model / 70D monitoring (feature-grade rebuild).
 *
 * Health claims come ONLY from backend verdicts:
 *  - /api/models/integrity: semantic integrity (tensors/scaler/compatibility).
 *    An artifact existing is NOT health — integrity VALID/INVALID is decided
 *    by the backend champion inspector.
 *  - /api/v1/model/status: serving bundle + warmup + inference enablement.
 *  - /api/v1/features/status: warmup + missing features.
 *  - /api/models/shadow70/summary: 70D shadow runtime/store/worker state.
 *  - /api/v1/shadow/status|runs|70d: shadow-comparison pipeline (60D store),
 *    run inventory, drift alerts + feature health.
 *  - /api/operator/calibration: identity-bound calibration evidence
 *    (splits, deficits, ECE/Brier, the risk multiplier the RiskEngine applies).
 *
 * The 50D↔70D panel splits the effective feature vector by the CANONICAL
 * family layout the backend publishes (BASE 0..49 | FAMILY/NEWS 50..59 |
 * LIQUIDITY 60..69) — index ranges, not guesses about names.
 */

import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { mlApi } from "@/api/mlApi";
import { operatorApi, shadowApi } from "@/pages/_shared/edgeApi";
import type { EngineSnapshot } from "@/types/domain";
import {
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  ProbBar,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { AgeNote, SectionState, errorText, fmtAge, TriBadge } from "@/pages/_shared/SectionState";
import { useI18n } from "@/stores/i18nStore";
import { InfoChip, SortableTable, type Column } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { formatNumber, formatPct } from "@/lib/format";
import { ApiError } from "@/types/api";
import "@/pages/_shared/pages.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

/** Static style objects hoisted out of render — the values never change, so
 *  React gets a stable object reference instead of a fresh literal per tick. */
const NOTE_STYLE_TIGHT: CSSProperties = { marginTop: 4 };
const NOTE_STYLE_CAL: CSSProperties = { marginTop: 6 };

/**
 * Stable React key for one untyped drift-alert row (`Record<string, unknown>`,
 * no id field in the payload). Built from the row's own identity fields with a
 * collision counter, so a reordered page of alerts reconciles by identity
 * instead of by array position — and two identical records still never share
 * a key. Pure function of the row list; the rendered cells are untouched.
 */
function driftAlertKey(rows: readonly Record<string, unknown>[], i: number): string {
  const base = (r: Record<string, unknown>): string =>
    `${String(r.timestamp ?? r.ts ?? "")}|${String(r.feature ?? r.name ?? "")}|${String(r.reason ?? r.kind ?? "")}`;
  const key = base(rows[i] ?? {});
  let dup = 0;
  for (let j = 0; j < i; j++) if (base(rows[j] ?? {}) === key) dup++;
  return dup === 0 ? key : `${key}#${dup}`;
}

/**
 * Verdict cell for a 70D shadow observation (BUG-278).
 *
 * The backend now reports the disagreement class only for rows that actually
 * compared a champion decision against a real shadow inference; rows that
 * never compared anything carry their error code instead. Those render as a
 * muted error chip so the table can never imply a shadow trade decision that
 * never happened.
 */
function VerdictChip({ disagreement, valid }: { disagreement: string; valid?: boolean }) {
  const t = useI18n((s) => s.t);
  const compared = valid !== false && disagreement && !disagreement.startsWith("SHADOW_") && disagreement !== "NOT_COMPARED";
  if (!disagreement) return <span className="tiny">—</span>;
  if (compared) return <span className="tiny">{disagreement}</span>;
  return (
    <span className="tiny" title={t("ml.verdict.tip", "No shadow inference ran for this tick — the shadow model was not attached or the 70D vector was rejected. This is not a trade decision.")}>
      {disagreement}
    </span>
  );
}

/** Column set for the recent-70D-observations table. perf: built ONCE at
 *  module scope (9 sortValue/render closures) instead of re-allocated on every
 *  render of a 15s-refreshed panel — keys, labels and cell output unchanged. */
const OBSERVATION_COLUMN_KEYS: Array<{ key: string; fallback: string; num?: boolean }> = [
  { key: "t", fallback: "Time" },
  { key: "c", fallback: "Champion" },
  { key: "s", fallback: "Shadow" },
  { key: "conf", fallback: "Conf C/S", num: true },
  { key: "dis", fallback: "Disagreement" },
  { key: "rg", fallback: "Regime" },
  { key: "nw", fallback: "News" },
  { key: "liq", fallback: "Liquidity" },
  { key: "out", fallback: "Outcome" },
];

const OBSERVATION_LABEL_OF: Record<string, string> = { t: "ml.obs.time", c: "ml.obs.champion", s: "ml.obs.shadow", conf: "ml.obs.conf", dis: "ml.obs.disagreement", rg: "ml.obs.regime", nw: "ml.obs.news", liq: "ml.obs.liquidity", out: "ml.obs.outcome" };

const OBSERVATION_RENDER: Record<string, (o: Record<string, any>) => ReactNode> = {
  t: (o) => o.timestamp.slice(0, 19),
  c: (o) => o.champion_action,
  s: (o) => <span className={`l4-chip ${o.shadow_action !== o.champion_action ? "warn" : ""}`}>{o.shadow_action}</span>,
  conf: (o) => `${formatNumber(o.champion_confidence)} / ${formatNumber(o.shadow_confidence)}`,
  dis: (o) => <VerdictChip disagreement={o.disagreement} valid={o.valid} />,
  rg: (o) => o.regime || "—",
  nw: (o) => o.news_state || "—",
  liq: (o) => o.liquidity_state || "—",
  out: (o) => o.outcome,
};

const OBSERVATION_SORT: Record<string, (o: Record<string, any>) => string | number | null> = {
  t: (o) => o.timestamp,
  c: (o) => o.champion_action,
  s: (o) => o.shadow_action,
  conf: (o) => o.champion_confidence,
  dis: (o) => o.disagreement,
  rg: (o) => o.regime,
  nw: (o) => o.news_state,
  liq: (o) => o.liquidity_state,
  out: (o) => o.outcome,
};

const OBSERVATION_FILTER = (o: Record<string, any>, q: string): boolean =>
  o.champion_action.toLowerCase().includes(q) ||
  o.shadow_action.toLowerCase().includes(q) ||
  (o.regime ?? "").toLowerCase().includes(q);

/** Canonical 70D family blocks (backend schema_contract / liquidity_runtime). */
const FAMILY_BLOCKS = [
  { id: "base", key: "ml.family.base", fallback: "BASE 0–49 (scalp_v1 protected)", from: 0, to: 49 },
  { id: "family", key: "ml.family.family_news", fallback: "FAMILY/NEWS 50–59", from: 50, to: 59 },
  { id: "liquidity", key: "ml.family.liquidity", fallback: "LIQUIDITY 60–69 (70D only)", from: 60, to: 69 },
] as const;

function featureValue(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 10) return v.toFixed(2);
  return v.toFixed(3);
}

function FeatureBlock({
  label,
  hint,
  features,
}: {
  label: string;
  hint: string;
  features: EngineSnapshot["features"];
}) {
  const t = useI18n((s) => s.t);
  if (features.length === 0) {
    return (
      <div>
        <div className="section-title">{label}</div>
        <EmptyState message={t("ml.feat.empty", "No features in this block.")} hint={hint} />
      </div>
    );
  }
  const valid = features.filter((f) => (f.status ?? "").toUpperCase() === "VALID").length;
  const nan = features.filter((f) => (f.status ?? "").toUpperCase() === "NAN").length;
  const unavail = features.length - valid - nan;
  return (
    <div>
      <div className="section-title">
        {label}{" "}
        <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>
          {t("ml.feat.slots", "· {n} slots", { n: features.length })}
        </span>
      </div>
      <div className="l4-chip-row" style={{ marginBlockEnd: 6 }}>
        <span className="l4-chip good">{valid} {t("ml.feat.valid", "VALID")}</span>
        <span className={`l4-chip ${nan ? "warn" : ""}`}>{nan} {t("ml.feat.nan", "NAN")}</span>
        <span className={`l4-chip ${unavail ? "" : ""}`}>{unavail} {t("ml.feat.unavailable", "UNAVAILABLE")}</span>
      </div>
      <div tabIndex={0} className="l4-features" style={{ maxBlockSize: 210 }}>
        {features.map((f) => (
          <div key={`${f.index}-${f.name}`} className={`l4-feature ${(f.status ?? "").toUpperCase() === "VALID" ? "" : (f.status ?? "").toUpperCase() === "NAN" ? "nan" : "unavailable"}`} title={`${f.name} · ${f.status}`}>
            <div className="n">{f.index} {f.name}</div>
            <div className="v">{featureValue(f.value)}</div>
          </div>
        ))}
      </div>
      <div className="l4-note" style={NOTE_STYLE_TIGHT}>{hint}</div>
    </div>
  );
}

export default function MLPage({ snapshot }: Props) {
  const t = useI18n((s) => s.t);
  const [runsPage, setRunsPage] = useState(1);

  const integrityQuery = useQuery({
    queryKey: ["model-integrity"],
    queryFn: ({ signal }) => mlApi.integrity(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const statusQuery = useQuery({
    queryKey: ["model-status"],
    queryFn: ({ signal }) => mlApi.modelStatus(signal),
    refetchInterval: 10_000,
    retry: false,
  });
  const identityQuery = useQuery({
    queryKey: ["model-identity"],
    queryFn: ({ signal }) => mlApi.modelIdentity(signal),
    refetchInterval: 60_000,
    retry: false,
  });
  const featuresQuery = useQuery({
    queryKey: ["features-status"],
    queryFn: ({ signal }) => mlApi.featuresStatus(signal),
    refetchInterval: 10_000,
    retry: false,
  });
  const shadow70Query = useQuery({
    queryKey: ["shadow70"],
    queryFn: ({ signal }) => mlApi.shadow70(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const shadow70V1Query = useQuery({
    queryKey: ["shadow70-v1"],
    queryFn: ({ signal }) => shadowApi.shadow70(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const shadowStatusQuery = useQuery({
    queryKey: ["shadow-status"],
    queryFn: ({ signal }) => shadowApi.status(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const shadowRunsQuery = useQuery({
    queryKey: ["shadow-runs", runsPage],
    queryFn: ({ signal }) => shadowApi.runs(runsPage, 15, signal),
    placeholderData: (prev) => prev,
    retry: false,
  });
  const calibrationQuery = useQuery({
    queryKey: ["operator-calibration"],
    queryFn: ({ signal }) => operatorApi.calibration(signal),
    refetchInterval: 60_000,
    retry: false,
  });

  const integ = integrityQuery.data;
  const integrityLevel =
    integ?.state === "ACTIVE" ? "good" : integ?.state === "INCOMPATIBLE" || integ?.state === "INVALID" ? "bad" : integ?.state === "NO_CHAMPION" || integ?.state === "UNAVAILABLE" ? "warn" : "unknown";
  const s70 = shadow70Query.data;
  // perf: the observations window (slice + sort/filter inputs for the shared
  // table) derives once per shadow70 payload, not on every render.
  const observations = useMemo(
    () => (s70?.store?.recent_observations ?? []).slice(0, 25),
    [s70?.store?.recent_observations],
  );
  const features = snapshot?.features ?? [];

  const blocks = useMemo(
    () =>
      FAMILY_BLOCKS.map((b) => ({
        ...b,
        label: t(b.key, b.fallback),
        rows: features.filter((f) => f.index >= b.from && f.index <= b.to),
      })),
    [features, t],
  );
  const dim = snapshot?.model.feature_dimension ?? features.length ?? null;
  const is70 = dim === 70;

  const runCols = useMemo<Array<Column<Record<string, unknown>>>>(
    () => [
      { key: "run", label: t("ml.th.run", "Run id"), sortValue: (r) => (typeof r.run_id === "string" ? r.run_id : null), render: (r) => <span className="small">{String(r.run_id ?? "—")}</span> },
      { key: "status", label: t("ml.th.status", "Status"), sortValue: (r) => (typeof r.status === "string" ? r.status : null), render: (r) => <StatusBadge status={String(r.status ?? null)} /> },
      { key: "champ", label: t("ml.th.champion", "Champion"), sortValue: (r) => (typeof r.champion_model === "string" ? r.champion_model : null), render: (r) => String(r.champion_model ?? "—") },
      { key: "shadow", label: t("ml.th.shadow", "Shadow"), sortValue: (r) => (typeof r.shadow_model === "string" ? r.shadow_model : null), render: (r) => String(r.shadow_model ?? "—") },
      { key: "started", label: t("ml.th.started", "Started"), sortValue: (r) => (typeof r.started_at === "string" ? r.started_at : null), render: (r) => String(r.started_at ?? "—") },
      { key: "finished", label: t("ml.th.finished", "Finished"), sortValue: (r) => (typeof r.finished_at === "string" ? r.finished_at : null), render: (r) => String(r.finished_at ?? "—") },
    ],
    [t],
  );

  return (
    <div>
      <div className="grid cols-4">
        <MetricCard
          label={t("ml.kpi.integrity", "Model integrity (backend verdict)")}
          value={integrityQuery.isPending ? "…" : integrityQuery.isError ? "ERROR" : (integ?.state ?? "UNKNOWN")}
          tone={integrityLevel === "good" ? "pos" : integrityLevel === "bad" ? "neg" : "dim"}
          sub={
            integ?.reason ??
            (integ?.state === "ACTIVE"
              ? t("ml.kpi.integrity_ok", "champion valid + serving")
              : t("ml.kpi.integrity_fallback", "backend decides — not inferred from artifact presence"))
          }
        />
        <MetricCard
          label={t("ml.kpi.serving", "Serving bundle")}
          value={statusQuery.data ? (statusQuery.data.bundle_loaded ? "LOADED" : "NOT LOADED") : "—"}
          tone={statusQuery.data?.bundle_loaded ? "pos" : "dim"}
          sub={t("ml.kpi.serving_sub", "inference {i} · warmup {w}", {
            i: statusQuery.data?.inference_enabled ? "ENABLED" : "BLOCKED",
            w: statusQuery.data?.warmup_state ?? "—",
          })}
        />
        <MetricCard
          label={t("ml.kpi.schema", "Effective schema")}
          value={snapshot?.model.feature_schema_id ?? "—"}
          tone="dim"
          sub={t("ml.kpi.schema_sub", "{d}D {family} · scaler {s}", {
            d: dim ?? "?",
            family: is70 ? t("ml.st.70d", "(70D liquidity)") : dim === 50 ? t("ml.st.50d", "(50D base)") : "",
            s: snapshot?.model.scaler_ready === null || snapshot?.model.scaler_ready === undefined
              ? "—"
              : snapshot.model.scaler_ready
                ? "READY"
                : "NOT FITTED",
          })}
        />
        <MetricCard
          label={t("ml.kpi.latency", "Inference latency")}
          value={snapshot?.model.latency_ms === null || snapshot?.model.latency_ms === undefined ? "—" : `${snapshot.model.latency_ms.toFixed(1)} ms`}
          tone="dim"
          sub={
            snapshot?.model.latency_breakdown
              ? t("ml.kpi.latency_sub", "fwd {a} · feat {b} · e2e {c}", {
                  a: snapshot.model.latency_breakdown.model_forward_ms ?? "—",
                  b: snapshot.model.latency_breakdown.feature_ms ?? "—",
                  c: snapshot.model.latency_breakdown.e2e_ms ?? "—",
                })
              : t("ml.kpi.latency_fallback", "backend-measured only")
          }
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title={t("ml.panel.probs", "Live probabilities (engine)")} accent right={<AgeNote label={t("ml.age.inference", "inference age")} ageSec={snapshot?.diagnostics.inference_age_sec} />}>
          {snapshot?.probs.available ? (
            <ProbBar
              rows={[
                { label: "P(NO_TRADE)", value: snapshot.probs.no_trade, tone: "flat" },
                { label: "P(BUY)", value: snapshot.probs.buy, tone: "buy" },
                { label: "P(SELL)", value: snapshot.probs.sell, tone: "sell" },
              ]}
            />
          ) : (
            <EmptyState
              message={t("ml.probs.empty", "No live inference yet (model warming up or engine stopped).")}
              hint={t("ml.probs.empty_hint", "probs.available=false — not rendered as zeros.")}
            />
          )}
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>{t("ml.probs.decision", "decision")}</dt>
            <dd>{snapshot?.ai_decision ?? "—"}</dd>
            <dt>{t("ml.probs.confidence", "confidence")}</dt>
            <dd>{snapshot?.ai_confidence === null || snapshot?.ai_confidence === undefined ? "—" : formatPct(snapshot.ai_confidence * 100, 1)}</dd>
            <dt>{t("ml.probs.feature_age", "feature age")}</dt>
            <dd>{fmtAge(snapshot?.diagnostics.features_age_sec)}</dd>
            <dt>{t("ml.probs.inference_stamp", "inference stamp")}</dt>
            <dd className="small">{snapshot?.probs.inference_timestamp ?? "—"}</dd>
          </dl>
        </Panel>

        <Panel title={t("ml.panel.identity", "Artifact identity (manifest)")} right={<span className="timestamp-note">/api/v1/model/identity</span>}>
          {identityQuery.isPending ? (
            <Skeleton count={4} />
          ) : identityQuery.isError ? (
            <ErrorState message={errorText(identityQuery.error, t("ml.err.identity", "Identity endpoint failed."))} requestId={identityQuery.error instanceof ApiError ? identityQuery.error.requestId : null} onRetry={() => void identityQuery.refetch()} />
          ) : identityQuery.data?.available === false ? (
            <EmptyState
              message={identityQuery.data.reason ?? t("ml.identity.empty", "No model bundle loaded.")}
              hint={t("ml.identity.empty_hint", "Identity is absent, not invalid — the backend said so.")}
            />
          ) : identityQuery.data ? (
            <dl className="kv">
              <dt>model_id</dt>
              <dd>{identityQuery.data.model_id ?? "—"}</dd>
              <dt>artifact_id</dt>
              <dd className="small">{identityQuery.data.artifact_id ?? "—"}</dd>
              <dt>schema_id</dt>
              <dd>{identityQuery.data.schema_id ?? "—"}</dd>
              <dt>{t("ml.identity.version", "version")}</dt>
              <dd>{identityQuery.data.version ?? "—"}</dd>
              <dt>{t("ml.identity.created", "created")}</dt>
              <dd className="small">{identityQuery.data.created_at ?? "—"}</dd>
              <dt>{t("ml.identity.schema_hash", "schema hash")}</dt>
              <dd className="small">{identityQuery.data.feature_schema_hash ? `${identityQuery.data.feature_schema_hash.slice(0, 16)}…` : "—"}</dd>
              <dt>{t("ml.identity.serving_champion", "serving champion")}</dt>
              <dd>{snapshot?.model.model_id ?? "—"}</dd>
            </dl>
          ) : null}
        </Panel>
      </div>

      {/* Feature pipeline + integrity dimensions */}
      <div className="grid cols-2">
        <Panel title={t("ml.panel.pipeline", "Feature pipeline")} right={<AgeNote label={t("ml.age.age", "age")} ageSec={featuresQuery.dataUpdatedAt ? (Date.now() - featuresQuery.dataUpdatedAt) / 1000 : null} />}>
          <SectionState
            query={featuresQuery}
            emptyMessage={t("ml.pipe.empty", "Feature status unavailable.")}
            emptyHint={t("ml.pipe.empty_hint", "features/status endpoint returned nothing — warmup state unknown.")}
            errorFallback={t("ml.err.feature_status", "Feature status endpoint failed.")}
            emptyWhen={() => false}
          >
            {(fs) => (
              <dl className="kv">
                <dt>{t("ml.pipe.warmup", "warmup")}</dt>
                <dd><StatusBadge status={fs.warmup_state} /></dd>
                <dt>{t("ml.pipe.inference", "inference")}</dt>
                <dd><TriBadge value={fs.inference_enabled} on="ENABLED" off="BLOCKED" /></dd>
                <dt>{t("ml.pipe.last_vector", "last vector")}</dt>
                <dd><TriBadge value={fs.last_vector_available} on="AVAILABLE" off="NONE" /></dd>
                <dt>{t("ml.pipe.missing", "missing features")}</dt>
                <dd>{fs.missing_features.length === 0 ? "—" : <span className="pnl-neg">{fs.missing_features.join(", ")}</span>}</dd>
                <dt>{t("ml.pipe.probed", "probed")}</dt>
                <dd className="small">{fs.probed_at}</dd>
              </dl>
            )}
          </SectionState>
          <div style={{ marginBlockStart: 10 }}>
            <SectionState
              query={integrityQuery}
              emptyMessage={t("ml.integ.empty", "Integrity verdict unavailable.")}
              errorFallback={t("ml.err.integrity", "Integrity endpoint failed.")}
              emptyWhen={() => false}
            >
              {(iv) => (
                <dl className="kv">
                  <dt>{t("ml.integ.declared_dim", "declared dim")}</dt>
                  <dd>{iv.feature_dimension ?? "—"}</dd>
                  <dt>{t("ml.integ.actual_input", "actual input dim")}</dt>
                  <dd className={(iv.actual_input_dimension !== null && iv.feature_dimension !== null && iv.actual_input_dimension !== iv.feature_dimension) ? "pnl-neg" : undefined}>{iv.actual_input_dimension ?? "—"}</dd>
                  <dt>{t("ml.integ.actual_classes", "actual classes")}</dt>
                  <dd>{iv.actual_output_classes ?? "—"}</dd>
                  <dt>{t("ml.integ.scaler_dim", "scaler dim")}</dt>
                  <dd>{iv.scaler_dimension ?? "—"}</dd>
                  <dt>{t("ml.integ.compatibility", "compatibility")}</dt>
                  <dd><StatusBadge status={iv.compatibility ?? null} /></dd>
                </dl>
              )}
            </SectionState>
          </div>
        </Panel>

        {/* 50D vs 70D family panels */}
        <Panel
          title={t("ml.panel.families", "Feature families ({d}D effective)", { d: dim ?? "?" })}
          right={<span className="timestamp-note">{t("ml.families.layout", "BASE 0–49 · FAMILY 50–59 · LIQUIDITY 60–69 (backend layout)")}</span>}
        >
          {features.length === 0 ? (
            <EmptyState
              message={t("ml.families.empty", "No feature vector published yet.")}
              hint={t("ml.families.empty_hint", "snapshot.features is empty — nothing to split by family.")}
            />
          ) : (
            <div style={{ display: "grid", gap: 12 }}>
              {blocks.map((b) => (
                <FeatureBlock
                  key={b.id}
                  label={b.label}
                  features={b.rows}
                  hint={
                    b.id === "liquidity"
                      ? is70
                        ? t("ml.families.liquidity_present", "Present: the 70D bundle is serving and the liquidity block is populated.")
                        : t("ml.families.liquidity_absent", "Absent by contract: the 50D runtime (scalp_v1) does not carry the liquidity block.")
                      : b.id === "family"
                        ? t("ml.families.news_hint", "News/family indices — protected 50D core is untouched by the 70D extension.")
                        : t("ml.families.core_hint", "The protected scalp_v1 core, identical in 50D and 70D.")
                  }
                />
              ))}
            </div>
          )}
        </Panel>
      </div>

      {/* Calibration evidence */}
      <Panel
        title={t("ml.panel.calibration", "Calibration evidence (identity-bound)")}
        right={
          <>
            <span className="timestamp-note">/api/operator/calibration</span>
            <button aria-label={t("ml.cal.refresh_aria", "Refresh calibration")} className="btn small ghost" onClick={() => void calibrationQuery.refetch()} disabled={calibrationQuery.isFetching}>⟳</button>
          </>
        }
      >
        {calibrationQuery.isPending && !calibrationQuery.data ? (
          <Skeleton count={4} />
        ) : calibrationQuery.isError ? (
          <ErrorState message={errorText(calibrationQuery.error, t("ml.err.calibration", "Calibration endpoint failed."))} requestId={calibrationQuery.error instanceof ApiError ? calibrationQuery.error.requestId : null} onRetry={() => void calibrationQuery.refetch()} />
        ) : calibrationQuery.data?.available === false ? (
          <EmptyState
            message={t("ml.cal.empty", "Calibration monitor unavailable.")}
            hint={t("ml.cal.empty_hint", "The backend reported available:false — no numbers are inferred.")}
          />
        ) : calibrationQuery.data ? (
          <>
            <div className="grid cols-4">
              <MetricCard
                label={t("ml.cal.status", "Calibration status")}
                value={calibrationQuery.data.calibration_status ?? "—"}
                tone={calibrationQuery.data.calibration_status === "CALIBRATED" ? "pos" : "dim"}
                sub={t("ml.cal.artifact_sub", "artifact {v}", { v: calibrationQuery.data.artifact_status ?? "—" })}
              />
              <MetricCard
                label={t("ml.cal.collector", "Collector")}
                value={calibrationQuery.data.collector_status ?? "—"}
                tone={calibrationQuery.data.collector_status === "COLLECTED" ? "pos" : "dim"}
                sub={t("ml.cal.needs_sub", "needs {v} per split", { v: calibrationQuery.data.required_per_split ?? "?" })}
              />
              <MetricCard
                label={t("ml.cal.ece", "ECE / Brier")}
                value={`${calibrationQuery.data.ece === null || calibrationQuery.data.ece === undefined ? "—" : Number(calibrationQuery.data.ece).toFixed(4)} / ${calibrationQuery.data.brier === null || calibrationQuery.data.brier === undefined ? "—" : Number(calibrationQuery.data.brier).toFixed(4)}`}
                tone="dim"
                sub={t("ml.cal.ece_sub", "expected calibration error / Brier score")}
              />
              <MetricCard
                label={t("ml.cal.risk_multiplier", "Risk multiplier")}
                value={calibrationQuery.data.risk_multiplier === null || calibrationQuery.data.risk_multiplier === undefined ? "—" : formatNumber(calibrationQuery.data.risk_multiplier, 3)}
                tone="dim"
                sub={t("ml.cal.risk_sub", "floor {v} — the value the RiskEngine applies", {
                  v: calibrationQuery.data.min_risk_multiplier_floor ?? "—",
                })}
              />
            </div>
            <dl className="kv" style={{ marginBlockStart: 10 }}>
              <dt>{t("ml.cal.serving_fingerprint", "serving fingerprint")}</dt>
              <dd>{calibrationQuery.data.serving_fingerprint ?? "—"}</dd>
              <dt>{t("ml.cal.artifact_mtime", "artifact mtime")}</dt>
              <dd className="small">{calibrationQuery.data.serving_artifact_mtime ?? "—"}</dd>
              <dt>{t("ml.cal.matches_serving", "matches serving")}</dt>
              <dd><TriBadge value={calibrationQuery.data.matches_serving} on="MATCH" off="MISMATCH" /></dd>
              <dt>{t("ml.cal.split", "calibration / validation split")}</dt>
              <dd>{t("ml.cal.split_line", "{a} / {b} (of {n} eligible)", {
                a: calibrationQuery.data.calibration_split ?? "—",
                b: calibrationQuery.data.validation_split ?? "—",
                n: calibrationQuery.data.total_eligible ?? "—",
              })}</dd>
              <dt>{t("ml.cal.deficit", "deficit")}</dt>
              <dd>{Object.entries(calibrationQuery.data.deficit ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</dd>
              <dt>{t("ml.cal.excluded", "excluded rows")}</dt>
              <dd className="small">{Object.entries(calibrationQuery.data.excluded ?? {}).map(([k, v]) => `${k}:${v}`).join(" · ") || "—"}</dd>
              <dt>{t("ml.cal.oos", "OOS cutoff")}</dt>
              <dd className="small">{calibrationQuery.data.oos_cutoff ?? "—"}</dd>
            </dl>
            <div className="l4-note" style={NOTE_STYLE_CAL}>
              {t(
                "ml.cal.note",
                "Eligibility, fingerprint binding and the 60/40 chronological split are computed by the backend monitor; this panel only displays them. An INSUFFICIENT_EVIDENCE collector status is an honest gap, not a failure of the model.",
              )}
            </div>
          </>
        ) : null}
      </Panel>

      {/* 70D shadow runtime */}
      <div className="grid cols-2">
        <Panel
          title={t("ml.panel.shadow70", "70D shadow runtime")}
          right={s70?.runtime?.state ? <StatusBadge status={String(s70.runtime.state)} /> : <span className="l4-chip">/api/models/shadow70/summary</span>}
        >
          {shadow70Query.isPending ? (
            <Skeleton count={4} />
          ) : shadow70Query.isError ? (
            <ErrorState message={errorText(shadow70Query.error, t("ml.err.shadow70", "Shadow70 endpoint failed."))} requestId={shadow70Query.error instanceof ApiError ? shadow70Query.error.requestId : null} onRetry={() => void shadow70Query.refetch()} />
          ) : !s70 || s70.available === false ? (
            <EmptyState
              message={t("ml.s70.empty", "Shadow70 subsystem unavailable (worker not attached or engine offline).")}
              hint={t("ml.s70.empty_hint", "Rendered as UNKNOWN — the UI never claims the 70D model is healthy without backend evidence.")}
            />
          ) : (
            <dl className="kv">
              <dt>{t("ml.s70.runtime_state", "runtime state")}</dt>
              <dd><StatusBadge status={String(s70.runtime?.state ?? null)} /></dd>
              <dt>{t("ml.s70.load_result", "load result")}</dt>
              <dd className="small">{String((s70.runtime?.load_result as { model_id?: string } | undefined)?.model_id ?? "—")}</dd>
              <dt>{t("ml.s70.worker", "worker")}</dt>
              <dd>{s70.worker ? String((s70.worker as { state?: string }).state ?? "—") : "—"}</dd>
              <dt>{t("ml.s70.observations", "observations")}</dt>
              <dd>{String((s70.store as { total_observations?: number } | undefined)?.total_observations ?? (s70.store ? "present" : "—"))}</dd>
              <dt>{t("ml.s70.disagreements", "disagreements")}</dt>
              <dd>{s70.store?.disagreement_counts ? Object.entries(s70.store.disagreement_counts).map(([k, v]) => `${k}:${v}`).join(" · ") || "—" : "—"}</dd>
            </dl>
          )}
        </Panel>

        {/* v1 shadow70: drift alerts + feature health (audit-DB reads) */}
        <Panel
          title={t("ml.panel.drift", "70D drift & feature health (v1)")}
          right={
            <>
              <InfoChip k={t("ml.chip.generated", "generated")} v={shadow70V1Query.data?.generated_at ? fmtAge((Date.now() - Date.parse(shadow70V1Query.data.generated_at)) / 1000) : "—"} />
              <button aria-label={t("ml.drift.refresh_aria", "Refresh shadow 70D")} className="btn small ghost" onClick={() => void shadow70V1Query.refetch()} disabled={shadow70V1Query.isFetching}>⟳</button>
            </>
          }
        >
          <SectionState
            query={shadow70V1Query}
            emptyMessage={t("ml.drift.empty", "No drift alerts and no feature-health rows recorded.")}
            emptyHint={t("ml.drift.empty_hint", "Shadow70Store answered with empty collections — an observed fact, not a failure.")}
            errorFallback={t("ml.err.shadow70_v1", "v1 shadow70 endpoint failed.")}
            emptyWhen={(d) => (d.drift_alerts?.length ?? 0) === 0 && (!d.feature_health || Object.keys(d.feature_health).length === 0)}
          >
            {(d) => (
              <div style={{ display: "grid", gap: 10 }}>
                {d.feature_health && Object.keys(d.feature_health).length > 0 && (
                  <dl className="kv">
                    <dt className="section-title" style={{ gridColumn: "1 / -1" }}>{t("ml.drift.feature_health", "feature health (latest)")}</dt>
                    {Object.entries(d.feature_health).slice(0, 12).map(([k, v]) => (
                      <div key={k} style={{ display: "contents" }}>
                        <dt className="small">{k}</dt>
                        <dd className="small">{typeof v === "number" ? formatNumber(v, 4) : String(v ?? "—")}</dd>
                      </div>
                    ))}
                  </dl>
                )}
                {(d.drift_alerts?.length ?? 0) > 0 ? (
                  <>
                    <div className="section-title">{t("ml.drift.alerts_title", "drift alerts ({n})", { n: d.drift_alerts!.length })}</div>
                    <DataTable headers={[
                      { label: t("ml.th.time", "Time") },
                      { label: t("ml.th.feature", "Feature") },
                      { label: t("ml.th.metric", "Metric"), num: true },
                      { label: t("ml.th.detail", "Detail") },
                    ]}>
                      {d.drift_alerts!.slice(0, 10).map((a, i, rows) => (
                        <tr key={driftAlertKey(rows, i)}>
                          <td className="small">{String(a.timestamp ?? a.ts ?? "—")}</td>
                          <td>{String(a.feature ?? a.name ?? "—")}</td>
                          <td className="num">{typeof a.value === "number" ? formatNumber(a.value, 4) : String(a.value ?? "—")}</td>
                          <td className="small">{String(a.reason ?? a.kind ?? "—")}</td>
                        </tr>
                      ))}
                    </DataTable>
                  </>
                ) : (
                  <div className="l4-note">{t("ml.drift.none", "No drift alerts: the backend has not flagged the 70D feature distribution.")}</div>
                )}
              </div>
            )}
          </SectionState>
        </Panel>
      </div>

      {/* Shadow comparison (60D store) + run inventory */}
      <Panel
        title={t("ml.panel.shadow_cmp", "Shadow comparison (challenger pipeline)")}
        right={
          <>
            <InfoChip
              k="60d"
              v={
                shadowStatusQuery.data?.shadow_60d?.available
                  ? t("ml.chip.decisions", "{n} decisions", { n: shadowStatusQuery.data.shadow_60d.decisions ?? 0 })
                  : shadowStatusQuery.data
                    ? t("ml.st.store_empty", "STORE EMPTY")
                    : "…"
              }
              tone={shadowStatusQuery.data?.shadow_60d?.available ? "good" : ""}
            />
            <button aria-label={t("ml.shadow.refresh_aria", "Refresh shadow status")} className="btn small ghost" onClick={() => void shadowStatusQuery.refetch()} disabled={shadowStatusQuery.isFetching}>⟳</button>
          </>
        }
      >
        {shadowStatusQuery.isPending && !shadowStatusQuery.data ? (
          <Skeleton count={3} />
        ) : shadowStatusQuery.isError ? (
          <ErrorState message={errorText(shadowStatusQuery.error, t("ml.err.shadow_status", "Shadow status endpoint failed."))} onRetry={() => void shadowStatusQuery.refetch()} />
        ) : (
          <div className="grid cols-2">
            <div>
              <div className="section-title">{t("ml.shadow.store_title", "Shadow store (60D comparisons)")}</div>
              {shadowStatusQuery.data?.shadow_60d ? (
                <dl className="kv">
                  <dt>{t("ml.shadow.available", "available")}</dt>
                  <dd><TriBadge value={shadowStatusQuery.data.shadow_60d.available} on="YES" off="NO" /></dd>
                  <dt>{t("ml.shadow.decisions", "decisions")}</dt>
                  <dd>{shadowStatusQuery.data.shadow_60d.decisions ?? "—"}</dd>
                  <dt>{t("ml.shadow.promotions", "promotions")}</dt>
                  <dd>{shadowStatusQuery.data.shadow_60d.promotions ?? "—"}</dd>
                  {Object.entries(shadowStatusQuery.data.shadow_60d.runs ?? {}).map(([k, v]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>{t("ml.shadow.run_label", "run · {k}", { k })}</dt>
                      <dd>{v}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <EmptyState
                  message={t("ml.shadow.empty", "Shadow store did not answer.")}
                  hint={t("ml.shadow.empty_hint", "generated_at: — means the endpoint returned no block.")}
                />
              )}
              <div className="l4-note" style={NOTE_STYLE_CAL}>
                {shadowStatusQuery.data?.generated_at
                  ? t("ml.shadow.gen_line", "status generated {v}", { v: shadowStatusQuery.data.generated_at })
                  : t("ml.shadow.gen_none", "no generated_at in payload")}
              </div>
            </div>
            <div>
              <div className="l4-toolbar" style={{ justifyContent: "space-between" }}>
                <span className="section-title" style={{ margin: 0 }}>{t("ml.runs.title", "Run inventory (v1)")}</span>
                <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                  {(shadowRunsQuery.data?.items.length ?? 0) > 0 && (
                    <button
                      className="btn small ghost"
                      onClick={() =>
                        downloadCsv({
                          filename: `nse-shadow-runs-${stampForFilename()}.csv`,
                          headers: ["run_id", "status", "champion_model", "shadow_model", "started_at", "finished_at"],
                          rows: (shadowRunsQuery.data?.items ?? []).map((r) => [r.run_id ?? "", r.status ?? "", String(r.champion_model ?? ""), String(r.shadow_model ?? ""), String(r.started_at ?? ""), String(r.finished_at ?? "")]),
                        })
                      }
                    >
                      {t("ml.runs.csv", "⇩ CSV")}
                    </button>
                  )}
                  <button aria-label={t("ml.runs.prev_aria", "Previous page")} className="btn small" disabled={runsPage <= 1} onClick={() => setRunsPage((p) => Math.max(1, p - 1))}>‹</button>
                  <span className="small faint inline-mono">p{runsPage}</span>
                  <button aria-label={t("ml.runs.next_aria", "Next page")} className="btn small" disabled={!shadowRunsQuery.data?.has_more} onClick={() => setRunsPage((p) => p + 1)}>›</button>
                </span>
              </div>
              <SectionState
                query={shadowRunsQuery}
                emptyMessage={t("ml.runs.empty", "No shadow runs recorded.")}
                emptyHint={t("ml.runs.empty_hint", "The challenger pipeline has not run yet (empty store ≠ failure).")}
                errorFallback={t("ml.err.runs", "Run inventory endpoint failed.")}
                emptyWhen={(d) => d.items.length === 0}
              >
                {(d) => (
                  <SortableTable
                    columns={runCols}
                    rows={d.items as Array<Record<string, unknown>>}
                    rowKey={(r, i) => String(r.run_id ?? i)}
                    emptyMessage={t("ml.runs.no_runs", "No runs.")}
                    maxHeight={260}
                  />
                )}
              </SectionState>
            </div>
          </div>
        )}
      </Panel>

      {s70?.store?.recent_observations && s70.store.recent_observations.length > 0 && (
        <Panel
          title={t("ml.panel.obs", "Recent 70D shadow observations")}
          tight
          right={<span className="timestamp-note">{t("ml.obs.note", "champion vs 70D, engine-recorded")}</span>}
        >
          <SortableTable
            columns={OBSERVATION_COLUMN_KEYS.map((c) => ({
              key: c.key,
              label: t(OBSERVATION_LABEL_OF[c.key] as string, c.fallback),
              num: c.num ?? false,
              sortValue: OBSERVATION_SORT[c.key] as (o: Record<string, any>) => string | number | null,
              render: OBSERVATION_RENDER[c.key] as (o: Record<string, any>) => ReactNode,
            }))}
            rows={observations}
            rowKey={(o) => o.observation_id}
            initialSort={{ key: "t", dir: "desc" }}
            filter={OBSERVATION_FILTER}
            emptyMessage={t("ml.obs.empty", "No observations.")}
          />
        </Panel>
      )}
    </div>
  );
}
