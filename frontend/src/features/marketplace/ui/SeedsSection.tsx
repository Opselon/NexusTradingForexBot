/**
 * PURPOSE:  Seeds storefront — table or card view, filter/sort chips derived
 *           ONLY from loaded rows, lifecycle commands + detail drawer.
 * OWNER:    uiux-w6-marketplace
 * CONSUMES: ../hooks (useMktSeeds + command hooks), ../model (ENABLE_MODES,
 *           lifecycleLevel), ./storeViewModel (facetCounts, seedCardMeta),
 *           ./SeedDetailDrawer, ./shared, ./marketplace-store.css
 * PROVIDES: SeedsSection, fmtScore
 * INVARIANTS: enable/disable/repair/research keep their confirm step and the
 *           backend response decides (OK / PENDING / DENIED verbatim); chips
 *           only offer categories present in the loaded page (no hardcoded
 *           vocabulary); client-side sort is display-only and labeled derived;
 *           Skeleton/ErrorState/EmptyState keep their existing strings;
 *           search, pagination and every row action keep working.
 * EXTEND:   add a facet by reading MktSeed fields; keep localStorage under
 *           the w6.marketplace.* prefix.
 */

import { useMemo, useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { ConfirmModal, DataTable, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useDisableSeed, useEnableSeed, useMktSeeds, useRepairSeed, useRunResearch } from "../hooks";
import { ENABLE_MODES, lifecycleLevel, type SeedVM } from "../model";
import type { MktEnableMode } from "../types";
import { SeedDetailDrawer } from "./SeedDetailDrawer";
import { FreshnessNote, asErrorText } from "./shared";
import { facetCounts, seedCardMeta } from "./storeViewModel";
import "./marketplace.css";
import "./marketplace-store.css";
import "./marketplace-store-detail.css";

type PendingCmd =
  | { kind: "enable"; seed: string; mode: MktEnableMode }
  | { kind: "disable"; seed: string }
  | { kind: "repair"; seed: string }
  | { kind: "research"; seed: string }
  | null;

/** Display-only sort of loaded rows — persisted, labeled derived in the UI. */
type SortMode = "backend" | "name" | "lifecycle" | "updated";
const SORTS: Array<{ id: SortMode; label: string }> = [
  { id: "backend", label: "backend order" },
  { id: "name", label: "name A→Z" },
  { id: "lifecycle", label: "lifecycle" },
  { id: "updated", label: "updated ↓" },
];
const LS_SORT = "w6.marketplace.sort";
const LS_VIEW = "w6.marketplace.view";

function lsGet(key: string, fallback: string): string {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function lsSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* display state only — a blocked localStorage never breaks the page */
  }
}

function sortRows(rows: SeedVM[], mode: SortMode): SeedVM[] {
  if (mode === "backend") return rows;
  const out = [...rows];
  if (mode === "name") out.sort((a, b) => a.label.localeCompare(b.label));
  else if (mode === "lifecycle") out.sort((a, b) => a.lifecycle.localeCompare(b.lifecycle) || a.label.localeCompare(b.label));
  else out.sort((a, b) => String(b.seed.updated_at ?? "").localeCompare(String(a.seed.updated_at ?? "")));
  return out;
}

