/**
 * ML — model / 70D monitoring.
 *
 * Health claims come ONLY from backend verdicts:
 *  - /api/models/integrity: semantic integrity (tensors/scaler/compatibility).
 *    An artifact existing is NOT health — integrity VALID/INVALID is decided
 *    by the backend champion inspector.
 *  - /api/v1/model/status: serving bundle + warmup + inference enablement.
 *  - /api/v1/features/status: warmup + missing features.
 *  - /api/models/shadow70/summary: 70D shadow runtime/store/worker state.
 *
 * i18n: state words (ACTIVE/LOADED/ENABLED/READY/PASS...) are backend
 * verdicts and render verbatim; panel titles, metric labels and hint copy
 * are UI copy and translate through alt.ml.*.
 */

import { useQuery } from "@tanstack/react-query";
import { mlApi } from "@/api/mlApi";
import type { EngineSnapshot } from "@/types/domain";
import { EmptyState, MetricCard, Panel, ProbBar, StatusBadge } from "@/components/primitives";
import { formatNumber, formatPct } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { ErrorState } from "@/components/primitives";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

export default function MLPage({ snapshot }: Props) {
  const t = useI18n((s) => s.t);
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

  const integ = integrityQuery.data;
  const integrityLevel =
    integ?.state === "ACTIVE" ? "good" : integ?.state === "INCOMPATIBLE" || integ?.state === "INVALID" ? "bad" : integ?.state === "NO_CHAMPION" || integ?.state === "UNAVAILABLE" ? "warn" : "unknown";
  const s70 = shadow70Query.data;

  return (
    <div>
      <div className="grid cols-4">
        <MetricCard
          label={t("alt.ml.metric_integrity", "Model integrity (backend verdict)")}
          value={integrityQuery.isPending ? "…" : (integ?.state ?? "UNKNOWN")}
          tone={integrityLevel === "good" ? "pos" : integrityLevel === "bad" ? "neg" : "dim"}
          sub={integ?.reason ?? (integ?.state === "ACTIVE" ? t("alt.ml.integrity_sub_active", "champion valid + serving") : t("alt.ml.integrity_sub", "backend decides — not inferred from artifact presence"))}
        />
        <MetricCard
          label={t("alt.ml.metric_bundle", "Serving bundle")}
          value={statusQuery.data ? (statusQuery.data.bundle_loaded ? "LOADED" : "NOT LOADED") : "—"}
          tone={statusQuery.data?.bundle_loaded ? "pos" : "dim"}
          sub={t("alt.ml.bundle_sub", "inference {inf} · warmup {warm}", { inf: statusQuery.data?.inference_enabled ? "ENABLED" : "BLOCKED", warm: statusQuery.data?.warmup_state ?? "—" })}
        />
        <MetricCard
          label={t("alt.ml.metric_schema", "70D schema (bundle)")}
          value={snapshot?.model.feature_schema_id ?? "—"}
          tone="dim"
          sub={`${snapshot?.model.feature_dimension ?? "?"}D · scaler ${snapshot?.model.scaler_ready === null || snapshot?.model.scaler_ready === undefined ? "—" : snapshot.model.scaler_ready ? "READY" : "NOT FITTED"}`}
        />
        <MetricCard
          label={t("alt.ml.metric_latency", "Inference latency")}
          value={snapshot?.model.latency_ms === null || snapshot?.model.latency_ms === undefined ? "—" : `${snapshot.model.latency_ms.toFixed(1)} ms`}
          tone="dim"
          sub={snapshot?.model.latency_breakdown ? t("alt.ml.latency_sub", "fwd {fwd} · feat {feat}", { fwd: snapshot.model.latency_breakdown.model_forward_ms ?? "—", feat: snapshot.model.latency_breakdown.feature_ms ?? "—" }) : t("alt.ml.latency_sub_backend", "backend-measured only")}
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title={t("alt.ml.panel_probs", "Live probabilities (engine)")} accent>
          {snapshot?.probs.available ? (
            <ProbBar
              rows={[
                { label: "P(NO_TRADE)", value: snapshot.probs.no_trade, tone: "flat" },
                { label: "P(BUY)", value: snapshot.probs.buy, tone: "buy" },
                { label: "P(SELL)", value: snapshot.probs.sell, tone: "sell" },
              ]}
            />
          ) : (
            <EmptyState message={t("alt.ml.empty_probs", "No live inference yet (model warming up or engine stopped).")} />
          )}
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>{t("alt.ml.dt_decision", "decision")}</dt>
            <dd>{snapshot?.ai_decision ?? "—"}</dd>
            <dt>{t("alt.ml.dt_confidence", "confidence")}</dt>
            <dd>{snapshot?.ai_confidence === null || snapshot?.ai_confidence === undefined ? "—" : formatPct(snapshot.ai_confidence * 100, 1)}</dd>
            <dt>{t("alt.ml.dt_inference_age", "inference age")}</dt>
            <dd>{snapshot?.diagnostics.inference_age_sec === null || snapshot?.diagnostics.inference_age_sec === undefined ? "—" : `${snapshot.diagnostics.inference_age_sec.toFixed(1)}s`}</dd>
            <dt>{t("alt.ml.dt_feature_age", "feature age")}</dt>
            <dd>{snapshot?.diagnostics.features_age_sec === null || snapshot?.diagnostics.features_age_sec === undefined ? "—" : `${snapshot.diagnostics.features_age_sec.toFixed(1)}s`}</dd>
          </dl>
        </Panel>

        <Panel title={t("alt.ml.panel_identity", "Artifact identity (manifest)")}>
          {identityQuery.isPending ? (
            <div className="muted small">{t("alt.common.loading", "loading…")}</div>
          ) : identityQuery.data?.available === false ? (
            <EmptyState message={identityQuery.data.reason ?? t("alt.ml.empty_identity", "No model bundle loaded.")} />
          ) : identityQuery.data ? (
            <dl className="kv">
              <dt>model_id</dt>
              <dd>{identityQuery.data.model_id ?? "—"}</dd>
              <dt>schema_id</dt>
              <dd>{identityQuery.data.schema_id ?? "—"}</dd>
              <dt>version</dt>
              <dd>{identityQuery.data.version ?? "—"}</dd>
              <dt>{t("alt.ml.dt_schema_hash", "schema hash")}</dt>
              <dd className="small">{identityQuery.data.feature_schema_hash ? `${identityQuery.data.feature_schema_hash.slice(0, 16)}…` : "—"}</dd>
              <dt>{t("alt.ml.dt_champion", "champion id")}</dt>
              <dd>{snapshot?.model.model_id ?? "—"}</dd>
            </dl>
          ) : (
            <ErrorState message={t("alt.ml.err_identity", "Identity endpoint failed.")} onRetry={() => identityQuery.refetch()} />
          )}
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel title={t("alt.ml.panel_pipeline", "Feature pipeline")}>
          {featuresQuery.data ? (
            <dl className="kv">
              <dt>{t("alt.ml.dt_warmup", "warmup")}</dt>
              <dd><StatusBadge status={featuresQuery.data.warmup_state} /></dd>
              <dt>{t("alt.ml.dt_inference", "inference")}</dt>
              <dd>{featuresQuery.data.inference_enabled ? <span className="badge good">ENABLED</span> : <span className="badge warn">BLOCKED</span>}</dd>
              <dt>{t("alt.ml.dt_last_vector", "last vector")}</dt>
              <dd>{featuresQuery.data.last_vector_available ? <span className="badge good">AVAILABLE</span> : <span className="badge warn">NONE</span>}</dd>
              <dt>{t("alt.ml.dt_missing", "missing features")}</dt>
              <dd>{featuresQuery.data.missing_features.length === 0 ? "—" : <span className="pnl-neg">{featuresQuery.data.missing_features.join(", ")}</span>}</dd>
              <dt>{t("alt.ml.dt_active_dim", "active dimension")}</dt>
              <dd>{snapshot?.features.length ? t("alt.ml.entries_value", "{n} entries", { n: snapshot.features.length }) : "—"}</dd>
            </dl>
          ) : (
            <EmptyState message={t("alt.ml.empty_features", "Feature status unavailable.")} />
          )}
        </Panel>

        <Panel
          title={t("alt.ml.panel_shadow", "70D shadow runtime")}
          right={s70?.runtime?.state ? <StatusBadge status={String(s70.runtime.state)} /> : undefined}
        >
          {shadow70Query.isPending ? (
            <div className="muted small">{t("alt.common.loading", "loading…")}</div>
          ) : s70?.available === false || !s70 ? (
            <EmptyState message={t("alt.ml.empty_shadow", "Shadow70 subsystem unavailable (worker not attached or engine offline).")} hint={t("alt.ml.empty_shadow_hint", "Rendered as UNKNOWN — the UI never claims the 70D model is healthy without backend evidence.")} />
          ) : (
            <dl className="kv">
              <dt>{t("alt.ml.dt_runtime", "runtime state")}</dt>
              <dd><StatusBadge status={String(s70.runtime?.state ?? null)} /></dd>
              <dt>{t("alt.ml.dt_shadow_model", "shadow model")}</dt>
              <dd className="small">{String((s70.runtime?.load_result as { model_id?: string } | undefined)?.model_id ?? "—")}</dd>
              <dt>{t("alt.ml.dt_worker", "worker")}</dt>
              <dd>{s70.worker ? String((s70.worker as { state?: string }).state ?? "—") : "—"}</dd>
              <dt>{t("alt.ml.dt_observations", "observations")}</dt>
              <dd>{String((s70.store as { total_observations?: number } | undefined)?.total_observations ?? (s70.store ? "present" : "—"))}</dd>
              <dt>{t("alt.ml.dt_disagreements", "disagreements")}</dt>
              <dd>{s70.store?.disagreement_counts ? Object.entries(s70.store.disagreement_counts).map(([k, v]) => `${k}:${v}`).join(" · ") || "—" : "—"}</dd>
            </dl>
          )}
        </Panel>
      </div>

      {s70?.store?.recent_observations && s70.store.recent_observations.length > 0 && (
        <Panel title={t("alt.ml.panel_recent", "Recent 70D shadow observations")} tight>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t("alt.common.col_time", "Time")}</th>
                  <th>{t("alt.ml.col_champion", "Champion")}</th>
                  <th>{t("alt.ml.col_shadow", "Shadow")}</th>
                  <th>{t("alt.ml.col_conf_cs", "Conf C/S")}</th>
                  <th>{t("alt.ml.col_disagreement", "Disagreement")}</th>
                  <th>{t("alt.common.col_regime", "Regime")}</th>
                  <th>{t("alt.ml.col_news", "News")}</th>
                  <th>{t("alt.common.col_outcome", "Outcome")}</th>
                </tr>
              </thead>
              <tbody>
                {s70.store.recent_observations.slice(0, 15).map((o) => (
                  <tr key={o.observation_id}>
                    <td>{o.timestamp.slice(0, 19)}</td>
                    <td>{o.champion_action}</td>
                    <td>{o.shadow_action}</td>
                    <td className="num">{formatNumber(o.champion_confidence)} / {formatNumber(o.shadow_confidence)}</td>
                    <td>{o.disagreement || "—"}</td>
                    <td>{o.regime || "—"}</td>
                    <td>{o.news_state || "—"}</td>
                    <td>{o.outcome}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}
