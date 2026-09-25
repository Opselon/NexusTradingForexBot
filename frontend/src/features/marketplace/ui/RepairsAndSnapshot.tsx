/**
 * Repairs list + runtime snapshot panel.
 *
 * GET /repairs returns append-only mk_repairs rows (optional seed filter);
 * GET /runtime-snapshot returns the immutable enabled set + version. The JSON
 * view shows exactly what the backend sent — the snapshot store is read-only
 * from the console, so no commands live here.
 */

import { useMemo, useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { useMktRepairs, useMktSnapshot } from "../hooks";
import { repairOutcomeOf } from "../model";
import { FreshnessNote, asErrorText, jsonInline, jsonPretty } from "./shared";

export function RepairsSection() {
  const t = useI18n((s) => s.t);
  const [seed, setSeed] = useState("");
  const [applied, setApplied] = useState("");
  const repairs = useMktRepairs(applied);

  // perf: serialize each repair outcome once per fetch — the count/JSON text
  // no longer recomputed on every render of the history table.
  const repairRows = useMemo(() => {
    const data = repairs.data ?? [];
    return data.map((r) => {
      const out = repairOutcomeOf(r);
      const outJson = out ? jsonInline(out) : null;
      return {
        r,
        outTitle: outJson ?? undefined,
        outText: outJson !== null ? outJson.slice(0, 90) : r.outcome ? String(r.outcome).slice(0, 90) : "—",
      };
    });
  }, [repairs.data]);

  return (
    <Panel
      title={t("marketplace.repairs.title", "Repair & evolution history ({n})", { n: (repairs.data ?? []).length })}
      right={
        <>
          <input
            className="input"
            style={{ width: 170 }}
            placeholder={t("marketplace.repairs.filter_ph", "filter by seed id…")}
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setApplied(seed.trim())}
            aria-label={t("marketplace.repairs.filter_aria", "repair seed filter")}
          />
          <button className="btn small ghost" onClick={() => setApplied(seed.trim())}>
            {t("marketplace.repairs.filter", "Filter")}
          </button>
          <FreshnessNote updatedAtMs={repairs.dataUpdatedAt ?? null} label={t("marketplace.fresh.repairs", "repairs")} />
        </>
      }
    >
      {repairs.isPending ? (
        <Skeleton count={4} height={20} />
      ) : repairs.isError ? (
        <ErrorState message={asErrorText(repairs.error, t)} onRetry={() => repairs.refetch()} />
      ) : (repairs.data ?? []).length === 0 ? (
        <EmptyState
          message={applied ? t("marketplace.repairs.empty_filtered", "No repair records for {s}.", { s: applied }) : t("marketplace.repairs.empty", "No repair runs recorded.")}
          hint={t("marketplace.repairs.empty_hint", "Trigger a repair from the seeds table — records append as PENDING and settle after research.")}
        />
      ) : (
        <DataTable headers={[{ label: t("marketplace.th.created", "created") }, { label: t("marketplace.th.parent_child", "parent → child") }, { label: t("marketplace.th.trigger", "trigger") }, { label: t("marketplace.th.status", "status") }, { label: t("marketplace.th.outcome", "outcome") }]}>
          {repairRows.map(({ r, outTitle, outText }) => (
            <tr key={r.repair_id}>
              <td>{r.created_at ? formatDateTime(String(r.created_at)) : "—"}</td>
              <td className="inline-mono tiny">
                {String(r.parent_seed_id ?? "—")} → {String(r.seed_id ?? "—")}
              </td>
              <td>{String(r.trigger ?? "—")}</td>
              <td>
                <StatusBadge status={String(r.status ?? t("marketplace.status.unknown", "UNKNOWN"))} />
              </td>
              <td className="tiny muted" title={outTitle}>
                {outText}
              </td>
            </tr>
          ))}
        </DataTable>
      )}
    </Panel>
  );
}

export function RuntimeSnapshotSection() {
  const t = useI18n((s) => s.t);
  const snap = useMktSnapshot();
  const [showJson, setShowJson] = useState(false);
  const d = snap.data;

  // perf: the raw snapshot <pre> serializes once per fetch, not per render.
  const snapJson = useMemo(() => (d ? jsonPretty(d) : ""), [d]);

  return (
    <Panel
      title={t("marketplace.snap.title", "Runtime snapshot store")}
      right={
        <>
          <span className="badge neutral">{t("marketplace.snap.version", "version {v}", { v: d?.version ?? "—" })}</span>
          <button className="btn small ghost" onClick={() => setShowJson((v) => !v)}>
            {showJson ? t("marketplace.snap.summary", "summary") : t("marketplace.snap.raw", "raw")}
          </button>
        </>
      }
    >
      {snap.isPending ? (
        <Skeleton count={2} height={40} />
      ) : snap.isError ? (
        <ErrorState message={asErrorText(snap.error, t)} onRetry={() => snap.refetch()} />
      ) : !d ? (
        <EmptyState message={t("marketplace.snap.empty", "No runtime snapshot returned.")} />
      ) : showJson ? (
        <pre tabIndex={0} className="mkt-json">{snapJson}</pre>
      ) : (
        <div className="mkt-snapshot-grid">
          <div className="mkt-snapshot-tile">
            <div className="k">{t("marketplace.snap.k_version", "Version")}</div>
            <div className="v">{String(d.version)}</div>
          </div>
          <div className="mkt-snapshot-tile">
            <div className="k">{t("marketplace.snap.k_enabled", "Enabled seeds")}</div>
            <div className="v">{(d.enabled_set ?? []).length}</div>
          </div>
          <div className="mkt-snapshot-tile">
            <div className="k">{t("marketplace.snap.k_source", "Source")}</div>
            <div className="v">{d.source || "—"}</div>
          </div>
          <div className="mkt-snapshot-tile">
            <div className="k">{t("marketplace.snap.k_created", "Created")}</div>
            <div className="v">{d.created_at ? formatDateTime(d.created_at) : "—"}</div>
          </div>
        </div>
      )}
      {(d?.enabled_set ?? []).length === 0 ? (
        <EmptyState message={t("marketplace.snap.empty_set", "The runtime enabled set is empty — no seed is active in the engine right now.")} />
      ) : (
        <div className="mkt-enabled-chips" style={{ marginTop: 10 }}>
          {(d?.enabled_set ?? []).map((id) => (
            <span className="mkt-enabled-chip" key={id} title={t("marketplace.snap.chip_title", "enabled in runtime set v{v}", { v: String(d?.version ?? "?") })}>
              <i aria-hidden="true" />
              {id}
            </span>
          ))}
        </div>
      )}
      <div className="tiny faint" style={{ marginTop: 10 }}>
        {t("marketplace.snap.footer", "immutable versioned set (RuntimeConfig pattern) — the console reads it; enablement flows through the gated seed commands, never a direct write.")}
      </div>
      <FreshnessNote updatedAtMs={snap.dataUpdatedAt ?? null} label={t("marketplace.fresh.snapshot", "snapshot")} />
    </Panel>
  );
}
