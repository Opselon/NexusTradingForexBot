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

import { useMemo, useState } from "react";
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
import { InfoChip, SortableTable, type Column } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { formatNumber, formatPct } from "@/lib/format";
import { ApiError } from "@/types/api";
import "@/pages/_shared/pages.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
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
  const compared = valid !== false && disagreement && !disagreement.startsWith("SHADOW_") && disagreement !== "NOT_COMPARED";
  if (!disagreement) return <span className="tiny">—</span>;
  if (compared) return <span className="tiny">{disagreement}</span>;
  return (
    <span className="tiny"  title="No shadow inference ran for this tick — the shadow model was not attached or the 70D vector was rejected. This is not a trade decision.">
      {disagreement}
    </span>
  );
}

/** Canonical 70D family blocks (backend schema_contract / liquidity_runtime). */
const FAMILY_BLOCKS = [
  { id: "base", label: "BASE 0–49 (scalp_v1 protected)", from: 0, to: 49 },
  { id: "family", label: "FAMILY/NEWS 50–59", from: 50, to: 59 },
  { id: "liquidity", label: "LIQUIDITY 60–69 (70D only)", from: 60, to: 69 },
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
  if (features.length === 0) {
    return (
      <div>
        <div className="section-title">{label}</div>
        <EmptyState message="No features in this block." hint={hint} />
      </div>
    );
  }
  const valid = features.filter((f) => (f.status ?? "").toUpperCase() === "VALID").length;
  const nan = features.filter((f) => (f.status ?? "").toUpperCase() === "NAN").length;
  const unavail = features.length - valid - nan;
  return (
    <div>
      <div className="section-title">
        {label} <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>· {features.length} slots</span>
      </div>
      <div className="l4-chip-row" style={{ marginBlockEnd: 6 }}>
        <span className="l4-chip good">{valid} VALID</span>
        <span className={`l4-chip ${nan ? "warn" : ""}`}>{nan} NAN</span>
        <span className={`l4-chip ${unavail ? "" : ""}`}>{unavail} UNAVAILABLE</span>
      </div>
      <div tabIndex={0} className="l4-features" style={{ maxBlockSize: 210 }}>
        {features.map((f) => (
          <div key={`${f.index}-${f.name}`} className={`l4-feature ${(f.status ?? "").toUpperCase() === "VALID" ? "" : (f.status ?? "").toUpperCase() === "NAN" ? "nan" : "unavailable"}`} title={`${f.name} · ${f.status}`}>
            <div className="n">{f.index} {f.name}</div>
            <div className="v">{featureValue(f.value)}</div>
          </div>
        ))}
      </div>
      <div className="l4-note" style={{ marginTop: 4 }}>{hint}</div>
    </div>
  );
}

