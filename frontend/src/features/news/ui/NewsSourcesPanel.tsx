/**
 * Sources grid — /api/news/sources rows + the subsystem health block
 * (/api/news/health: worker telemetry, LLM budget, calendar gate).
 *
 * Each source row renders the backend's own health columns; the heat strip is
 * a rendering of `consecutive_failures` only (null = UNKNOWN row).
 *
 * StatusBadge label words (OK/FAILING/…) are tone-mapped by the shared badge,
 * so they stay verbatim (tone keys must keep matching); natural-language
 * labels and section titles translate here.
 */

import { useMemo } from "react";
import { EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { HeatBar, Sparkline } from "@/components/viz";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { useNewsHealth, useNewsSources } from "../hooks";
import type { SourceVM } from "../model";
import { FreshnessNote, asErrorText, jsonInline, jsonPretty } from "./shared";

export function NewsSourcesPanel() {
  const t = useI18n((s) => s.t);
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
        title={t("news.sources.registry", "Source registry ({n})", { n: rows.length })}
        right={<FreshnessNote updatedAtMs={sources.dataUpdatedAt ?? null} label={t("news.fresh.sources", "sources")} />}
      >
        {sources.isPending ? (
          <Skeleton count={5} height={30} />
        ) : sources.isError ? (
          <ErrorState message={asErrorText(sources.error, t)} onRetry={() => sources.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            message={t("news.sources.empty", "No sources registered.")}
            hint={t(
              "news.sources.empty_hint",
              "The news DB has no seed rows — run self-heal or start the engine.",
            )}
          />
        ) : (
          <div className="news-sources">
            {rows.map((s) => (
              <div className="news-source" key={s.source.source_id}>
                <div className="name">
                  {s.source.name || s.source.source_id} <StatusBadge status={s.label} />
                </div>
                <div className="sub">
                  {t("news.sources.meta1", "{tier} · {kind} · poll {p}s · prio {pr}", {
                    tier: s.source.tier ?? "—",
                    kind: s.source.kind ?? "RSS",
                    p: s.source.poll_interval_sec ?? "—",
                    pr: formatNumber(s.source.priority, 2),
                  })}
                </div>
                <div className="sub">
                  {t("news.sources.meta2", "ok {ok} · fail {fail}", {
                    ok: s.lastSuccessAt
                      ? formatDateTime(s.lastSuccessAt)
                      : t("news.sources.never", "never"),
                    fail: s.lastFailureAt ? formatDateTime(s.lastFailureAt) : "—",
                  })}
                </div>
                <div className="sub">
                  {t("news.sources.meta3", "streak {n} · rate-limited {rl} · backoff {b}", {
                    n: s.failStreak ?? "—",
                    rl: s.rateLimited
                      ? t("news.sources.yes", "yes")
                      : t("news.sources.no", "no"),
                    b: s.backoffUntil ? formatDateTime(s.backoffUntil) : "—",
                  })}
                </div>
                <div style={{ marginTop: 6 }}>
                  <HeatBar
                    segments={5}
                    invert
                    items={[
                      {
                        label: t("news.sources.fail_label", "fail"),
                        value: s.successRatio === null ? null : 1 - s.successRatio,
                        caption:
                          s.successRatio === null
                            ? t("news.sources.unknown", "unknown")
                            : t("news.sources.ok_pct", "{p}% ok", {
                                p: Math.round(s.successRatio * 100),
                              }),
                      },
                    ]}
                    scaleCaptions={[
                      t("news.sources.scale_clean", "clean"),
                      t("news.sources.scale_fails", "5+ fails"),
                    ]}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title={t("news.sources.health_title", "Subsystem health")}
        right={
          <>
            <button className="btn small ghost" onClick={() => health.refetch()} disabled={health.isFetching}>
              {health.isFetching
                ? t("news.sources.checking", "checking…")
                : t("news.sources.recheck", "Re-check")}
            </button>
            <FreshnessNote updatedAtMs={health.dataUpdatedAt ?? null} label={t("news.fresh.health", "health")} staleAfterMs={90_000} />
          </>
        }
      >
        {health.isPending ? (
          <Skeleton count={4} height={28} />
        ) : health.isError ? (
          <ErrorState message={asErrorText(health.error, t)} onRetry={() => health.refetch()} />
        ) : !health.data?.available ? (
          <EmptyState
            message={t("news.sources.unavailable", "News engine reports available=false.")}
            hint={t(
              "news.sources.unavailable_hint",
              "Enable the news engine in the state panel above; health telemetry starts with the worker.",
            )}
          />
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            <dl className="kv">
              <dt>{t("news.sources.f_subsystem", "subsystem")}</dt>
              <dd>{h?.subsystem ?? "NEWS_INTELLIGENCE"}</dd>
              <dt>{t("news.sources.f_state", "state")}</dt>
              <dd>{h?.state ?? "—"}</dd>
              <dt>{t("news.sources.f_stale", "stale")}</dt>
              <dd>
                {h?.stale === undefined
                  ? "—"
                  : h.stale
                    ? t("news.sources.yes", "yes")
                    : t("news.sources.no", "no")}
              </dd>
              <dt>cycle_count</dt>
              <dd>{h?.cycle_count ?? "—"}</dd>
              <dt>last_cycle_at</dt>
              <dd>{h?.last_cycle_at ? formatDateTime(h.last_cycle_at) : "—"}</dd>
              <dt>last_error</dt>
              <dd style={{ textAlign: "start", color: h?.last_error ? "var(--red)" : undefined }}>
                {h?.last_error || t("news.sources.none", "none")}
              </dd>
            </dl>
            {h?.db && (
              <div>
                <div className="section-title">{t("news.sources.db_title", "news.db summary (backend)")}</div>
                <pre tabIndex={0} className="tiny inline-mono" style={{ background: "var(--bg-inset)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, margin: 0, overflowX: "auto" }}>
                  {dbJson}
                </pre>
              </div>
            )}
            {worker && (
              <div>
                <div className="section-title">{t("news.sources.worker_title", "worker telemetry")}</div>
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
                <div className="section-title">{t("news.sources.budget_title", "LLM budget (news-scoped spend)")}</div>
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
                <div className="section-title">{t("news.sources.gate_title", "Calendar event gate")}</div>
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
  const t = useI18n((s) => s.t);
  if (series.length < 2) return <span className="faint tiny">{t("news.sources.no_history", "no history")}</span>;
  return (
    <Sparkline
      values={series}
      tone="dim"
      label={t("news.sources.failure_streak", "failure streak")}
      width={90}
      height={18}
    />
  );
}
