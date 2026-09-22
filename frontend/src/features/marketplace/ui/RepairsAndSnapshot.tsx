/**
 * Repairs list + runtime snapshot panel.
 *
 * GET /repairs returns append-only mk_repairs rows (optional seed filter);
 * GET /runtime-snapshot returns the immutable enabled set + version. The JSON
 * view shows exactly what the backend sent — the snapshot store is read-only
 * from the console, so no commands live here.
 */

import { useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useMktRepairs, useMktSnapshot } from "../hooks";
import { repairOutcomeOf } from "../model";
import { FreshnessNote, asErrorText } from "./shared";

export function RepairsSection() {
  const [seed, setSeed] = useState("");
  const [applied, setApplied] = useState("");
  const repairs = useMktRepairs(applied);

  return (
    <Panel
      title={`Repair & evolution history (${(repairs.data ?? []).length})`}
      right={
        <>
          <input
            className="input"
            style={{ width: 170 }}
            placeholder="filter by seed id…"
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setApplied(seed.trim())}
            aria-label="repair seed filter"
          />
          <button className="btn small ghost" onClick={() => setApplied(seed.trim())}>
            Filter
          </button>
          <FreshnessNote updatedAtMs={repairs.dataUpdatedAt ?? null} label="repairs" />
        </>
      }
    >
      {repairs.isPending ? (
        <Skeleton count={4} height={20} />
      ) : repairs.isError ? (
        <ErrorState message={asErrorText(repairs.error)} onRetry={() => repairs.refetch()} />
      ) : (repairs.data ?? []).length === 0 ? (
        <EmptyState message={applied ? `No repair records for ${applied}.` : "No repair runs recorded."} hint="Trigger a repair from the seeds table — records append as PENDING and settle after research." />
      ) : (
        <DataTable headers={[{ label: "created" }, { label: "parent → child" }, { label: "trigger" }, { label: "status" }, { label: "outcome" }]}>
          {(repairs.data ?? []).map((r) => {
            const out = repairOutcomeOf(r);
            return (
              <tr key={r.repair_id}>
                <td>{r.created_at ? formatDateTime(String(r.created_at)) : "—"}</td>
                <td className="inline-mono tiny">
                  {String(r.parent_seed_id ?? "—")} → {String(r.seed_id ?? "—")}
                </td>
                <td>{String(r.trigger ?? "—")}</td>
                <td>
                  <StatusBadge status={String(r.status ?? "UNKNOWN")} />
                </td>
                <td className="tiny muted" title={out ? JSON.stringify(out) : undefined}>
                  {out ? JSON.stringify(out).slice(0, 90) : r.outcome ? String(r.outcome).slice(0, 90) : "—"}
                </td>
              </tr>
            );
          })}
        </DataTable>
      )}
    </Panel>
  );
}

export function RuntimeSnapshotSection() {
  const snap = useMktSnapshot();
  const [showJson, setShowJson] = useState(false);
  const d = snap.data;

  return (
    <Panel
      title="Runtime snapshot store"
      right={
        <>
          <span className="badge neutral">version {d?.version ?? "—"}</span>
          <button className="btn small ghost" onClick={() => setShowJson((v) => !v)}>
            {showJson ? "summary" : "raw"}
          </button>
        </>
      }
    >
      {snap.isPending ? (
        <Skeleton count={2} height={40} />
      ) : snap.isError ? (
        <ErrorState message={asErrorText(snap.error)} onRetry={() => snap.refetch()} />
      ) : !d ? (
        <EmptyState message="No runtime snapshot returned." />
      ) : showJson ? (
        <pre className="mkt-json">{JSON.stringify(d, null, 2)}</pre>
      ) : (
        <div className="mkt-snapshot-grid">
          <div className="mkt-snapshot-tile">
            <div className="k">Version</div>
            <div className="v">{String(d.version)}</div>
          </div>
          <div className="mkt-snapshot-tile">
            <div className="k">Enabled seeds</div>
            <div className="v">{(d.enabled_set ?? []).length}</div>
          </div>
          <div className="mkt-snapshot-tile">
            <div className="k">Source</div>
            <div className="v">{d.source || "—"}</div>
          </div>
          <div className="mkt-snapshot-tile">
            <div className="k">Created</div>
            <div className="v">{d.created_at ? formatDateTime(d.created_at) : "—"}</div>
          </div>
        </div>
      )}
      {(d?.enabled_set ?? []).length === 0 ? (
        <EmptyState message="The runtime enabled set is empty — no seed is active in the engine right now." />
      ) : (
        <div className="mkt-enabled-chips" style={{ marginTop: 10 }}>
          {(d?.enabled_set ?? []).map((id) => (
            <span className="mkt-enabled-chip" key={id} title={`enabled in runtime set v${String(d?.version ?? "?")}`}>
              <i aria-hidden="true" />
              {id}
            </span>
          ))}
        </div>
      )}
      <div className="tiny faint" style={{ marginTop: 10 }}>
        immutable versioned set (RuntimeConfig pattern) — the console reads it; enablement flows
        through the gated seed commands, never a direct write.
      </div>
      <FreshnessNote updatedAtMs={snap.dataUpdatedAt ?? null} label="snapshot" />
    </Panel>
  );
}
