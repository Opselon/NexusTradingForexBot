/**
 * Seeds table + lifecycle commands + seed detail drawer.
 *
 * Enablement goes through a mode picker with a confirm step; the backend can
 * answer OK / PENDING (202) / DENIED (403) — all three are shown verbatim and
 * the table refetches either way (the response, not the click, decides).
 * Disable and repair are destructive-ish and confirm-guarded. Run-research is
 * a queue request (202), not a synchronous result.
 *
 * i18n: command results are stored STRUCTURED (kind + backend payload/error),
 * never as a pre-rendered sentence, so the visible text is re-t()d each render
 * and follows language switches.
 */

import { useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { ConfirmModal, DataTable, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useDisableSeed, useEnableSeed, useMktSeeds, useRepairSeed, useRunResearch } from "../hooks";
import { ENABLE_MODES, lifecycleLevel, type SeedVM } from "../model";
import type { MktEnableMode } from "../types";
import { SeedDetailDrawer } from "./SeedDetailDrawer";
import { FreshnessNote, asErrorText } from "./shared";

type CmdKind = "enable" | "disable" | "repair" | "research";

type PendingCmd =
  | { kind: "enable"; seed: string; mode: MktEnableMode }
  | { kind: "disable"; seed: string }
  | { kind: "repair"; seed: string }
  | { kind: "research"; seed: string }
  | null;

type SeedNote = { ok: boolean; kind: CmdKind; payload?: string; err?: unknown };

export function SeedsSection() {
  const t = useI18n((s) => s.t);
  const [page, setPage] = useState(1);
  const [family, setFamily] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [appliedQ, setAppliedQ] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingCmd>(null);
  const [note, setNote] = useState<SeedNote | null>(null);

  const seeds = useMktSeeds({ page, family, status, q: appliedQ });
  const enable = useEnableSeed();
  const disable = useDisableSeed();
  const repair = useRepairSeed();
  const research = useRunResearch();

  const rows: SeedVM[] = seeds.data?.seeds ?? [];

  /** Render-time label map — keys are literals, values recompute with t. */
  const modeLabels: Record<MktEnableMode, string> = {
    RESEARCH: t("marketplace.seeds.research", "Research"),
    PAPER: t("marketplace.mode.paper", "Paper"),
    SHADOW: t("marketplace.mode.shadow", "Shadow"),
    LIVE_REQUEST: t("marketplace.mode.live_request", "Live request"),
  };
  const modeHints: Record<MktEnableMode, string> = {
    RESEARCH: t("marketplace.mode.research_hint", "run inside the research pipeline only"),
    PAPER: t("marketplace.mode.paper_hint", "simulated execution (gates still enforced)"),
    SHADOW: t("marketplace.mode.shadow_hint", "shadow signals, no orders"),
    LIVE_REQUEST: t("marketplace.mode.live_request_hint", "PENDING until governance grants — never silent"),
  };
  const cmdLabel = (k: CmdKind): string =>
    k === "enable"
      ? t("common.enable", "enable")
      : k === "disable"
        ? t("common.disable", "disable")
        : k === "repair"
          ? t("marketplace.cmd.repair", "repair")
          : t("marketplace.cmd.research", "research");

  const runCmd = (): void => {
    if (!pending) return;
    const kind: CmdKind = pending.kind;
    const done = {
      onSuccess: (res: unknown) => {
        setNote({ ok: true, kind, payload: JSON.stringify(res) });
        setPending(null);
      },
      onError: (e: unknown) => {
        setNote({ ok: false, kind, err: e });
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
      title={t("marketplace.seeds.title", "Installed seeds ({n})", { n: `${rows.length}${seeds.data?.hasMore ? "+" : ""}` })}
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
            {t("marketplace.seeds.search", "Search")}
          </button>
          <FreshnessNote updatedAtMs={seeds.dataUpdatedAt ?? null} label={t("marketplace.fresh.seeds", "seeds")} />
        </>
      }
    >
      <div className="mkt-actions" style={{ marginBottom: 8 }}>
        <select className="select" value={family} onChange={(e) => { setFamily(e.target.value); setPage(1); }} aria-label={t("marketplace.seeds.family_aria", "family filter")}>
          <option value="">{t("marketplace.seeds.family_all", "all families")}</option>
          {["PRICE_ACTION", "ICT", "ICHIMOKU", "BREAKOUT", "MEAN_REVERSION", "MOMENTUM", "HYBRID"].map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
        <select className="select" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label={t("marketplace.seeds.lifecycle_aria", "lifecycle filter")}>
          <option value="">{t("marketplace.seeds.lifecycle_all", "all lifecycles")}</option>
          {["INSTALLED", "RESEARCH_PENDING", "RESEARCH_RUNNING", "VALIDATED", "LIVE_CANDIDATE", "LIVE_ELIGIBLE", "REJECTED", "QUARANTINED", "DISABLED", "RETIRED"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn small ghost" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || seeds.isFetching}>
          {t("marketplace.seeds.prev", "← prev")}
        </button>
        <span className="timestamp-note">
          {t("marketplace.seeds.page_note", "page {p}", { p: page })}
          {seeds.data ? ` · ${t("marketplace.seeds.per_page", "{n}/page", { n: seeds.data.pageSize })}` : ""}
        </span>
        <button className="btn small ghost" onClick={() => setPage((p) => p + 1)} disabled={!seeds.data?.hasMore || seeds.isFetching}>
          {t("marketplace.seeds.next", "next →")}
        </button>
      </div>

      {note && (
        <div className={`cmd-result ${note.ok ? "ok" : "fail"}`}>
          {note.ok ? "✓" : "✕"}{" "}
          {note.ok ? (
            <>
              {t("marketplace.seeds.note_ok_a", "{c} accepted — backend:", { c: cmdLabel(note.kind) })}{" "}
              <span dir="ltr" className="inline-mono">
                {note.payload}
              </span>
            </>
          ) : (
            <>
              {t("marketplace.seeds.note_err_a", "{c} refused:", { c: cmdLabel(note.kind) })}{" "}
              <span dir="ltr" className="inline-mono">
                {asErrorText(note.err, t)}
              </span>
            </>
          )}
        </div>
      )}

      {seeds.isPending ? (
        <Skeleton count={6} height={22} />
      ) : seeds.isError ? (
        <ErrorState message={asErrorText(seeds.error, t)} onRetry={() => seeds.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          message={t("marketplace.seeds.empty", "No seeds match this filter.")}
          hint={t("marketplace.seeds.empty_hint", "Install a pack above — seeds appear here after the backend stores them.")}
        />
      ) : (
        <DataTable
          headers={[
            { label: t("marketplace.th.seed", "seed") },
            { label: t("marketplace.th.family", "family") },
            { label: t("marketplace.th.lifecycle", "lifecycle") },
            { label: t("marketplace.th.risk", "risk") },
            { label: t("marketplace.th.pack", "pack") },
            { label: t("marketplace.th.updated", "updated") },
            { label: t("marketplace.th.actions", "actions") },
          ]}
        >
          {rows.map((s) => (
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
              <td>
                <div className="mkt-actions mkt-row-actions" style={{ justifyContent: "flex-end" }}>
                  <button className="btn small" onClick={() => setPending({ kind: "research", seed: s.seed.seed_id })} disabled={busy}>
                    {t("marketplace.seeds.research", "Research")}
                  </button>
                  <select
                    className="select"
                    style={{ padding: "2px 4px", fontSize: 10 }}
                    value=""
                    aria-label={t("marketplace.seeds.enable_mode_aria", "enable mode for {s}", { s: s.seed.seed_id })}
                    onChange={(e) => {
                      const mode = e.target.value as MktEnableMode;
                      if (mode) setPending({ kind: "enable", seed: s.seed.seed_id, mode });
                    }}
                  >
                    <option value="">{t("common.enable", "enable…")}</option>
                    {ENABLE_MODES.map((m) => (
                      <option key={m.id} value={m.id} title={modeHints[m.id]}>{modeLabels[m.id]}</option>
                    ))}
                  </select>
                  <span className="mkt-row-sep" aria-hidden="true" />
                  <button className="btn small danger" onClick={() => setPending({ kind: "disable", seed: s.seed.seed_id })} disabled={busy}>
                    {t("common.disable", "Disable")}
                  </button>
                  <button className="btn small" onClick={() => setPending({ kind: "repair", seed: s.seed.seed_id })} disabled={busy} title={t("marketplace.seeds.repair_title", "evolution-operator repair")}>
                    {t("marketplace.seeds.repair", "Repair")}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </DataTable>
      )}

      {pending && (
        <ConfirmModal
          title={
            pending.kind === "enable"
              ? t("marketplace.confirm.enable", "Enable {s} for {m}", { s: pending.seed, m: modeLabels[pending.mode] })
              : pending.kind === "disable"
                ? t("marketplace.confirm.disable", "Disable seed {s}", { s: pending.seed })
                : pending.kind === "repair"
                  ? t("marketplace.confirm.repair", "Trigger repair on {s}", { s: pending.seed })
                  : t("marketplace.confirm.research", "Queue research run for {s}", { s: pending.seed })
          }
          danger={pending.kind === "disable" || pending.kind === "repair"}
          confirmLabel={cmdLabel(pending.kind)}
          busy={busy}
          onConfirm={runCmd}
          onCancel={() => setPending(null)}
        >
          {pending.kind === "enable" && (
            <div>
              {t("marketplace.confirm.enable_body1", "Gates are enforced server-side.")}{" "}
              <b className="inline-mono">LIVE_REQUEST</b>{" "}
              {t("marketplace.confirm.enable_body2", "answers PENDING (202) until governance grants — the console never grants silently. A DENIED answer (403) shows the backend reason verbatim.")}
            </div>
          )}
          {pending.kind === "disable" && (
            <div>{t("marketplace.confirm.disable_body", "Transition to DISABLED/RETIRED for this seed. A running-position guard may refuse (CONFLICT) — the response decides.")}</div>
          )}
          {pending.kind === "repair" && (
            <div>
              {t("marketplace.confirm.repair_body1", "Runs the mutation operator (trigger")}{" "}
              <b className="inline-mono">MANUAL_TRIGGER</b>
              {t("marketplace.confirm.repair_body2", ") and installs the child seed as a repair pack. The repair record is appended to the history as PENDING; the outcome is settled by later research.")}
            </div>
          )}
          {pending.kind === "research" && (
            <div>{t("marketplace.confirm.research_body", "Queues the seed on the existing research pipeline (202 Accepted). Progress shows up in lifecycle events and score snapshots afterwards.")}</div>
          )}
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
