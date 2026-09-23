/**
 * Health — subsystem matrix (parity: Web/app.js tab-health + debug health
 * widgets + v1 system surface).
 *
 * Sources polled every 10s (version/capabilities slower — build metadata
 * changes only on rebuild): /api/debug/health, /api/v1/system/health,
 * readiness, workers, /api/mt5/status, /api/news/health plus the v1
 * status/runtime/version/capabilities identity blocks. Every cell carries its
 * own fetch age; stale cells recolor to amber (an old GREEN reading is not
 * green truth). Failed reads fail their cell, never the page.
 */

import { useState } from "react";
import { DataTable, EmptyState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { Dot, FreshnessCaption, KeyValueList, MonoValue, PollControl, ResultStrip, usePolling } from "@/features/config/ui/kit";
import "@/features/config/ui/kit.css";
import { formatAgeMs } from "@/lib/format";
import type { ShellPageProps } from "@/app/featureModule";
import {
  useCapabilities,
  useDebugHealth,
  useForensicsHealth,
  useMt5,
  useNewsHealth,
  useProbe,
  useReadiness,
  useRuntime,
  useSystemHealth,
  useSystemStatus,
  useVersion,
  useWorkers,
} from "../useCases";
import { cellFromCheck, cellFromSubsystem, cellFromWorker, cellTone, matrixSummary, type MatrixCell } from "../model";
import "./health.css";

function MatrixCellView({ cell, nowMs }: { cell: MatrixCell; nowMs: number }) {
  const tone = cellTone(cell, nowMs);
  return (
    <div className={`l3-health-cell hl-cell ${tone.level} ${tone.staleClass}`}>
      <div className="name">
        <span title={cell.id}>{cell.name}</span>
        <Dot status={cell.status} />
      </div>
      <div>
        <StatusBadge status={cell.status} />{" "}
        <span className="timestamp-note">{tone.ageMs === null ? "never read" : formatAgeMs(tone.ageMs)}</span>
      </div>
      {cell.detail && <div className="detail">{cell.detail}</div>}
      {cell.metrics.length > 0 && (
        <div className="metrics">
          {cell.metrics.map(([k, v]) => (
            <div className="mrow" key={k}>
              <span className="mk">{k}</span>
              <span><MonoValue value={v} /></span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FailedCell({ name, error, onRetry }: { name: string; error: unknown; onRetry: () => void }) {
  return (
    <div className="l3-health-cell bad hl-cell">
      <div className="name"><span>{name}</span><Dot status="ERROR" /></div>
      <StatusBadge status="ERROR" />
      <div className="detail">{error instanceof Error ? error.message : "read failed"}</div>
      <div><button className="btn small" onClick={onRetry}>Retry</button></div>
    </div>
  );
}

export default function HealthPage(props: ShellPageProps) {
  void props;
  const poll = usePolling(10_000);
  const [tab, setTab] = useState<"matrix" | "layers" | "workers" | "identity">("matrix");
  const nowMs = props.nowMs ?? Date.now();

  const debug = useDebugHealth(poll.paused);
  const system = useSystemHealth(poll.paused);
  const readiness = useReadiness(poll.paused);
  const workers = useWorkers(poll.paused);
  const mt5 = useMt5(poll.paused);
  const news = useNewsHealth(poll.paused);
  const forensics = useForensicsHealth(poll.paused);
  const probe = useProbe(poll.paused);
  const runtime = useRuntime(poll.paused);
  const status = useSystemStatus(poll.paused);
  const version = useVersion(poll.paused);
  const capabilities = useCapabilities(poll.paused);

  const cells: MatrixCell[] = [];
  if (debug.data) for (const sub of debug.data.subsystems) cells.push(cellFromSubsystem(sub, "debug/health", debug.dataUpdatedAt || null));
  if (system.data) {
    const sys = system.data;
    for (const check of sys.checks) cells.push(cellFromCheck(check, "v1/system", system.dataUpdatedAt || null));
  }
  if (workers.data) for (const w of workers.data.workers) cells.push(cellFromWorker(w, workers.dataUpdatedAt || null));
  if (mt5.data) {
    cells.push({
      id: "mt5:connection",
      name: "MT5 broker",
      status: mt5.data.available ? "CONNECTED" : "DISCONNECTED",
      level: mt5.data.available ? "good" : "bad",
      detail: mt5.data.available ? "" : String(mt5.data.reason ?? mt5.data.error_state ?? "adapter unavailable"),
      metrics: Object.entries(mt5.data.connection ?? {}).slice(0, 4),
      fetchedAtMs: mt5.dataUpdatedAt || null,
      source: "mt5",
    });
  }
  if (news.data) {
    cells.push({
      id: "news:subsystem",
      name: "News intelligence",
      status: news.data.available ? (news.data.enabled ? "ACTIVE" : "IDLE") : "UNAVAILABLE",
      level: news.data.available ? "good" : "neutral",
      detail: news.data.available ? String(news.data.worker ?? "") : "news module not attached",
      metrics: Object.entries(news.data.health ?? {}).slice(0, 4),
      fetchedAtMs: news.dataUpdatedAt || null,
      source: "news",
    });
  }
  if (forensics.data) {
    cells.push({
      id: "forensics:matrix",
      name: "Forensic checks",
      status: forensics.data.available ? "ACTIVE" : "UNAVAILABLE",
      level: forensics.data.available ? "good" : "neutral",
      detail: String((forensics.data.error as { message?: string } | undefined)?.message ?? ""),
      metrics: [],
      fetchedAtMs: forensics.dataUpdatedAt || null,
      source: "forensics",
    });
  }
  const failed: Array<{ name: string; error: unknown; retry: () => void }> = [];
  for (const [name, q] of [["debug/health", debug], ["v1 system health", system], ["readiness", readiness], ["workers", workers], ["mt5", mt5], ["news", news], ["forensics", forensics], ["probe /health", probe], ["runtime", runtime], ["status", status], ["version", version], ["capabilities", capabilities]] as const) {
    if (q.isError) failed.push({ name, error: q.error, retry: () => void q.refetch() });
  }
  const summary = matrixSummary(cells);
  const overall =
    summary.bad > 0 ? "DEGRADED-FAIL" : summary.warn > 0 ? "WARNING" : summary.good > 0 ? "HEALTHY" : "UNKNOWN";

  return (
    <div className="l3-wrap hl-page">
      <header className="hl-hero">
        <div className="hl-hero-main">
          <div className="hl-kicker">
            <span className="dot" aria-hidden="true" />
            SAFETY &amp; GOVERNANCE
          </div>
          <h1 className="hl-title">
            <span className="hl-mark" aria-hidden="true">+</span>
            <span className="word">Health</span>
          </h1>
          <p className="hl-desc">
            subsystem matrix from debug + v1 system + broker + news (legacy tab-health)
          </p>
        </div>
        <div className="hl-hero-side" role="group" aria-label="Backend status and provenance">
          <StatusBadge status={system.data?.verdict ?? probe.data?.status ?? overall} label="v1 verdict" />
          <div className="hl-chips">
            {readiness.data && <span className={`hl-chip ${readiness.data.ready ? "pos" : "neg"}`}>readiness {readiness.data.ready ? "READY" : "NOT READY"}</span>}
            {runtime.data && <span className={`hl-chip ${runtime.data.engine_running ? "pos" : "dim"}`}>engine {runtime.data.engine_running ? "RUNNING" : "STOPPED"}</span>}
            {status.data && status.data.critical_failures.length > 0 && (
              <span className="hl-chip neg">critical: {status.data.critical_failures.join(", ")}</span>
            )}
            <span className="hl-chip dim">{cells.length} cells</span>
            <span className="hl-chip dim">poll 10s</span>
          </div>
        </div>
      </header>
      <div className="l3-note hl-note">
        Each cell is one independent read with its own age — a stale GOOD recolors to amber, a failed endpoint fails only
        its cell. Verdict words are the backend's (HealthEngine contract); nothing here is inferred client-side.
      </div>

      <Panel
        title="Overview"
        accent
        right={
          <>
            <FreshnessCaption fetchedAtMs={Math.max(debug.dataUpdatedAt ?? 0, system.dataUpdatedAt ?? 0) || null} nowMs={nowMs} intervalMs={10_000} stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={10_000} busy={debug.isFetching || system.isFetching || workers.isFetching || mt5.isFetching} />
          </>
        }
      >
        <div className="hl-section">
          <div className="hl-section-label">
            <span className="lbl">Overview</span>
            <span className="src">
              <span className="hl-chip dim">/api/v1/system/health</span>
              <span className="hl-chip dim">/health</span>
            </span>
          </div>
          <div className="hl-toolbar l3-toolbar">
            <span className="hl-segmented segmented" role="tablist">
              {(["matrix", "layers", "workers", "identity"] as const).map((t) => (
                <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
                  {t}
                </button>
              ))}
            </span>
          </div>
          <div className="hl-metrics">
            <MetricCard label="cells good" value={summary.good} tone="pos" />
            <MetricCard label="warn / stale" value={summary.warn} tone={summary.warn ? "neg" : "dim"} />
            <MetricCard label="failing" value={summary.bad} tone={summary.bad ? "neg" : "dim"} />
            <MetricCard label="idle / n-a" value={summary.neutral} tone="dim" sub={`${cells.length} total`} />
          </div>
        </div>
      </Panel>

      {tab === "matrix" && (
        <div className="hl-section">
          <div className="hl-section-label">
            <span className="lbl">Subsystem matrix</span>
            <span className="src">
              <span className="hl-chip dim">/api/debug/health</span>
              <span className="hl-chip dim">/api/v1/system/health</span>
              <span className="hl-chip dim">/api/v1/system/workers</span>
              <span className="hl-chip dim">/api/mt5/status</span>
              <span className="hl-chip dim">/api/news/health</span>
              <span className="hl-chip dim">/api/forensics/health</span>
            </span>
          </div>
          <Panel title="Subsystem matrix" tight={cells.length === 0}>
            {cells.length === 0 && failed.length === 0 ? (
              <Skeleton count={4} />
            ) : (
              <div className="l3-health-grid">
                {cells.map((c) => <MatrixCellView key={c.id} cell={c} nowMs={nowMs} />)}
                {failed.map((f) => <FailedCell key={f.name} name={f.name} error={f.error} onRetry={f.retry} />)}
              </div>
            )}
          </Panel>
        </div>
      )}

      {tab === "layers" && (
        <div className="hl-section">
          <div className="hl-section-label">
            <span className="lbl">Required vs optional layers</span>
            <span className="src">
              <span className="hl-chip dim">/api/v1/system/readiness</span>
            </span>
          </div>
          <Panel title="Required vs optional layers (/api/v1/system/readiness)">
          {readiness.isPending ? (
            <Skeleton count={4} />
          ) : readiness.isError ? (
            <ResultStrip result={{ running: false, lastResult: false, lastMessage: readiness.error instanceof Error ? readiness.error.message : "readiness failed" }} />
          ) : readiness.data ? (
            <>
              <div className="hl-sublabel">required (must PASS for READY) · {readiness.data.required_layers.length}</div>
              <DataTable headers={[{ label: "CATEGORY" }, { label: "VERDICT" }, { label: "REASON" }, { label: "SUGGESTION" }]}>
                {readiness.data.required_layers.map((c) => (
                  <tr key={`r:${c.category}`}>
                    <td>{c.category}</td>
                    <td><StatusBadge status={c.verdict} /></td>
                    <td className="l3-cell" title={c.reason}>{c.reason ?? "—"}</td>
                    <td className="l3-cell" title={c.suggestion}>{c.suggestion ?? "—"}</td>
                  </tr>
                ))}
              </DataTable>
              <div className="hl-sublabel">optional (may WARN without blocking) · {readiness.data.optional_layers.length}</div>
              <DataTable headers={[{ label: "CATEGORY" }, { label: "VERDICT" }, { label: "REASON" }]}>
                {readiness.data.optional_layers.map((c) => (
                  <tr key={`o:${c.category}`}>
                    <td>{c.category}</td>
                    <td><StatusBadge status={c.verdict} /></td>
                    <td className="l3-cell" title={c.reason}>{c.reason ?? "—"}</td>
                  </tr>
                ))}
              </DataTable>
            </>
          ) : (
            <EmptyState message="Readiness payload empty." />
          )}
          </Panel>
        </div>
      )}

      {tab === "workers" && (
        <div className="hl-section">
          <div className="hl-section-label">
            <span className="lbl">Workers &amp; runtime</span>
            <span className="src">
              <span className="hl-chip dim">/api/v1/system/workers</span>
              <span className="hl-chip dim">/api/v1/system/runtime</span>
            </span>
          </div>
          <Panel title="Workers (/api/v1/system/workers)" right={<span className="timestamp-note">engine_attached={String(workers.data?.engine_attached ?? "—")}</span>}>
          {workers.isPending ? (
            <Skeleton count={3} />
          ) : workers.data && workers.data.workers.length === 0 ? (
            <EmptyState message="No worker attributes attached to the engine object." hint="NOT_ATTACHED is a state the backend reports — it is never hidden." />
          ) : workers.data ? (
            <DataTable headers={[{ label: "WORKER" }, { label: "STATE" }]}>
              {workers.data.workers.map((w) => (
                <tr key={w.name}>
                  <td className="inline-mono">{w.name}</td>
                  <td><StatusBadge status={String(w.state ?? "UNKNOWN")} /></td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <FailedCell name="workers" error={workers.error} onRetry={() => void workers.refetch()} />
          )}
          <div className="hl-sublabel">runtime</div>
          {runtime.data ? (
            <KeyValueList
              rows={[
                ["engine", runtime.data.engine_running ? <span key="e" className="badge good">RUNNING</span> : <span key="e" className="badge neutral">STOPPED</span>],
                ["warmup", String(runtime.data.warmup_state ?? "—")],
                ["inference_enabled", String(runtime.data.inference_enabled)],
                ["mode", String(runtime.data.mode ?? "—")],
                ["effective_mode", String(runtime.data.effective_mode ?? "—")],
                ["freshness.overall", String((runtime.data.freshness as { overall?: string } | null)?.overall ?? "—")],
              ]}
            />
          ) : (
            <Skeleton count={2} />
          )}
          </Panel>
        </div>
      )}

      {tab === "identity" && (
        <div className="hl-section">
          <div className="hl-section-label">
            <span className="lbl">Identity &amp; capabilities</span>
            <span className="src">
              <span className="hl-chip dim">/api/v1/system/version</span>
              <span className="hl-chip dim">/api/v1/system/capabilities</span>
            </span>
          </div>
          <div className="l3-split">
          <Panel title="Version / build (/api/v1/system/version)">
            {version.isPending ? (
              <Skeleton count={3} />
            ) : version.isError ? (
              <FailedCell name="version" error={version.error} onRetry={() => void version.refetch()} />
            ) : version.data ? (
              <KeyValueList rows={Object.entries(version.data).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => [k, <MonoValue key={k} value={v} />])} />
            ) : null}
          </Panel>
          <Panel title="API capabilities (/api/v1/system/capabilities)" right={<span className="timestamp-note">every {60}s · route-table truth</span>}>
            {capabilities.isPending ? (
              <Skeleton count={3} />
            ) : capabilities.data ? (
              <>
                <KeyValueList
                  rows={[
                    ["api_version", capabilities.data.api_version],
                    ["domains", capabilities.data.domain_count],
                    ["endpoints", capabilities.data.endpoint_count],
                    ["spec", capabilities.data.spec],
                  ]}
                />
                <div className="l3-rule-params">
                  {Object.entries(capabilities.data.domains ?? {}).map(([d, n]) => (
                    <span className="l3-param-chip" key={d}>{d}=<b>{String(n)}</b></span>
                  ))}
                </div>
              </>
            ) : (
              <FailedCell name="capabilities" error={capabilities.error} onRetry={() => void capabilities.refetch()} />
            )}
          </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
