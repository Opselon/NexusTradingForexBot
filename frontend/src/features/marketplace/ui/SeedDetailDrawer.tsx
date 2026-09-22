/**
 * Seed detail drawer — GET /api/v1/marketplace/seeds/{id} (detail + lifecycle
 * events + enablement + recent scores/repairs) and the 14-factor score history
 * from GET /scores/{id}/history. Missing sections say they are missing.
 */

import { useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { HeatBar, Sparkline } from "@/components/viz";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useMktScoreHistory, useMktSeedDetail } from "../hooks";
import { detailSections, factorsOf, lifecycleLevel } from "../model";
import { FreshnessNote, asErrorText } from "./shared";

function fmtScore(total: number | null | undefined): string {
  return total === null || total === undefined || Number.isNaN(total) ? "NOT_AVAILABLE" : formatNumber(total, 3);
}

export function SeedDetailDrawer({ seedId, onClose }: { seedId: string; onClose: () => void }) {
  const detail = useMktSeedDetail(seedId);
  const history = useMktScoreHistory(seedId);
  const [showRaw, setShowRaw] = useState(false);

  const d = detail.data;
  const sections = d ? detailSections(d) : [];

  return (
    <div className="mkt-drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="mkt-drawer" role="dialog" aria-modal="true" aria-label={`Seed detail ${seedId}`}>
        <header aria-label="Seed detail">
          <span>Seed detail</span>
          <span className="inline-mono tiny faint">{seedId}</span>
          <button className="btn small" style={{ marginInlineStart: "auto" }} onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? "structured" : "raw JSON"}
          </button>
          <button className="btn small ghost" onClick={onClose}>
            Close <kbd>esc</kbd>
          </button>
        </header>
        <div className="body">
          {detail.isPending ? (
            <Skeleton count={4} height={48} />
          ) : detail.isError ? (
            <ErrorState message={asErrorText(detail.error)} onRetry={() => detail.refetch()} />
          ) : !d ? (
            <EmptyState message="Seed detail payload was empty." />
          ) : showRaw ? (
            <pre>{JSON.stringify(d, null, 2)}</pre>
          ) : (
            <>
              <section>
                <div style={{ fontSize: 13.5, fontWeight: 700 }}>{d.name || d.seed_id}</div>
                <div className="statline" style={{ marginTop: 4 }}>
                  <span className={`badge ${lifecycleLevel(String(d.lifecycle ?? ""))}`}>{String(d.lifecycle ?? "UNKNOWN")}</span>
                  <span>{d.family || "—"}</span>
                  <span>v{String(d.version ?? "—")}</span>
                  <span>pack {String(d.pack_id ?? "—")}</span>
                  <span>risk {String(d.risk_profile ?? "—")}</span>
                  <span>source {String(d.source ?? "—")}</span>
                </div>
                {d.description ? <p className="small muted">{d.description}</p> : null}
              </section>

              <section>
                <div className="section-title">Enablement (backend rows)</div>
                {(d.enablement ?? []).length === 0 ? (
                  <EmptyState message="No enablement rows — this seed is not enabled for any mode." />
                ) : (
                  <DataTable headers={[{ label: "mode" }, { label: "status" }, { label: "reason" }, { label: "actor" }, { label: "updated" }]}>
                    {(d.enablement ?? []).map((e, i) => (
                      <tr key={i}>
                        <td>{String(e.mode ?? "—")}</td>
                        <td>
                          <StatusBadge status={String(e.status ?? "UNKNOWN")} />
                        </td>
                        <td className="tiny muted">{String(e.reason ?? "—")}</td>
                        <td>{String(e.actor ?? "—")}</td>
                        <td>{e.updated_at ? formatDateTime(String(e.updated_at)) : "—"}</td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </section>

              <section>
                <div className="section-title">14-factor score — history ({(history.data?.items ?? []).length} snapshots)</div>
                {history.isPending ? (
                  <Skeleton count={2} height={24} />
                ) : history.isError ? (
                  <ErrorState message={asErrorText(history.error)} onRetry={() => history.refetch()} />
                ) : (history.data?.items ?? []).length === 0 ? (
                  <EmptyState message="No score snapshots yet." hint="Queue a research run — totals and factors are written by scoring." />
                ) : (
                  <>
                    <Sparkline
                      values={(history.data?.items ?? [])
                        .slice(0, 40)
                        .reverse()
                        .map((s) => (typeof s.total === "number" ? s.total : null))}
                      tone="neu"
                      label="score total history"
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
                        emptyHint="latest snapshot has no numeric factors"
                        scaleCaptions={["0", "1.0"]}
                      />
                    </div>
                    <DataTable headers={[{ label: "scored at" }, { label: "profile" }, { label: "total", num: true }, { label: "verdict" }]}>
                      {(history.data?.items ?? []).slice(0, 12).map((s, i) => (
                        <tr key={i}>
                          <td>{s.created_at ? formatDateTime(String(s.created_at)) : "—"}</td>
                          <td>{String(s.profile_id ?? "default")} · v{s.profile_version ?? "—"}</td>
                          <td className="num mkt-score-cell">{fmtScore(s.total)}</td>
                          <td>
                            <StatusBadge status={String(s.verdict ?? "UNKNOWN")} />
                          </td>
                        </tr>
                      ))}
                    </DataTable>
                    <FreshnessNote updatedAtMs={history.dataUpdatedAt ?? null} label="scores" />
                  </>
                )}
              </section>

              <section>
                <div className="section-title">Lifecycle events</div>
                {(d.lifecycle_events ?? []).length === 0 ? (
                  <EmptyState message="No lifecycle transitions recorded." />
                ) : (
                  <DataTable headers={[{ label: "at" }, { label: "from → to" }, { label: "actor" }, { label: "reason" }]}>
                    {(d.lifecycle_events ?? []).map((e, i) => (
                      <tr key={i}>
                        <td>{e.created_at ? formatDateTime(String(e.created_at)) : "—"}</td>
                        <td className="inline-mono">
                          {String(e.from_lifecycle ?? "—")} → {String(e.to_lifecycle ?? "—")}
                        </td>
                        <td>{String(e.actor ?? "system")}</td>
                        <td className="tiny muted">{String(e.reason ?? "—")}</td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </section>

              <section>
                <div className="section-title">Recent repairs (child/parent links)</div>
                {(d.recent_repairs ?? []).length === 0 ? (
                  <EmptyState message="No repair records reference this seed." />
                ) : (
                  <DataTable headers={[{ label: "created" }, { label: "trigger" }, { label: "status" }, { label: "seed → child" }]}>
                    {(d.recent_repairs ?? []).map((r, i) => (
                      <tr key={i}>
                        <td>{r.created_at ? formatDateTime(String(r.created_at)) : "—"}</td>
                        <td>{String(r.trigger ?? "—")}</td>
                        <td>
                          <StatusBadge status={String(r.status ?? "UNKNOWN")} />
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
                  <div className="section-title">DSL (stored spec)</div>
                  <pre>{JSON.stringify(d.dsl, null, 2)}</pre>
                </section>
              )}
              {sections.length < 7 && (
                <Panel title="Backend sections present" tight={false}>
                  <div className="tiny muted">
                    {sections.length === 0 ? "only the base seed row was returned" : sections.join(" · ")}
                    {" · "}missing sections are not rendered rather than filled with placeholders.
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
