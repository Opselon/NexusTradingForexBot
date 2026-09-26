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

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
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
import { formatDateTime, formatNumber } from "@/lib/format";
import { DistBars, FreshnessCaption, GateStepper, InfoRow, StatusPill } from "./lane5Kit";
import { RsTable, applySort, useSort, type RsColumn, type SortValue } from "./rsTable";
import { registryCounters, obj, str, type ResearchStrategyVo, type Row } from "../model";
import { researchQueries, researchUseCases } from "../useCases";
import { GATE_CHAIN } from "../handbook/gateChain";
import ResearchCommands from "./ResearchCommands";
import StrategyDrawer from "./StrategyDrawer";
import StrategyPlaybookLazy from "./StrategyPlaybookLazy";
import "./research.css";
import "./research-hero.css";

type Tab = "registry" | "queue" | "worker" | "analytics" | "history" | "datasets" | "playbook";

const PAGE_SIZE_HINT = "bounded server-side (limit params enforced by the backend)";

type RegistryCol = "strategy" | "lifecycle" | "conf" | "samples" | "score" | "updated";

/** Registry table headers — a `key` marks the column as sortable. */
const REGISTRY_COLS: Array<RsColumn<RegistryCol>> = [
  { label: "strategy", key: "strategy", title: "sort by strategy id" },
  { label: "lifecycle", key: "lifecycle", title: "sort by lifecycle state" },
  { label: "conf", key: "conf", num: true, title: "sort by confidence" },
  { label: "samples", key: "samples", num: true, title: "sort by sample count" },
  { label: "score", key: "score", num: true, title: "sort by score" },
  { label: "updated", key: "updated", title: "sort by last update" },
  { label: "" },
];

/**
 * Pure accessors over the already-loaded rows: a header click only REORDERS
 * what the backend returned (null/absent cells sink, never zero-filled).
 */
const REGISTRY_ACCESSORS: Record<RegistryCol, (r: ResearchStrategyVo) => SortValue> = {
  strategy: (r) => r.strategyId,
  lifecycle: (r) => r.lifecycle,
  conf: (r) => r.confidence,
  samples: (r) => r.sampleCount,
  score: (r) => r.score,
  updated: (r) => r.updatedAt,
};

/**
 * Endpoint provenance chips — verbatim GET paths this page's own queries
 * issue (see ../api.ts). Provenance only: the chips are a constant list of
 * the routes already being called, never a trigger for a fetch.
 */
const RESEARCH_ENDPOINTS: readonly string[] = [
  "GET /api/research/summary",
  "GET /api/research/registry",
  "GET /api/research/queue",
  "GET /api/research/worker",
  "GET /api/research/analytics",
  "GET /api/research/history",
  "GET /api/research/diagnostics",
  "GET /api/v1/research/status",
  "GET /api/v1/research/datasets",
];

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
 * Same backend word, mapped to the hero status-pill modifier. The pill only
 * restates `summary.worker.status` — no frontend verdict, no color invented
 * for a word the backend did not send.
 */
