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
 */

import { useQuery } from "@tanstack/react-query";
import { mlApi } from "@/api/mlApi";
import type { EngineSnapshot } from "@/types/domain";
import { EmptyState, MetricCard, Panel, ProbBar, StatusBadge } from "@/components/primitives";
import { formatNumber, formatPct } from "@/lib/format";
import { ErrorState } from "@/components/primitives";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

export default function MLPage({ snapshot }: Props) {
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
          label="Model integrity (backend verdict)"
          value={integrityQuery.isPending ? "…" : (integ?.state ?? "UNKNOWN")}
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
          label="70D schema (bundle)"
          value={snapshot?.model.feature_schema_id ?? "—"}
          tone="dim"
          sub={`${snapshot?.model.feature_dimension ?? "?"}D · scaler ${snapshot?.model.scaler_ready === null || snapshot?.model.scaler_ready === undefined ? "—" : snapshot.model.scaler_ready ? "READY" : "NOT FITTED"}`}
        />
        <MetricCard
          label="Inference latency"
          value={snapshot?.model.latency_ms === null || snapshot?.model.latency_ms === undefined ? "—" : `${snapshot.model.latency_ms.toFixed(1)} ms`}
          tone="dim"
          sub={snapshot?.model.latency_breakdown ? `fwd ${snapshot.model.latency_breakdown.model_forward_ms ?? "—"} · feat ${snapshot.model.latency_breakdown.feature_ms ?? "—"}` : "backend-measured only"}
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title="Live probabilities (engine)" accent>
          {snapshot?.probs.available ? (
            <ProbBar
              rows={[
                { label: "P(NO_TRADE)", value: snapshot.probs.no_trade, tone: "flat" },
                { label: "P(BUY)", value: snapshot.probs.buy, tone: "buy" },
                { label: "P(SELL)", value: snapshot.probs.sell, tone: "sell" },
              ]}
            />
          ) : (
            <EmptyState message="No live inference yet (model warming up or engine stopped)." />
          )}
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>decision</dt>
            <dd>{snapshot?.ai_decision ?? "—"}</dd>
            <dt>confidence</dt>
            <dd>{snapshot?.ai_confidence === null || snapshot?.ai_confidence === undefined ? "—" : formatPct(snapshot.ai_confidence * 100, 1)}</dd>
            <dt>inference age</dt>
            <dd>{snapshot?.diagnostics.inference_age_sec === null || snapshot?.diagnostics.inference_age_sec === undefined ? "—" : `${snapshot.diagnostics.inference_age_sec.toFixed(1)}s`}</dd>
            <dt>feature age</dt>
            <dd>{snapshot?.diagnostics.features_age_sec === null || snapshot?.diagnostics.features_age_sec === undefined ? "—" : `${snapshot.diagnostics.features_age_sec.toFixed(1)}s`}</dd>
          </dl>
        </Panel>

        <Panel title="Artifact identity (manifest)">
          {identityQuery.isPending ? (
            <div className="muted small">loading…</div>
          ) : identityQuery.data?.available === false ? (
            <EmptyState message={identityQuery.data.reason ?? "No model bundle loaded."} />
          ) : identityQuery.data ? (
            <dl className="kv">
              <dt>model_id</dt>
              <dd>{identityQuery.data.model_id ?? "—"}</dd>
              <dt>schema_id</dt>
              <dd>{identityQuery.data.schema_id ?? "—"}</dd>
              <dt>version</dt>
              <dd>{identityQuery.data.version ?? "—"}</dd>
              <dt>schema hash</dt>
              <dd className="small">{identityQuery.data.feature_schema_hash ? `${identityQuery.data.feature_schema_hash.slice(0, 16)}…` : "—"}</dd>
              <dt>champion id</dt>
              <dd>{snapshot?.model.model_id ?? "—"}</dd>
            </dl>
          ) : (
            <ErrorState message="Identity endpoint failed." onRetry={() => identityQuery.refetch()} />
          )}
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel title="Feature pipeline">
          {featuresQuery.data ? (
            <dl className="kv">
              <dt>warmup</dt>
              <dd><StatusBadge status={featuresQuery.data.warmup_state} /></dd>
              <dt>inference</dt>
              <dd>{featuresQuery.data.inference_enabled ? <span className="badge good">ENABLED</span> : <span className="badge warn">BLOCKED</span>}</dd>
              <dt>last vector</dt>
              <dd>{featuresQuery.data.last_vector_available ? <span className="badge good">AVAILABLE</span> : <span className="badge warn">NONE</span>}</dd>
              <dt>missing features</dt>
              <dd>{featuresQuery.data.missing_features.length === 0 ? "—" : <span className="pnl-neg">{featuresQuery.data.missing_features.join(", ")}</span>}</dd>
              <dt>active dimension</dt>
              <dd>{snapshot?.features.length ? `${snapshot.features.length} entries` : "—"}</dd>
            </dl>
          ) : (
            <EmptyState message="Feature status unavailable." />
          )}
        </Panel>

        <Panel
          title="70D shadow runtime"
          right={s70?.runtime?.state ? <StatusBadge status={String(s70.runtime.state)} /> : undefined}
        >
          {shadow70Query.isPending ? (
            <div className="muted small">loading…</div>
          ) : s70?.available === false || !s70 ? (
            <EmptyState message="Shadow70 subsystem unavailable (worker not attached or engine offline)." hint="Rendered as UNKNOWN — the UI never claims the 70D model is healthy without backend evidence." />
          ) : (
            <dl className="kv">
              <dt>runtime state</dt>
              <dd><StatusBadge status={String(s70.runtime?.state ?? null)} /></dd>
              <dt>shadow model</dt>
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
      </div>

      {s70?.store?.recent_observations && s70.store.recent_observations.length > 0 && (
        <Panel title="Recent 70D shadow observations" tight>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th><th>Champion</th><th>Shadow</th><th>Conf C/S</th><th>Disagreement</th><th>Regime</th><th>News</th><th>Outcome</th>
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
