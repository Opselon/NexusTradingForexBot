/**
 * Seed detail drawer — GET /api/v1/marketplace/seeds/{id} (detail + lifecycle
 * events + enablement + recent scores/repairs) and the 14-factor score history
 * from GET /scores/{id}/history. Missing sections say they are missing.
 */

import { useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { HeatBar, Sparkline } from "@/components/viz";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useMktScoreHistory, useMktSeedDetail } from "../hooks";
import { detailSections, factorsOf, lifecycleLevel, type DetailSectionKey } from "../model";
import { FreshnessNote, asErrorText } from "./shared";

function fmtScore(total: number | null | undefined): string {
  return total === null || total === undefined || Number.isNaN(total) ? "NOT_AVAILABLE" : formatNumber(total, 3);
}

export function SeedDetailDrawer({ seedId, onClose }: { seedId: string; onClose: () => void }) {
  const t = useI18n((s) => s.t);
  const detail = useMktSeedDetail(seedId);
  const history = useMktScoreHistory(seedId);
  const [showRaw, setShowRaw] = useState(false);

  const d = detail.data;
  const sections = d ? detailSections(d) : [];

  /** Section ids -> display labels (literal keys; rebuilt with t each render). */
  const sectionLabel = (s: DetailSectionKey): string =>
    s === "dsl"
      ? t("marketplace.detail.sec_dsl", "DSL")
      : s === "parameter_schema"
        ? t("marketplace.detail.sec_parameter_schema", "parameter schema")
        : s === "default_parameters"
          ? t("marketplace.detail.sec_default_parameters", "default parameters")
          : s === "lifecycle_events"
            ? t("marketplace.detail.lifecycle_title", "Lifecycle events")
            : s === "enablement"
              ? t("marketplace.detail.sec_enablement", "enablement")
              : s === "recent_scores"
                ? t("marketplace.detail.sec_recent_scores", "recent scores")
                : t("marketplace.detail.sec_recent_repairs", "recent repairs");

  return (
    <div className="mkt-drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="mkt-drawer" role="dialog" aria-modal="true" aria-label={t("marketplace.drawer.aria", "Seed detail {id}", { id: seedId })}>
        <header>
          <span>{t("marketplace.drawer.title", "Seed detail")}</span>
          <span className="inline-mono tiny faint">{seedId}</span>
          <button className="btn small" style={{ marginInlineStart: "auto" }} onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? t("marketplace.drawer.structured", "structured") : t("marketplace.drawer.raw_json", "raw JSON")}
          </button>
          <button className="btn small ghost" onClick={onClose}>
            {t("common.close", "Close")} <kbd>esc</kbd>
          </button>
        </header>
        <div className="body">
          {detail.isPending ? (
            <Skeleton count={4} height={48} />
          ) : detail.isError ? (
            <ErrorState message={asErrorText(detail.error, t)} onRetry={() => detail.refetch()} />
          ) : !d ? (
            <EmptyState message={t("marketplace.drawer.empty", "Seed detail payload was empty.")} />
          ) : showRaw ? (
            <pre dir="ltr">{JSON.stringify(d, null, 2)}</pre>
          ) : (
            <>
              <section>
                <div style={{ fontSize: 13.5, fontWeight: 700 }}>{d.name || d.seed_id}</div>
                <div className="statline" style={{ marginTop: 4 }}>
                  <span className={`badge ${lifecycleLevel(String(d.lifecycle ?? ""))}`}>{String(d.lifecycle ?? t("marketplace.status.unknown", "UNKNOWN"))}</span>
                  <span>{d.family || "—"}</span>
                  <span>v{String(d.version ?? "—")}</span>
                  <span>{t("marketplace.th.pack", "pack")} {String(d.pack_id ?? "—")}</span>
                  <span>{t("marketplace.th.risk", "risk")} {String(d.risk_profile ?? "—")}</span>
                  <span>{t("marketplace.snap.k_source", "source")} {String(d.source ?? "—")}</span>
                </div>
                {d.description ? <p className="small muted">{d.description}</p> : null}
              </section>

              <section>
                <div className="section-title">{t("marketplace.detail.enablement_title", "Enablement (backend rows)")}</div>
                {(d.enablement ?? []).length === 0 ? (
                  <EmptyState message={t("marketplace.detail.enablement_empty", "No enablement rows — this seed is not enabled for any mode.")} />
                ) : (
                  <DataTable
                    headers={[
                      { label: t("marketplace.th.mode", "mode") },
                      { label: t("marketplace.th.status", "status") },
                      { label: t("marketplace.th.reason", "reason") },
                      { label: t("marketplace.th.actor", "actor") },
                      { label: t("marketplace.th.updated", "updated") },
                    ]}
                  >
                    {(d.enablement ?? []).map((e, i) => (
                      <tr key={i}>
                        <td>{String(e.mode ?? "—")}</td>
                        <td>
                          <StatusBadge status={String(e.status ?? t("marketplace.status.unknown", "UNKNOWN"))} />
                        </td>
                        <td className="tiny muted">{String(e.reason ?? "—")}</td>
                        <td>{String(e.actor ?? t("marketplace.val.system", "system"))}</td>
                        <td>{e.updated_at ? formatDateTime(String(e.updated_at)) : "—"}</td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </section>

              <section>
                <div className="section-title">
                  {t("marketplace.detail.score_title", "14-factor score — history ({n} snapshots)", { n: (history.data?.items ?? []).length })}
                </div>
                {history.isPending ? (
                  <Skeleton count={2} height={24} />
                ) : history.isError ? (
                  <ErrorState message={asErrorText(history.error, t)} onRetry={() => history.refetch()} />
                ) : (history.data?.items ?? []).length === 0 ? (
                  <EmptyState
                    message={t("marketplace.detail.score_empty", "No score snapshots yet.")}
                    hint={t("marketplace.detail.score_empty_hint", "Queue a research run — totals and factors are written by scoring.")}
                  />
                ) : (
                  <>
                    <Sparkline
                      values={(history.data?.items ?? [])
                        .slice(0, 40)
                        .reverse()
                        .map((s) => (typeof s.total === "number" ? s.total : null))}
                      tone="neu"
                      label={t("marketplace.detail.spark_label", "score total history")}
                      width={200}
                      height={26}
                    />
                    <div style={{ marginTop: 8 }}>
                      <HeatBar
                        items={(() => {
                          const latest = (history.data?.items ?? [])[0];
                          const f = latest ? factorsOf(latest) : null;
                          if (!f) return [];
                          return Object.entries(f).map(([k, v]) => ({
                            label: k,
                            value: Math.max(0, Math.min(1, v)),
                            caption: formatNumber(v, 3),
                            title: `${k} = ${v}`,
                          }));
                        })()}
                        emptyHint={t("marketplace.detail.heat_empty", "latest snapshot has no numeric factors")}
                        scaleCaptions={["0", "1.0"]}
                      />
                    </div>
                    <DataTable
                      headers={[
                        { label: t("marketplace.th.scored_at", "scored at") },
                        { label: t("marketplace.th.profile", "profile") },
                        { label: t("marketplace.th.total", "total"), num: true },
                        { label: t("marketplace.th.verdict", "verdict") },
                      ]}
                    >
                      {(history.data?.items ?? []).slice(0, 12).map((s, i) => (
                        <tr key={i}>
                          <td>{s.created_at ? formatDateTime(String(s.created_at)) : "—"}</td>
                          <td>{String(s.profile_id ?? t("marketplace.val.default", "default"))} · v{s.profile_version ?? "—"}</td>
                          <td className="num mkt-score-cell" dir="ltr">{fmtScore(s.total)}</td>
                          <td>
                            <StatusBadge status={String(s.verdict ?? t("marketplace.status.unknown", "UNKNOWN"))} />
                          </td>
                        </tr>
                      ))}
                    </DataTable>
                    <FreshnessNote updatedAtMs={history.dataUpdatedAt ?? null} label={t("marketplace.fresh.scores", "scores")} />
                  </>
                )}
              </section>

              <section>
                <div className="section-title">{t("marketplace.detail.lifecycle_title", "Lifecycle events")}</div>
                {(d.lifecycle_events ?? []).length === 0 ? (
                  <EmptyState message={t("marketplace.detail.lifecycle_empty", "No lifecycle transitions recorded.")} />
                ) : (
                  <DataTable
                    headers={[
                      { label: t("marketplace.th.at", "at") },
                      { label: t("marketplace.th.from_to", "from → to") },
                      { label: t("marketplace.th.actor", "actor") },
                      { label: t("marketplace.th.reason", "reason") },
                    ]}
                  >
                    {(d.lifecycle_events ?? []).map((e, i) => (
                      <tr key={i}>
                        <td>{e.created_at ? formatDateTime(String(e.created_at)) : "—"}</td>
                        <td className="inline-mono">
                          {String(e.from_lifecycle ?? "—")} → {String(e.to_lifecycle ?? "—")}
                        </td>
                        <td>{String(e.actor ?? t("marketplace.val.system", "system"))}</td>
                        <td className="tiny muted">{String(e.reason ?? "—")}</td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </section>

              <section>
                <div className="section-title">{t("marketplace.detail.repairs_title", "Recent repairs (child/parent links)")}</div>
                {(d.recent_repairs ?? []).length === 0 ? (
                  <EmptyState message={t("marketplace.detail.repairs_empty", "No repair records reference this seed.")} />
                ) : (
                  <DataTable
                    headers={[
                      { label: t("marketplace.th.created", "created") },
                      { label: t("marketplace.th.trigger", "trigger") },
                      { label: t("marketplace.th.status", "status") },
                      { label: t("marketplace.th.seed_child", "seed → child") },
                    ]}
                  >
                    {(d.recent_repairs ?? []).map((r, i) => (
                      <tr key={i}>
                        <td>{r.created_at ? formatDateTime(String(r.created_at)) : "—"}</td>
                        <td>{String(r.trigger ?? "—")}</td>
                        <td>
                          <StatusBadge status={String(r.status ?? t("marketplace.status.unknown", "UNKNOWN"))} />
                        </td>
                        <td className="inline-mono tiny">
                          {String(r.parent_seed_id ?? "—")} → {String(r.seed_id ?? "—")}
                        </td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </section>

              {d.dsl && (
                <section>
                  <div className="section-title">{t("marketplace.dsl.title", "DSL (stored spec)")}</div>
                  <pre dir="ltr">{JSON.stringify(d.dsl, null, 2)}</pre>
                </section>
              )}
              {sections.length < 7 && (
                <Panel title={t("marketplace.detail.sections_title", "Backend sections present")} tight={false}>
                  <div className="tiny muted">
                    {sections.length === 0
                      ? t("marketplace.detail.sections_base", "only the base seed row was returned")
                      : sections.map(sectionLabel).join(" · ")}
                    {" · "}
                    {t("marketplace.detail.sections_missing", "missing sections are not rendered rather than filled with placeholders.")}
                  </div>
                </Panel>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
