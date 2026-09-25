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
import { useI18n } from "@/stores/i18nStore";
import { FreshnessCaption } from "@/features/config/ui/kit";
import { observabilityApi } from "../simulationApi";

function workerTone(status: string | undefined): "good" | "warn" | "bad" | "unknown" {
  const s = (status ?? "STOPPED").toUpperCase();
  if (s === "READY") return "good";
  if (s === "DEGRADED") return "warn";
  if (s === "STOPPED") return "unknown";
  return "bad";
}

export function TelegramWorkerPanel() {
  const t = useI18n((s) => s.t);
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
      title={t("debug.tg.title", "Telegram notifier (live worker)")}
      right={<FreshnessCaption fetchedAtMs={q.dataUpdatedAt || null} intervalMs={15_000} stale={q.isError} />}
      tight
    >
      {q.isPending ? (
        <Skeleton count={3} />
      ) : q.isError ? (
        <ErrorState
          message={q.error instanceof Error ? q.error.message : t("debug.tg.unreadable", "observability endpoint failed")}
          onRetry={() => void q.refetch()}
        />
      ) : q.data ? (
        <div style={{ display: "grid", gap: 8 }}>
          <div className="grid cols-2">
            <MetricCard
              label={t("debug.tg.queue", "notifier queue")}
              value={String(q.data.tg_queue)}
              sub={t("debug.tg.queue_sub", "pending outbound messages")}
            />
            <MetricCard
              label={t("debug.tg.armed_label", "notifier armed")}
              value={q.data.tg_enabled ? t("debug.tg.armed", "ARMED") : t("debug.tg.disarmed", "DISARMED")}
              sub="/api/observability/stats · tg_enabled"
            />
          </div>
          <dl className="kv" style={{ marginTop: 2 }}>
            <dt>{t("debug.tg.worker_state", "worker state")}</dt>
            <dd>
              <StatusBadge status={status} label={workerTone(status)} />
            </dd>
            <dt>{t("debug.tg.counters", "sent / failed / retries")}</dt>
            <dd>
              {String(tg.sent_count ?? "--")} / {String(tg.failed_count ?? "--")} / {String(tg.retry_count ?? "--")}
            </dd>
            {tg.failure_category ? (
              <>
                <dt>{t("debug.tg.last_failure", "last failure")}</dt>
                <dd className="pnl-neg">{String(tg.failure_category)}</dd>
              </>
            ) : null}
            {tg.last_success && tg.last_success !== "-" ? (
              <>
                <dt>{t("debug.tg.last_success", "last success")}</dt>
                <dd>{String(tg.last_success)}</dd>
              </>
            ) : null}
          </dl>
          {(!tg.status || !tg.last_success) && !tg.failure_category ? (
            <EmptyState message={t("debug.tg.empty", "No delivery telemetry yet — the notifier has not attempted a send.")} />
          ) : null}
        </div>
      ) : (
        <EmptyState
          message={t("debug.tg.unavailable", "Notifier telemetry unavailable.")}
          hint={t("debug.tg.unavailable_hint", "The backend answered with no body.")}
        />
      )}
    </Panel>
  );
}
