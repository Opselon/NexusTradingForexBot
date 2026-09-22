/**
 * Shadow70DeepPanel — feature health + Champion-vs-Shadow disagreements.
 *
 * Port of the legacy "70D SHADOW MODEL PANEL" (Web/app.js loadShadow70Panel /
 * renderShadow70) — the two read paths the v1 /api/v1/shadow/70d envelope does
 * not surface:
 *  - GET /api/models/shadow70/health        (feature stats + drift severity)
 *  - GET /api/models/shadow70/disagreements (spec-30 disagreement rows)
 *
 * Truthfulness: `available: false` means the 70D runtime is not attached and
 * renders an explicit unavailable state (never healthy-looking zeroes).
 * Missing numeric fields render "--" exactly as the legacy panel did.
 */

import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { shadow70Api } from "../shadow70Api";
import { FreshnessCaption } from "../../research/ui/lane5Kit";

/** Render "--" for absent numerics (legacy parity). */
function dash(v: number | null | undefined, digits = 3, suffix = ""): string {
  return v == null || !Number.isFinite(v) ? "--" : `${v.toFixed(digits)}${suffix}`;
}

export function Shadow70DeepPanel() {
  const t = useI18n((s) => s.t);
  const healthQ = useQuery({
    queryKey: ["shadow70", "health"],
    queryFn: ({ signal }) => shadow70Api.health(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const disQ = useQuery({
    queryKey: ["shadow70", "disagreements"],
    queryFn: ({ signal }) => shadow70Api.disagreements(30, signal),
    refetchInterval: 30_000,
    retry: false,
  });

  if (healthQ.data && healthQ.data.available === false) {
    return (
      <Panel title={t("ai-analysis.shadow.deep_panel", "Shadow 70D deep (feature health · disagreements)")} tight>
        <EmptyState
          message={t("ai-analysis.shadow.not_attached", "70D shadow runtime not attached.")}
          hint={t(
            "ai-analysis.shadow.not_attached_hint",
            "/api/models/shadow70/health answers available:false — attach a validated 70D candidate to begin observing.",
          )}
        />
      </Panel>
    );
  }

  const featureHealth = healthQ.data?.feature_health ?? [];
  const driftSeverity = (healthQ.data?.drift?.severity ?? "NORMAL").toUpperCase();
  const rows = disQ.data?.rows ?? [];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <Panel
        title={t("ai-analysis.shadow.health_panel", "Shadow 70D feature health")}
        right={
          <>
            <StatusBadge
              status={driftSeverity}
              label={t("ai-analysis.shadow.drift_label", "drift: {s}", { s: driftSeverity.toLowerCase() })}
            />
            <FreshnessCaption isFetching={healthQ.isFetching} error={healthQ.isError} />
          </>}
        tight
      >
        {healthQ.isPending ? (
          <Skeleton count={4} />
        ) : healthQ.isError ? (
          <ErrorState
            message={healthQ.error instanceof Error ? healthQ.error.message : t("ai-analysis.shadow.health_failed", "shadow70 health endpoint failed")}
            onRetry={() => void healthQ.refetch()}
          />
        ) : featureHealth.length === 0 ? (
          <EmptyState message={t("ai-analysis.shadow.no_feature_health", "No live feature health yet — the observer has not sampled the 70D feature stream.")} />
        ) : (
          <DataTable
            headers={[
              { label: t("ai-analysis.th.feature", "feature") },
              { label: t("ai-analysis.shadow.th_mean", "mean"), num: true },
              { label: t("ai-analysis.shadow.th_std", "std"), num: true },
              { label: t("ai-analysis.shadow.th_missing", "missing"), num: true },
              { label: t("ai-analysis.shadow.th_zero", "zero"), num: true },
              { label: t("ai-analysis.shadow.th_samples", "samples"), num: true },
            ]}
          >
            {featureHealth.map((h, i) => (
              <tr key={String(h.name ?? i)}>
                <td className="tiny" dir="ltr">{h.name ?? "—"}</td>
                <td className="num tiny">{dash(h.mean)}</td>
                <td className="num tiny">{dash(h.std)}</td>
                <td className="num tiny">{h.missing_rate == null ? "--" : `${(100 * h.missing_rate).toFixed(1)}%`}</td>
                <td className="num tiny">{h.zero_rate == null ? "--" : `${(100 * h.zero_rate).toFixed(1)}%`}</td>
                <td className="num tiny">{h.samples ?? "--"}</td>
              </tr>
            ))}
          </DataTable>
        )}
      </Panel>

      <Panel
        title={t("ai-analysis.shadow.disagreements_panel", "Champion vs Shadow disagreements (spec 30)")}
        right={<FreshnessCaption isFetching={disQ.isFetching} error={disQ.isError} />}
        tight
      >
        {disQ.isPending ? (
          <Skeleton count={3} />
        ) : disQ.isError ? (
          <ErrorState
            message={disQ.error instanceof Error ? disQ.error.message : t("ai-analysis.shadow.disagreements_failed", "disagreements endpoint failed")}
            onRetry={() => void disQ.refetch()}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            message={t("ai-analysis.shadow.no_disagreements", "No disagreements recorded.")}
            hint={t(
              "ai-analysis.shadow.no_disagreements_hint",
              "The shadow observer agrees with the champion on every recorded observation so far.",
            )}
          />
        ) : (
          <DataTable
            headers={[
              { label: t("ai-analysis.shadow.th_timestamp", "timestamp") },
              { label: "champion" },
              { label: "shadow" },
              { label: t("ai-analysis.th.type", "type") },
              { label: t("ai-analysis.shadow.th_outcome", "outcome") },
            ]}
          >
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="tiny">{formatDateTime((r.timestamp ?? "").slice(0, 19))}</td>
                <td className="tiny" dir="ltr">{r.champion_action || "—"}</td>
                <td className="tiny" dir="ltr">{r.shadow_action || "—"}</td>
                <td className="tiny" style={{ color: "var(--amber)" }}>
                  {r.disagreement || "—"}
                </td>
                <td className="tiny">{r.outcome || t("ai-analysis.shadow.pending", "PENDING")}</td>
              </tr>
            ))}
          </DataTable>
        )}
      </Panel>

      {healthQ.data?.persisted_alerts && healthQ.data.persisted_alerts.length > 0 ? (
        <Panel title={t("ai-analysis.shadow.alerts_panel", "Persisted drift alerts (latest 25)")} tight>
          <DataTable
            headers={[
              { label: t("ai-analysis.th.feature", "feature") },
              { label: t("ai-analysis.th.kind", "kind") },
              { label: t("ai-analysis.th.value", "value"), num: true },
              { label: t("ai-analysis.th.at", "at") },
            ]}
          >
            {healthQ.data.persisted_alerts.slice(0, 12).map((a, i) => (
              <tr key={i}>
                <td className="tiny" dir="ltr">{a.feature ?? a.kind ?? "—"}</td>
                <td className="tiny">{a.kind ?? a.alert_type ?? "—"}</td>
                <td className="num tiny">{formatNumber(Number(a.value ?? a.score ?? NaN), 3)}</td>
                <td className="tiny">{formatDateTime(a.created_at ?? a.detected_at)}</td>
              </tr>
            ))}
          </DataTable>
        </Panel>
      ) : null}
    </div>
  );
}