export default function MLPage({ snapshot }: Props) {
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
  const features = snapshot?.features ?? [];

  const blocks = useMemo(
    () =>
      FAMILY_BLOCKS.map((b) => ({
        ...b,
        rows: features.filter((f) => f.index >= b.from && f.index <= b.to),
      })),
    [features],
  );
  const dim = snapshot?.model.feature_dimension ?? features.length ?? null;
  const is70 = dim === 70;

  const runCols = useMemo<Array<Column<Record<string, unknown>>>>(
    () => [
      { key: "run", label: "Run id", sortValue: (r) => (typeof r.run_id === "string" ? r.run_id : null), render: (r) => <span className="small">{String(r.run_id ?? "—")}</span> },
      { key: "status", label: "Status", sortValue: (r) => (typeof r.status === "string" ? r.status : null), render: (r) => <StatusBadge status={String(r.status ?? null)} /> },
      { key: "champ", label: "Champion", sortValue: (r) => (typeof r.champion_model === "string" ? r.champion_model : null), render: (r) => String(r.champion_model ?? "—") },
      { key: "shadow", label: "Shadow", sortValue: (r) => (typeof r.shadow_model === "string" ? r.shadow_model : null), render: (r) => String(r.shadow_model ?? "—") },
      { key: "started", label: "Started", sortValue: (r) => (typeof r.started_at === "string" ? r.started_at : null), render: (r) => String(r.started_at ?? "—") },
      { key: "finished", label: "Finished", sortValue: (r) => (typeof r.finished_at === "string" ? r.finished_at : null), render: (r) => String(r.finished_at ?? "—") },
    ],
    [],
  );

  return (
    <div>
      <div className="grid cols-4">
        <MetricCard
          label="Model integrity (backend verdict)"
          value={integrityQuery.isPending ? "…" : integrityQuery.isError ? "ERROR" : (integ?.state ?? "UNKNOWN")}
          tone={integrityLevel === "good" ? "pos" : integrityLevel === "bad" ? "neg" : "dim"}
          sub={integ?.reason ?? (integ?.state === "ACTIVE" ? "champion valid + serving" : "backend decides — not inferred from artifact presence")}
        />
        <MetricCard
          label="Serving bundle"
          value={statusQuery.data ? (statusQuery.data.bundle_loaded ? "LOADED" : "NOT LOADED") : "—"}
          tone={statusQuery.data?.bundle_loaded ? "pos" : "dim"}
          sub={`inference ${statusQuery.data?.inference_enabled ? "ENABLED" : "BLOCKED"} · warmup ${statusQuery.data?.warmup_state ?? "—"}`}
        />
        <MetricCard
          label="Effective schema"
          value={snapshot?.model.feature_schema_id ?? "—"}
          tone="dim"
          sub={`${dim ?? "?"}D ${is70 ? "(70D liquidity)" : dim === 50 ? "(50D base)" : ""} · scaler ${snapshot?.model.scaler_ready === null || snapshot?.model.scaler_ready === undefined ? "—" : snapshot.model.scaler_ready ? "READY" : "NOT FITTED"}`}
        />
        <MetricCard
          label="Inference latency"
          value={snapshot?.model.latency_ms === null || snapshot?.model.latency_ms === undefined ? "—" : `${snapshot.model.latency_ms.toFixed(1)} ms`}
          tone="dim"
          sub={snapshot?.model.latency_breakdown ? `fwd ${snapshot.model.latency_breakdown.model_forward_ms ?? "—"} · feat ${snapshot.model.latency_breakdown.feature_ms ?? "—"} · e2e ${snapshot.model.latency_breakdown.e2e_ms ?? "—"}` : "backend-measured only"}
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title="Live probabilities (engine)" accent right={<AgeNote label="inference age" ageSec={snapshot?.diagnostics.inference_age_sec} />}>
          {snapshot?.probs.available ? (
            <ProbBar
              rows={[
                { label: "P(NO_TRADE)", value: snapshot.probs.no_trade, tone: "flat" },
                { label: "P(BUY)", value: snapshot.probs.buy, tone: "buy" },
                { label: "P(SELL)", value: snapshot.probs.sell, tone: "sell" },
              ]}
            />
          ) : (
            <EmptyState message="No live inference yet (model warming up or engine stopped)." hint="probs.available=false — not rendered as zeros." />
          )}
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>decision</dt>
            <dd>{snapshot?.ai_decision ?? "—"}</dd>
            <dt>confidence</dt>
            <dd>{snapshot?.ai_confidence === null || snapshot?.ai_confidence === undefined ? "—" : formatPct(snapshot.ai_confidence * 100, 1)}</dd>
            <dt>feature age</dt>
            <dd>{fmtAge(snapshot?.diagnostics.features_age_sec)}</dd>
            <dt>inference stamp</dt>
            <dd className="small">{snapshot?.probs.inference_timestamp ?? "—"}</dd>
          </dl>
        </Panel>

        <Panel title="Artifact identity (manifest)" right={<span className="timestamp-note">/api/v1/model/identity</span>}>
          {identityQuery.isPending ? (
            <Skeleton count={4} />
          ) : identityQuery.isError ? (
            <ErrorState message={errorText(identityQuery.error, "Identity endpoint failed.")} requestId={identityQuery.error instanceof ApiError ? identityQuery.error.requestId : null} onRetry={() => void identityQuery.refetch()} />
          ) : identityQuery.data?.available === false ? (
            <EmptyState message={identityQuery.data.reason ?? "No model bundle loaded."} hint="Identity is absent, not invalid — the backend said so." />
          ) : identityQuery.data ? (
            <dl className="kv">
              <dt>model_id</dt>
              <dd>{identityQuery.data.model_id ?? "—"}</dd>
              <dt>artifact_id</dt>
              <dd className="small">{identityQuery.data.artifact_id ?? "—"}</dd>
              <dt>schema_id</dt>
              <dd>{identityQuery.data.schema_id ?? "—"}</dd>
              <dt>version</dt>
              <dd>{identityQuery.data.version ?? "—"}</dd>
              <dt>created</dt>
              <dd className="small">{identityQuery.data.created_at ?? "—"}</dd>
              <dt>schema hash</dt>
              <dd className="small">{identityQuery.data.feature_schema_hash ? `${identityQuery.data.feature_schema_hash.slice(0, 16)}…` : "—"}</dd>
              <dt>serving champion</dt>
              <dd>{snapshot?.model.model_id ?? "—"}</dd>
            </dl>
          ) : null}
        </Panel>
      </div>

      {/* Feature pipeline + integrity dimensions */}
      <div className="grid cols-2">
        <Panel title="Feature pipeline" right={<AgeNote label="age" ageSec={featuresQuery.dataUpdatedAt ? (Date.now() - featuresQuery.dataUpdatedAt) / 1000 : null} />}>
          <SectionState
            query={featuresQuery}
            emptyMessage="Feature status unavailable."
            emptyHint="features/status endpoint returned nothing — warmup state unknown."
            errorFallback="Feature status endpoint failed."
            emptyWhen={() => false}
          >
            {(fs) => (
              <dl className="kv">
                <dt>warmup</dt>
                <dd><StatusBadge status={fs.warmup_state} /></dd>
                <dt>inference</dt>
                <dd><TriBadge value={fs.inference_enabled} on="ENABLED" off="BLOCKED" /></dd>
                <dt>last vector</dt>
                <dd><TriBadge value={fs.last_vector_available} on="AVAILABLE" off="NONE" /></dd>
                <dt>missing features</dt>
                <dd>{fs.missing_features.length === 0 ? "—" : <span className="pnl-neg">{fs.missing_features.join(", ")}</span>}</dd>
                <dt>probed</dt>
                <dd className="small">{fs.probed_at}</dd>
              </dl>
            )}
          </SectionState>
          <div style={{ marginBlockStart: 10 }}>
            <SectionState
              query={integrityQuery}
              emptyMessage="Integrity verdict unavailable."
              errorFallback="Integrity endpoint failed."
              emptyWhen={() => false}
            >
              {(iv) => (
                <dl className="kv">
                  <dt>declared dim</dt>
                  <dd>{iv.feature_dimension ?? "—"}</dd>
                  <dt>actual input dim</dt>
                  <dd className={(iv.actual_input_dimension !== null && iv.feature_dimension !== null && iv.actual_input_dimension !== iv.feature_dimension) ? "pnl-neg" : undefined}>{iv.actual_input_dimension ?? "—"}</dd>
                  <dt>actual classes</dt>
                  <dd>{iv.actual_output_classes ?? "—"}</dd>
                  <dt>scaler dim</dt>
                  <dd>{iv.scaler_dimension ?? "—"}</dd>
                  <dt>compatibility</dt>
                  <dd><StatusBadge status={iv.compatibility ?? null} /></dd>
                </dl>
              )}
            </SectionState>
          </div>
        </Panel>

        {/* 50D vs 70D family panels */}
        <Panel
          title={`Feature families (${dim ?? "?"}D effective)`}
          right={<span className="timestamp-note">BASE 0–49 · FAMILY 50–59 · LIQUIDITY 60–69 (backend layout)</span>}
        >
          {features.length === 0 ? (
            <EmptyState message="No feature vector published yet." hint="snapshot.features is empty — nothing to split by family." />
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
                        ? "Present: the 70D bundle is serving and the liquidity block is populated."
                        : "Absent by contract: the 50D runtime (scalp_v1) does not carry the liquidity block."
                      : b.id === "family"
                        ? "News/family indices — protected 50D core is untouched by the 70D extension."
                        : "The protected scalp_v1 core, identical in 50D and 70D."
                  }
                />
              ))}
            </div>
          )}
        </Panel>
      </div>

      {/* Calibration evidence */}
      <Panel
        title="Calibration evidence (identity-bound)"
        right={
          <>
            <span className="timestamp-note">/api/operator/calibration</span>
            <button aria-label="Refresh calibration" className="btn small ghost" onClick={() => void calibrationQuery.refetch()} disabled={calibrationQuery.isFetching}>⟳</button>
          </>
        }
      >
        {calibrationQuery.isPending && !calibrationQuery.data ? (
          <Skeleton count={4} />
        ) : calibrationQuery.isError ? (
          <ErrorState message={errorText(calibrationQuery.error, "Calibration endpoint failed.")} requestId={calibrationQuery.error instanceof ApiError ? calibrationQuery.error.requestId : null} onRetry={() => void calibrationQuery.refetch()} />
        ) : calibrationQuery.data?.available === false ? (
          <EmptyState message="Calibration monitor unavailable." hint="The backend reported available:false — no numbers are inferred." />
        ) : calibrationQuery.data ? (
          <>
            <div className="grid cols-4">
              <MetricCard label="Calibration status" value={calibrationQuery.data.calibration_status ?? "—"} tone={calibrationQuery.data.calibration_status === "CALIBRATED" ? "pos" : "dim"} sub={`artifact ${calibrationQuery.data.artifact_status ?? "—"}`} />
              <MetricCard
                label="Collector"
                value={calibrationQuery.data.collector_status ?? "—"}
                tone={calibrationQuery.data.collector_status === "COLLECTED" ? "pos" : "dim"}
                sub={`needs ${calibrationQuery.data.required_per_split ?? "?"} per split`}
              />
              <MetricCard
                label="ECE / Brier"
                value={`${calibrationQuery.data.ece === null || calibrationQuery.data.ece === undefined ? "—" : Number(calibrationQuery.data.ece).toFixed(4)} / ${calibrationQuery.data.brier === null || calibrationQuery.data.brier === undefined ? "—" : Number(calibrationQuery.data.brier).toFixed(4)}`}
                tone="dim"
                sub="expected calibration error / Brier score"
              />
              <MetricCard
                label="Risk multiplier"
                value={calibrationQuery.data.risk_multiplier === null || calibrationQuery.data.risk_multiplier === undefined ? "—" : formatNumber(calibrationQuery.data.risk_multiplier, 3)}
                tone="dim"
                sub={`floor ${calibrationQuery.data.min_risk_multiplier_floor ?? "—"} · the value the RiskEngine applies`}
              />
            </div>
            <dl className="kv" style={{ marginBlockStart: 10 }}>
              <dt>serving fingerprint</dt>
              <dd>{calibrationQuery.data.serving_fingerprint ?? "—"}</dd>
              <dt>artifact mtime</dt>
              <dd className="small">{calibrationQuery.data.serving_artifact_mtime ?? "—"}</dd>
              <dt>matches serving</dt>
              <dd><TriBadge value={calibrationQuery.data.matches_serving} on="MATCH" off="MISMATCH" /></dd>
              <dt>calibration / validation split</dt>
              <dd>{calibrationQuery.data.calibration_split ?? "—"} / {calibrationQuery.data.validation_split ?? "—"} (of {calibrationQuery.data.total_eligible ?? "—"} eligible)</dd>
              <dt>deficit</dt>
              <dd>{Object.entries(calibrationQuery.data.deficit ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</dd>
              <dt>excluded rows</dt>
              <dd className="small">{Object.entries(calibrationQuery.data.excluded ?? {}).map(([k, v]) => `${k}:${v}`).join(" · ") || "—"}</dd>
              <dt>OOS cutoff</dt>
              <dd className="small">{calibrationQuery.data.oos_cutoff ?? "—"}</dd>
            </dl>
            <div className="l4-note" style={{ marginTop: 6 }}>
              Eligibility, fingerprint binding and the 60/40 chronological split are computed by the backend monitor; this panel only displays them. An
              INSUFFICIENT_EVIDENCE collector status is an honest gap, not a failure of the model.
            </div>
          </>
        ) : null}
      </Panel>

      {/* 70D shadow runtime */}
      <div className="grid cols-2">
        <Panel
          title="70D shadow runtime"
          right={s70?.runtime?.state ? <StatusBadge status={String(s70.runtime.state)} /> : <span className="l4-chip">/api/models/shadow70/summary</span>}
        >
          {shadow70Query.isPending ? (
            <Skeleton count={4} />
          ) : shadow70Query.isError ? (
            <ErrorState message={errorText(shadow70Query.error, "Shadow70 endpoint failed.")} requestId={shadow70Query.error instanceof ApiError ? shadow70Query.error.requestId : null} onRetry={() => void shadow70Query.refetch()} />
          ) : !s70 || s70.available === false ? (
            <EmptyState message="Shadow70 subsystem unavailable (worker not attached or engine offline)." hint="Rendered as UNKNOWN — the UI never claims the 70D model is healthy without backend evidence." />
          ) : (
            <dl className="kv">
              <dt>runtime state</dt>
              <dd><StatusBadge status={String(s70.runtime?.state ?? null)} /></dd>
              <dt>load result</dt>
              <dd className="small">{String((s70.runtime?.load_result as { model_id?: string } | undefined)?.model_id ?? "—")}</dd>
              <dt>worker</dt>
              <dd>{s70.worker ? String((s70.worker as { state?: string }).state ?? "—") : "—"}</dd>
              <dt>observations</dt>
              <dd>{String((s70.store as { total_observations?: number } | undefined)?.total_observations ?? (s70.store ? "present" : "—"))}</dd>
              <dt>disagreements</dt>
              <dd>{s70.store?.disagreement_counts ? Object.entries(s70.store.disagreement_counts).map(([k, v]) => `${k}:${v}`).join(" · ") || "—" : "—"}</dd>
            </dl>
          )}
        </Panel>

        {/* v1 shadow70: drift alerts + feature health (audit-DB reads) */}
        <Panel
          title="70D drift & feature health (v1)"
          right={
            <>
              <InfoChip k="generated" v={shadow70V1Query.data?.generated_at ? fmtAge((Date.now() - Date.parse(shadow70V1Query.data.generated_at)) / 1000) : "—"} />
              <button aria-label="Refresh shadow 70D" className="btn small ghost" onClick={() => void shadow70V1Query.refetch()} disabled={shadow70V1Query.isFetching}>⟳</button>
            </>
          }
        >
          <SectionState
            query={shadow70V1Query}
            emptyMessage="No drift alerts and no feature-health rows recorded."
            emptyHint="Shadow70Store answered with empty collections — an observed fact, not a failure."
            errorFallback="v1 shadow70 endpoint failed."
            emptyWhen={(d) => (d.drift_alerts?.length ?? 0) === 0 && (!d.feature_health || Object.keys(d.feature_health).length === 0)}
          >
            {(d) => (
              <div style={{ display: "grid", gap: 10 }}>
                {d.feature_health && Object.keys(d.feature_health).length > 0 && (
                  <dl className="kv">
                    <dt className="section-title" style={{ gridColumn: "1 / -1" }}>feature health (latest)</dt>
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
                    <div className="section-title">drift alerts ({d.drift_alerts!.length})</div>
                    <DataTable headers={[{ label: "Time" }, { label: "Feature" }, { label: "Metric", num: true }, { label: "Detail" }]}>
                      {d.drift_alerts!.slice(0, 10).map((a, i) => (
                        <tr key={i}>
                          <td className="small">{String(a.timestamp ?? a.ts ?? "—")}</td>
                          <td>{String(a.feature ?? a.name ?? "—")}</td>
                          <td className="num">{typeof a.value === "number" ? formatNumber(a.value, 4) : String(a.value ?? "—")}</td>
                          <td className="small">{String(a.reason ?? a.kind ?? "—")}</td>
                        </tr>
                      ))}
                    </DataTable>
                  </>
                ) : (
                  <div className="l4-note">No drift alerts: the backend has not flagged the 70D feature distribution.</div>
                )}
              </div>
            )}
          </SectionState>
        </Panel>
      </div>

      {/* Shadow comparison (60D store) + run inventory */}
      <Panel
        title="Shadow comparison (challenger pipeline)"
        right={
          <>
            <InfoChip k="60d" v={shadowStatusQuery.data?.shadow_60d?.available ? `${shadowStatusQuery.data.shadow_60d.decisions ?? 0} decisions` : shadowStatusQuery.data ? "STORE EMPTY" : "…"} tone={shadowStatusQuery.data?.shadow_60d?.available ? "good" : ""} />
            <button aria-label="Refresh shadow status" className="btn small ghost" onClick={() => void shadowStatusQuery.refetch()} disabled={shadowStatusQuery.isFetching}>⟳</button>
          </>
        }
      >
        {shadowStatusQuery.isPending && !shadowStatusQuery.data ? (
          <Skeleton count={3} />
        ) : shadowStatusQuery.isError ? (
          <ErrorState message={errorText(shadowStatusQuery.error, "Shadow status endpoint failed.")} onRetry={() => void shadowStatusQuery.refetch()} />
        ) : (
          <div className="grid cols-2">
            <div>
              <div className="section-title">Shadow store (60D comparisons)</div>
              {shadowStatusQuery.data?.shadow_60d ? (
                <dl className="kv">
                  <dt>available</dt>
                  <dd><TriBadge value={shadowStatusQuery.data.shadow_60d.available} on="YES" off="NO" /></dd>
                  <dt>decisions</dt>
                  <dd>{shadowStatusQuery.data.shadow_60d.decisions ?? "—"}</dd>
                  <dt>promotions</dt>
                  <dd>{shadowStatusQuery.data.shadow_60d.promotions ?? "—"}</dd>
                  {Object.entries(shadowStatusQuery.data.shadow_60d.runs ?? {}).map(([k, v]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>run · {k}</dt>
                      <dd>{v}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <EmptyState message="Shadow store did not answer." hint="generated_at: — means the endpoint returned no block." />
              )}
              <div className="l4-note" style={{ marginTop: 6 }}>
                {shadowStatusQuery.data?.generated_at ? `status generated ${shadowStatusQuery.data.generated_at}` : "no generated_at in payload"}
              </div>
            </div>
            <div>
              <div className="l4-toolbar" style={{ justifyContent: "space-between" }}>
                <span className="section-title" style={{ margin: 0 }}>Run inventory (v1)</span>
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
                      ⇩ CSV
                    </button>
                  )}
                  <button aria-label="Previous page" className="btn small" disabled={runsPage <= 1} onClick={() => setRunsPage((p) => Math.max(1, p - 1))}>‹</button>
                  <span className="small faint inline-mono">p{runsPage}</span>
                  <button aria-label="Next page" className="btn small" disabled={!shadowRunsQuery.data?.has_more} onClick={() => setRunsPage((p) => p + 1)}>›</button>
                </span>
              </div>
              <SectionState
                query={shadowRunsQuery}
                emptyMessage="No shadow runs recorded."
                emptyHint="The challenger pipeline has not run yet (empty store ≠ failure)."
                errorFallback="Run inventory endpoint failed."
                emptyWhen={(d) => d.items.length === 0}
              >
                {(d) => (
                  <SortableTable
                    columns={runCols}
                    rows={d.items as Array<Record<string, unknown>>}
                    rowKey={(r, i) => String(r.run_id ?? i)}
                    emptyMessage="No runs."
                    maxHeight={260}
                  />
                )}
              </SectionState>
            </div>
          </div>
        )}
      </Panel>

      {s70?.store?.recent_observations && s70.store.recent_observations.length > 0 && (
        <Panel title="Recent 70D shadow observations" tight right={<span className="timestamp-note">champion vs 70D, engine-recorded</span>}>
          <SortableTable
            columns={[
              { key: "t", label: "Time", sortValue: (o) => o.timestamp, render: (o) => o.timestamp.slice(0, 19) },
              { key: "c", label: "Champion", sortValue: (o) => o.champion_action, render: (o) => o.champion_action },
              { key: "s", label: "Shadow", sortValue: (o) => o.shadow_action, render: (o) => <span className={`l4-chip ${o.shadow_action !== o.champion_action ? "warn" : ""}`}>{o.shadow_action}</span> },
              { key: "conf", label: "Conf C/S", num: true, sortValue: (o) => o.champion_confidence, render: (o) => `${formatNumber(o.champion_confidence)} / ${formatNumber(o.shadow_confidence)}` },
              { key: "dis", label: "Disagreement", sortValue: (o) => o.disagreement, render: (o) => <VerdictChip disagreement={o.disagreement} valid={o.valid} /> },
              { key: "rg", label: "Regime", sortValue: (o) => o.regime, render: (o) => o.regime || "—" },
              { key: "nw", label: "News", render: (o) => o.news_state || "—" },
              { key: "liq", label: "Liquidity", render: (o) => o.liquidity_state || "—" },
              { key: "out", label: "Outcome", sortValue: (o) => o.outcome, render: (o) => o.outcome },
            ]}
            rows={s70.store.recent_observations.slice(0, 25)}
            rowKey={(o) => o.observation_id}
            initialSort={{ key: "t", dir: "desc" }}
            filter={(o, q) => o.champion_action.toLowerCase().includes(q) || o.shadow_action.toLowerCase().includes(q) || (o.regime ?? "").toLowerCase().includes(q)}
            emptyMessage="No observations."
          />
        </Panel>
      )}
    </div>
  );
}