export function SeedsSection() {
  const [page, setPage] = useState(1);
  const t = useI18n((s) => s.t);
  const [family, setFamily] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [appliedQ, setAppliedQ] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingCmd>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [sort, setSort] = useState<SortMode>(() => (lsGet(LS_SORT, "backend") as SortMode));
  const [view, setView] = useState<"table" | "cards">(() => (lsGet(LS_VIEW, "table") === "cards" ? "cards" : "table"));

  const seeds = useMktSeeds({ page, family, status, q: appliedQ });
  const enable = useEnableSeed();
  const disable = useDisableSeed();
  const repair = useRepairSeed();
  const research = useRunResearch();

  const rows: SeedVM[] = seeds.data?.seeds ?? [];

  // Facets come ONLY from the rows this response actually delivered.
  const familyFacets = useMemo(
    () => facetCounts(rows.map((r) => r.seed), "family"),
    [rows],
  );
  const lifecycleFacets = useMemo(
    () => facetCounts(rows.map((r) => r.seed), "lifecycle"),
    [rows],
  );
  const visibleRows = useMemo(() => sortRows(rows, sort), [rows, sort]);

  const changeSort = (m: SortMode): void => {
    setSort(m);
    lsSet(LS_SORT, m);
  };
  const changeView = (v: "table" | "cards"): void => {
    setView(v);
    lsSet(LS_VIEW, v);
  };

  /** Reset the server query to a clean key (family/status chips share this). */
  const clearFilters = (): void => {
    setFamily("");
    setStatus("");
    setPage(1);
    setAppliedQ("");
    setQ("");
  };

  const runCmd = (): void => {
    if (!pending) return;
    const done = {
      onSuccess: (res: unknown) => {
        setNote({ ok: true, text: `${pending.kind} accepted — backend: ${JSON.stringify(res)}` });
        setPending(null);
      },
      onError: (e: unknown) => {
        setNote({ ok: false, text: `${pending.kind} refused: ${asErrorText(e)}` });
        setPending(null);
      },
    };
    if (pending.kind === "enable") enable.mutate({ seedId: pending.seed, mode: pending.mode }, done);
    else if (pending.kind === "disable") disable.mutate(pending.seed, done);
    else if (pending.kind === "repair") repair.mutate({ seedId: pending.seed, trigger: "MANUAL_TRIGGER" }, done);
    else research.mutate(pending.seed, done);
  };

  const busy = enable.isPending || disable.isPending || repair.isPending || research.isPending;
  const hasFilter = family !== "" || status !== "" || appliedQ !== "";

  const rowActions = (s: SeedVM) => (
    <div className="mkt-actions mkt-row-actions" style={{ justifyContent: "flex-end" }}>
      <button className="btn small" onClick={() => setPending({ kind: "research", seed: s.seed.seed_id })} disabled={busy}>
        {busy ? "running…" : "Research"}
      </button>
      <select
        className="select"
        style={{ padding: "2px 4px", fontSize: 10 }}
        value=""
        aria-label={`enable mode for ${s.seed.seed_id}`}
        onChange={(e) => {
          const mode = e.target.value as MktEnableMode;
          if (mode) setPending({ kind: "enable", seed: s.seed.seed_id, mode });
        }}
      >
        <option value="">enable…</option>
        {ENABLE_MODES.map((m) => (
          <option key={m.id} value={m.id} title={m.hint}>{m.label}</option>
        ))}
      </select>
      <span className="mkt-row-sep" aria-hidden="true" />
      <button className="btn small danger" onClick={() => setPending({ kind: "disable", seed: s.seed.seed_id })} disabled={busy}>
        {busy ? "running…" : "Disable"}
      </button>
      <button className="btn small" onClick={() => setPending({ kind: "repair", seed: s.seed.seed_id })} disabled={busy} title={t("marketplace.seeds.repair_title", "evolution-operator repair")}>
        {busy ? "running…" : "Repair"}
      </button>
    </div>
  );

  return (
    <Panel
      title={`Installed seeds (${rows.length}${seeds.data?.hasMore ? "+" : ""})`}
      right={
        <>
          <input
            className="input"
            style={{ width: 150 }}
            placeholder={t("marketplace.seeds.search_ph", "search id/name…")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setPage(1);
                setAppliedQ(q.trim());
              }
            }}
            aria-label={t("marketplace.seeds.search_aria", "search seeds")}
          />
          <button
            className="btn small ghost"
            onClick={() => {
              setPage(1);
              setAppliedQ(q.trim());
            }}
          >
            Search
          </button>
          <div className="mkt-store-viewtoggle" role="group" aria-label={t("marketplace.seeds.view_aria", "seeds view")}>
            <button aria-pressed={view === "table"} onClick={() => changeView("table")}>table</button>
            <button aria-pressed={view === "cards"} onClick={() => changeView("cards")}>cards</button>
          </div>
          <FreshnessNote updatedAtMs={seeds.dataUpdatedAt ?? null} label={t("marketplace.fresh.seeds", "seeds")} />
        </>
      }
    >
      {/* Facet chips — categories/count labels derived from the loaded page only */}
      <div className="mkt-store-chips" style={{ marginBottom: 6 }} role="group" aria-label={t("marketplace.seeds.family_aria", "family filter (loaded rows)")}>
        <span className="tiny faint" style={{ letterSpacing: "0.08em" }}>family</span>
        {familyFacets.length === 0 ? (
          <span className="tiny faint">no families in the loaded page</span>
        ) : (
          familyFacets.map((f) => (
            <button
              key={f.value}
              className="mkt-store-chip"
              aria-pressed={family === f.value}
              onClick={() => {
                setFamily(family === f.value ? "" : f.value);
                setPage(1);
              }}
              title={`${f.value} — ${f.count} of ${rows.length} loaded rows`}
            >
              <span className="swatch" aria-hidden="true" />
              {f.value}
              <span className="cnt">{f.count}</span>
            </button>
          ))
        )}
      </div>
      <div className="mkt-store-chips" style={{ marginBottom: 8 }} role="group" aria-label={t("marketplace.seeds.lifecycle_aria", "lifecycle filter (loaded rows)")}>
        <span className="tiny faint" style={{ letterSpacing: "0.08em" }}>lifecycle</span>
        {lifecycleFacets.length === 0 ? (
          <span className="tiny faint">no lifecycles in the loaded page</span>
        ) : (
          lifecycleFacets.map((f) => (
            <button
              key={f.value}
              className="mkt-store-chip"
              aria-pressed={status === f.value}
              onClick={() => {
                setStatus(status === f.value ? "" : f.value);
                setPage(1);
              }}
              title={`${f.value} — ${f.count} of ${rows.length} loaded rows`}
            >
              {f.value}
              <span className="cnt">{f.count}</span>
            </button>
          ))
        )}
        {hasFilter && (
          <button className="mkt-store-chip" aria-pressed={false} onClick={clearFilters} title={t("marketplace.seeds.clear_title", "clear family/lifecycle/search filters")}>
            ✕ clear filters
          </button>
        )}
        <span className="spacer" style={{ flex: 1 }} />
        <span className="tiny faint" style={{ letterSpacing: "0.08em" }}>sort</span>
        {SORTS.map((s) => (
          <button
            key={s.id}
            className="mkt-store-chip"
            aria-pressed={sort === s.id}
            onClick={() => changeSort(s.id)}
            title={s.id === "backend" ? "order served by the backend" : "client-side sort of loaded rows (derived display order)"}
          >
            {s.label}
          </button>
        ))}
      </div>
      {sort !== "backend" && (
        <div className="tiny faint" style={{ marginBottom: 8 }}>
          client-side sort of the loaded page (derived display order — the backend ranking is untouched)
        </div>
      )}

      <div className="mkt-actions" style={{ marginBottom: 8 }}>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn small ghost" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || seeds.isFetching}>
          ← prev
        </button>
        <span className="timestamp-note">page {page}{seeds.data ? ` · ${seeds.data.pageSize}/page` : ""}</span>
        <button className="btn small ghost" onClick={() => setPage((p) => p + 1)} disabled={!seeds.data?.hasMore || seeds.isFetching}>
          next →
        </button>
      </div>

      {note && <div className={`cmd-result ${note.ok ? "ok" : "fail"}`}>{note.ok ? "✓" : "✕"} {note.text}</div>}

      {seeds.isPending ? (
        <Skeleton count={6} height={22} />
      ) : seeds.isError ? (
        <ErrorState message={asErrorText(seeds.error)} onRetry={() => seeds.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState message={t("marketplace.seeds.empty", "No seeds match this filter.")} hint={t("marketplace.seeds.empty_hint", "Install a pack above — seeds appear here after the backend stores them.")} />
      ) : view === "cards" ? (
        <div className="mkt-store-seeds">
          {visibleRows.map((s) => (
            <div className="mkt-store-seed" key={`${s.seed.seed_id}:${String(s.seed.version ?? "")}`}>
              <div className="mkt-store-seed-head">
                <div>
                  <button className="btn small ghost nm" onClick={() => setDetail(s.seed.seed_id)} title={t("marketplace.seeds.open_detail", "open detail drawer")} style={{ padding: 0, border: "none", background: "none" }}>
                    {s.seed.name || s.seed.seed_id}
                  </button>
                  <div className="id">{s.seed.seed_id}</div>
                </div>
                <span className={`badge ${lifecycleLevel(s.lifecycle)}`}>{s.lifecycle}</span>
              </div>
              <div className="mkt-store-seed-meta">
                {seedCardMeta(s.seed).map((m) => (
                  <span className="kv" key={`${m.label}:${m.text}`} title={m.label}>
                    {m.label === "family" || m.label === "v" || m.label === "license" ? m.text : `${m.label} ${m.text}`}
                  </span>
                ))}
              </div>
              <div className="tiny faint">{s.seed.updated_at ? `updated ${formatDateTime(String(s.seed.updated_at))}` : "updated —"}</div>
              <div className="mkt-store-seed-foot">{rowActions(s)}</div>
            </div>
          ))}
        </div>
      ) : (
        <DataTable
          headers={[
            { label: "seed" },
            { label: "family" },
            { label: "lifecycle" },
            { label: "risk" },
            { label: "pack" },
            { label: "updated" },
            { label: "actions" },
          ]}
        >
          {visibleRows.map((s) => (
            <tr key={`${s.seed.seed_id}:${String(s.seed.version ?? "")}`}>
              <td>
                <button className="btn small ghost" onClick={() => setDetail(s.seed.seed_id)} title={t("marketplace.seeds.open_detail", "open detail drawer")}>
                  {s.seed.name || s.seed.seed_id}
                </button>
                <div className="tiny faint inline-mono">{s.seed.seed_id} · v{String(s.seed.version ?? "—")}</div>
              </td>
              <td>{s.seed.family || "—"}</td>
              <td>
                <span className={`badge ${lifecycleLevel(s.lifecycle)}`}>{s.lifecycle}</span>
              </td>
              <td>
                {typeof s.seed.risk_profile === "string" && s.seed.risk_profile ? (
                  <span className="mkt-family-tag" title={t("marketplace.seeds.risk_title", "risk profile")}>
                    <span className="swatch" aria-hidden="true" />
                    {s.seed.risk_profile}
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="inline-mono tiny">{String(s.seed.pack_id ?? "—")}</td>
              <td>{s.seed.updated_at ? formatDateTime(String(s.seed.updated_at)) : "—"}</td>
              <td>{rowActions(s)}</td>
            </tr>
          ))}
        </DataTable>
      )}

      {pending && (
        <ConfirmModal
          title={
            pending.kind === "enable"
              ? `Enable ${pending.seed} for ${pending.mode}`
              : pending.kind === "disable"
                ? `Disable seed ${pending.seed}`
                : pending.kind === "repair"
                  ? `Trigger repair on ${pending.seed}`
                  : `Queue research run for ${pending.seed}`
          }
          danger={pending.kind === "disable" || pending.kind === "repair"}
          confirmLabel={pending.kind}
          busy={busy}
          onConfirm={runCmd}
          onCancel={() => setPending(null)}
        >
          {pending.kind === "enable" && (
            <div>
              Gates are enforced server-side. <b className="inline-mono">LIVE_REQUEST</b> answers PENDING (202) until governance grants — the console never grants silently. A DENIED answer (403) shows the backend reason verbatim.
            </div>
          )}
          {pending.kind === "disable" && <div>Transition to DISABLED/RETIRED for this seed. A running-position guard may refuse (CONFLICT) — the response decides.</div>}
          {pending.kind === "repair" && (
            <div>
              Runs the mutation operator (trigger <b className="inline-mono">MANUAL_TRIGGER</b>) and installs the child seed as a repair pack. The repair record is appended to the history as PENDING; the outcome is settled by later research.
            </div>
          )}
          {pending.kind === "research" && <div>Queues the seed on the existing research pipeline (202 Accepted). Progress shows up in lifecycle events and score snapshots afterwards.</div>}
        </ConfirmModal>
      )}

      {detail && <SeedDetailDrawer seedId={detail} onClose={() => setDetail(null)} />}
    </Panel>
  );
}

/** Compact 14-factor total display: NOT_AVAILABLE for missing, else 3 decimals. */
export function fmtScore(total: number | null | undefined): string {
  return total === null || total === undefined || Number.isNaN(total) ? "NOT_AVAILABLE" : formatNumber(total, 3);
}
