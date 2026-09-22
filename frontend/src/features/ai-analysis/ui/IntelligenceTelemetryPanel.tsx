/**
 * IntelligenceTelemetryPanel — behavior · anomalies · evolution candidates.
 *
 * Port of the legacy Web/index.html "AI INTELLIGENCE CENTER" intelligence
 * block (Web/app.js loadIntelligenceBehavior / loadIntelligenceAnomalies /
 * loadIntelligenceEvolution / scanIntelligenceEvolution).
 *
 * Honesty contract (unchanged from legacy):
 *  - `available: false`  -> the intelligence subsystem is not attached: the
 *    panel renders an explicit "unavailable" state. It NEVER renders 0.
 *  - `available: true` + empty list -> explicit "NO DATA — nothing recorded".
 *  - Rows are rendered verbatim; missing optional fields fall back to
 *    "UNKNOWN"/"—" exactly the way the legacy esc() path did.
 *  - The scan button is a real POST; the backend bounds discovery and the
 *    returned candidates are never live. Failure renders the backend's own
 *    message, never a fabricated candidate.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { intelligenceTelemetryApi } from "../intelligenceApi";
import { FreshnessCaption } from "../../research/ui/lane5Kit";

const LIMIT = 8;

/** Stable label for a detection row (legacy pattern||behavior_type||behavior);
 *  null when the backend recorded none — the caller renders a localized
 *  UNKNOWN through the t() seam. */
function behaviorLabel(b: {
  pattern?: string;
  behavior_type?: string;
  behavior?: string;
}): string | null {
  return b.pattern || b.behavior_type || b.behavior || null;
}

function scalarSummary(row: Record<string, unknown>): string {
  const s = (row.summary ?? row.detail ?? "") as string;
  return s || "";
}

