/**
 * Repairs list + runtime snapshot panel.
 *
 * GET /repairs returns append-only mk_repairs rows (optional seed filter);
 * GET /runtime-snapshot returns the immutable enabled set + version. The JSON
 * view shows exactly what the backend sent — the snapshot store is read-only
 * from the console, so no commands live here.
 */

import { memo, useMemo, useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useMktRepairs, useMktSnapshot } from "../hooks";
import { repairOutcomeOf } from "../model";
import type { MktRepair } from "../types";
import { FreshnessNote, asErrorText, requestIdOf } from "./shared";

/** Constant style objects hoisted out of the render (no per-render allocs). */
const REPAIR_FILTER_INPUT_STYLE = { width: 170 } as const;
const ENABLED_CHIPS_STYLE = { marginTop: 10 } as const;
const SNAPSHOT_NOTE_STYLE = { marginTop: 10 } as const;

/** One repair history row. Memoized: the seed filter keystrokes and the
 *  freshness caption no longer re-run JSON parsing for every visible row. */
const RepairRow = memo(function RepairRow({ repair }: { repair: MktRepair }) {
  const out = repairOutcomeOf(repair);
  return (
    <tr key={repair.repair_id}>
      <td>{repair.created_at ? formatDateTime(String(repair.created_at)) : "—"}</td>
      <td className="inline-mono tiny">
        {String(repair.parent_seed_id ?? "—")} → {String(repair.seed_id ?? "—")}
      </td>
      <td>{String(repair.trigger ?? "—")}</td>
      <td>
        <StatusBadge status={String(repair.status ?? "UNKNOWN")} />
      </td>
      <td className="tiny muted" title={out ? JSON.stringify(out) : undefined}>
        {out ? JSON.stringify(out).slice(0, 90) : repair.outcome ? String(repair.outcome).slice(0, 90) : "—"}
      </td>
    </tr>
  );
});

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
            style={REPAIR_FILTER_INPUT_STYLE}
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
        <ErrorState
          message={asErrorText(repairs.error)}
          requestId={requestIdOf(repairs.error)}
          onRetry={() => repairs.refetch()}
        />
      ) : (repairs.data ?? []).length === 0 ? (
        <EmptyState
          message={applied ? `No repair records for ${applied}.` : "No repair runs recorded."}
          hint="GET /api/v1/marketplace/repairs returned an empty list — trigger a repair from the seeds table; records append as PENDING and settle after research."
        />
      ) : (
        <DataTable headers={[{ label: "created" }, { label: "parent → child" }, { label: "trigger" }, { label: "status" }, { label: "outcome" }]}>
          {(repairs.data ?? []).map((r) => (
            <RepairRow key={r.repair_id} repair={r} />
          ))}
        </DataTable>
      )}
    </Panel>
  );
}

export function RuntimeSnapshotSection() {
  const snap = useMktSnapshot();
  const [showJson, setShowJson] = useState(false);
  const d = snap.data;
  // Pretty-printed payload derived once per data change (not per render).
  const rawJson = useMemo(() => (d ? JSON.stringify(d, null, 2) : ""), [d]);

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
        <ErrorState
          message={asErrorText(snap.error)}
          requestId={requestIdOf(snap.error)}
          onRetry={() => snap.refetch()}
        />
      ) : !d ? (
        <EmptyState message="No runtime snapshot returned." hint="GET /api/v1/marketplace/runtime-snapshot answered without a payload." />
      ) : showJson ? (
        <pre tabIndex={0} className="mkt-json">{rawJson}</pre>
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
        <EmptyState
          message="The runtime enabled set is empty — no seed is active in the engine right now."
          hint="Backend set from GET /api/v1/marketplace/runtime-snapshot."
        />
      ) : (
        <div className="mkt-enabled-chips" style={ENABLED_CHIPS_STYLE}>
          {(d?.enabled_set ?? []).map((id) => (
            <span className="mkt-enabled-chip" key={id} title={`enabled in runtime set v${String(d?.version ?? "?")}`}>
              <i aria-hidden="true" />
              {id}
            </span>
          ))}
        </div>
      )}
      <div className="tiny faint" style={SNAPSHOT_NOTE_STYLE}>
        immutable versioned set (RuntimeConfig pattern) — the console reads it; enablement flows
        through the gated seed commands, never a direct write.
      </div>
      <FreshnessNote updatedAtMs={snap.dataUpdatedAt ?? null} label="snapshot" />
    </Panel>
  );
}
