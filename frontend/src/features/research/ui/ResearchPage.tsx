/**
 * Research — candidate validation pipeline (legacy tab-research parity).
 *
 * Sections: summary KPIs · registry list (drawer on click) · gate queue ·
 * worker heartbeat · analytics (failure heatmap + families) · retention
 * history · v1 datasets. Commands live in ResearchCommands (confirm-guarded).
 * Availability is backend-decided: every legacy /api/research/* route answers
 * {available:false, reason} when the research subsystem is detached and the
 * UI renders that verbatim instead of showing zeros.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import {
  DataTable,
  EmptyState,
  MetricCard,
  Panel,
  Segmented,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { DistBars, FreshnessCaption, GateStepper, InfoRow, StatusPill } from "./lane5Kit";
import { registryCounters, obj, str, type Row } from "../model";
import { researchQueries, researchUseCases } from "../useCases";
import ResearchCommands from "./ResearchCommands";
import StrategyDrawer from "./StrategyDrawer";

type Tab = "registry" | "queue" | "worker" | "analytics" | "history" | "datasets";

const PAGE_SIZE_HINT = "bounded server-side (limit params enforced by the backend)";

export default function ResearchPage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("registry");
  const [lifecycle, setLifecycle] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const summaryQ = useQuery({
    queryKey: ["research", "summary"],
    queryFn: ({ signal }) => researchQueries.summary(signal),
    refetchInterval: 20_000,
    retry: false,
  });
  const registryQ = useQuery({
    queryKey: ["research", "registry", lifecycle ?? "ALL"],
    queryFn: ({ signal }) => researchQueries.registry(lifecycle, signal),
    retry: false,
    enabled: tab === "registry",
  });
  const queueQ = useQuery({
    queryKey: ["research", "queue"],
    queryFn: ({ signal }) => researchQueries.queue(signal),
    refetchInterval: 15_000,
    retry: false,
    enabled: tab === "queue",
  });
  const workerQ = useQuery({
    queryKey: ["research", "worker"],
    queryFn: ({ signal }) => researchQueries.worker(signal),
    refetchInterval: 15_000,
    retry: false,
    enabled: tab === "worker",
  });
  const analyticsQ = useQuery({
    queryKey: ["research", "analytics"],
    queryFn: ({ signal }) => researchQueries.analytics(signal),
    retry: false,
    enabled: tab === "analytics",
  });
  const historyQ = useQuery({
    queryKey: ["research", "history"],
    queryFn: ({ signal }) => researchQueries.history(signal),
    retry: false,
    enabled: tab === "history",
  });
  const datasetsQ = useQuery({
    queryKey: ["research", "v1-datasets"],
    queryFn: ({ signal }) => researchQueries.v1Datasets(signal),
    retry: false,
    enabled: tab === "datasets",
  });
  const v1StatusQ = useQuery({
    queryKey: ["research", "v1-status"],
    queryFn: ({ signal }) => researchQueries.v1Status(signal),
    retry: false,
  });

  const summary = summaryQ.data?.summary;
  const rows = summaryQ.data;
  const registry = registryQ.data?.available === true ? researchUseCases.registryList(registryQ.data.registry ?? []) : [];
  const queue = obj(queueQ.data?.queue);
  const queued: Array<{ label: string; count: number }> = [];
  for (const [gateType, statusMap] of Object.entries(obj(queue.queued))) {
    for (const [status, count] of Object.entries(obj(statusMap))) {
      if (status === "QUEUED" || status === "RUNNING") queued.push({ label: `${gateType} · ${status}`, count: Number(count) || 0 });
    }
  }
  const heatmap = obj(analyticsQ.data?.heatmap);
  const byGate = obj(heatmap.by_gate);
  const rejectionReasons = obj(heatmap.rejection_reasons);

  const unavailable = (data: { available?: boolean; reason?: string } | undefined) =>
    !data || data.available === false ? (
      <EmptyState message="Research subsystem unavailable" hint={data?.reason ?? "backend answered without availability — nothing to show"} />
    ) : null;

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>Research</h2>
        <span className="muted small">candidate validation pipeline · legacy tab-research parity</span>
        <FreshnessCaption
          timestamp={v1StatusQ.data?.generated_at ?? undefined}
          source="v1 /api/v1/research/status"
          isFetching={summaryQ.isFetching}
          error={summaryQ.isError}
        />
      </div>

      <div className="grid cols-4">
        <MetricCard
          label="Registry total"
          value={summaryQ.isPending ? "…" : summary?.available === false ? "n/a" : String(summary?.total ?? 0)}
          sub={rows?.available === false ? (rows.reason ?? "unavailable") : "strategy_intelligence_registry"}
        />
        <MetricCard
          label="Validated"
          value={String(summary?.by_lifecycle?.VALIDATED ?? 0)}
          tone={summary?.by_lifecycle?.VALIDATED ? "pos" : "dim"}
          sub="lifecycle census"
        />
        <MetricCard
          label="Active strategies"
          value={String(summary?.by_lifecycle?.ACTIVE ?? 0)}
          tone={summary?.by_lifecycle?.ACTIVE ? "pos" : "dim"}
          sub="backend lifecycle counts"
        />
        <MetricCard
          label="Research worker"
          value={<StatusBadge status={str(obj(summary?.worker).status) ?? undefined} />}
          sub={obj(summary?.outcome_quality).available ? `closed outcomes: ${String(obj(summary?.outcome_quality).closed_outcomes ?? 0)}` : "outcome quality not reported"}
        />
      </div>

      <Panel
        title="Lifecycle census (registry summary)"
        right={<FreshnessCaption timestamp={null} isFetching={summaryQ.isFetching} error={summaryQ.isError} />}
        tight
      >
        {summaryQ.isPending ? (
          <Skeleton count={2} />
        ) : (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {registryCounters(summary).length === 0 ? (
              <span className="muted small">no lifecycle rows reported</span>
            ) : (
              registryCounters(summary).map((c) => (
                <button
                  key={c.label}
                  className={`btn small ${lifecycle === c.label ? "primary" : "ghost"}`}
                  title={lifecycle === c.label ? "filter: clear" : `filter registry by ${c.label}`}
                  onClick={() => {
                    setLifecycle(lifecycle === c.label ? undefined : c.label);
                    void queryClient.invalidateQueries({ queryKey: ["research", "registry"] });
                  }}
                >
                  {c.label} · {c.value}
                </button>
              ))
            )}
          </div>
        )}
      </Panel>

      <div style={{ marginBlock: 12 }}>
        <ResearchCommands strategyId={selected} />
      </div>

      <Segmented
        options={[
          { id: "registry" as const, label: "Registry" },
          { id: "queue" as const, label: "Gate queue" },
          { id: "worker" as const, label: "Worker" },
          { id: "analytics" as const, label: "Analytics" },
          { id: "history" as const, label: "Retention" },
          { id: "datasets" as const, label: "Datasets (v1)" },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
        {tab === "registry" && (
          <Panel title={`Registry list ${lifecycle ? `· ${lifecycle}` : ""}`} right={<span className="tiny muted">{PAGE_SIZE_HINT}</span>} tight>
            {registryQ.isPending ? (
              <Skeleton count={4} />
            ) : unavailable(registryQ.data) ?? (
              <>
                {registry.length === 0 ? (
                  <EmptyState message="Registry is empty for this filter." hint="/api/research/health explains WHY (source trades, rejections, attempts)." />
                ) : (
                  <DataTable
                    headers={[
                      { label: "strategy" },
                      { label: "lifecycle" },
                      { label: "conf", num: true },
                      { label: "samples", num: true },
                      { label: "score", num: true },
                      { label: "updated" },
                      { label: "" },
                    ]}
                  >
                    {registry.map((s, i) => (
                      <tr key={`${s.strategyId}-${i}`}>
                        <td className="inline-mono tiny" title={s.strategyId}>
                          {s.strategyId.slice(0, 16)}…{s.version ? ` v${s.version}` : ""}
                        </td>
                        <td>
                          <StatusPill status={s.lifecycle} />
                        </td>
                        <td className="num tiny">{s.confidence === null ? "—" : formatNumber(s.confidence, 3)}</td>
                        <td className="num tiny">{s.sampleCount ?? "—"}</td>
                        <td className="num tiny">{s.score === null ? "—" : formatNumber(s.score, 2)}</td>
                        <td className="tiny">{s.updatedAt ? formatDateTime(s.updatedAt) : "—"}</td>
                        <td>
                          <button className="btn small ghost" onClick={() => setSelected(s.strategyId)}>
                            trace
                          </button>
                        </td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </>
            )}
          </Panel>
        )}

        {tab === "queue" && (
          <Panel title="Gate queue census" right={<FreshnessCaption timestamp={null} isFetching={queueQ.isFetching} error={queueQ.isError} />} tight>
            {queueQ.isPending ? (
              <Skeleton count={3} />
            ) : unavailable(queueQ.data) ?? (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">queued / running by gate type</div>
                  <DistBars rows={queued} tone="var(--amber)" />
                </div>
                <div>
                  <div className="section-title">running now</div>
                  {(queue.running as Row[] | undefined)?.length ? (
                    <DataTable headers={[{ label: "gate" }, { label: "strategy" }, { label: "status" }]}>
                      {(queue.running as Row[]).map((r: Row, i: number) => (
                        <tr key={i}>
                          <td className="tiny">{str(r.gate_type) ?? "—"}</td>
                          <td className="inline-mono tiny">{str(r.strategy_id)?.slice(0, 14) ?? "—"}</td>
                          <td>
                            <StatusPill status={str(r.status)} />
                          </td>
                        </tr>
                      ))}
                    </DataTable>
                  ) : (
                    <EmptyState message="Nothing running right now." />
                  )}
                </div>
              </div>
            )}
          </Panel>
        )}

        {tab === "worker" && (
          <Panel title="Worker heartbeat + diagnostics" right={<FreshnessCaption timestamp={null} isFetching={workerQ.isFetching || !workerQ.isFetched} error={workerQ.isError} />} tight>
            {workerQ.isPending ? (
              <Skeleton count={3} />
            ) : unavailable(workerQ.data) ?? (
              <div className="grid cols-2">
                <div>
                  <dl className="kv">
                    <InfoRow label="health" value={<StatusPill status={str(obj(workerQ.data?.worker).health) ?? "UNKNOWN"} />} />
                    <InfoRow label="last beat" value={formatDateTime(str(obj(obj(workerQ.data?.worker).heartbeat).last_beat_at))} />
                    <InfoRow label="cycle" value={String(obj(obj(workerQ.data?.worker).runtime).cycle_count ?? "—")} />
                    <InfoRow label="status" value={str(obj(obj(workerQ.data?.worker).runtime).status) ?? "—"} />
                    <InfoRow label="last error" value={str(obj(obj(workerQ.data?.worker).runtime).last_error) ?? "none reported"} />
                  </dl>
                </div>
                <ResearchDiagMini />
              </div>
            )}
          </Panel>
        )}

        {tab === "analytics" && (
          <Panel title="Failure heatmap + families" right={<FreshnessCaption timestamp={null} isFetching={analyticsQ.isFetching} error={analyticsQ.isError} />} tight>
            {analyticsQ.isPending ? (
              <Skeleton count={3} />
            ) : unavailable(analyticsQ.data) ?? (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">failed gates (total: {String(heatmap.total_failures ?? 0)})</div>
                  <DistBars rows={Object.entries(byGate).map(([k, v]) => ({ label: k, count: Number(v) || 0 }))} tone="var(--red)" />
                </div>
                <div>
                  <div className="section-title">rejection reasons</div>
                  <DistBars
                    rows={Object.entries(rejectionReasons)
                      .slice(0, 15)
                      .map(([k, v]) => ({ label: k, count: Number(v) || 0 }))}
                    tone="var(--amber)"
                  />
                </div>
              </div>
            )}
          </Panel>
        )}

        {tab === "history" && (
          <Panel title="Retention (live vs archive — archive-only contract)" tight>
            {historyQ.isPending ? (
              <Skeleton count={2} />
            ) : unavailable(historyQ.data) ?? (
              <dl className="kv">
                {Object.entries(obj(historyQ.data?.retention)).map(([k, v]) => (
                  <InfoRow key={k} label={k} value={formatNumber(Number(v) || 0, 0)} />
                ))}
              </dl>
            )}
          </Panel>
        )}

        {tab === "datasets" && (
          <Panel title="v1 datasets (provenance from real runs)" right={<span className="tiny muted">/api/v1/research/datasets</span>} tight>
            {datasetsQ.isPending ? (
              <Skeleton count={3} />
            ) : datasetsQ.isError ? (
              <div className="small tx-bad" >
                {datasetsQ.error instanceof Error ? datasetsQ.error.message : "request failed"}
              </div>
            ) : (datasetsQ.data?.datasets ?? []).length === 0 ? (
              <EmptyState message="No datasets derived from runs yet." />
            ) : (
              <DataTable headers={[{ label: "dataset_id" }, { label: "runs", num: true }]}>
                {(datasetsQ.data?.datasets ?? []).map((d, i) => (
                  <tr key={i}>
                    <td className="inline-mono tiny">{d.dataset_id ?? "—"}</td>
                    <td className="num tiny">{d.run_count ?? 0}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>
        )}
      </div>

      {selected && <StrategyDrawer strategyId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

/** Compact diagnostics block inside the worker tab (blocked-gate census). */
function ResearchDiagMini() {
  const diagQ = useQuery({
    queryKey: ["research", "diagnostics"],
    queryFn: ({ signal }) => researchQueries.diagnostics(signal),
    retry: false,
  });
  const blocked = diagQ.data?.blocked_gates ?? [];
  return (
    <div>
      <div className="section-title">blocked / failed gates (diagnostics)</div>
      {diagQ.isPending ? (
        <Skeleton count={2} />
      ) : blocked.length === 0 ? (
        <EmptyState message="No blocked gates reported." />
      ) : (
        <GateStepper
          gates={blocked.slice(0, 8).map((g) => ({
            name: `${str(g.gate_type) ?? "gate"} · ${(str(g.strategy_id) ?? "").slice(0, 10)}`,
            status: str(g.status) ?? "UNKNOWN",
            reason: str(g.failure_reason),
          }))}
        />
      )}
    </div>
  );
}
