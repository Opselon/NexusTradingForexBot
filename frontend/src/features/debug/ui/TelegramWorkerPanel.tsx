/**
 * TelegramWorkerPanel — notifier queue + truthful worker telemetry.
 *
 * Port of the legacy monitoring-tab Telemetry & Notifications strip
 * (Web/app.js updateHeartbeats), which reads GET /api/observability/stats:
 *  - tg_queue      : notifier queue depth
 *  - tg_enabled    : notifier armed flag
 *  - telegram      : notifier.health_state() — READY/DEGRADED/STOPPED with
 *                    sent/failed/retry counters (BUG-072: truthful worker
 *                    state; never a fake "Active" badge)
 *
 * The Config feature owns the token/admin editor; this panel only renders the
 * LIVE worker telemetry, which the settings endpoint does not expose.
 */

import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { observabilityApi } from "../simulationApi";
import { FreshnessCaption } from "../../../features/research/ui/lane5Kit";

function workerTone(status: string | undefined): "good" | "warn" | "bad" | "unknown" {
  const s = (status ?? "STOPPED").toUpperCase();
  if (s === "READY") return "good";
  if (s === "DEGRADED") return "warn";
  if (s === "STOPPED") return "unknown";
  return "bad";
}

export function TelegramWorkerPanel() {
  const q = useQuery({
    queryKey: ["observability", "stats"],
    queryFn: ({ signal }) => observabilityApi.stats(signal),
    refetchInterval: 15_000,
    retry: false,
  });

  const tg = q.data?.telegram ?? {};
  const status = (tg.status as string | undefined) ?? "STOPPED";

  return (
    <Panel
      title="Telegram notifier (live worker)"
      right={<FreshnessCaption isFetching={q.isFetching} error={q.isError} />}
      tight
    >
      {q.isPending ? (
        <Skeleton count={3} />
      ) : q.isError ? (
        <ErrorState
          message={q.error instanceof Error ? q.error.message : "observability endpoint failed"}
          onRetry={() => void q.refetch()}
        />
      ) : q.data ? (
        <div style={{ display: "grid", gap: 8 }}>
          <div className="grid cols-2">
            <MetricCard label="notifier queue" value={String(q.data.tg_queue)} sub="pending outbound messages" />
            <MetricCard
              label="notifier armed"
              value={q.data.tg_enabled ? "ARMED" : "DISARMED"}
              sub="/api/observability/stats · tg_enabled"
            />
          </div>
          <dl className="kv" style={{ marginTop: 2 }}>
            <dt>worker state</dt>
            <dd>
              <StatusBadge status={status} label={workerTone(status)} />
            </dd>
            <dt>sent / failed / retries</dt>
            <dd>
              {String(tg.sent_count ?? "--")} / {String(tg.failed_count ?? "--")} / {String(tg.retry_count ?? "--")}
            </dd>
            {tg.failure_category ? (
              <>
                <dt>last failure</dt>
                <dd className="pnl-neg">{String(tg.failure_category)}</dd>
              </>
            ) : null}
            {tg.last_success && tg.last_success !== "-" ? (
              <>
                <dt>last success</dt>
                <dd>{String(tg.last_success)}</dd>
              </>
            ) : null}
          </dl>
          {(!tg.status || !tg.last_success) && !tg.failure_category ? (
            <EmptyState message="No delivery telemetry yet — the notifier has not attempted a send." />
          ) : null}
        </div>
      ) : (
        <EmptyState message="Notifier telemetry unavailable." hint="The backend answered with no body." />
      )}
    </Panel>
  );
}
