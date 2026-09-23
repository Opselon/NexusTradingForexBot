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
  if (overviewQ.isPending && fleetQ.isPending) {
    return (
      <div className="grid cols-2">
        <ChartCard title="loading analysis…">
          <Skeleton count={5} />
        </ChartCard>
        <ChartCard title="loading distributions…">
          <Skeleton count={5} />
        </ChartCard>
      </div>
    );
  }
  if (!overview && overviewQ.isError) {
    return (
      <ErrorState
        message={`overview endpoint failed — ${overviewQ.error instanceof Error ? overviewQ.error.message : "unknown error"}`}
        onRetry={() => void overviewQ.refetch()}
      />
    );
  }
  if (!overview) {
    return <EmptyState message="Backend returned no overview payload." hint="overview.available !== true" />;
  }

  const funnel = pipelineFunnel(overview);
  const gates = gateOutcomes(overview);
  const life = lifecycleSegments(overview);
  const elig = eligibilityCounts(fleet);
  const conf = histogram(fleetConfidenceSamples(fleet), { lo: 0, hi: 1, bins: 10 });
  const healthVals = fleetHealthSamples(fleet);
  const healthKnown = healthVals.filter((v): v is number => v !== null);
  const healthHi = healthKnown.length > 0 ? Math.max(1, Math.ceil(Math.max(...healthKnown) * 10) / 10) : 1;
  const health = histogram(healthVals, { lo: 0, hi: healthHi, bins: 10 });
  const rings = evidenceRings(fleet);

  const donutSegments = life.map((s) => ({
    label: s.key,
    count: s.count,
    share: s.share,
    terminal: s.terminal,
    color: LIFECYCLE_COLOR[s.key] ?? "var(--accent)",
  }));

  return (
    <div className="cc-analysis">
      {fleetQ.isError && (
        <ErrorState
          message={`fleet endpoint failed — ${fleetQ.error instanceof Error ? fleetQ.error.message : "unknown error"}`}
          onRetry={() => void fleetQ.refetch()}
        />
      )}

      <div className="grid cols-2">
        <ChartCard
          title="Evaluation funnel — where the fleet drops"
          hint="evaluation_pipeline counts (transient telemetry, not lifecycle) · % = value ÷ entered stage"
        >
          <FunnelRows stages={funnel} />
        </ChartCard>
        <ChartCard title="Gate outcomes — pass / fail per evaluation gate" hint="overview.evaluation_metrics · rate UNKNOWN when a gate was never tested">
          <GateOutcomeRows gates={gates} />
        </ChartCard>
      </div>

      <div className="grid cols-2">
        <ChartCard title="Lifecycle census" hint="by_lifecycle + terminal (persistent lifecycle scope)">
          <Donut segments={donutSegments} centerLabel="strategies" centerValue={String(overview.total_strategies ?? life.reduce((a, s) => a + s.count, 0))} />
        </ChartCard>
        <ChartCard title="Execution eligibility" hint="fleet rows, eligibility_state from the domain authority">
          {elig.length === 0 ? (
            <EmptyState message="No fleet rows returned." hint={fleetQ.isError ? "fleet query failed — retry above" : "count=0"} />
          ) : (
            <DistBars rows={elig.map((e) => ({ label: e.state, count: e.count }))} tone="var(--accent)" />
          )}
          <div className="tiny faint cc-elig-foot">
            eligible (YES): {String(overview.execution_eligible_count ?? "—")} · blocked: {String(overview.blocked_count ?? "—")} · running evaluations:{" "}
            {String(overview.running_evaluations ?? 0)}
          </div>
        </ChartCard>
      </div>

      <div className="grid cols-3">
        <ChartCard title="Confidence distribution" hint="fleet rows · confidence 0..1">
          <HistogramChart data={conf} label="confidence" formatBucket={(lo, hi) => `${lo.toFixed(1)}–${hi.toFixed(1)}`} />
        </ChartCard>
        <ChartCard title="Health score distribution" hint="fleet rows · health_final (score verdict present)">
          <HistogramChart
            data={health}
            label="health"
            tone="var(--green)"
            formatBucket={(lo, hi) => `${formatNumber(lo, 2)}–${formatNumber(hi, 2)}`}
          />
        </ChartCard>
        <ChartCard title="Evidence depth" hint="PASS count among backtest/walk-forward/OOS/robustness">
          <EvidenceRingsChart buckets={rings.buckets} missing={rings.missing} total={rings.total} />
        </ChartCard>
      </div>
    </div>
  );
}