function workerPill(status: string | undefined): string {
  const s = (status ?? "").toUpperCase();
  if (s === "HEALTHY") return "on";
  if (s === "DEGRADED") return "warn";
  if (s === "STUCK" || s === "FAILED") return "bad";
  return "unknown";
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
  // perf: registry map+sort derived only when the query data changes
  // (deps: registryQ.data — the only reactive value read).
  const registry = useMemo(
    () => (registryQ.data?.available === true ? researchUseCases.registryList(registryQ.data.registry ?? []) : []),
    [registryQ.data],
  );
  // perf: the header sort is a pure function of the loaded rows + the sort
  // state; untouched (key=null) the accessor list stays empty and the array
  // is returned as-is, i.e. exactly the backend's own order.
  const regSort = useSort<RegistryCol>();
  const registryRows = useMemo(
    () => applySort(registry, regSort.sort.key ? REGISTRY_ACCESSORS[regSort.sort.key] : null, regSort.sort.dir),
    [registry, regSort.sort.key, regSort.sort.dir],
  );
  // perf: queued/running census derived only when the queue payload changes
  // (deps: queueQ.data — the only reactive value read).
  const queued = useMemo(() => {
    const queueData = obj(queueQ.data?.queue);
    const queuedMap = obj(queueData.queued);
    const out: Array<{ label: string; count: number }> = [];
    for (const [gateType, statusMap] of Object.entries(queuedMap)) {
      for (const [status, count] of Object.entries(obj(statusMap))) {
        if (status === "QUEUED" || status === "RUNNING") out.push({ label: `${gateType} · ${status}`, count: Number(count) || 0 });
      }
    }
    return out;
  }, [queueQ.data]);
  const queue = useMemo(() => obj(queueQ.data?.queue), [queueQ.data]);
  // perf: heatmap slices derived only when the analytics payload changes
  // (deps: analyticsQ.data — the only reactive value read).
  const heatmap = useMemo(() => obj(analyticsQ.data?.heatmap), [analyticsQ.data]);
  const byGate = useMemo(() => obj(heatmap.by_gate), [heatmap]);
  const rejectionReasons = useMemo(() => obj(heatmap.rejection_reasons), [heatmap]);

  // perf: lifecycle census counters + the derived rail totals are pure
  // functions of the summary payload (the only reactive value read) —
  // memoizing keeps the 1s/15s refetch ticks from re-deriving them per render.
  const counters = useMemo(() => registryCounters(summary), [summary]);
  const registryTotal = useMemo(() => Math.max(1, Number(summary?.total ?? 0) || 1), [summary]);
  const workerStatus = useMemo(() => str(obj(summary?.worker).status) ?? undefined, [summary]);
  // perf: rejection-reason bar rows derived only when that slice changes.
  const rejectionRows = useMemo(
    () => Object.entries(rejectionReasons).slice(0, 15).map(([k, v]) => ({ label: k, count: Number(v) || 0 })),
    [rejectionReasons],
  );
  // perf: by-gate bar rows derived only when the byGate slice changes.
  const byGateRows = useMemo(
    () => Object.entries(byGate).map(([k, v]) => ({ label: k, count: Number(v) || 0 })),
    [byGate],
  );
  const unavailable = (data: { available?: boolean; reason?: string } | undefined) =>
    !data || data.available === false ? (
      <EmptyState message="Research subsystem unavailable" hint={data?.reason ?? "backend answered without availability — nothing to show"} />
    ) : null;

  /**
   * §9 error presentation: a read that FAILED renders the shared error
   * surface with the backend's own message instead of collapsing into the
   * "subsystem unavailable" empty state, which would misreport a transport
   * failure as an intentional absence. Read-only — no refetch/retry wiring,
   * no query option, key or interval touched.
   */
  const failed = (q: { isError: boolean; error: unknown }): ReactNode =>
    q.isError ? (
      <ErrorState message={q.error instanceof Error ? q.error.message : "backend request failed"} />
    ) : null;

  const summaryUnavailable = rows?.available === false;
  /** summary read failed with nothing ever fetched — the KPIs must not show
   *  a zero the backend never sent (§9). */
  const summaryLost = summaryQ.isError && summary === undefined;

  return (
    <div className="rs-page">
      {/* ---------------------------------------------------------- hero */}
      <header className="rs-hero" aria-label="Research pipeline overview">
        <div className="rs-hero-row">
          <div className="rs-hero-main">
            <div className="rs-kicker">
              <span className="dot" aria-hidden="true" />
              MARKET &amp; RESEARCH
            </div>
            <h1 className="rs-title">
              <span className="glyph" aria-hidden="true">
                ◈
              </span>
              <span className="word">Research</span>
            </h1>
            <p className="rs-hero-sub">
              Candidate validation pipeline — dataset → discovery → {GATE_CHAIN.join(" → ")} → registry,
              promotion always operator-gated. Numbers below are backend responses; the playbook is
              documentation compiled from source (legacy tab-research parity).
            </p>
            <div className="rs-ep-chips" role="list" aria-label="Endpoints this page reads">
              {RESEARCH_ENDPOINTS.map((ep) => (
                <span className="rs-ep" role="listitem" key={ep}>
                  {ep}
                </span>
              ))}
            </div>
          </div>
          <div className="rs-hero-side">
            <div className="rs-side-pills">
              <span
                className={`rs-state-pill ${workerPill(workerStatus)}`}
                title="worker status — summary.worker.status as the backend reports it"
              >
                <span className={`rs-live-dot ${liveTone(workerStatus)}`} aria-hidden="true" />
                worker <strong>{workerStatus ?? "not reported"}</strong>
              </span>
              <span className="rs-side-pill" title="Static documentation lives in the Playbook tab">
                playbook · {GATE_CHAIN.length} gates documented
              </span>
              <button type="button" className="rs-chip accent" onClick={() => setTab("playbook")}>
                open strategy handbook →
              </button>
            </div>
            <FreshnessCaption
              timestamp={v1StatusQ.data?.generated_at ?? undefined}
              source="v1 /api/v1/research/status"
              isFetching={summaryQ.isFetching}
              error={summaryQ.isError}
            />
          </div>
        </div>

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
      </header>

      {/* ----------------------------------------------------------- KPIs */}
      <div className="grid cols-4 rs-stagger">
        <div className="rs-kpi">
          <MetricCard
            label="Registry total"
            value={
              summaryQ.isPending ? (
                "…"
              ) : summaryUnavailable ? (
                "n/a"
              ) : summaryLost ? (
                "—"
              ) : (
                <AnimatedNumber value={Number(summary?.total ?? 0)} />
              )
            }
            sub={
              summaryLost
                ? summaryQ.error instanceof Error
                  ? summaryQ.error.message
                  : "summary request failed"
                : summaryUnavailable
                  ? (rows?.reason ?? "unavailable")
                  : "strategy_intelligence_registry"
            }
          />
        </div>
        <div className="rs-kpi">
          <MetricCard
            label="Validated"
            value={summaryQ.isPending ? "…" : summaryLost ? "—" : <AnimatedNumber value={Number(summary?.by_lifecycle?.VALIDATED ?? 0)} />}
            tone={summary?.by_lifecycle?.VALIDATED ? "pos" : "dim"}
            sub="lifecycle census"
          />
        </div>
        <div className="rs-kpi">
          <MetricCard
            label="Active strategies"
            value={summaryQ.isPending ? "…" : summaryLost ? "—" : <AnimatedNumber value={Number(summary?.by_lifecycle?.ACTIVE ?? 0)} />}
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
        ) : (
          failed(summaryQ) ?? (
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
          )
        )}
      </Panel>

      <div className="rs-cmdbar">
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
      <div className="rs-pane" key={tab}>
        {tab === "registry" && (
          <Panel title={`Registry list ${lifecycle ? `· ${lifecycle}` : ""}`} right={<span className="tiny muted">{PAGE_SIZE_HINT}</span>} tight>
            {registryQ.isPending ? (
              <Skeleton count={4} />
            ) : failed(registryQ) ?? unavailable(registryQ.data) ?? (
              <>
                {registry.length === 0 ? (
                  <EmptyState message="Registry is empty for this filter." hint="/api/research/health explains WHY (source trades, rejections, attempts)." />
                ) : (
                  <RsTable cols={REGISTRY_COLS} sort={regSort.sort} onToggle={regSort.toggle}>
                    {registryRows.map((s) => (
                      <tr key={s.strategyId}>
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
                  </RsTable>
                )}
              </>
            )}
          </Panel>
        )}

        {tab === "queue" && (
          <Panel title="Gate queue census" right={<FreshnessCaption timestamp={null} isFetching={queueQ.isFetching} error={queueQ.isError} />} tight>
            {queueQ.isPending ? (
              <Skeleton count={3} />
            ) : failed(queueQ) ?? unavailable(queueQ.data) ?? (
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
            ) : failed(workerQ) ?? unavailable(workerQ.data) ?? (
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
            ) : failed(analyticsQ) ?? unavailable(analyticsQ.data) ?? (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">failed gates (total: {String(heatmap.total_failures ?? 0)})</div>
                  <DistBars rows={byGateRows} tone="var(--red)" />
                </div>
                <div>
                  <div className="section-title">rejection reasons</div>
                  <DistBars rows={rejectionRows} tone="var(--amber)" />
                </div>
              </div>
            )}
          </Panel>
        )}

        {tab === "history" && (
          <Panel title="Retention (live vs archive — archive-only contract)" tight>
            {historyQ.isPending ? (
              <Skeleton count={2} />
            ) : failed(historyQ) ?? unavailable(historyQ.data) ?? (
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
            ) : failed(datasetsQ) ?? (
              (datasetsQ.data?.datasets ?? []).length === 0 ? (
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
              )
            )}
          </Panel>
        )}

        {tab === "playbook" && (
          <Panel
            title="Strategy playbook — gates, lifecycle, scoring, economics"
            right={<span className="tiny muted">documentation · compiled from backend source</span>}
            tight
          >
            <StrategyPlaybookLazy />
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
  // perf: stepper rows derived only when the diagnostics payload changes
  // (deps: diagQ.data — the only reactive value read).
  const gateRows = useMemo(
    () =>
      blocked.slice(0, 8).map((g) => ({
        name: `${str(g.gate_type) ?? "gate"} · ${(str(g.strategy_id) ?? "").slice(0, 10)}`,
        status: str(g.status) ?? "UNKNOWN",
        reason: str(g.failure_reason),
      })),
    [blocked],
  );
  return (
    <div>
      <div className="section-title">blocked / failed gates (diagnostics)</div>
      {diagQ.isPending ? (
        <Skeleton count={2} />
      ) : diagQ.isError ? (
        <ErrorState message={diagQ.error instanceof Error ? diagQ.error.message : "diagnostics request failed"} />
      ) : gateRows.length === 0 ? (
        <EmptyState message="No blocked gates reported." />
      ) : (
        <GateStepper gates={gateRows} />
      )}
    </div>
  );
}
