/**
 * Sources grid — /api/news/sources rows + the subsystem health block
 * (/api/news/health: worker telemetry, LLM budget, calendar gate).
 *
 * Each source row renders the backend's own health columns; the heat strip is
 * a rendering of `consecutive_failures` only (null = UNKNOWN row).
 */

import { useMemo } from "react";
import { EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { HeatBar, Sparkline } from "@/components/viz";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useNewsHealth, useNewsSources } from "../hooks";
import type { SourceVM } from "../model";
import { FreshnessNote, asErrorText, jsonInline, jsonPretty } from "./shared";

export function NewsSourcesPanel() {
  const sources = useNewsSources();
  const health = useNewsHealth();

  const rows: SourceVM[] = sources.data ?? [];
  const h = health.data?.health;
  const worker = health.data?.worker;
  const budget = health.data?.llm_budget;
  const gate = health.data?.event_gate;

  // perf: serialize/slice the health blocks once per data change instead of on
  // every render — the 30s health poll re-renders this panel on every tick.
  const dbJson = useMemo(() => (h?.db ? jsonPretty(h.db) : ""), [h?.db]);
  const gateJson = useMemo(() => (gate ? jsonPretty(gate) : ""), [gate]);
  const workerStats = useMemo(
    () =>
      (worker ? Object.entries(worker).slice(0, 8) : []).map(([k, v]) => ({
        k,
        text: typeof v === "object" ? jsonInline(v) : String(v),
      })),
    [worker],
  );
  const budgetStats = useMemo(
    () =>
      (budget ? Object.entries(budget).slice(0, 8) : []).map(([k, v]) => ({
        k,
        text: typeof v === "object" ? jsonInline(v) : String(v),
      })),
    [budget],
  );

  return (
    <div className="grid cols-2">
      <Panel
        title={`Source registry (${rows.length})`}
        right={<FreshnessNote updatedAtMs={sources.dataUpdatedAt ?? null} label="sources" />}
      >
        {sources.isPending ? (
          <Skeleton count={5} height={30} />
        ) : sources.isError ? (
          <ErrorState message={asErrorText(sources.error)} onRetry={() => sources.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState message="No sources registered." hint="The news DB has no seed rows — run self-heal or start the engine." />
        ) : (
          <div className="news-sources">
            {rows.map((s) => (
              <div className="news-source" key={s.source.source_id}>
                <div className="name">
                  {s.source.name || s.source.source_id} <StatusBadge status={s.label} />
                </div>
                <div className="sub">
                  {s.source.tier ?? "—"} · {s.source.kind ?? "RSS"} · poll {s.source.poll_interval_sec ?? "—"}s · prio {formatNumber(s.source.priority, 2)}
                </div>
                <div className="sub">
                  ok {s.lastSuccessAt ? formatDateTime(s.lastSuccessAt) : "never"} · fail {s.lastFailureAt ? formatDateTime(s.lastFailureAt) : "—"}
                </div>
                <div className="sub">
                  streak {s.failStreak ?? "—"} · rate-limited {s.rateLimited ? "yes" : "no"} · backoff {s.backoffUntil ? formatDateTime(s.backoffUntil) : "—"}
                </div>
                <div style={{ marginTop: 6 }}>
                  <HeatBar
                    segments={5}
                    invert
                    items={[{ label: "fail", value: s.successRatio === null ? null : 1 - s.successRatio, caption: s.successRatio === null ? "unknown" : `${Math.round(s.successRatio * 100)}% ok` }]}
                    scaleCaptions={["clean", "5+ fails"]}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="Subsystem health"
        right={
          <>
            <button className="btn small ghost" onClick={() => health.refetch()} disabled={health.isFetching}>
              {health.isFetching ? "checking…" : "Re-check"}
            </button>
            <FreshnessNote updatedAtMs={health.dataUpdatedAt ?? null} label="health" staleAfterMs={90_000} />
          </>
        }
      >
        {health.isPending ? (
          <Skeleton count={4} height={28} />
        ) : health.isError ? (
          <ErrorState message={asErrorText(health.error)} onRetry={() => health.refetch()} />
        ) : !health.data?.available ? (
          <EmptyState message="News engine reports available=false." hint="Enable the news engine in the state panel above; health telemetry starts with the worker." />
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            <dl className="kv">
              <dt>subsystem</dt>
              <dd>{h?.subsystem ?? "NEWS_INTELLIGENCE"}</dd>
              <dt>state</dt>
              <dd>{h?.state ?? "—"}</dd>
              <dt>stale</dt>
              <dd>{h?.stale === undefined ? "—" : h.stale ? "yes" : "no"}</dd>
              <dt>cycle_count</dt>
              <dd>{h?.cycle_count ?? "—"}</dd>
              <dt>last_cycle_at</dt>
              <dd>{h?.last_cycle_at ? formatDateTime(h.last_cycle_at) : "—"}</dd>
              <dt>last_error</dt>
              <dd style={{ textAlign: "start", color: h?.last_error ? "var(--red)" : undefined }}>{h?.last_error || "none"}</dd>
            </dl>
            {h?.db && (
              <div>
                <div className="section-title">news.db summary (backend)</div>
                <pre tabIndex={0} className="tiny inline-mono" style={{ background: "var(--bg-inset)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, margin: 0, overflowX: "auto" }}>
                  {dbJson}
                </pre>
              </div>
            )}
            {worker && (
              <div>
                <div className="section-title">worker telemetry</div>
                <div className="statline">
                  {workerStats.map((s) => (
                    <span key={s.k}>
                      {s.k}: <b className="inline-mono">{s.text}</b>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {budget && (
              <div>
                <div className="section-title">LLM budget (news-scoped spend)</div>
                <div className="statline">
                  {budgetStats.map((s) => (
                    <span key={s.k}>
                      {s.k}: <b className="inline-mono">{s.text}</b>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {gate && (
              <div>
                <div className="section-title">Calendar event gate</div>
                <pre tabIndex={0} className="tiny inline-mono" style={{ background: "var(--bg-inset)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, margin: 0, overflowX: "auto" }}>
                  {gateJson}
                </pre>
              </div>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}

/** Tiny per-source failure streak trend, rendered when the backend sends a series. */
export function SourceTrend({ series }: { series: number[] }) {
  if (series.length < 2) return <span className="faint tiny">no history</span>;
  return <Sparkline values={series} tone="dim" label="failure streak" width={90} height={18} />;
}
