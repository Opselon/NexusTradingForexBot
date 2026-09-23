/**
 * Seeds table + lifecycle commands + seed detail drawer.
 *
 * Enablement goes through a mode picker with a confirm step; the backend can
 * answer OK / PENDING (202) / DENIED (403) — all three are shown verbatim and
 * the table refetches either way (the response, not the click, decides).
 * Disable and repair are destructive-ish and confirm-guarded. Run-research is
 * a queue request (202), not a synchronous result.
 */

import { memo, useCallback, useState } from "react";
import { ConfirmModal, DataTable, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useDisableSeed, useEnableSeed, useMktSeeds, useRepairSeed, useRunResearch } from "../hooks";
import { ENABLE_MODES, lifecycleLevel, type SeedVM } from "../model";
import type { MktEnableMode } from "../types";
import { SeedDetailDrawer } from "./SeedDetailDrawer";
import { FreshnessNote, asErrorText, requestIdOf } from "./shared";

type PendingCmd =
  | { kind: "enable"; seed: string; mode: MktEnableMode }
  | { kind: "disable"; seed: string }
  | { kind: "repair"; seed: string }
  | { kind: "research"; seed: string }
  | null;

/** Row-level command (no null) — what a memoized row may ask the section to do. */
type RowCmd = Exclude<PendingCmd, null>;

/** Constant style objects hoisted out of the row map (no per-render allocs). */
const ROW_ACTIONS_STYLE = { justifyContent: "flex-end" } as const;
const SEARCH_INPUT_STYLE = { width: 150 } as const;
const ENABLE_SELECT_STYLE = { padding: "2px 4px", fontSize: 10 } as const;
const FILTERS_STYLE = { marginBottom: 8 } as const;

/**
 * One seed table row. Memoized: the table re-renders on every keystroke in the
 * search box, and rows only depend on their own VM + the stable handlers.
 */
const SeedRow = memo(function SeedRow({
  vm,
  busy,
  onOpen,
  onCommand,
}: {
  vm: SeedVM;
  busy: boolean;
  onOpen: (seedId: string) => void;
  onCommand: (cmd: RowCmd) => void;
}) {
  const s = vm;
  return (
    <tr key={`${s.seed.seed_id}:${String(s.seed.version ?? "")}`}>
      <td>
        <button className="btn small ghost" onClick={() => onOpen(s.seed.seed_id)} title="open detail drawer">
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
          <span className="mkt-family-tag" title="risk profile">
            <span className="swatch" aria-hidden="true" />
            {s.seed.risk_profile}
          </span>
        ) : (
          "—"
        )}
      </td>
      <td className="inline-mono tiny">{String(s.seed.pack_id ?? "—")}</td>
      <td>{s.seed.updated_at ? formatDateTime(String(s.seed.updated_at)) : "—"}</td>
      <td>
        <div className="mkt-actions mkt-row-actions" style={ROW_ACTIONS_STYLE}>
          <button className="btn small" onClick={() => onCommand({ kind: "research", seed: s.seed.seed_id })} disabled={busy}>
            Research
          </button>
          <select
            className="select"
            style={ENABLE_SELECT_STYLE}
            value=""
            aria-label={`enable mode for ${s.seed.seed_id}`}
            onChange={(e) => {
              const mode = e.target.value as MktEnableMode;
              if (mode) onCommand({ kind: "enable", seed: s.seed.seed_id, mode });
            }}
          >
            <option value="">enable…</option>
            {ENABLE_MODES.map((m) => (
              <option key={m.id} value={m.id} title={m.hint}>{m.label}</option>
            ))}
          </select>
          <span className="mkt-row-sep" aria-hidden="true" />
          <button className="btn small danger" onClick={() => onCommand({ kind: "disable", seed: s.seed.seed_id })} disabled={busy}>
            Disable
          </button>
          <button className="btn small" onClick={() => onCommand({ kind: "repair", seed: s.seed.seed_id })} disabled={busy} title="evolution-operator repair">
            Repair
          </button>
        </div>
      </td>
    </tr>
  );
});

export function SeedsSection() {
  const [page, setPage] = useState(1);
  const [family, setFamily] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [appliedQ, setAppliedQ] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingCmd>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const seeds = useMktSeeds({ page, family, status, q: appliedQ });
  const enable = useEnableSeed();
  const disable = useDisableSeed();
  const repair = useRepairSeed();
  const research = useRunResearch();

  const rows: SeedVM[] = seeds.data?.seeds ?? [];

  // Stable row callbacks: rows are memoized, so the section's keystrokes and
  // note banners no longer re-render every table row.
  const openDetail = useCallback((seedId: string) => setDetail(seedId), []);
  const queueCmd = useCallback((cmd: RowCmd) => setPending(cmd), []);

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

  return (
    <Panel
      title={`Installed seeds (${rows.length}${seeds.data?.hasMore ? "+" : ""})`}
      right={
        <>
          <input
            className="input"
            style={SEARCH_INPUT_STYLE}
            placeholder="search id/name…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setPage(1);
                setAppliedQ(q.trim());
              }
            }}
            aria-label="search seeds"
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
          <FreshnessNote updatedAtMs={seeds.dataUpdatedAt ?? null} label="seeds" />
        </>
      }
    >
      <div className="mkt-actions" style={FILTERS_STYLE}>
        <select className="select" value={family} onChange={(e) => { setFamily(e.target.value); setPage(1); }} aria-label="family filter">
          <option value="">all families</option>
          {["PRICE_ACTION", "ICT", "ICHIMOKU", "BREAKOUT", "MEAN_REVERSION", "MOMENTUM", "HYBRID"].map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
        <select className="select" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="lifecycle filter">
          <option value="">all lifecycles</option>
          {["INSTALLED", "RESEARCH_PENDING", "RESEARCH_RUNNING", "VALIDATED", "LIVE_CANDIDATE", "LIVE_ELIGIBLE", "REJECTED", "QUARANTINED", "DISABLED", "RETIRED"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
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
        <ErrorState
          message={asErrorText(seeds.error)}
          requestId={requestIdOf(seeds.error)}
          onRetry={() => seeds.refetch()}
        />
      ) : rows.length === 0 ? (
        <EmptyState
          message="No seeds match this filter."
          hint="GET /api/v1/marketplace/seeds returned an empty page — install a pack above and seeds appear once the backend stores them."
        />
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
          {rows.map((s) => (
            <SeedRow
              key={`${s.seed.seed_id}:${String(s.seed.version ?? "")}`}
              vm={s}
              busy={busy}
              onOpen={openDetail}
              onCommand={queueCmd}
            />
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
