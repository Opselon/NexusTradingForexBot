/**
 * AnalysisSection — charts + drop-off analysis over the Command Center
 * overview/fleet payloads (BUG-312 wave: "why is the fleet in this state").
 *
 * Every number rendered here comes from backend fields via ../analysis
 * (pure derivations): the funnel explains WHERE strategies leave the
 * evaluation pipeline, the gate table shows pass/fail per gate, the donut is
 * the lifecycle census, and the histograms show confidence/health/evidence
 * depth across the fleet. Nothing is imputed; gaps render as gaps.
 */

import { useMemo } from "react";
import { EmptyState, ErrorState, Skeleton } from "@/components/primitives";
import { DistBars } from "../../research/ui/lane5Kit";
import { formatNumber } from "@/lib/format";
import {
  eligibilityCounts,
  evidenceRings,
  fleetConfidenceSamples,
  fleetHealthSamples,
  gateOutcomes,
  histogram,
  lifecycleSegments,
  pipelineFunnel,
} from "../analysis";
import type { CcFleetDto, CcOverviewDto } from "../model";
import { ChartCard, Donut, EvidenceRingsChart, FunnelRows, GateOutcomeRows, HistogramChart } from "./charts";
import { useI18n } from "@/stores/i18nStore";
import "../command-center.css";

/** Presentation-only lifecycle -> theme token map (colors, never verdicts). */
const LIFECYCLE_COLOR: Record<string, string> = {
  ACTIVE: "var(--green)",
  SHADOW: "var(--amber)",
  VALIDATED: "#84cc16",
  DISCOVERED: "var(--accent)",
  BACKTESTING: "#38bdf8",
  VALIDATING: "#818cf8",
  OOS_TESTING: "#22d3ee",
  ROBUSTNESS_TESTING: "#a78bfa",
  REJECTED: "var(--red)",
  DEGRADED: "#f97316",
  RETIRED: "var(--text-faint)",
};

