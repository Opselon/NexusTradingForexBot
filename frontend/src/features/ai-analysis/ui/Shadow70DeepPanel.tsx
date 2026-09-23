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

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { shadow70Api } from "../shadow70Api";
import { FreshnessCaption } from "../../research/ui/lane5Kit";

/** Render "--" for absent numerics (legacy parity). */
function dash(v: number | null | undefined, digits = 3, suffix = ""): string {
  return v == null || !Number.isFinite(v) ? "--" : `${v.toFixed(digits)}${suffix}`;
}

export function Shadow70DeepPanel() {
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

  // The persisted-alert table shows the first 12 of the backend's list: slice
  // chain memoized on exactly the array it reads (re-runs only on a payload
  // change). Hooks stay above the early return below (rules of hooks).
  const persistedAlerts = useMemo(
    () => (healthQ.data?.persisted_alerts ?? []).slice(0, 12),
    [healthQ.data?.persisted_alerts],
  );

  if (healthQ.data && healthQ.data.available === false) {
    return (
      <Panel title="Shadow 70D deep (feature health · disagreements)" tight>
        <EmptyState
          message="70D shadow runtime not attached."
          hint="/api/models/shadow70/health answers available:false — attach a validated 70D candidate to begin observing."
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
        title="Shadow 70D feature health"
        right={
          <>
            <StatusBadge
              status={driftSeverity}
              label={`drift: ${driftSeverity.toLowerCase()}`}
            />
            <FreshnessCaption isFetching={healthQ.isFetching} error={healthQ.isError} />
          </>
        }
        tight
      >
        {healthQ.isPending ? (
          <Skeleton count={4} />
        ) : healthQ.isError ? (
          <ErrorState
            message={healthQ.error instanceof Error ? healthQ.error.message : "shadow70 health endpoint failed"}
            onRetry={() => void healthQ.refetch()}
          />
        ) : featureHealth.length === 0 ? (
          <EmptyState message="No live feature health yet — the observer has not sampled the 70D feature stream." />
        ) : (
          <DataTable
            headers={[
              { label: "feature" },
              { label: "mean", num: true },
              { label: "std", num: true },
              { label: "missing", num: true },
              { label: "zero", num: true },
              { label: "samples", num: true },
            ]}
          >
            {featureHealth.map((h, i) => (
              <tr key={String(h.name ?? i)}>
                <td className="tiny">{h.name ?? "—"}</td>
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
        title="Champion vs Shadow disagreements (spec 30)"
        right={<FreshnessCaption isFetching={disQ.isFetching} error={disQ.isError} />}
        tight
      >
        {disQ.isPending ? (
          <Skeleton count={3} />
        ) : disQ.isError ? (
          <ErrorState
            message={disQ.error instanceof Error ? disQ.error.message : "disagreements endpoint failed"}
            onRetry={() => void disQ.refetch()}
          />
        ) : rows.length === 0 ? (
          <EmptyState message="No disagreements recorded." hint="The shadow observer agrees with the champion on every recorded observation so far." />
        ) : (
          <DataTable headers={[{ label: "timestamp" }, { label: "champion" }, { label: "shadow" }, { label: "type" }, { label: "outcome" }]}>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="tiny">{formatDateTime((r.timestamp ?? "").slice(0, 19))}</td>
                <td className="tiny">{r.champion_action || "—"}</td>
                <td className="tiny">{r.shadow_action || "—"}</td>
                <td className="tiny tx-warn" >
                  {r.disagreement || "—"}
                </td>
                <td className="tiny">{r.outcome || "PENDING"}</td>
              </tr>
            ))}
          </DataTable>
        )}
      </Panel>

      {healthQ.data?.persisted_alerts && healthQ.data.persisted_alerts.length > 0 ? (
        <Panel title="Persisted drift alerts (latest 25)" tight>
          <DataTable headers={[{ label: "feature" }, { label: "kind" }, { label: "value", num: true }, { label: "at" }]}>
            {persistedAlerts.map((a, i) => (
              <tr key={i}>
                <td className="tiny">{a.feature ?? a.kind ?? "—"}</td>
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
