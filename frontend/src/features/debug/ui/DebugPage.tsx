/**
 * Debug — the full debug hub (parity: Web/app.js debug sections +
 * Web/forensic_console.js read surfaces, rebuilt as typed panels).
 *
 * Tabs: State (raw+typed viewer) · Health · Features freshness · Freshness
 * diagnostic · IPC telemetry · Compare (two snapshots, key-level diff) ·
 * Snapshots (capture/detail) · Model test (validated vector) · Trace
 * (execution timeline) · Research reads (diagnostics/events/evidence/
 * gates/history/trace, read-only).
 *
 * Polling is operator-controlled (pause/resume per tab). Every value shown is
 * the backend's; missing sections render their own reason + correlation id.
 */

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import {
  Dot,
  FreshnessCaption,
  JsonView,
  KeyValueList,
  MonoValue,
  PollControl,
  QuerySection,
  ResultStrip,
  scalarRows,
  usePolling,
} from "@/features/config/ui/kit";
import "@/features/config/ui/kit.css";
import type { ShellPageProps } from "@/app/featureModule";
import type { DebugState, CompareResult, DebugFeatures, DebugFreshness, DebugHealth, IpcTelemetry, SnapshotList } from "../api";
import { debugApi } from "../api";
import {
  useCompareQuery,
  useDebugFeaturesQuery,
  useDebugFreshnessQuery,
  useDebugHealthQuery,
  useDebugStateQuery,
  useIpcTelemetryQuery,
  useModelTest,
  useResearchRead,
  useSnapshotDetail,
  useSnapshotsQuery,
  useTraceQuery,
} from "../hooks";
import {
  DEBUG_SECTIONS,
  diffCounts,
  diffSnapshotSections,
  featureCells,
  healthLevel,
  snapshotSections,
  traceTimeline,
  vectorSpec,
} from "../model";

type TabId =
  | "state"
  | "health"
  | "features"
  | "freshness"
  | "ipc"
  | "compare"
  | "snapshots"
  | "modeltest"
  | "trace"
  | "research";

const TABS: Array<{ id: TabId; label: string }> = [
  { id: "state", label: "State" },
  { id: "health", label: "Health" },
  { id: "features", label: "Features" },
  { id: "freshness", label: "Freshness" },
  { id: "ipc", label: "IPC" },
  { id: "compare", label: "Compare" },
  { id: "snapshots", label: "Snapshots" },
  { id: "modeltest", label: "Model test" },
  { id: "trace", label: "Trace" },
  { id: "research", label: "Research" },
];

/* ------------------------------------------------------------------ */
/* State viewer                                                        */
/* ------------------------------------------------------------------ */

function StateTab() {
  const poll = usePolling(20_000);
  const query = useDebugStateQuery(poll.paused);
  const [section, setSection] = useState<string>("runtime");
  const [raw, setRaw] = useState(false);

  return (
    <QuerySection<DebugState>
      title="Canonical debug snapshot (/api/debug/state)"
      accent
      query={query}
      skeletonRows={6}
      emptyMessage="Snapshot unavailable."
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} nowMs={undefined} intervalMs={20_000} note={query.data?.snapshot_id ? `id ${query.data.snapshot_id}` : undefined} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={20_000} busy={query.isFetching} />
          <button className="btn small ghost" onClick={() => setRaw((r) => !r)}>{raw ? "typed view" : "raw JSON"}</button>
        </>
      }
    >
      {(snap) => (
        <div>
          {snap.available === false && <div className="l3-note bad">Snapshot flagged unavailable: {String(snap.reason ?? "UNKNOWN")} — the sections below are what the backend could still provide.</div>}
          <div className="l3-db-obj" style={{ marginBottom: 10 }}>
            <button className={`l3-db-chip ${section === "__all" ? "active" : ""}`} onClick={() => setSection("__all")}>all</button>
            {DEBUG_SECTIONS.filter((s) => s in snap).map((s) => (
              <button key={s} className={`l3-db-chip ${section === s ? "active" : ""}`} onClick={() => setSection(s)}>
                {s}
              </button>
            ))}
          </div>
          {raw || section === "__all" ? (
            <div className="l3-scroll">
              <JsonView value={snap} name="state" />
            </div>
          ) : (
            (() => {
              const sec = (snap as Record<string, unknown>)[section];
              if (!sec) return <EmptyState message={`Section "${section}" absent from this snapshot.`} />;
              const obj = sec as Record<string, unknown>;
              if (obj.available === false) {
                return (
                  <div className="l3-note warn">
                    {section}: UNAVAILABLE — {String(obj.reason ?? "no reason given")}
                    {obj.correlation_id ? ` (correlation ${String(obj.correlation_id)})` : ""}
                  </div>
                );
              }
              const rows = scalarRows(obj).filter(([k]) => k !== "_section");
              return (
                <>
                  <KeyValueList rows={rows} />
                  <div className="l3-scroll sm" style={{ marginTop: 8 }}>
                    <JsonView value={obj} name={section} depth={1} />
                  </div>
                </>
              );
            })()
          )}
        </div>
      )}
    </QuerySection>
  );
}

