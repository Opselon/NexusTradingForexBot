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

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
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
import { useI18n } from "@/stores/i18nStore";
import { DistBars, FreshnessCaption, GateStepper, InfoRow, StatusPill } from "./lane5Kit";
import { registryCounters, obj, str, type Row } from "../model";
import { researchQueries, researchUseCases } from "../useCases";
import { GATE_CHAIN } from "../handbook";
import ResearchCommands from "./ResearchCommands";
import StrategyDrawer from "./StrategyDrawer";
import StrategyPlaybook from "./StrategyPlaybook";
import "./research.css";

type Tab = "registry" | "queue" | "worker" | "analytics" | "history" | "datasets" | "playbook";

/** One-line role per chain node in the hero pipeline map (handbook text). */
const GATE_META: Record<string, { en: string; key: string }> = {
  STATIC_VALIDATION: { en: "schema + identity", key: "research.page.gm_static" },
  BACKTEST: { en: "deterministic replay", key: "research.page.gm_backtest" },
  WALK_FORWARD: { en: "purged folds", key: "research.page.gm_walkforward" },
  OOS: { en: "holdout + bootstrap CI", key: "research.page.gm_oos" },
  ROBUSTNESS: { en: "6 stress scenarios", key: "research.page.gm_robustness" },
  SCORING: { en: "verdict + weights", key: "research.page.gm_scoring" },
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
  const t = useI18n((s) => s.t);
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
      <EmptyState
        message={t("research.page.unavailable", "Research subsystem unavailable")}
        hint={data?.reason ?? t("research.page.no_availability", "backend answered without availability — nothing to show")}
      />
    ) : null;

  const summaryUnavailable = rows?.available === false;

  return (
    <div>
      {/* ---------------------------------------------------------- hero */}
      <section className="rs-hero" aria-label={t("research.page.hero_aria", "Research pipeline overview")}>
        <div className="rs-hero-top">
          <h2>{t("nav.feature.research", "Research")}</h2>
          <span className="rs-chip docs" title={t("research.page.playbook_chip_title", "Static documentation lives in the Playbook tab")}>
            {t("research.page.playbook_chip", "playbook · {n} gates documented", { n: GATE_CHAIN.length })}
          </span>
          <button type="button" className="rs-chip accent" onClick={() => setTab("playbook")}>
            {t("research.page.open_playbook", "open strategy handbook →")}
          </button>
          <span className="rs-chip" title={t("research.page.offline_chip_title", "Research runs offline/background and never blocks the tick path")}>
            <span className={`rs-live-dot ${liveTone(workerStatus)}`} aria-hidden="true" />
            {t("research.page.worker_label", "worker")} <strong>{workerStatus ?? t("research.page.worker_not_reported", "not reported")}</strong>
          </span>
        </div>
        <p className="rs-hero-sub">
          {t(
            "research.page.hero_sub",
            "Candidate validation pipeline — dataset → discovery → {chain} → registry, promotion always operator-gated. Numbers below are backend responses; the playbook is documentation compiled from source (legacy tab-research parity).",
            { chain: GATE_CHAIN.join(" → ") },
          )}
        </p>
        <FreshnessCaption
          timestamp={v1StatusQ.data?.generated_at ?? undefined}
          source="v1 /api/v1/research/status"
          isFetching={summaryQ.isFetching}
          error={summaryQ.isError}
        />

        <div className="rs-pipe" role="list" aria-label={t("research.page.chain_aria", "Gate chain order")}>
          {GATE_CHAIN.map((g, i) => (
            <div
              key={g}
              role="listitem"
              className="rs-pipe-node"
              style={{ animationDelay: `${0.06 * i}s`, ["--rs-delay" as string]: `${0.45 * i}s` } as CSSProperties}
            >
              <span className="rs-pipe-idx" dir="ltr">{i + 1}/{GATE_CHAIN.length}</span>
              <div className="rs-pipe-name" dir="ltr">{g}</div>
              <div className="rs-pipe-meta">{GATE_META[g] ? t(GATE_META[g].key, GATE_META[g].en) : t("research.page.gate_fallback", "gate")}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ----------------------------------------------------------- KPIs */}
      <div className="grid cols-4 rs-stagger">
        <div className="rs-kpi">
          <MetricCard
            label={t("research.page.kpi_total", "Registry total")}
            value={
              summaryQ.isPending ? "…" : summaryUnavailable ? t("research.page.unavailable_short", "n/a") : <AnimatedNumber value={Number(summary?.total ?? 0)} />
            }
            sub={summaryUnavailable ? (rows?.reason ?? t("research.page.unavailable_short", "unavailable")) : "strategy_intelligence_registry"}
          />
        </div>
        <div className="rs-kpi">
          <MetricCard
            label={t("research.page.kpi_validated", "Validated")}
            value={summaryQ.isPending ? "…" : <AnimatedNumber value={Number(summary?.by_lifecycle?.VALIDATED ?? 0)} />}
            tone={summary?.by_lifecycle?.VALIDATED ? "pos" : "dim"}
            sub={t("research.page.kpi_validated_sub", "lifecycle census")}
          />
        </div>
        <div className="rs-kpi">
          <MetricCard
            label={t("research.page.kpi_active", "Active strategies")}
            value={summaryQ.isPending ? "…" : <AnimatedNumber value={Number(summary?.by_lifecycle?.ACTIVE ?? 0)} />}
            tone={summary?.by_lifecycle?.ACTIVE ? "pos" : "dim"}
            sub={t("research.page.kpi_active_sub", "backend lifecycle counts")}
          />
        </div>
        <div className="rs-kpi">
          <MetricCard
            label={t("research.page.kpi_worker", "Research worker")}
            value={<StatusBadge status={workerStatus} />}
            sub={
              obj(summary?.outcome_quality).available
                ? t("research.page.kpi_worker_closed", "closed outcomes: {n}", { n: String(obj(summary?.outcome_quality).closed_outcomes ?? 0) })
                : t("research.page.kpi_worker_noquality", "outcome quality not reported")
            }
          />
        </div>
      </div>

      {/* ----------------------------------------------- lifecycle rail */}
      <Panel
        title={t("research.page.lifecycle_panel", "Lifecycle census (registry summary)")}
        right={<FreshnessCaption timestamp={null} isFetching={summaryQ.isFetching} error={summaryQ.isError} />}
        tight
      >
        {summaryQ.isPending ? (
          <Skeleton count={2} />
        ) : (
          <div className="rs-rail" role="group" aria-label={t("research.page.filter_by_lifecycle", "Filter registry by lifecycle state")}>
            {counters.length === 0 ? (
              <span className="muted small">{t("research.page.no_lifecycle_rows", "no lifecycle rows reported")}</span>
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
                    dir="ltr"
                    title={
                      lifecycle === c.label
                        ? t("research.page.filter_clear", "filter: clear")
                        : t(
                            "research.page.filter_by",
                            "filter registry by {s} · {c} of {t} rows ({p}%)",
                            { s: c.label, c: c.value, t: summary?.total ?? 0, p: pct },
                          )
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
          { id: "registry" as const, label: t("research.page.tab_registry", "Registry") },
          { id: "queue" as const, label: t("research.page.tab_queue", "Gate queue") },
          { id: "worker" as const, label: t("research.page.tab_worker", "Worker") },
          { id: "analytics" as const, label: t("research.page.tab_analytics", "Analytics") },
          { id: "history" as const, label: t("research.page.tab_history", "Retention") },
          { id: "datasets" as const, label: t("research.page.tab_datasets", "Datasets (v1)") },
          { id: "playbook" as const, label: t("research.page.tab_playbook", "Playbook") },
        ]}
        value={tab}
        onChange={setTab}
      />

      {/* key={tab} re-mounts the pane so the entrance animation replays per tab */}
      <div className="rs-pane" key={tab} style={{ marginTop: 12, display: "grid", gap: 12 }}>
        {tab === "registry" && (
          <Panel title={t("research.page.registry_list", "Registry list {l}", { l: lifecycle ? `· ${lifecycle}` : "" })} right={<span className="tiny muted">{t("research.page.size_hint", "bounded server-side (limit params enforced by the backend)")}</span>} tight>
            {registryQ.isPending ? (
              <Skeleton count={4} />
            ) : unavailable(registryQ.data) ?? (
              <>
                {registry.length === 0 ? (
                  <EmptyState
                    message={t("research.page.registry_empty", "Registry is empty for this filter.")}
                    hint={t("research.page.registry_empty_hint", "/api/research/health explains WHY (source trades, rejections, attempts).")}
                  />
                ) : (
                  <DataTable
                    headers={[
                      { label: t("research.th.strategy", "strategy") },
                      { label: t("research.th.lifecycle", "lifecycle") },
                      { label: t("research.th.conf", "conf"), num: true },
                      { label: t("research.th.samples", "samples"), num: true },
                      { label: t("research.th.score", "score"), num: true },
                      { label: t("research.th.updated", "updated") },
                      { label: "" },
                    ]}
                  >
                    {registry.map((s, i) => (
                      <tr key={`${s.strategyId}-${i}`}>
                        <td className="inline-mono tiny" title={s.strategyId} dir="ltr">
                          {s.strategyId.slice(0, 16)}…{s.version ? ` v${s.version}` : ""}
                        </td>
                        <td>
                          <StatusPill status={s.lifecycle} />
                        </td>
                        <td className="num tiny" dir="ltr">{s.confidence === null ? "—" : formatNumber(s.confidence, 3)}</td>
                        <td className="num tiny" dir="ltr">{s.sampleCount ?? "—"}</td>
                        <td className="num tiny" dir="ltr">{s.score === null ? "—" : formatNumber(s.score, 2)}</td>
                        <td className="tiny">{s.updatedAt ? formatDateTime(s.updatedAt) : "—"}</td>
                        <td>
                          <button className="btn small ghost" onClick={() => setSelected(s.strategyId)}>
                            {t("research.page.trace_btn", "trace")}
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
          <Panel title={t("research.page.queue_panel", "Gate queue census")} right={<FreshnessCaption timestamp={null} isFetching={queueQ.isFetching} error={queueQ.isError} />} tight>
            {queueQ.isPending ? (
              <Skeleton count={3} />
            ) : unavailable(queueQ.data) ?? (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">{t("research.page.queue_by_gate", "queued / running by gate type")}</div>
                  <DistBars rows={queued} tone="var(--amber)" />
                </div>
                <div>
                  <div className="section-title">{t("research.page.running_now", "running now")}</div>
                  {(queue.running as Row[] | undefined)?.length ? (
                    <DataTable
                      headers={[
                        { label: t("research.th.gate", "gate") },
                        { label: t("research.th.strategy", "strategy") },
                        { label: t("research.th.status", "status") },
                      ]}
                    >
                      {(queue.running as Row[]).map((r: Row, i: number) => (
                        <tr key={i}>
                          <td className="tiny" dir="ltr">{str(r.gate_type) ?? "—"}</td>
                          <td className="inline-mono tiny" dir="ltr">{str(r.strategy_id)?.slice(0, 14) ?? "—"}</td>
                          <td>
                            <StatusPill status={str(r.status)} />
                          </td>
                        </tr>
                      ))}
                    </DataTable>
                  ) : (
                    <EmptyState message={t("research.page.nothing_running", "Nothing running right now.")} />
                  )}
                </div>
              </div>
            )}
          </Panel>
        )}

        {tab === "worker" && (
          <Panel title={t("research.page.worker_panel", "Worker heartbeat + diagnostics")} right={<FreshnessCaption timestamp={null} isFetching={workerQ.isFetching || !workerQ.isFetched} error={workerQ.isError} />} tight>
            {workerQ.isPending ? (
              <Skeleton count={3} />
            ) : unavailable(workerQ.data) ?? (
              <div className="grid cols-2">
                <div>
                  <dl className="kv">
                    <InfoRow label={t("research.page.w_health", "health")} value={<StatusPill status={str(obj(workerQ.data?.worker).health) ?? "UNKNOWN"} />} />
                    <InfoRow label={t("research.page.w_lastbeat", "last beat")} value={formatDateTime(str(obj(obj(workerQ.data?.worker).heartbeat).last_beat_at))} />
                    <InfoRow label={t("research.page.w_cycle", "cycle")} value={String(obj(obj(workerQ.data?.worker).runtime).cycle_count ?? "—")} />
                    <InfoRow label={t("research.th.status", "status")} value={str(obj(obj(workerQ.data?.worker).runtime).status) ?? "—"} />
                    <InfoRow label={t("research.page.w_lasterror", "last error")} value={str(obj(obj(workerQ.data?.worker).runtime).last_error) ?? t("research.page.w_no_error", "none reported")} />
                  </dl>
                </div>
                <ResearchDiagMini />
              </div>
            )}
          </Panel>
        )}

        {tab === "analytics" && (
          <Panel title={t("research.page.analytics_panel", "Failure heatmap + families")} right={<FreshnessCaption timestamp={null} isFetching={analyticsQ.isFetching} error={analyticsQ.isError} />} tight>
            {analyticsQ.isPending ? (
              <Skeleton count={3} />
            ) : unavailable(analyticsQ.data) ?? (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">{t("research.page.failed_gates", "failed gates (total: {n})", { n: String(heatmap.total_failures ?? 0) })}</div>
                  <DistBars rows={Object.entries(byGate).map(([k, v]) => ({ label: k, count: Number(v) || 0 }))} tone="var(--red)" />
                </div>
                <div>
                  <div className="section-title">{t("research.page.rejection_reasons", "rejection reasons")}</div>
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
          <Panel title={t("research.page.retention_panel", "Retention (live vs archive — archive-only contract)")} tight>
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
          <Panel title={t("research.page.datasets_panel", "v1 datasets (provenance from real runs)")} right={<span className="tiny muted">/api/v1/research/datasets</span>} tight>
            {datasetsQ.isPending ? (
              <Skeleton count={3} />
            ) : datasetsQ.isError ? (
              <div className="small tx-bad">
                {datasetsQ.error instanceof Error ? datasetsQ.error.message : t("research.page.request_failed", "request failed")}
              </div>
            ) : (datasetsQ.data?.datasets ?? []).length === 0 ? (
              <EmptyState message={t("research.page.no_datasets", "No datasets derived from runs yet.")} />
            ) : (
              <DataTable headers={[{ label: "dataset_id" }, { label: t("research.th.runs", "runs"), num: true }]}>
                {(datasetsQ.data?.datasets ?? []).map((d, i) => (
                  <tr key={i}>
                    <td className="inline-mono tiny" dir="ltr">{d.dataset_id ?? "—"}</td>
                    <td className="num tiny" dir="ltr">{d.run_count ?? 0}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>
        )}

        {tab === "playbook" && (
          <Panel
            title={t("research.page.playbook_panel", "Strategy playbook — gates, lifecycle, scoring, economics")}
            right={<span className="tiny muted">{t("research.page.playbook_panel_sub", "documentation · compiled from backend source")}</span>}
            tight
          >
            <StrategyPlaybook />
          </Panel>
        )}
      </div>

      {selected && <StrategyDrawer strategyId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

/** Compact diagnostics block inside the worker tab (blocked-gate census). */
function ResearchDiagMini() {
  const t = useI18n((s) => s.t);
  const diagQ = useQuery({
    queryKey: ["research", "diagnostics"],
    queryFn: ({ signal }) => researchQueries.diagnostics(signal),
    retry: false,
  });
  const blocked = diagQ.data?.blocked_gates ?? [];
  return (
    <div>
      <div className="section-title">{t("research.page.blocked_diag", "blocked / failed gates (diagnostics)")}</div>
      {diagQ.isPending ? (
        <Skeleton count={2} />
      ) : blocked.length === 0 ? (
        <EmptyState message={t("research.page.no_blocked", "No blocked gates reported.")} />
      ) : (
        <GateStepper
          gates={blocked.slice(0, 8).map((g) => ({
            name: `${str(g.gate_type) ?? t("research.page.gate_fallback", "gate")} · ${(str(g.strategy_id) ?? "").slice(0, 10)}`,
            status: str(g.status) ?? "UNKNOWN",
            reason: str(g.failure_reason),
          }))}
        />
      )}
    </div>
  );
}
