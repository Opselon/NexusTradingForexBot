/**
 * Research — candidate validation pipeline (legacy tab-research parity).
 *
 * Sections: hero (pipeline map) · summary KPIs (animated) · lifecycle rail
 * (share fills + filter) · registry list (drawer on click) · gate queue ·
 * worker heartbeat · analytics (failure heatmap + families) · retention
 * history · v1 datasets · playbook (static handbook, docs). Commands live
 * in ResearchCommands (confirm-guarded). Availability is backend-decided:
 * every legacy /api/research/* route answers {available:false, reason}
 * when the research subsystem is detached and the UI renders that verbatim
 * instead of showing zeros.
 *
 * Motion: .rs-* classes from ./research.css (staggered entrances, count-up
 * KPIs, rail fills, travelling pipeline pulses). All of it freezes under
 * prefers-reduced-motion. Live numbers NEVER come from the handbook — the
 * playbook is documentation; queries above are the data.
 */

import { lazy, Suspense, useEffect, useRef, useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import {
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  Segmented,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { ApiError } from "@/types/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { DistBars, FreshnessCaption, GateStepper, InfoRow, StatusPill } from "./lane5Kit";
import { registryCounters, obj, str, type Row } from "../model";
import { researchQueries, researchUseCases } from "../useCases";
import { GATE_CHAIN } from "../handbook/gates/chain";
import ResearchCommands from "./ResearchCommands";
import "./research.css";

/* Inner splits (perf lane): the drawer and the playbook (static handbook,
 * ~140 kB of docs text) render only behind state — chunk loads on first
 * open behind LOCAL Suspense boundaries, never the shell's shared one. */
const StrategyDrawer = lazy(() => import("./StrategyDrawer"));
const StrategyPlaybook = lazy(() => import("./StrategyPlaybook"));

type Tab = "registry" | "queue" | "worker" | "analytics" | "history" | "datasets" | "playbook";

const PAGE_SIZE_HINT = "bounded server-side (limit params enforced by the backend)";

/** Failed research read: message + request_id + Retry (never collapsed into
 *  an empty state or a red text line). */
function QueryError({ error, source, onRetry }: { error: unknown; source: string; onRetry: () => void }) {
  return (
    <ErrorState
      message={error instanceof Error ? error.message : `${source} failed`}
      requestId={error instanceof ApiError ? error.requestId : null}
      onRetry={onRetry}
    />
  );
}

/** One-line role per chain node in the hero pipeline map (handbook text). */
const GATE_META: Record<string, string> = {
  STATIC_VALIDATION: "schema + identity",
  BACKTEST: "deterministic replay",
  WALK_FORWARD: "purged folds",
  OOS: "holdout + bootstrap CI",
  ROBUSTNESS: "6 stress scenarios",
  SCORING: "verdict + weights",
};

/** Worker status -> live-dot tone (signal, not decoration). */
function liveTone(status: string | undefined): string {
  const s = (status ?? "").toUpperCase();
  if (s === "HEALTHY") return "";
  if (s === "DEGRADED") return "warn";
  if (s === "STUCK" || s === "FAILED") return "bad";
  return "idle";
}

/**
 * Count-up for KPI integers: animates from the previous displayed value
 * (mount: 0), rAF-eased, reduced-motion-safe. Marks numbers as freshly
 * fetched without lying about magnitude.
 */
function AnimatedNumber({ value }: { value: number }) {
  const [display, setDisplay] = useState(0);
  const shownRef = useRef(0);
  useEffect(() => {
    const reduce = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !Number.isFinite(value)) {
      shownRef.current = value;
      setDisplay(value);
      return;
    }
    const from = shownRef.current;
    if (from === value) return;
    const start = performance.now();
    const dur = 650;
    let raf = requestAnimationFrame(function tick(t: number) {
      const p = Math.min(1, (t - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      const next = from + (value - from) * eased;
      shownRef.current = next;
      setDisplay(Math.round(next));
      if (p < 1) raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <span className="rs-kpi-val">{formatNumber(display, 0)}</span>;
}

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

  const counters = registryCounters(summary);
  const registryTotal = Math.max(1, Number(summary?.total ?? 0) || 1);
  const workerStatus = str(obj(summary?.worker).status) ?? undefined;
  const unavailable = (data: { available?: boolean; reason?: string } | undefined) =>
    !data || data.available === false ? (
      <EmptyState message="Research subsystem unavailable" hint={data?.reason ?? "backend answered without availability — nothing to show"} />
    ) : null;

  const summaryUnavailable = rows?.available === false;

  return (
    <div>
      {/* ---------------------------------------------------------- hero */}
      <section className="rs-hero" aria-label="Research pipeline overview">
        <div className="rs-hero-top">
          <h2>Research</h2>
          <span className="rs-chip docs" title="Static documentation lives in the Playbook tab">
            playbook · {GATE_CHAIN.length} gates documented
          </span>
          <button type="button" className="rs-chip accent" onClick={() => setTab("playbook")}>
            open strategy handbook →
          </button>
          <span className="rs-chip" title="Research runs offline/background and never blocks the tick path">
            <span className={`rs-live-dot ${liveTone(workerStatus)}`} aria-hidden="true" />
            worker <strong>{workerStatus ?? "not reported"}</strong>
          </span>
        </div>
        <p className="rs-hero-sub">
          Candidate validation pipeline — dataset → discovery → {GATE_CHAIN.join(" → ")} → registry,
          promotion always operator-gated. Numbers below are backend responses; the playbook is
          documentation compiled from source (legacy tab-research parity).
        </p>
        <FreshnessCaption
          timestamp={v1StatusQ.data?.generated_at ?? undefined}
          source="v1 /api/v1/research/status"
          isFetching={summaryQ.isFetching}
          error={summaryQ.isError}
        />

        <div className="rs-pipe" role="list" aria-label="Gate chain order">
          {GATE_CHAIN.map((g, i) => (
            <div
              key={g}
              role="listitem"
              className="rs-pipe-node"
              style={{ animationDelay: `${0.06 * i}s`, ["--rs-delay" as string]: `${0.45 * i}s` } as CSSProperties}
            >
              <span className="rs-pipe-idx">{i + 1}/{GATE_CHAIN.length}</span>
              <div className="rs-pipe-name">{g}</div>
              <div className="rs-pipe-meta">{GATE_META[g] ?? "gate"}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ----------------------------------------------------------- KPIs */}
      <div className="grid cols-4 rs-stagger">
        <div className="rs-kpi">
          <MetricCard
            label="Registry total"
            value={
              summaryQ.isPending ? "…" : summaryUnavailable ? "n/a" : <AnimatedNumber value={Number(summary?.total ?? 0)} />
            }
            sub={summaryUnavailable ? (rows?.reason ?? "unavailable") : "strategy_intelligence_registry"}
          />
        </div>
        <div className="rs-kpi">
          <MetricCard
            label="Validated"
            value={summaryQ.isPending ? "…" : <AnimatedNumber value={Number(summary?.by_lifecycle?.VALIDATED ?? 0)} />}
            tone={summary?.by_lifecycle?.VALIDATED ? "pos" : "dim"}
            sub="lifecycle census"
          />
        </div>
        <div className="rs-kpi">
          <MetricCard
            label="Active strategies"
            value={summaryQ.isPending ? "…" : <AnimatedNumber value={Number(summary?.by_lifecycle?.ACTIVE ?? 0)} />}
            tone={summary?.by_lifecycle?.ACTIVE ? "pos" : "dim"}
            sub="backend lifecycle counts"
          />
        </div>
        <div className="rs-kpi">
          <MetricCard
            label="Research worker"
            value={<StatusBadge status={workerStatus} />}
            sub={
              obj(summary?.outcome_quality).available
                ? `closed outcomes: ${String(obj(summary?.outcome_quality).closed_outcomes ?? 0)}`
                : "outcome quality not reported"
            }
          />
        </div>
      </div>

      {/* ----------------------------------------------- lifecycle rail */}
      <Panel
        title="Lifecycle census (registry summary)"
        right={<FreshnessCaption timestamp={null} isFetching={summaryQ.isFetching} error={summaryQ.isError} />}
        tight
      >
        {summaryQ.isPending ? (
          <Skeleton count={2} />
        ) : summaryQ.isError ? (
          <QueryError error={summaryQ.error} source="/api/research/summary" onRetry={() => void summaryQ.refetch()} />
        ) : (
          <div className="rs-rail" role="group" aria-label="Filter registry by lifecycle state">
            {counters.length === 0 ? (
              <span className="muted small">no lifecycle rows reported</span>
            ) : (
              counters.map((c) => {
                const pct = Math.round((Number(c.value) / registryTotal) * 100);
                return (
                  <button
                    key={c.label}
                    type="button"
                    className={`rs-rail-chip ${lifecycle === c.label ? "active" : ""}`}
                    style={{ ["--pct" as string]: `${pct}%` } as CSSProperties}
                    aria-pressed={lifecycle === c.label}
                    title={
                      lifecycle === c.label
                        ? "filter: clear"
                        : `filter registry by ${c.label} · ${c.value} of ${summary?.total ?? 0} rows (${pct}%)`
                    }
                    onClick={() => {
                      setLifecycle(lifecycle === c.label ? undefined : c.label);
                      void queryClient.invalidateQueries({ queryKey: ["research", "registry"] });
                    }}
                  >
                    <span className="rs-rail-count">{Number(c.value) || 0}</span>
                    {c.label}
                  </button>
                );
              })
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
          { id: "playbook" as const, label: "Playbook" },
        ]}
        value={tab}
        onChange={setTab}
      />

      {/* key={tab} re-mounts the pane so the entrance animation replays per tab */}
      <div className="rs-pane" key={tab} style={{ marginTop: 12, display: "grid", gap: 12 }}>
        {tab === "registry" && (
          <Panel title={`Registry list ${lifecycle ? `· ${lifecycle}` : ""}`} right={<span className="tiny muted">{PAGE_SIZE_HINT}</span>} tight>
            {registryQ.isPending ? (
              <Skeleton count={4} />
            ) : registryQ.isError ? (
              <QueryError error={registryQ.error} source="/api/research/registry" onRetry={() => void registryQ.refetch()} />
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
            ) : queueQ.isError ? (
              <QueryError error={queueQ.error} source="/api/research/queue" onRetry={() => void queueQ.refetch()} />
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
            ) : workerQ.isError ? (
              <QueryError error={workerQ.error} source="/api/research/worker" onRetry={() => void workerQ.refetch()} />
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
            ) : analyticsQ.isError ? (
              <QueryError error={analyticsQ.error} source="/api/research/analytics" onRetry={() => void analyticsQ.refetch()} />
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
            ) : historyQ.isError ? (
              <QueryError error={historyQ.error} source="/api/research/history" onRetry={() => void historyQ.refetch()} />
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
              <QueryError
                error={datasetsQ.error}
                source="/api/v1/research/datasets"
                onRetry={() => void datasetsQ.refetch()}
              />
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

        {tab === "playbook" && (
          <Panel
            title="Strategy playbook — gates, lifecycle, scoring, economics"
            right={<span className="tiny muted">documentation · compiled from backend source</span>}
            tight
          >
            <Suspense fallback={<Skeleton count={6} height={40} />}>
              <StrategyPlaybook />
            </Suspense>
          </Panel>
        )}
      </div>

      {selected && (
        <Suspense fallback={<Skeleton count={4} height={64} />}>
          <StrategyDrawer strategyId={selected} onClose={() => setSelected(null)} />
        </Suspense>
      )}
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
      ) : diagQ.isError ? (
        <QueryError error={diagQ.error} source="/api/research/diagnostics" onRetry={() => void diagQ.refetch()} />
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