/* ------------------------------------------------------------------ */
/* Health tab                                                          */
/* ------------------------------------------------------------------ */

function HealthTab() {
  const poll = usePolling(15_000);
  const query = useDebugHealthQuery(poll.paused);
  return (
    <QuerySection<DebugHealth>
      title="Debug subsystem health (/api/debug/health)"
      accent
      query={query}
      skeletonRows={4}
      emptyMessage="No subsystem data returned."
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={15_000} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={15_000} busy={query.isFetching} />
        </>
      }
    >
      {(data) => (
        <div>
          <div className="l3-toolbar">
            overall: <StatusBadge status={data.overall_status} />
            <span className="timestamp-note">checked {data.checked_at}</span>
          </div>
          <div className="l3-health-grid">
            {data.subsystems.map((sub) => (
              <div key={sub.name} className={`l3-health-cell ${healthLevel(sub.status)}`}>
                <div className="name">
                  <span>{sub.name}</span>
                  <Dot status={sub.status} />
                </div>
                <StatusBadge status={sub.status} />
                <div className="detail">{sub.detail}</div>
                {Object.keys(sub.metrics ?? {}).length > 0 && (
                  <div className="metrics">
                    {Object.entries(sub.metrics).map(([k, v]) => (
                      <div className="mrow" key={k}>
                        <span className="mk">{k}</span>
                        <span><MonoValue value={v} /></span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </QuerySection>
  );
}

/* ------------------------------------------------------------------ */
/* Features freshness grid                                             */
/* ------------------------------------------------------------------ */

function FeaturesTab() {
  const poll = usePolling(15_000);
  const query = useDebugFeaturesQuery(poll.paused);
  const [onlyAnomalies, setOnlyAnomalies] = useState(false);

  return (
    <QuerySection<DebugFeatures>
      title="Feature contract (/api/debug/features)"
      accent
      query={query}
      skeletonRows={6}
      emptyMessage="Backend returned no feature rows."
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={15_000} note={query.data?.timestamp_utc ? `vector @ ${query.data.timestamp_utc}` : undefined} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={15_000} busy={query.isFetching} />
          <button className="btn small ghost" onClick={() => setOnlyAnomalies((v) => !v)}>{onlyAnomalies ? "show all" : "anomalies only"}</button>
        </>
      }
    >
      {(data) => {
        const cells = featureCells(data.features).filter((c) => (onlyAnomalies ? c.status !== "VALID" : true));
        return (
          <div>
            <div className="l3-toolbar">
              <MetricCard label="engine" value={data.engine_online ? "ONLINE" : "OFFLINE"} tone={data.engine_online ? "pos" : "neg"} />
              <MetricCard label="valid" value={`${data.features.length - data.anomaly_count}/${data.feature_count}`} tone={data.all_valid ? "pos" : "neg"} />
              <MetricCard label="NaN / Inf" value={`${data.nan_count} / ${data.inf_count}`} tone={data.anomaly_count ? "neg" : "dim"} />
              <MetricCard
                label="vector age"
                value={data.age_seconds === null ? "—" : `${data.age_seconds.toFixed(1)}s`}
                tone={data.is_stale ? "neg" : "pos"}
                sub={data.is_stale ? `STALE (>${data.stale_threshold_seconds}s)` : "fresh"}
              />
            </div>
            {data.is_stale && <div className="l3-note warn" style={{ marginBottom: 8 }}>The feature snapshot is older than {data.stale_threshold_seconds}s — the tick pipeline is not feeding the model right now.</div>}
            <div className="l3-grid-features">
              {cells.map((c) => (
                <div key={c.index} className={`l3-feat ${c.status === "VALID" ? "" : c.level === "warn" ? "warn" : "bad"}`} title={`${c.key} · ${c.status}`}>
                  <span className="n">{c.index} {c.name}</span>
                  <span className="v">{c.value}</span>
                </div>
              ))}
            </div>
          </div>
        );
      }}
    </QuerySection>
  );
}

/* ------------------------------------------------------------------ */
/* Freshness diagnostic tab                                            */
/* ------------------------------------------------------------------ */

function FreshnessTab() {
  const poll = usePolling(60_000);
  const query = useDebugFreshnessQuery(poll.paused);
  return (
    <QuerySection<DebugFreshness>
      title="Live-inference frozen-state diagnostic (/api/debug/freshness)"
      accent
      query={query}
      skeletonRows={4}
      emptyMessage="Freshness diagnostic unavailable."
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={60_000} note="runs a live no-cache re-diagnosis server-side" stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={60_000} busy={query.isFetching} />
        </>
      }
    >
      {(data) => {
        if (!data.available) {
          return <div className="l3-note warn">available=false — reason: {String(data.reason ?? "UNKNOWN")} · frozen_at {String(data.frozen_at ?? "UNKNOWN")}</div>;
        }
        return (
          <div className="l3-split">
            <div>
              <div className="section-title">live freshness (engine view)</div>
              <JsonView value={data.live_freshness ?? null} name="live_freshness" />
            </div>
            <div>
              <div className="section-title">no-cache diagnostic (frozen-at localization)</div>
              <div className="l3-scroll">
                <JsonView value={data.diagnostic ?? null} name="diagnostic" />
              </div>
              <div className="tiny faint" style={{ marginTop: 6 }}>checked_at {String(data.checked_at ?? "—")}</div>
            </div>
          </div>
        );
      }}
    </QuerySection>
  );
}

/* ------------------------------------------------------------------ */
/* IPC telemetry tab                                                   */
/* ------------------------------------------------------------------ */

function IpcTab() {
  const poll = usePolling(20_000);
  const query = useIpcTelemetryQuery(poll.paused);
  return (
    <QuerySection<IpcTelemetry>
      title="MT5 IPC telemetry (/api/debug/ipc-telemetry)"
      accent
      query={query}
      skeletonRows={5}
      emptyMessage="No broker execution events recorded yet."
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={20_000} note={`avg latency ${query.data?.avg_latency_ms ?? "—"} ms`} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={20_000} busy={query.isFetching} />
        </>
      }
    >
      {(data) => (
        <div>
          <div className="l3-toolbar">
            <MetricCard label="events" value={data.event_count} />
            <MetricCard label="avg latency" value={`${data.avg_latency_ms} ms`} />
            <MetricCard label="positions / pendings" value={`${data.exposure.positions} / ${data.exposure.pendings}`} sub={`cap ${data.max_total_exposure}`} />
          </div>
          <div className="l3-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  {["timestamp", "event", "state", "reason / retcode", "latency"].map((h) => <th key={h}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.events.map((ev, i) => {
                  const keys = Object.keys(ev);
                  const ts = keys.find((k) => /time|ts|date/i.test(k));
                  const evName = keys.find((k) => /event|action|kind/i.test(k));
                  const state = keys.find((k) => /state|status/i.test(k));
                  const reason = keys.find((k) => /reason|retcode|code/i.test(k));
                  const lat = keys.find((k) => /latency/i.test(k));
                  return (
                    <tr key={i}>
                      <td>{ts ? String(ev[ts] ?? "—") : "—"}</td>
                      <td>{evName ? String(ev[evName] ?? "—") : "—"}</td>
                      <td>{state ? <StatusBadge status={String(ev[state])} /> : "—"}</td>
                      <td className="l3-cell" title={reason ? String(ev[reason]) : undefined}>{reason ? String(ev[reason] ?? "—") : "—"}</td>
                      <td className="num">{lat ? String(ev[lat] ?? "—") : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {data.events.length === 0 && <EmptyState message="Event log empty for this window." />}
          </div>
        </div>
      )}
    </QuerySection>
  );
}

/* ------------------------------------------------------------------ */
/* Snapshots tab (list + capture + detail)                             */
/* ------------------------------------------------------------------ */

function SnapshotsTab({ onSendToCompare }: { onSendToCompare: (id: string, slot: "a" | "b") => void }) {
  const poll = usePolling(30_000);
  const query = useSnapshotsQuery(poll.paused);
  const [detailId, setDetailId] = useState<string | null>(null);
  const detail = useSnapshotDetail(detailId);
  const queryClient = useQueryClient();

  const capture = async () => {
    // The canonical way to capture: GET /api/debug/state — the server pushes
    // the payload into the rolling store as a side effect (debug_research_routes).
    try {
      await debugApi.state();
      await queryClient.invalidateQueries({ queryKey: ["debug", "snapshots"] });
    } catch {
      /* the list poll will surface the failure state anyway */
    }
  };

  return (
    <>
      <QuerySection<SnapshotList>
        title="Snapshot ring (64 max, in-memory) (/api/debug/snapshots)"
        accent
        query={query}
        skeletonRows={4}
        emptyMessage="No snapshots stored yet — capture one (state poll also fills the ring)."
        right={
          <>
            <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={30_000} stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={query.isFetching} />
            <button className="btn small primary" onClick={() => void capture()}>Capture now</button>
          </>
        }
      >
        {(data) => (
          <div>
            {!data.available && <div className="l3-note warn">snapshot store not attached on this server process</div>}
            <div className="l3-scroll sm">
              <DataTable headers={[{ label: "SNAPSHOT ID" }, { label: "TIMESTAMP" }, { label: "ACTIONS" }]}>
                {(data.snapshots ?? []).slice().reverse().map((s) => (
                  <tr key={String(s.snapshot_id)}>
                    <td className="inline-mono">{String(s.snapshot_id ?? "—")}</td>
                    <td>{String(s.timestamp ?? "—")}</td>
                    <td>
                      <div className="l3-row-actions">
                        <button className="btn small" onClick={() => setDetailId(String(s.snapshot_id))}>Detail</button>
                        <button className="btn small ghost" onClick={() => onSendToCompare(String(s.snapshot_id), "a")}>A=</button>
                        <button className="btn small ghost" onClick={() => onSendToCompare(String(s.snapshot_id), "b")}>B=</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </DataTable>
            </div>
          </div>
        )}
      </QuerySection>
      {detailId && (
        <Panel title={`Snapshot ${detailId}`} right={<button className="btn small ghost" onClick={() => setDetailId(null)}>close</button>}>
          {detail.isPending ? (
            <Skeleton count={3} />
          ) : detail.isError ? (
            <ErrorState message={detail.error instanceof Error ? detail.error.message : "snapshot read failed"} onRetry={() => void detail.refetch()} />
          ) : (
            <div className="l3-scroll">
              <JsonView value={detail.data} name={detailId} />
            </div>
          )}
        </Panel>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Compare tab (two snapshots, key-level diff)                         */
/* ------------------------------------------------------------------ */

function CompareTab({ a, b, setA, setB }: { a: string | null; b: string | null; setA: (v: string | null) => void; setB: (v: string | null) => void }) {
  const list = useSnapshotsQuery(true);
  const serverDiff = useCompareQuery(a, b);
  const snapA = useSnapshotDetail(a);
  const snapB = useSnapshotDetail(b);
  const [section, setSection] = useState<string>("features");

  const clientRows = useMemo(
    () => diffSnapshotSections((snapA.data as DebugState | undefined) ?? null, (snapB.data as DebugState | undefined) ?? null, section),
    [snapA.data, snapB.data, section],
  );
  const counts = diffCounts(clientRows);
  const ids = (list.data?.snapshots ?? []).map((s) => String(s.snapshot_id ?? ""));

  return (
    <Panel
      title="Snapshot compare"
      accent
      right={<span className="timestamp-note">server diff + key-level client diff</span>}
    >
      <div className="l3-toolbar">
        <span className="lab timestamp-note">A</span>
        <select className="select" value={a ?? ""} onChange={(e) => setA(e.target.value || null)}>
          <option value="">— pick —</option>
          {ids.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
        <span className="lab timestamp-note">B</span>
        <select className="select" value={b ?? ""} onChange={(e) => setB(e.target.value || null)}>
          <option value="">— pick —</option>
          {ids.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
      </div>
      {!a || !b ? (
        <EmptyState message="Pick two snapshots (or use A=/B= in the Snapshots tab)." hint="Capture snapshots first — the ring fills as /api/debug/state is polled." />
      ) : (
        <>
          {serverDiff.isPending ? (
            <Skeleton count={2} />
          ) : serverDiff.data && !serverDiff.data.available ? (
            <div className="l3-note warn">server diff: {String((serverDiff.data as CompareResult).reason ?? "unavailable")}</div>
          ) : serverDiff.data ? (
            <CompareSummary diff={serverDiff.data} />
          ) : null}
          <div className="section-title" style={{ marginTop: 10 }}>
            key-level diff · section{" "}
            <select className="select" style={{ display: "inline-block", marginInlineStart: 6 }} value={section} onChange={(e) => setSection(e.target.value)}>
              {Array.from(new Set([...snapshotSections((snapA.data as DebugState | undefined) ?? null), ...snapshotSections((snapB.data as DebugState | undefined) ?? null)])).map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <span className="inline-badge">
              {" "}· <span className="badge good">+{counts.added}</span> <span className="badge bad">−{counts.removed}</span> <span className="badge warn">~{counts.changed}</span>
            </span>
          </div>
          <div className="l3-scroll">
            {clientRows.length === 0 ? (
              <EmptyState message="No key differences in this section." />
            ) : (
              <div className="l3-diff">
                {clientRows.slice(0, 200).map((r) => (
                  <div className={`l3-diff-row ${r.kind}`} key={r.path}>
                    <span className="l3-diff-key" title={r.path}>{r.path.split(".").slice(-2).join(".")}</span>
                    <span className="old l3-cell">{r.a}</span>
                    <span className="new l3-cell">{r.b}</span>
                    <span><StatusBadge status={r.kind === "added" ? "ACTIVE" : r.kind === "removed" ? "STOPPED" : "STALE"} label={r.kind} /></span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {(snapA.isError || snapB.isError) && (
            <div className="l3-note bad">one of the snapshot reads failed — client diff shown only for what could be read.</div>
          )}
        </>
      )}
    </Panel>
  );
}

function CompareSummary({ diff }: { diff: CompareResult }) {
  const featureDiffs = diff.feature_diffs ?? [];
  const sections: Array<[string, Record<string, { t0: unknown; t1: unknown }> | undefined]> = [
    ["model", diff.model],
    ["confidence", diff.confidence],
    ["regime", diff.regime],
    ["liquidity", diff.liquidity],
    ["news", diff.news],
    ["policy", diff.policy],
    ["risk", diff.risk],
  ];
  return (
    <div>
      <div className="l3-toolbar timestamp-note">
        {String(diff.a_id)} @ {String(diff.a_timestamp)} ⇄ {String(diff.b_id)} @ {String(diff.b_timestamp)}
      </div>
      {featureDiffs.length > 0 && (
        <>
          <div className="section-title">feature deltas ({featureDiffs.length})</div>
          <div className="l3-scroll sm">
            <DataTable headers={[{ label: "IDX" }, { label: "NAME" }, { label: "A", num: true }, { label: "B", num: true }, { label: "Δ", num: true }]}>
              {featureDiffs.slice(0, 100).map((f) => (
                <tr key={f.index}>
                  <td>{f.index}</td>
                  <td>{f.name ?? "—"}</td>
                  <td className="num">{f.t0.toFixed(4)}</td>
                  <td className="num">{f.t1.toFixed(4)}</td>
                  <td className={`num ${f.delta > 0 ? "pnl-pos" : "pnl-neg"}`}>{f.delta > 0 ? "+" : ""}{f.delta.toFixed(4)}</td>
                </tr>
              ))}
            </DataTable>
          </div>
        </>
      )}
      {sections.map(([name, block]) => {
        const entries = Object.entries(block ?? {});
        if (entries.length === 0) return null;
        return (
          <div key={name} style={{ marginTop: 8 }}>
            <div className="section-title">{name} changes</div>
            <DataTable headers={[{ label: "KEY" }, { label: "A" }, { label: "B" }]}>
              {entries.map(([k, v]) => (
                <tr key={k}>
                  <td className="inline-mono">{k}</td>
                  <td><MonoValue value={v.t0} /></td>
                  <td><MonoValue value={v.t1} /></td>
                </tr>
              ))}
            </DataTable>
          </div>
        );
      })}
      {featureDiffs.length === 0 && sections.every(([, b]) => !b || Object.keys(b).length === 0) && (
        <EmptyState message="Server diff reports no material changes between the two snapshots." />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Model test tab                                                      */
/* ------------------------------------------------------------------ */

function ModelTestTab() {
  const featuresQuery = useDebugFeaturesQuery(true);
  const spec = vectorSpec(featuresQuery.data?.feature_count ?? null);
  const cells = useMemo(() => {
    const rows = featuresQuery.data?.features ?? [];
    return rows.map((r) => (r.value === null || r.value === undefined ? "0" : String(r.value)));
  }, [featuresQuery.data]);
  const [useLive, setUseLive] = useState(true);
  const [localCells, setLocalCells] = useState<string[]>([]);
  const run = useModelTest();

  const source = cells;
  const shown = useLive ? cells.map(() => "—") : localCells;

  const startCustom = () => {
    setLocalCells(source.length > 0 ? [...source] : []);
    setUseLive(false);
  };

  const submit = async () => {
    await run.mutateAsync({ spec, cells: localCells, useLive });
  };

  const badIdx = new Set<number>();
  if (!useLive && spec) {
    localCells.forEach((c, i) => {
      const n = Number(c);
      if (c.trim() === "" || !Number.isFinite(n) || n < spec.min || n > spec.max) badIdx.add(i);
    });
  }

  return (
    <Panel
      title="Model instant test (/api/debug/model-test)"
      accent
      right={<span className="timestamp-note">{spec ? `contract: ${spec.dimension} values ∈ [${spec.min}, ${spec.max}]` : "contract not loaded — send blocked"}</span>}
    >
      <div className="l3-toolbar">
        <span className="timestamp-note">feature source</span>
        <span className="segmented">
          <button className={useLive ? "active" : ""} onClick={() => setUseLive(true)}>live vector</button>
          <button className={!useLive ? "active" : ""} onClick={startCustom}>custom vector</button>
        </span>
        <button className="btn small" onClick={() => void featuresQuery.refetch()} disabled={featuresQuery.isFetching}>reload contract</button>
        <span className="timestamp-note">{featuresQuery.data ? `last vector @ ${featuresQuery.data.timestamp_utc ?? "—"} · ${featuresQuery.data.is_stale ? "STALE" : "fresh"}` : "no features read yet"}</span>
      </div>
      {!useLive && (
        <>
          <div className="l3-vector-grid l3-scroll" style={{ marginBottom: 10 }}>
            {shown.map((c, i) => (
              <label className={`l3-vector-cell ${badIdx.has(i) ? "invalid" : ""}`} key={i}>
                <span className="ix">{i}</span>
                <input
                  className="input"
                  value={c}
                  onChange={(e) =>
                    setLocalCells((prev) => {
                      const next = [...prev];
                      next[i] = e.target.value;
                      return next;
                    })
                  }
                  inputMode="decimal"
                  aria-label={`feature ${i}`}
                />
              </label>
            ))}
            {shown.length === 0 && <div className="l3-note warn">custom mode needs the live contract first — press "reload contract".</div>}
          </div>
          {badIdx.size > 0 && <div className="l3-note bad">{badIdx.size} cell(s) invalid — non-finite or out of bounds. The POST is blocked until they pass.</div>}
        </>
      )}
      <div className="l3-toolbar" style={{ justifyContent: "flex-end" }}>
        <button
          className="btn primary"
          disabled={run.isPending || (!useLive && (!spec || localCells.length === 0 || badIdx.size > 0))}
          onClick={() => void submit()}
        >
          {run.isPending ? "inferring…" : useLive ? "Run on LIVE vector" : "Run on custom vector"}
        </button>
      </div>
      <ResultStrip result={run.isPending ? { running: true, lastResult: null, lastMessage: null } : run.data ? { running: false, lastResult: run.data.ok, lastMessage: run.data.message } : null} />
      {run.data?.ok && run.data.result && (
        <div style={{ marginTop: 10 }}>
          <div className="l3-verdict">
            <MetricCard label="verdict" value={run.data.result.predicted_label ?? "—"} tone={run.data.result.predicted_label === "BUY_MARKET" ? "pos" : run.data.result.predicted_label === "SELL_MARKET" ? "neg" : "dim"} sub={`class ${run.data.result.predicted_class_index ?? "?"}`} />
            <MetricCard label="confidence" value={((run.data.result.confidence ?? 0) * 100).toFixed(1) + "%"} sub={`argmax over ${(run.data.result.probabilities ?? []).length} heads`} />
            <MetricCard label="e2e" value={`${run.data.result.latency_ms ?? "—"} ms`} sub={`model ${String(run.data.result.model_forward_ms ?? "—")} ms`} />
            <MetricCard label="source" value={run.data.result.model_source ?? "—"} sub={`features: ${run.data.result.feature_source ?? "—"} · sanitized ${String(run.data.result.sanitized_inputs ?? 0)}`} />
          </div>
          <div style={{ marginTop: 8 }}>
            <div className="probbar">
              {(
                [
                  ["NO_TRADE", run.data.result.ai_no_trade ?? null, "flat"],
                  ["BUY", run.data.result.ai_buy ?? null, "buy"],
                  ["SELL", run.data.result.ai_sell ?? null, "sell"],
                  ["WAIT", run.data.result.ai_wait ?? null, "flat"],
                ] as Array<[string, number | null, "buy" | "sell" | "flat"]>
              ).map(([label, value, tone]) => (
                <div className="row" key={label}>
                  <span className="lab">{label}</span>
                  <span className="track"><i className={tone} style={{ width: `${(value ?? 0) * 100}%` }} /></span>
                  <span className="val">{value === null ? "—" : `${(value * 100).toFixed(1)}%`}</span>
                </div>
              ))}
            </div>
          </div>
          {run.data.result.latency_breakdown && (
            <div className="tiny faint" style={{ marginTop: 8 }}>
              latency breakdown: {Object.entries(run.data.result.latency_breakdown).slice(0, 10).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
            </div>
          )}
          <div className="tiny faint">evaluated_at {String(run.data.result.evaluated_at ?? "—")}</div>
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Trace tab                                                           */
/* ------------------------------------------------------------------ */

function TraceTab() {
  const [id, setId] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const trace = useTraceQuery(submitted);
  const shapeOk = id.trim() === "" || /^[A-Za-z0-9_-]{4,}$/.test(id.trim());

  return (
    <Panel title="Execution trace (/api/debug/trace/{execution_id})" accent right={<span className="timestamp-note">read-only join: audit_signals + audit_orders</span>}>
      <div className="l3-toolbar">
        <input
          className="input"
          style={{ minWidth: 280 }}
          placeholder="EXEC-…"
          value={id}
          aria-invalid={!shapeOk}
          onChange={(e) => setId(e.target.value)}
        />
        <button className="btn primary" disabled={!shapeOk || id.trim() === ""} onClick={() => setSubmitted(id.trim())}>
          Trace
        </button>
        {!shapeOk && <span className="l3-field-error">id must be ≥4 chars of [A-Za-z0-9_-]</span>}
      </div>
      {!submitted ? (
        <EmptyState message="No execution id submitted." hint="Find ids in audit/orders views or the legacy forensic console." />
      ) : trace.isPending ? (
        <Skeleton count={4} />
      ) : trace.isError ? (
        <ErrorState message={trace.error instanceof Error ? trace.error.message : "trace failed"} onRetry={() => void trace.refetch()} />
      ) : !trace.data?.available ? (
        <div className="l3-note warn">trace unavailable: {String(trace.data?.reason ?? "UNKNOWN")}</div>
      ) : (
        <>
          <div className="timestamp-note" style={{ marginBottom: 6 }}>
            {submitted} · {trace.data.signal?.length ?? 0} signal row(s) · {trace.data.orders?.length ?? 0} order row(s)
          </div>
          <div className="l3-timeline">
            {traceTimeline(trace.data).map((row, i) => (
              <div className="l3-tl-row" key={i}>
                <span className="t">{row.ts}</span>
                <span>
                  <b>{row.stage}</b>
                  <div className="tiny faint l3-cell" title={row.detail}>{row.detail}</div>
                </span>
              </div>
            ))}
            {traceTimeline(trace.data).length === 0 && <EmptyState message="Rows found but no timestamped stages to place on the timeline." />}
          </div>
        </>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Research read-only panels                                           */
/* ------------------------------------------------------------------ */

function ResearchTab() {
  const poll = usePolling(30_000);
  const [kind, setKind] = useState<"diagnostics" | "events" | "evidence" | "gates" | "history" | "trace">("diagnostics");
  const [strategyId, setStrategyId] = useState("");
  const params: Record<string, string> = strategyId.trim() ? { strategy_id: strategyId.trim() } : {};
  const needsId = kind === "trace";
  const read = useResearchRead(kind, params, !needsId || Object.keys(params).length > 0, poll.paused);

  return (
    <QuerySection<Record<string, unknown>>
      title="Research forensics (read-only)"
      accent
      query={read}
      skeletonRows={4}
      emptyMessage="Research engine unavailable on this process."
      right={
        <>
          <FreshnessCaption fetchedAtMs={read.dataUpdatedAt || null} intervalMs={30_000} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={read.isFetching} />
        </>
      }
    >
      {(data) => (
        <div>
          <div className="l3-toolbar">
            <span className="segmented" role="tablist">
              {(["diagnostics", "events", "evidence", "gates", "history", "trace"] as const).map((k) => (
                <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>
                  {k}
                </button>
              ))}
            </span>
            <input className="input" placeholder="strategy_id (filters events/evidence/gates/trace)" value={strategyId} onChange={(e) => setStrategyId(e.target.value)} style={{ minWidth: 260 }} aria-label="strategy id" />
          </div>
          {data.available === false && <div className="l3-note warn">available=false — research engine is not attached (engine offline or module absent).</div>}
          <div className="l3-scroll">
            <JsonView value={data} name={kind} />
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>
            read-only panels: mutations (discover/validate/promote/retry-gate/self-heal) stay in the Research feature
            (lane 5) — nothing here can change research state.
          </div>
        </div>
      )}
    </QuerySection>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function DebugPage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<TabId>("state");
  const [cmpA, setCmpA] = useState<string | null>(null);
  const [cmpB, setCmpB] = useState<string | null>(null);

  const sendToCompare = (id: string, slot: "a" | "b") => {
    if (slot === "a") setCmpA(id);
    else setCmpB(id);
    setTab("compare");
  };

  return (
    <div className="l3-wrap">
      <div className="l3-head">
        <h1>Debug hub</h1>
        <span className="crumb">PLATFORM</span>
        <span className="desc">canonical snapshot · subsystem health · feature contract · freshness · IPC · compare · snapshots · model test · traces · research (legacy tab-debug)</span>
      </div>
      <div className="l3-note">
        Everything here is a read of backend truth (or an explicitly-confirmed inference probe). Pause polling per panel
        when reading large payloads; UNAVAILABLE sections show their own reason + correlation id — never a blank lie.
      </div>
      <div className="l3-debug-tabs">
        <span className="segmented" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </span>
      </div>
      {tab === "state" && <StateTab />}
      {tab === "health" && <HealthTab />}
      {tab === "features" && <FeaturesTab />}
      {tab === "freshness" && <FreshnessTab />}
      {tab === "ipc" && <IpcTab />}
      {tab === "compare" && <CompareTab a={cmpA} b={cmpB} setA={setCmpA} setB={setCmpB} />}
      {tab === "snapshots" && <SnapshotsTab onSendToCompare={sendToCompare} />}
      {tab === "modeltest" && <ModelTestTab />}
      {tab === "trace" && <TraceTab />}
      {tab === "research" && <ResearchTab />}
    </div>
  );
}
