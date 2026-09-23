/**
 * PURPOSE:  CALIBRATION tab — identity-bound serving-model monitor.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../useCases (controlCenterQueries.calibration — existing GET),
 *           @/components/primitives, @/lib/format, ../../../../research/ui/lane5Kit
 *           (StatusPill, JsonBlock)
 * PROVIDES: CalibrationTab
 * INVARIANTS: available:false renders the endpoint's honest empty state (missing
 *             artifact — never faked numbers); split/deficit/excluded values are
 *             verbatim backend fields; probe multiplier is labeled as a probe.
 * EXTEND:   new metric = a field CalibrationDto already declares in model.ts.
 */
import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, MetricCard, Panel, Skeleton } from "@/components/primitives";
import { formatNumber } from "@/lib/format";
import { JsonBlock, StatusPill } from "../../../research/ui/lane5Kit";
import { controlCenterQueries } from "../../useCases";

export function CalibrationTab() {
  const calibrationQ = useQuery({
    queryKey: ["control-center", "calibration"],
    queryFn: ({ signal }) => controlCenterQueries.calibration(signal),
    retry: false,
  });

  return (
    <Panel title="Calibration monitor (identity-bound serving model)" right={<span className="tiny muted">/api/operator/calibration</span>} tight>
      {calibrationQ.isPending ? (
        <Skeleton count={4} />
      ) : calibrationQ.isError ? (
        <ErrorState
          message={calibrationQ.error instanceof Error ? calibrationQ.error.message : "calibration monitor failed"}
          requestId={(calibrationQ.error as { requestId?: string } | null)?.requestId ?? null}
          onRetry={() => void calibrationQ.refetch()}
        />
      ) : calibrationQ.data?.available !== true ? (
        <EmptyState message="calibration monitor not available" hint="the endpoint answers available:false when the artifact is missing" />
      ) : (
        <>
          <div className="grid cols-4">
            <MetricCard
              label="calibration"
              value={<StatusPill status={calibrationQ.data.calibration_status} />}
              sub={`serving fp ${calibrationQ.data.serving_fingerprint ?? "—"}`}
            />
            <MetricCard
              label="artifact"
              value={calibrationQ.data.artifact_status ?? "—"}
              tone={calibrationQ.data.artifact_status === "PRESENT" ? "pos" : "neg"}
              sub={`collector ${calibrationQ.data.collector_status ?? "—"}`}
            />
            <MetricCard
              label="splits (cal/val)"
              value={`${String(calibrationQ.data.calibration_split ?? 0)}/${String(calibrationQ.data.validation_split ?? 0)}`}
              sub={`required ${String(calibrationQ.data.required_per_split ?? "—")} · deficit ${String(calibrationQ.data.deficit ?? 0)}`}
            />
            <MetricCard
              label="risk multiplier (probe)"
              value={formatNumber(calibrationQ.data.risk_multiplier ?? NaN, 3)}
              sub={`ece ${formatNumber(calibrationQ.data.ece ?? NaN, 3)} · brier ${formatNumber(calibrationQ.data.brier ?? NaN, 3)}`}
            />
          </div>
          <JsonBlock
            value={{ excluded: calibrationQ.data.excluded, oos_cutoff: calibrationQ.data.oos_cutoff, matches_serving: calibrationQ.data.matches_serving }}
            maxChars={1600}
          />
        </>
      )}
    </Panel>
  );
}
