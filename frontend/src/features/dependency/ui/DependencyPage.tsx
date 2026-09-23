/**
 * DependencyPage — Dependency Intelligence console (legacy parity).
 *
 * Port of the standalone legacy surface Web/dependency.html (served at
 * /dependency), driven by Web/dependency_api.js + Web/dependency_ui.js.
 *
 * The legacy page's centerpiece is an interactive SVG force graph. This port
 * keeps the FULL data surface the legacy page exposed — summary/health KPIs,
 * top hotspots, cycles, architecture violations, node inspector (deps /
 * dependents / evidence), path explorer and impact explorer — using tables
 * and lists instead of a canvas. Selecting a node works the same way: pick a
 * node id, then run Find path / Impact.
 *
 * Every number comes from /api/dependency/* (read-only static analysis of the
 * repo). `status: "degraded"` from /health renders as a degraded verdict;
 * a 404 on a node renders "node not found" verbatim.
 */

import { useMemo, useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";
import { ApiError } from "@/types/api";
import { dependencyApi, type DependencyNode, type DependencyNodeDetailResponse } from "../api";
import { FreshnessCaption, InfoRow } from "../../research/ui/lane5Kit";

type Tab = "overview" | "cycles" | "violations" | "node";

function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.code === "RESOURCE_NOT_FOUND");
}

/** Extract a display id from a node row (id || qualified_name). */
function nodeId(n: DependencyNode | undefined): string {
  return String(n?.id ?? n?.qualified_name ?? "");
}