export function IntelligenceTelemetryPanel() {
  const t = useI18n((s) => s.t);
  const queryClient = useQueryClient();

  const behaviorQ = useQuery({
    queryKey: ["intel-telemetry", "behavior"],
    queryFn: ({ signal }) => intelligenceTelemetryApi.behavior(LIMIT, signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const anomaliesQ = useQuery({
    queryKey: ["intel-telemetry", "anomalies"],
    queryFn: ({ signal }) => intelligenceTelemetryApi.anomalies(LIMIT, signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const evolutionQ = useQuery({
    queryKey: ["intel-telemetry", "evolution"],
    queryFn: ({ signal }) => intelligenceTelemetryApi.evolution(LIMIT, signal),
    refetchInterval: 60_000,
    retry: false,
  });

  const scanMut = useMutation({
    mutationFn: () => intelligenceTelemetryApi.scanEvolution(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["intel-telemetry", "evolution"] });
    },
  });

  const behavior = behaviorQ.data?.detections ?? [];
  const anomalies = anomaliesQ.data?.anomalies ?? [];
  const candidates = evolutionQ.data?.candidates ?? [];

  const unavailable =
    (behaviorQ.data && behaviorQ.data.available === false) ||
    (anomaliesQ.data && anomaliesQ.data.available === false) ||
    (evolutionQ.data && evolutionQ.data.available === false);

  if (unavailable) {
    return (
      <Panel title={t("ai-analysis.intel.panel_title", "Intelligence telemetry (behavior · anomalies · evolution)")} tight>
        <EmptyState
          message={t("ai-analysis.intel.unavailable", "Intelligence subsystem unavailable (not attached).")}
          hint={t(
            "ai-analysis.intel.unavailable_hint",
            "/api/intelligence/* answers available:false — counts are not zero-filled.",
          )}
        />
      </Panel>
    );
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="grid cols-2">
        <MetricCard
          label={t("ai-analysis.intel.behavior_detections", "behavior detections")}
          value={behaviorQ.data ? String(behavior.length) : "—"}
          sub={t("ai-analysis.intel.latest_behavior", "latest 8 · /api/intelligence/behavior")}
        />
        <MetricCard
          label={t("ai-analysis.intel.anomaly_events", "anomaly events")}
          value={anomaliesQ.data ? String(anomalies.length) : "—"}
          sub={t("ai-analysis.intel.latest_anomalies", "latest 8 · /api/intelligence/anomalies")}
        />
      </div>

      <Panel
        title={t("ai-analysis.intel.behavior_panel", "Behavior detections")}
        right={<FreshnessCaption isFetching={behaviorQ.isFetching} error={behaviorQ.isError} />}
        tight
      >
        {behaviorQ.isPending ? (
          <Skeleton count={3} />
        ) : behaviorQ.isError ? (
          <ErrorState
            message={behaviorQ.error instanceof Error ? behaviorQ.error.message : t("ai-analysis.intel.behavior_failed", "behavior endpoint failed")}
            onRetry={() => void behaviorQ.refetch()}
          />
        ) : behavior.length === 0 ? (
          <EmptyState message={t("ai-analysis.intel.no_data_behavior", "NO DATA — no behavior detections recorded.")} />
        ) : (
          <DataTable
            headers={[
              { label: t("ai-analysis.th.pattern", "pattern") },
              { label: t("ai-analysis.th.symbol", "symbol") },
              { label: t("ai-analysis.th.detected", "detected") },
              { label: t("ai-analysis.intel.th_summary", "summary") },
            ]}
          >
            {behavior.map((b, i) => (
              <tr key={i}>
                <td className="small">
                  <strong>{behaviorLabel(b) ?? t("ai-analysis.intel.unknown", "UNKNOWN")}</strong>
                </td>
                <td className="small">{(b.symbol as string) || "—"}</td>
                <td className="tiny">{((b.detected_at as string) || "").slice(0, 16) || "—"}</td>
                <td className="tiny muted">{scalarSummary(b) || "—"}</td>
              </tr>
            ))}
          </DataTable>
        )}
      </Panel>

      <Panel
        title={t("ai-analysis.intel.anomaly_panel", "Anomaly events (evidence-based)")}
        right={<FreshnessCaption isFetching={anomaliesQ.isFetching} error={anomaliesQ.isError} />}
        tight
      >
        {anomaliesQ.isPending ? (
          <Skeleton count={3} />
        ) : anomaliesQ.isError ? (
          <ErrorState
            message={anomaliesQ.error instanceof Error ? anomaliesQ.error.message : t("ai-analysis.intel.anomalies_failed", "anomalies endpoint failed")}
            onRetry={() => void anomaliesQ.refetch()}
          />
        ) : anomalies.length === 0 ? (
          <EmptyState message={t("ai-analysis.intel.no_data_anomaly", "NO DATA — no anomaly events recorded.")} />
        ) : (
          <DataTable
            headers={[
              { label: t("ai-analysis.th.type", "type") },
              { label: t("ai-analysis.intel.th_severity", "severity") },
              { label: t("ai-analysis.intel.th_obs", "obs") },
              { label: t("ai-analysis.intel.th_explanation", "explanation") },
            ]}
          >
            {anomalies.map((a, i) => {
              const evidence = a.evidence;
              const explanation =
                evidence && typeof evidence === "object" && "explanation" in evidence
                  ? String((evidence as { explanation?: unknown }).explanation ?? "")
                  : evidence && typeof evidence !== "object"
                    ? String(evidence)
                    : "";
              const range =
                a.first_seen && a.last_seen && a.first_seen !== a.last_seen
                  ? `${String(a.first_seen).slice(0, 16)}..${String(a.last_seen).slice(0, 16)}`
                  : null;
              return (
                <tr key={i}>
                  <td className="small">
                    <strong>{a.anomaly_type || a.category || t("ai-analysis.intel.unknown", "UNKNOWN")}</strong>
                  </td>
                  <td>
                    <StatusBadge status={a.severity ?? null} />
                  </td>
                  <td className="num tiny">{a.observation_count ? `x${a.observation_count}` : "—"}</td>
                  <td className="tiny muted" title={range ?? ""}>
                    {explanation || "—"}
                  </td>
                </tr>
              );
            })}
          </DataTable>
        )}
      </Panel>

      <Panel
        title={t("ai-analysis.intel.evolution_panel", "Strategy evolution candidates")}
        right={
          <>
            <FreshnessCaption isFetching={evolutionQ.isFetching} error={evolutionQ.isError} />
            <button
              className="btn small"
              disabled={scanMut.isPending}
              onClick={() => void scanMut.mutateAsync()}
              title={t(
                "ai-analysis.intel.scan_title",
                "POST /api/intelligence/evolution/scan — bounded discovery; candidates are never live",
              )}
            >
              {scanMut.isPending ? t("ai-analysis.intel.scanning", "scanning…") : t("ai-analysis.intel.scan_now", "Scan now")}
            </button>
          </>
        }
        tight
      >
        {evolutionQ.isPending ? (
          <Skeleton count={3} />
        ) : evolutionQ.isError ? (
          <ErrorState
            message={evolutionQ.error instanceof Error ? evolutionQ.error.message : t("ai-analysis.intel.evolution_failed", "evolution endpoint failed")}
            onRetry={() => void evolutionQ.refetch()}
          />
        ) : scanMut.isError ? (
          <ErrorState
            message={scanMut.error instanceof Error ? scanMut.error.message : t("ai-analysis.intel.scan_failed", "scan failed")}
            onRetry={() => void scanMut.mutateAsync()}
          />
        ) : candidates.length === 0 ? (
          <EmptyState
            message={t("ai-analysis.intel.no_candidates", "No evolution candidates.")}
            hint={t(
              "ai-analysis.intel.no_candidates_hint",
              "Click Scan now to discover strategy variations — the backend records them as unvalidated.",
            )}
          />
        ) : (
          <DataTable
            headers={[
              { label: t("ai-analysis.intel.th_status", "status") },
              { label: t("ai-analysis.intel.th_candidate", "candidate") },
              { label: t("ai-analysis.intel.th_hypothesis", "hypothesis") },
            ]}
          >
            {candidates.map((c, i) => (
              <tr key={String(c.candidate_id ?? i)}>
                <td>
                  <StatusBadge status={c.status ?? null} />
                </td>
                <td className="inline-mono tiny" dir="ltr">{String(c.candidate_id ?? "—").slice(0, 14)}</td>
                <td className="tiny muted">{c.hypothesis || "—"}</td>
              </tr>
            ))}
          </DataTable>
        )}
      </Panel>
    </div>
  );
}
