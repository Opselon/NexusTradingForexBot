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
import { useI18n } from "@/stores/i18nStore";

export function CalibrationTab() {
  const t = useI18n((s) => s.t);
  const calibrationQ = useQuery({
    queryKey: ["control-center", "calibration"],
    queryFn: ({ signal }) => controlCenterQueries.calibration(signal),
    retry: false,
  });

  return (
    <Panel title={t("control-center.panel.calibration", "Calibration monitor (identity-bound serving model)")} right={<span className="tiny muted">/api/operator/calibration</span>} tight>
      {calibrationQ.isPending ? (
        <Skeleton count={4} />
      ) : calibrationQ.isError ? (
        /* §9: a failed GET is an error, never "monitor unavailable" — backend words + retry */
        <ErrorState
          message={calibrationQ.error instanceof Error ? calibrationQ.error.message : t("control-center.err.calibration", "calibration monitor failed")}
          onRetry={() => void calibrationQ.refetch()}
        />
      ) : calibrationQ.data?.available !== true ? (
        <EmptyState message={t("control-center.empty.calibration_unavailable", "calibration monitor not available")} hint={t("control-center.empty.calibration_hint", "the endpoint answers available:false when the artifact is missing")} />
      ) : (
        <>
          <div className="grid cols-4">
            <MetricCard
              label={t("control-center.kpi.calibration", "calibration")}
              value={<StatusPill status={calibrationQ.data.calibration_status} />}
              sub={t("control-center.kpi.serving_fp", "serving fp {fp}", { fp: calibrationQ.data.serving_fingerprint ?? "—" })}
            />
            <MetricCard
              label={t("control-center.kpi.artifact", "artifact")}
              value={calibrationQ.data.artifact_status ?? "—"}
              tone={calibrationQ.data.artifact_status === "PRESENT" ? "pos" : "neg"}
              sub={t("control-center.kpi.collector", "collector {v}", { v: calibrationQ.data.collector_status ?? "—" })}
            />
            <MetricCard
              label={t("control-center.kpi.splits", "splits (cal/val)")}
              value={`${String(calibrationQ.data.calibration_split ?? 0)}/${String(calibrationQ.data.validation_split ?? 0)}`}
              sub={t("control-center.kpi.required_deficit", "required {required} · deficit {deficit}", { required: String(calibrationQ.data.required_per_split ?? "—"), deficit: String(calibrationQ.data.deficit ?? 0) })}
            />
            <MetricCard
              label={t("control-center.kpi.risk_multiplier", "risk multiplier (probe)")}
              value={formatNumber(calibrationQ.data.risk_multiplier ?? NaN, 3)}
              sub={t("control-center.kpi.ece_brier", "ece {a} · brier {b}", { a: formatNumber(calibrationQ.data.ece ?? NaN, 3), b: formatNumber(calibrationQ.data.brier ?? NaN, 3) })}
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