export default function DependencyPage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("overview");
  const [selectedNode, setSelectedNode] = useState<string>("");
  const [pathTarget, setPathTarget] = useState<string>("");

  const summaryQ = useQuery({
    queryKey: ["dependency", "summary"],
    queryFn: ({ signal }) => dependencyApi.summary(signal),
    retry: false,
  });
  const graphQ = useQuery({
    queryKey: ["dependency", "graph"],
    queryFn: ({ signal }) => dependencyApi.graph(signal),
    retry: false,
  });
  const cyclesQ = useQuery({
    queryKey: ["dependency", "cycles"],
    queryFn: ({ signal }) => dependencyApi.cycles(signal),
    enabled: tab === "cycles",
    retry: false,
  });
  const violationsQ = useQuery({
    queryKey: ["dependency", "violations"],
    queryFn: ({ signal }) => dependencyApi.violations(signal),
    enabled: tab === "violations",
    retry: false,
  });
  const nodeQ = useQuery({
    queryKey: ["dependency", "node", selectedNode],
    queryFn: ({ signal }) => dependencyApi.node(selectedNode, signal),
    enabled: tab === "node" && selectedNode.trim() !== "",
    retry: false,
  });

  const nodes = graphQ.data?.nodes ?? [];
  const edges = graphQ.data?.edges ?? [];

  const nodeOptions = useMemo(
    () => nodes.map((n) => nodeId(n)).filter(Boolean).sort(),
    [nodes],
  );

  const repo = summaryQ.data?.repository ?? {};
  const health = summaryQ.data?.health ?? {};
  const hotspots = summaryQ.data?.hotspots ?? [];
  const healthy = cyclesQ.data?.status !== "degraded" && (health.architecture_violations ?? 0) === 0;

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>Dependency Intelligence</h2>
        <span className="muted small">static-analysis graph · cycles · violations · hotspots · impact</span>
        <FreshnessCaption
          timestamp={summaryQ.data?.generated_at}
          source="/api/dependency/summary"
          isFetching={summaryQ.isFetching}
          error={summaryQ.isError}
        />
      </div>

      <Panel title="Repository overview" tight>
        {summaryQ.isPending ? (
          <Skeleton count={3} />
        ) : summaryQ.isError ? (
          <ErrorState
            message={summaryQ.error instanceof Error ? summaryQ.error.message : "dependency summary failed"}
            requestId={summaryQ.error instanceof ApiError ? summaryQ.error.requestId : null}
            onRetry={() => void summaryQ.refetch()}
          />
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            <div className="grid cols-2">
              <MetricCard label="files analyzed" value={String(repo.files_analyzed ?? "—")} sub="static scan" />
              <MetricCard label="nodes / edges" value={`${repo.nodes ?? "—"} / ${repo.edges ?? "—"}`} sub="graph size" />
              <MetricCard label="modules" value={String(repo.modules ?? "—")} sub="non-module nodes excluded" />
              <MetricCard label="DI registrations" value={String(repo.di_registrations ?? "—")} sub="dependency-injection bindings" />
            </div>
            <div className="grid cols-2">
              <Panel title="Health verdict" tight>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <StatusBadge
                    status={healthy ? "ok" : "degraded"}
                    label={healthy ? "healthy" : "degraded"}
                  />
                  <span className="tiny muted">
                    cycles {health.cycles ?? "—"} · unresolved imports {health.unresolved_imports ?? "—"} · unresolved
                    DI {health.unresolved_di_bindings ?? "—"} · violations {health.architecture_violations ?? "—"}
                  </span>
                </div>
                {summaryQ.data?.scan_duration_ms != null && (
                  <div className="tiny faint" style={{ marginTop: 6 }}>
                    scan took {summaryQ.data.scan_duration_ms} ms · analyzer {summaryQ.data.analyzer_version ?? "—"}
                  </div>
                )}
              </Panel>
              <Panel title="Top hotspots" tight>
                {hotspots.length === 0 ? (
                  <EmptyState message="No hotspots reported." hint="analyze_graph returned an empty hotspot list." />
                ) : (
                  <DataTable headers={[{ label: "node" }, { label: "score", num: true }, { label: "reason" }]}>
                    {hotspots.slice(0, 10).map((h, i) => (
                      <tr key={String(h.id ?? h.qualified_name ?? h.name ?? i)}>
                        <td className="inline-mono tiny">{String(h.id ?? h.qualified_name ?? h.name ?? "—")}</td>
                        <td className="num tiny">{String(h.score ?? h.centrality ?? "—")}</td>
                        <td className="tiny muted">{String(h.reason ?? h.rationale ?? "—")}</td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </Panel>
            </div>
          </div>
        )}
      </Panel>

      <div style={{ marginBlock: 12 }}>
        <div className="segmented" role="tablist" style={{ display: "inline-flex" }}>
          {([
            { id: "overview" as const, label: "Graph browser" },
            { id: "cycles" as const, label: "Cycles" },
            { id: "violations" as const, label: "Violations" },
            { id: "node" as const, label: "Node inspector" },
          ]).map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              className={tab === t.id ? "active" : ""}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "overview" && (
        <Panel
          title="Graph browser (nodes + edges)"
          right={<FreshnessCaption timestamp={graphQ.data?.generated_at} isFetching={graphQ.isFetching} error={graphQ.isError} />}
          tight
        >
          {graphQ.isPending ? (
            <Skeleton count={5} />
          ) : graphQ.isError ? (
            <ErrorState
              message={graphQ.error instanceof Error ? graphQ.error.message : "dependency graph failed"}
              onRetry={() => void graphQ.refetch()}
            />
          ) : nodes.length === 0 ? (
            <EmptyState message="The dependency graph is empty." hint="The static analysis produced no nodes — check that the scan ran over the source tree." />
          ) : (
            <div style={{ display: "grid", gap: 10 }}>
              <div className="tiny muted">
                {nodes.length} nodes · {edges.length} edges · pick a node id below to open the inspector
              </div>
              <DataTable headers={[{ label: "node" }, { label: "kind" }, { label: "layer" }, { label: "status" }, { label: "criticality" }]}>
                {nodes.slice(0, 200).map((n, i) => (
                  <tr key={nodeId(n) || i}>
                    <td className="inline-mono tiny">{nodeId(n) || "—"}</td>
                    <td className="tiny">{String(n.kind ?? "—")}</td>
                    <td className="tiny">{String(n.layer ?? "—")}</td>
                    <td>
                      <StatusBadge status={String(n.status ?? null)} />
                    </td>
                    <td className="tiny">{String(n.criticality ?? "—")}</td>
                  </tr>
                ))}
              </DataTable>
              {nodes.length > 200 && (
                <div className="tiny faint">Showing the first 200 of {nodes.length} nodes — filter via the node inspector below.</div>
              )}
            </div>
          )}
        </Panel>
      )}

      {tab === "cycles" && (
        <Panel title="Circular dependencies" tight>
          {cyclesQ.isPending ? (
            <Skeleton count={3} />
          ) : cyclesQ.isError ? (
            <ErrorState
              message={cyclesQ.error instanceof Error ? cyclesQ.error.message : "cycles endpoint failed"}
              onRetry={() => void cyclesQ.refetch()}
            />
          ) : (cyclesQ.data?.count ?? 0) === 0 ? (
            <EmptyState message="No cycles detected." hint="detect_cycles returned count=0 — the graph is acyclic." />
          ) : (
            <DataTable headers={[{ label: "#" }, { label: "cycle" }]}>
              {(cyclesQ.data?.cycles ?? []).map((c, i) => (
                <tr key={i}>
                  <td className="tiny">{i + 1}</td>
                  <td className="inline-mono tiny" style={{ whiteSpace: "normal" }}>
                    {String(c.nodes ?? c.path ?? c.cycle ?? JSON.stringify(c))}
                  </td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      )}

      {tab === "violations" && (
        <Panel title="Architecture violations" tight>
          {violationsQ.isPending ? (
            <Skeleton count={3} />
          ) : violationsQ.isError ? (
            <ErrorState
              message={violationsQ.error instanceof Error ? violationsQ.error.message : "violations endpoint failed"}
              onRetry={() => void violationsQ.refetch()}
            />
          ) : (violationsQ.data?.count ?? 0) === 0 ? (
            <EmptyState message="No architecture violations." hint="validate_architecture returned count=0." />
          ) : (
            <DataTable headers={[{ label: "rule" }, { label: "severity" }, { label: "detail" }]}>
              {(violationsQ.data?.violations ?? []).map((v, i) => (
                <tr key={i}>
                  <td className="tiny">{String(v.rule ?? v.kind ?? "—")}</td>
                  <td>
                    <StatusBadge status={String(v.severity ?? null)} />
                  </td>
                  <td className="tiny muted" style={{ whiteSpace: "normal" }}>
                    {String(v.detail ?? v.message ?? v.description ?? "—")}
                  </td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      )}

      {tab === "node" && (
        <NodeInspector
          options={nodeOptions}
          selected={selectedNode}
          setSelected={setSelectedNode}
          pathTarget={pathTarget}
          setPathTarget={setPathTarget}
          query={nodeQ}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Node inspector + path/impact explorers                              */
/* ------------------------------------------------------------------ */

interface NodeInspectorProps {
  options: string[];
  selected: string;
  setSelected: (v: string) => void;
  pathTarget: string;
  setPathTarget: (v: string) => void;
  query: UseQueryResult<DependencyNodeDetailResponse>;
}

function NodeInspector({ options, selected, setSelected, pathTarget, setPathTarget, query }: NodeInspectorProps) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <Panel title="Node inspector" tight>
        <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
          <input
            className="input"
            style={{ flex: 1 }}
            list="dependency-node-options"
            aria-label="Source node id" placeholder="node id e.g. mod:src/nexus_scalp/engine"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          />
          <datalist id="dependency-node-options">
            {options.slice(0, 500).map((id) => (
              <option key={id} value={id} />
            ))}
          </datalist>
          <button className="btn small primary" disabled={!selected.trim()} onClick={() => setSelected(selected.trim())}>
            Inspect
          </button>
        </div>
        {!selected.trim() ? (
          <EmptyState message="Enter or pick a node id to inspect its dependencies, dependents and edge evidence." />
        ) : query.isPending ? (
          <Skeleton count={4} />
        ) : query.isError && isNotFound(query.error) ? (
          <EmptyState message={`Node not found: ${selected}`} hint="/api/dependency/node/{id} answered 404 — try a qualified name." />
        ) : query.isError ? (
          <ErrorState
            message={query.error instanceof Error ? query.error.message : "node endpoint failed"}
            onRetry={() => void query.refetch()}
          />
        ) : query.data ? (
          <NodeDetail data={query.data} />
        ) : null}
      </Panel>

      {query.data && (
        <div className="grid cols-2">
          <PathExplorer source={selected} target={pathTarget} setTarget={setPathTarget} options={options} />
          <ImpactExplorer nodePath={selected} />
        </div>
      )}
    </div>
  );
}

function NodeDetail({ data }: { data: DependencyNodeDetailResponse }) {
  const n = data.node ?? {};
  const m = data.metrics ?? null;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div className="kv">
        <InfoRow label="qualified name" value={String(n.qualified_name ?? n.id ?? "—")} />
        <InfoRow label="kind / layer" value={`${n.kind ?? "—"} · ${n.layer ?? "—"}`} />
        <InfoRow label="status" value={String(n.status ?? "—")} />
        <InfoRow label="criticality" value={String(n.criticality ?? "—")} />
      </div>
      {m ? (
        <div className="kv">
          <InfoRow label="in / out degree" value={`${m.in_degree ?? "—"} / ${m.out_degree ?? "—"}`} />
          <InfoRow label="fan in / out" value={`${m.fan_in ?? "—"} / ${m.fan_out ?? "—"}`} />
          <InfoRow label="centrality" value={m.centrality != null ? String(m.centrality) : "—"} />
        </div>
      ) : (
        <div className="tiny faint">No metrics computed for this node.</div>
      )}
      <div className="grid cols-2">
        <div>
          <div className="section-title">dependencies ({data.dependencies?.length ?? 0})</div>
          {(data.dependencies ?? []).length === 0 ? (
            <EmptyState message="No outgoing dependencies." />
          ) : (
            <ul className="tiny inline-mono" style={{ maxHeight: 160, overflow: "auto", margin: 0, paddingInlineStart: 16 }}>
              {(data.dependencies ?? []).map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <div className="section-title">dependents ({data.dependents?.length ?? 0})</div>
          {(data.dependents ?? []).length === 0 ? (
            <EmptyState message="No incoming dependents." />
          ) : (
            <ul className="tiny inline-mono" style={{ maxHeight: 160, overflow: "auto", margin: 0, paddingInlineStart: 16 }}>
              {(data.dependents ?? []).map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <div>
        <div className="section-title">edge evidence ({data.incident_edges?.length ?? 0})</div>
        {(data.incident_edges ?? []).length === 0 ? (
          <EmptyState message="No incident edges." />
        ) : (
          <DataTable headers={[{ label: "source" }, { label: "kind" }, { label: "target" }]}>
            {(data.incident_edges ?? []).map((e, i) => (
              <tr key={i}>
                <td className="inline-mono tiny">{String(e.source ?? "—")}</td>
                <td className="tiny">{String(e.kind ?? "—")}</td>
                <td className="inline-mono tiny">{String(e.target ?? "—")}</td>
              </tr>
            ))}
          </DataTable>
        )}
      </div>
    </div>
  );
}

function PathExplorer({
  source,
  target,
  setTarget,
  options,
}: {
  source: string;
  target: string;
  setTarget: (v: string) => void;
  options: string[];
}) {
  const q = useQuery({
    queryKey: ["dependency", "path", source, target],
    queryFn: ({ signal }) => dependencyApi.path(source, target, signal),
    enabled: target.trim() !== "" && target.trim() !== source,
    retry: false,
  });

  return (
    <Panel title="Path explorer (shortest path)" tight>
      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          list="dependency-node-options"
          aria-label="Target node id" placeholder="target node id"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        />
        <datalist id="dependency-node-options">
          {options.slice(0, 500).map((id) => (
            <option key={id} value={id} />
          ))}
        </datalist>
      </div>
      <div className="tiny muted" style={{ marginBottom: 8 }}>
        from <span className="inline-mono">{source || "—"}</span> to{" "}
        <span className="inline-mono">{target || "—"}</span>
      </div>
      {target.trim() === "" ? (
        <EmptyState message="Pick a target node to compute the shortest dependency path." />
      ) : q.isPending ? (
        <Skeleton count={3} />
      ) : q.isError ? (
        <ErrorState message={q.error instanceof Error ? q.error.message : "path endpoint failed"} onRetry={() => void q.refetch()} />
      ) : q.data?.found ? (
        <div>
          <div className="tiny muted" style={{ marginBottom: 6 }}>
            length {q.data.length ?? q.data.path?.length ?? "—"}
          </div>
          <ol className="tiny inline-mono" style={{ margin: 0, paddingInlineStart: 18 }}>
            {(q.data.path ?? []).map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ol>
        </div>
      ) : (
        <EmptyState message="No path found between these nodes." hint="shortest_path answered found=false" />
      )}
    </Panel>
  );
}

function ImpactExplorer({ nodePath }: { nodePath: string }) {
  const q = useQuery({
    queryKey: ["dependency", "impact", nodePath],
    queryFn: ({ signal }) => dependencyApi.impact(nodePath, signal),
    enabled: nodePath.trim() !== "",
    retry: false,
  });

  const risk = String(q.data?.risk_level ?? q.data?.risk ?? "—").toUpperCase();

  return (
    <Panel title="Impact explorer (blast radius)" tight>
      <div className="tiny muted" style={{ marginBottom: 8 }}>
        node <span className="inline-mono">{nodePath || "—"}</span>
      </div>
      {nodePath.trim() === "" ? (
        <EmptyState message="Select a node first, then inspect its impact." />
      ) : q.isPending ? (
        <Skeleton count={3} />
      ) : q.isError ? (
        <ErrorState message={q.error instanceof Error ? q.error.message : "impact endpoint failed"} onRetry={() => void q.refetch()} />
      ) : (
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <StatusBadge status={risk} label={risk.toLowerCase()} />
            <span className="tiny muted">
              impacted {q.data?.impacted_count ?? (q.data?.impacted ?? []).length ?? "—"}
            </span>
          </div>
          {(q.data?.impacted ?? []).length === 0 ? (
            <EmptyState message="No downstream impact recorded." />
          ) : (
            <ul className="tiny inline-mono" style={{ maxHeight: 200, overflow: "auto", margin: 0, paddingInlineStart: 16 }}>
              {(q.data?.impacted ?? []).map((p, i) => (
                <li key={i}>{String(p)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}