interface Queryish {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

export function AnalysisSection({
  overview,
  overviewQ,
  fleet,
  fleetQ,
}: {
  overview: CcOverviewDto | null;
  overviewQ: Queryish;
  fleet: CcFleetDto | undefined;
  fleetQ: Queryish;
}) {
  const t = useI18n((s) => s.t);
  if (overviewQ.isPending && fleetQ.isPending) {
    return (
      <div className="grid cols-2">
        <ChartCard title={t("command-center.analysis.loading_analysis", "loading analysis…")}>
          <Skeleton count={5} />
        </ChartCard>
        <ChartCard title={t("command-center.analysis.loading_distributions", "loading distributions…")}>
          <Skeleton count={5} />
        </ChartCard>
      </div>
    );
  }
  if (!overview && overviewQ.isError) {
    return (
      <ErrorState
        message={t("command-center.err.overview_endpoint", "overview endpoint failed — {e}", { e: overviewQ.error instanceof Error ? overviewQ.error.message : t("command-center.err.unknown", "unknown error") })}
        onRetry={() => void overviewQ.refetch()}
      />
    );
  }
  if (!overview) {
    return <EmptyState message={t("command-center.empty.no_overview", "Backend returned no overview payload.")} hint="overview.available !== true" />;
  }

  // perf: the 10 pure derivations in ../analysis are DOT-map/filter chains over
  // the overview/fleet payloads — derived once per data identity instead of on
  // every render of this section (both queries poll on 30s/60s intervals).
  // Deps are the exact arguments the derivations read; the pure module
  // functions are untouched and stay exportable.
  const funnel = useMemo(() => pipelineFunnel(overview), [overview]);
  const gates = useMemo(() => gateOutcomes(overview), [overview]);
  const life = useMemo(() => lifecycleSegments(overview), [overview]);
  const elig = useMemo(() => eligibilityCounts(fleet), [fleet]);
  const conf = useMemo(
    () => histogram(fleetConfidenceSamples(fleet), { lo: 0, hi: 1, bins: 10 }),
    [fleet],
  );
  const health = useMemo(() => {
    const healthVals = fleetHealthSamples(fleet);
    const healthKnown = healthVals.filter((v): v is number => v !== null);
    const healthHi = healthKnown.length > 0 ? Math.max(1, Math.ceil(Math.max(...healthKnown) * 10) / 10) : 1;
    return histogram(healthVals, { lo: 0, hi: healthHi, bins: 10 });
  }, [fleet]);
  const rings = useMemo(() => evidenceRings(fleet), [fleet]);

  const donutSegments = useMemo(
    () =>
      life.map((s) => ({
        label: s.key,
        count: s.count,
        share: s.share,
        terminal: s.terminal,
        color: LIFECYCLE_COLOR[s.key] ?? "var(--accent)",
      })),
    [life],
  );

  return (
    <div className="cc-analysis">
      {fleetQ.isError && (
        <ErrorState
          message={t("command-center.err.fleet_endpoint", "fleet endpoint failed — {e}", { e: fleetQ.error instanceof Error ? fleetQ.error.message : t("command-center.err.unknown", "unknown error") })}
          onRetry={() => void fleetQ.refetch()}
        />
      )}

      <div className="grid cols-2">
        <ChartCard
          title={t("command-center.analysis.funnel_title", "Evaluation funnel — where the fleet drops")}
          hint={t("command-center.analysis.funnel_hint", "evaluation_pipeline counts (transient telemetry, not lifecycle) · % = value ÷ entered stage")}
        >
          <FunnelRows stages={funnel} />
        </ChartCard>
        <ChartCard title={t("command-center.analysis.gates_title", "Gate outcomes — pass / fail per evaluation gate")} hint={t("command-center.analysis.gates_hint", "overview.evaluation_metrics · rate UNKNOWN when a gate was never tested")}>
          <GateOutcomeRows gates={gates} />
        </ChartCard>
      </div>

      <div className="grid cols-2">
        <ChartCard title={t("command-center.panel.lifecycle_census", "Lifecycle census")} hint={t("command-center.analysis.lifecycle_hint", "by_lifecycle + terminal (persistent lifecycle scope)")}>
          <Donut segments={donutSegments} centerLabel={t("command-center.kpi.strategies", "strategies")} centerValue={String(overview.total_strategies ?? life.reduce((a, s) => a + s.count, 0))} />
        </ChartCard>
        <ChartCard title={t("command-center.analysis.elig_title", "Execution eligibility")} hint={t("command-center.analysis.elig_hint", "fleet rows, eligibility_state from the domain authority")}>
          {elig.length === 0 ? (
            <EmptyState message={t("command-center.empty.no_fleet_rows", "No fleet rows returned.")} hint={fleetQ.isError ? t("command-center.empty.fleet_query_failed", "fleet query failed — retry above") : "count=0"} />
          ) : (
            <DistBars rows={elig.map((e) => ({ label: e.state, count: e.count }))} tone="var(--accent)" />
          )}
          <div className="tiny faint cc-elig-foot">
            {t("command-center.analysis.elig_foot", "eligible (YES): {a} · blocked: {b} · running evaluations: {c}", { a: String(overview.execution_eligible_count ?? "—"), b: String(overview.blocked_count ?? "—"), c: String(overview.running_evaluations ?? 0) })}
          </div>
        </ChartCard>
      </div>

      <div className="grid cols-3">
        <ChartCard title={t("command-center.analysis.conf_title", "Confidence distribution")} hint={t("command-center.analysis.conf_hint", "fleet rows · confidence 0..1")}>
          <HistogramChart data={conf} label={t("command-center.tip.confidence", "confidence")} formatBucket={(lo, hi) => `${lo.toFixed(1)}–${hi.toFixed(1)}`} />
        </ChartCard>
        <ChartCard title={t("command-center.analysis.health_title", "Health score distribution")} hint={t("command-center.analysis.health_hint", "fleet rows · health_final (score verdict present)")}>
          <HistogramChart
            data={health}
            label={t("command-center.analysis.axis_health", "health")}
            tone="var(--green)"
            formatBucket={(lo, hi) => `${formatNumber(lo, 2)}–${formatNumber(hi, 2)}`}
          />
        </ChartCard>
        <ChartCard title={t("command-center.analysis.evid_title", "Evidence depth")} hint={t("command-center.analysis.evid_hint", "PASS count among backtest/walk-forward/OOS/robustness")}>
          <EvidenceRingsChart buckets={rings.buckets} missing={rings.missing} total={rings.total} />
        </ChartCard>
      </div>
    </div>
  );
}
