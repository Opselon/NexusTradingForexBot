/**
 * DependencyPage — Dependency Intelligence console (legacy parity).
 *
 * Port of the standalone legacy surface Web/dependency.html (served at
 * /dependency), driven by Web/dependency_api.js + Web/dependency_ui.js.
 *
 * The legacy page's centerpiece is an interactive SVG force graph. This port
 * keeps the FULL data surface the legacy page exposed — summary/health KPIs,
 * top hotspots, cycles, architecture violations, node inspector (deps /
 * dependents / evidence), path explorer and impact explorer — using a
 * sortable/filterable/searchable node matrix instead of a canvas. Selecting a
 * node works the same way: pick a node id, then run Find path / Impact.
 *
 * Every number comes from /api/dependency/* (read-only static analysis of the
 * repo). `status: "degraded"` from /health renders as a degraded verdict;
 * a 404 on a node renders "node not found" verbatim.
 *
 * PRO PASS (2026-09-23): hero header with the producer's own health verdict,
 * stat cards, health strip, hotspot leaderboard with risk-score bars, node
 * matrix with kind chips + search highlight + sortable headers, cycle cards
 * with breakpoint guidance, violation cards, and a node inspector wired to a
 * blast-radius panel that finally reads the REAL impact payload (direct /
 * transitive / tests / api / runtime lists — the old UI read
 * impacted/impacted_count, keys the producer never emits, so it rendered
 * "No downstream impact recorded" on every node).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  EmptyState,
  ErrorState,
  Panel,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";
import { ApiError } from "@/types/api";
import {
  dependencyApi,
  type DependencyCycle,
  type DependencyHotspot,
  type DependencyNode,
  type DependencyNodeDetailResponse,
  type DependencyViolation,
} from "../api";
import {
  cycleLength,
  filterNodes,
  highlightRuns,
  hotspotFlags,
  hotspotIdSet,
  hotspotNode,
  hotspotScore,
  hotspotScorePct,
  hotspotTone,
  impactLevel,
  impactRowsNSE,
  impactTotal,
  impactUnknownNode,
  impactWord,
  instabilityPct,
  isCritical,
  isUnresolved,
  kindCounts,
  nodeId,
  pathFound,
  pathLength,
  pathUnknownNode,
  shortNodeLabel,
  sortNodes,
  summaryHealth,
  type BadgeLevel,
  type MetricsMap,
  type NodeKindFilter,
  type NodeSortKey,
  type SortDir,
} from "../dependencyModel";
import { FreshnessCaption } from "../../research/ui/lane5Kit";
import "./dependency.css";

type Tab = "overview" | "cycles" | "violations" | "node";

function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.code === "RESOURCE_NOT_FOUND");
}

function levelToClass(level: BadgeLevel): string {
  return level;
}

function verdictClass(level: BadgeLevel): string {
  if (level === "good") return "v-ok";
  if (level === "warn") return "v-warn";
  if (level === "bad") return "v-bad";
  return "v-unknown";
}

/** Click-to-copy; clipboard is optional, the button is still a copy trigger. */
function useCopyToClipboard(): (text: string) => void {
  return (text: string) => {
    if (!text) return;
    try {
      void navigator.clipboard?.writeText(text);
    } catch {
      /* clipboard unavailable — the selection path still works */
    }
  };
}

const KIND_CHIPS: Array<{ key: NodeKindFilter; label: string; countKey: string }> = [
  { key: "all", label: "all", countKey: "all" },
  { key: "MODULE", label: "modules", countKey: "MODULE" },
  { key: "CLASS", label: "classes", countKey: "CLASS" },
  { key: "PROTOCOL", label: "protocols", countKey: "PROTOCOL" },
  { key: "INTERFACE", label: "interfaces", countKey: "INTERFACE" },
  { key: "EXTERNAL", label: "external", countKey: "EXTERNAL" },
  { key: "UNRESOLVED", label: "unresolved", countKey: "UNRESOLVED" },
];

export default function DependencyPage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("overview");
  const [selectedNode, setSelectedNode] = useState<string>("");
  const [pathTarget, setPathTarget] = useState<string>("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<NodeKindFilter>("all");
  const [sort, setSort] = useState<{ key: NodeSortKey; dir: SortDir }>({
    key: "fan_in",
    dir: "desc",
  });
  const searchRef = useRef<HTMLInputElement | null>(null);

  // "/" focuses search (unless the operator is already typing somewhere).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
  const metricsQ = useQuery({
    queryKey: ["dependency", "metrics"],
    queryFn: ({ signal }) => dependencyApi.metrics(signal),
    enabled: tab === "overview",
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
  const nodeOptions = useMemo(
    () => nodes.map((n) => nodeId(n)).filter(Boolean).sort(),
    [nodes],
  );
  const copy = useCopyToClipboard();

  const repo = summaryQ.data?.repository ?? {};
  const health = summaryQ.data?.health ?? {};
  const hotspots = summaryQ.data?.hotspots ?? [];
  const verdict = summaryHealth(summaryQ.data);
  const hotSet = useMemo(() => hotspotIdSet(hotspots), [hotspots]);
  const kcounts = useMemo(() => kindCounts(nodes), [nodes]);
  const metricsMap: MetricsMap | undefined = metricsQ.data?.metrics;

  const filtered = useMemo(() => filterNodes(nodes, { query, kind }), [nodes, query, kind]);
  const sorted = useMemo(
    () => sortNodes(filtered, sort.key, sort.dir, metricsMap),
    [filtered, sort, metricsMap],
  );
  const maxScore = useMemo(
    () => hotspots.reduce((m, h) => Math.max(m, hotspotScore(h) ?? 0), 0),
    [hotspots],
  );

  const onSort = (key: NodeSortKey) =>
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "id" || key === "kind" ? "asc" : "desc" },
    );

  const tabs: Array<{ id: Tab; label: string; count?: number }> = [
    { id: "overview", label: "Graph browser", count: nodes.length },
    { id: "cycles", label: "Cycles", count: cyclesQ.data?.count },
    { id: "violations", label: "Violations", count: violationsQ.data?.count },
    { id: "node", label: "Node inspector" },
  ];

  return (
    <div className="dp-page">
      <header className="dp-hero">
        <div className="dp-hero-main">
          <div className="dp-kicker">
            <span className="dot" aria-hidden="true" />
            STATIC ANALYSIS · ARCHITECTURE
          </div>
          <h1 className="dp-title">
            <span className="glyph" aria-hidden="true">⌬</span>
            <span className="word">Dependency Intelligence</span>
          </h1>
          <p className="dp-desc">
            Whole-repo import / DI / inheritance graph from{" "}
            <span className="inline-mono">/api/dependency/*</span> — cycles, architecture
            violations, change hotspots and per-node blast radius. Every verdict below is the
            analyzer's own; the UI never invents a risk the static scan did not compute.
          </p>
        </div>
        <div className="dp-hero-side">
          <span
            className={`dp-verdict ${verdictClass(verdict.level)}`}
            title="The analyzer's own rolled-up health verdict (cycles + violations + unresolved imports/DI)."
          >
            <span className="d" aria-hidden="true" />
            {verdict.word} · analyzer {summaryQ.data?.analyzer_version ?? "—"}
          </span>
          <FreshnessCaption
            timestamp={summaryQ.data?.generated_at}
            source="/api/dependency/summary"
            isFetching={summaryQ.isFetching}
            error={summaryQ.isError}
          />
        </div>
      </header>

      <SummaryStats
        loading={summaryQ.isPending}
        error={summaryQ.isError}
        err={summaryQ.error}
        onRetry={() => void summaryQ.refetch()}
        repo={repo}
        health={health}
        scanDuration={summaryQ.data?.scan_duration_ms}
        hotspotCount={hotspots.length}
      />

      {hotspots.length > 0 && (
        <Panel
          title="Top hotspots"
          subtitle="composite risk score · click a node to open the inspector"
          right={
            <FreshnessCaption
              timestamp={summaryQ.data?.generated_at}
              isFetching={summaryQ.isFetching}
              error={summaryQ.isError}
            />
          }
        >
          <HotspotList hotspots={hotspots} maxScore={maxScore} onOpen={openNode} onCopy={copy} />
        </Panel>
      )}

      <div className="dp-tabs">
        <div className="dp-seg" role="tablist" aria-label="Dependency views">
          {tabs.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              className={tab === t.id ? "on" : ""}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.count != null && <span className="cnt">{t.count}</span>}
            </button>
          ))}
        </div>
      </div>

      {tab === "overview" && (
        <Panel
          title="Graph browser"
          subtitle="nodes + edges · click a row to open the inspector"
          right={
            <FreshnessCaption
              timestamp={graphQ.data?.generated_at}
              isFetching={graphQ.isFetching}
              error={graphQ.isError}
            />
          }
          tight
        >
          {graphQ.isPending ? (
            <Skeleton count={6} />
          ) : graphQ.isError ? (
            <ErrorState
              message={graphQ.error instanceof Error ? graphQ.error.message : "dependency graph failed"}
              onRetry={() => void graphQ.refetch()}
            />
          ) : nodes.length === 0 ? (
            <EmptyState
              message="The dependency graph is empty."
              hint="The static analysis produced no nodes — check that the scan ran over the source tree."
            />
          ) : (
            <div className="dp-overview">
              <div className="dp-toolbar">
                <span className="dp-search">
                  <svg
                    className="mag"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                    strokeLinecap="round"
                    aria-hidden="true"
                  >
                    <circle cx="11" cy="11" r="7" />
                    <path d="M21 21l-4.3-4.3" />
                  </svg>
                  <input
                    ref={searchRef}
                    id="dp-node-search"
                    name="dp-node-search"
                    className="input"
                    placeholder="search node id / qualified name…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    aria-label="Search dependency nodes"
                  />
                  <kbd aria-hidden="true">/</kbd>
                </span>
                {KIND_CHIPS.map((c) => (
                  <button
                    key={c.key}
                    className={`dp-chip ${kind === c.key ? "on" : ""}`}
                    aria-pressed={kind === c.key}
                    onClick={() => setKind(c.key)}
                  >
                    <span className="lbl">{c.label}</span>
                    <span className="cnt">{kcounts[c.countKey] ?? 0}</span>
                  </button>
                ))}
                <span className="dp-shown tiny muted">
                  {sorted.length}/{nodes.length} shown · sort: {sort.key} {sort.dir === "asc" ? "▲" : "▼"}
                </span>
              </div>

              {sorted.length === 0 ? (
                <EmptyState
                  message="No nodes match the current filter."
                  hint="Clear the search box or switch the kind filter."
                />
              ) : (
                <NodeMatrix
                  rows={sorted.slice(0, 400)}
                  metrics={metricsMap}
                  hotSet={hotSet}
                  q={query}
                  sort={sort}
                  onSort={onSort}
                  onOpen={openNode}
                  onCopy={copy}
                />
              )}
              {sorted.length > 400 && (
                <div className="tiny faint" style={{ padding: "8px 12px" }}>
                  Showing the first 400 of {sorted.length} matches — narrow the search to see the rest.
                </div>
              )}
              {metricsQ.isFetching && sorted.length > 0 && <Skeleton count={1} height={10} />}
            </div>
          )}
        </Panel>
      )}

      {tab === "cycles" && (
        <Panel
          title="Circular dependencies"
          subtitle="detect_cycles · import / DI / mixed"
          right={
            <FreshnessCaption
              timestamp={cyclesQ.data ? summaryQ.data?.generated_at : undefined}
              isFetching={cyclesQ.isFetching}
              error={cyclesQ.isError}
            />
          }
        >
          {cyclesQ.isPending ? (
            <Skeleton count={4} />
          ) : cyclesQ.isError ? (
            <ErrorState
              message={cyclesQ.error instanceof Error ? cyclesQ.error.message : "cycles endpoint failed"}
              onRetry={() => void cyclesQ.refetch()}
            />
          ) : (cyclesQ.data?.count ?? 0) === 0 ? (
            <EmptyState message="No cycles detected." hint="detect_cycles returned count=0 — the graph is acyclic." />
          ) : (
            <CycleList cycles={cyclesQ.data?.cycles ?? []} onOpen={openNode} />
          )}
        </Panel>
      )}

      {tab === "violations" && (
        <Panel
          title="Architecture violations"
          subtitle="validate_architecture · layer rules"
          right={
            <FreshnessCaption
              timestamp={violationsQ.data ? summaryQ.data?.generated_at : undefined}
              isFetching={violationsQ.isFetching}
              error={violationsQ.isError}
            />
          }
        >
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
            <ViolationList violations={violationsQ.data?.violations ?? []} />
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
          onOpen={openNode}
          onCopy={copy}
        />
      )}
    </div>
  );

  function openNode(id: string) {
    if (!id) return;
    setSelectedNode(id);
    setTab("node");
  }
}

/* ------------------------------ Summary stats ------------------------------ */

function SummaryStats({
  loading,
  error,
  err,
  onRetry,
  repo,
  health,
  scanDuration,
  hotspotCount,
}: {
  loading: boolean;
  error: boolean;
  err: Error | null;
  onRetry: () => void;
  repo: { files_analyzed?: number; modules?: number; nodes?: number; edges?: number; di_registrations?: number };
  health: {
    cycles?: number;
    unresolved_imports?: number;
    unresolved_di_bindings?: number;
    architecture_violations?: number;
  };
  scanDuration?: number;
  hotspotCount: number;
}) {
  if (loading) {
    return (
      <div className="dp-stats">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="dp-stat">
            <Skeleton count={2} />
          </div>
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <Panel title="Repository overview" tight>
        <ErrorState
          message={err instanceof Error ? err.message : "dependency summary failed"}
          onRetry={onRetry}
        />
      </Panel>
    );
  }
  return (
    <>
      <div className="dp-stats">
        <StatCard tone="blue" icon="▦" k="files analyzed" v={repo.files_analyzed ?? "—"} s="static scan" />
        <StatCard tone="violet" icon="⌬" k="nodes" v={repo.nodes ?? "—"} s={`${repo.modules ?? "—"} modules`} />
        <StatCard
          tone="green"
          icon="⇄"
          k="edges"
          v={repo.edges ?? "—"}
          s={`${repo.di_registrations ?? "—"} DI registrations`}
        />
        <StatCard
          tone={(health.cycles ?? 0) > 0 ? "red" : "green"}
          icon="◌"
          k="cycles"
          v={health.cycles ?? "—"}
          s={(health.cycles ?? 0) > 0 ? "circular imports" : "acyclic"}
        />
        <StatCard
          tone={hotspotCount > 0 ? "amber" : "green"}
          icon="◆"
          k="hotspots"
          v={hotspotCount}
          s={scanDuration != null ? `scan ${(scanDuration / 1000).toFixed(1)}s` : "top risk"}
        />
      </div>
      <div className="dp-health-strip">
        <HealthCell
          label="cycles"
          value={health.cycles}
          hint="detect_cycles"
          level={(health.cycles ?? 0) > 0 ? "bad" : "good"}
        />
        <HealthCell
          label="unresolved imports"
          value={health.unresolved_imports}
          hint="import resolution"
          level={(health.unresolved_imports ?? 0) > 0 ? "warn" : "good"}
        />
        <HealthCell
          label="unresolved DI"
          value={health.unresolved_di_bindings}
          hint="dependency injection"
          level={(health.unresolved_di_bindings ?? 0) > 0 ? "warn" : "good"}
        />
        <HealthCell
          label="architecture violations"
          value={health.architecture_violations}
          hint="layer rules"
          level={(health.architecture_violations ?? 0) > 0 ? "bad" : "good"}
        />
      </div>
    </>
  );
}

function StatCard({
  tone,
  icon,
  k,
  v,
  s,
}: {
  tone: string;
  icon: string;
  k: string;
  v: React.ReactNode;
  s: string;
}) {
  return (
    <div className={`dp-stat t-${tone}`}>
      <span className="ico" aria-hidden="true">
        {icon}
      </span>
      <span className="body">
        <span className="k">{k}</span>
        <span className="v">{v}</span>
        <span className="s">{s}</span>
      </span>
    </div>
  );
}

function HealthCell({
  label,
  value,
  hint,
  level,
}: {
  label: string;
  value: number | string | undefined;
  hint: string;
  level: BadgeLevel;
}) {
  return (
    <div className={`dp-hcell h-${levelToClass(level)}`}>
      <span className="k">{label}</span>
      <span className="v">{value ?? "—"}</span>
      <span className="s">{hint}</span>
    </div>
  );
}

/* ------------------------------ Hotspot list ------------------------------ */

function HotspotList({
  hotspots,
  maxScore,
  onOpen,
  onCopy,
}: {
  hotspots: DependencyHotspot[];
  maxScore: number;
  onOpen: (id: string) => void;
  onCopy: (id: string) => void;
}) {
  return (
    <div className="dp-hot">
      {hotspots.map((h, i) => {
        const id = hotspotNode(h);
        const score = hotspotScore(h);
        const tone = hotspotTone(h);
        const flags = hotspotFlags(h);
        return (
          <div className="dp-hot-row" key={id || i}>
            <span className="dp-hot-name">
              <button
                type="button"
                title={`inspect ${id}`}
                onClick={() => onOpen(id)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  onCopy(id);
                }}
              >
                <span className="txt">{shortNodeLabel(id) || id || "—"}</span>
                <span className="cpy">copy</span>
              </button>
            </span>
            <span className="dp-hot-score">
              <span className="n">{score !== null ? score.toFixed(1) : "—"}</span>
              <span
                className="dp-hot-bar"
                role="img"
                aria-label={`risk score ${score ?? "unknown"} of ${maxScore || "n/a"}`}
              >
                <i className={tone} style={{ width: `${hotspotScorePct(h, maxScore)}%` }} />
              </span>
            </span>
            <span className="dp-hot-meta">
              {flags.map((f) => (
                <span key={f} className={`dp-flag ${flagClass(f)}`}>
                  {f.replace(/_/g, " ").toLowerCase()}
                </span>
              ))}
              <span className="dp-fanline">
                fan {h.fan_in ?? "—"}/{h.fan_out ?? "—"} · I={instabilityText(h.instability)}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

function flagClass(flag: string): string {
  const f = flag.toUpperCase();
  if (f === "CYCLE") return "f-cycle";
  if (f === "ARCHITECTURE_VIOLATION") return "f-violation";
  if (f === "UNRESOLVED_DI") return "f-unresolved";
  if (f === "RUNTIME_CRITICAL") return "f-critical";
  if (f === "HIGH_FAN_IN" || f === "HIGH_FAN_OUT") return "f-fan";
  return "";
}

function instabilityText(v: number | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return v.toFixed(2);
}

/* ------------------------------ Node matrix ------------------------------ */

function NodeMatrix({
  rows,
  metrics,
  hotSet,
  q,
  sort,
  onSort,
  onOpen,
  onCopy,
}: {
  rows: DependencyNode[];
  metrics: MetricsMap | undefined;
  hotSet: Set<string>;
  q: string;
  sort: { key: NodeSortKey; dir: SortDir };
  onSort: (key: NodeSortKey) => void;
  onOpen: (id: string) => void;
  onCopy: (id: string) => void;
}) {
  const arrow = (key: NodeSortKey): string => (sort.key === key ? (sort.dir === "asc" ? "▲" : "▼") : "");
  const th = (key: NodeSortKey, label: string, extra?: string) => (
    <th className={extra}>
      <span className="th-in">
        <button type="button" className="dp-th-btn" onClick={() => onSort(key)} title={`sort by ${label}`}>
          {label}
          {arrow(key) && <span className="arrow">{arrow(key)}</span>}
        </button>
      </span>
    </th>
  );
  return (
    <div className="dp-table-wrap">
      <table className="dp-table">
        <thead>
          <tr>
            {th("id", "node")}
            {th("kind", "kind")}
            {th("fan_in", "fan in", "a-end")}
            {th("fan_out", "fan out", "a-end")}
            {th("instability", "instability", "a-end")}
            {th("criticality", "criticality")}
          </tr>
        </thead>
        <tbody>
          {rows.map((n) => {
            const id = nodeId(n);
            const m = metrics?.[id];
            const rowCls = [
              hotSet.has(id) ? "dp-hot-row-mark" : "",
              isCritical(n) ? "dp-crit" : "",
              isUnresolved(n) ? "dp-unres" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <tr key={id} className={rowCls}>
                <td>
                  <button
                    type="button"
                    className="dp-node-name"
                    title={`inspect ${id}`}
                    onClick={() => onOpen(id)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      onCopy(id);
                    }}
                  >
                    <span className="pfx">{shortNodeLabel(id) ? "" : ""}</span>
                    <Marked text={id} q={q} />
                    <span className="open">inspect</span>
                  </button>
                </td>
                <td>
                  <span className={`dp-kcell k-${String(n.kind ?? "other").toLowerCase()}`}>
                    <span className="sw" aria-hidden="true" />
                    {String(n.kind ?? "—")}
                  </span>
                </td>
                <td className="num">{m?.fan_in ?? "—"}</td>
                <td className="num">{m?.fan_out ?? "—"}</td>
                <td className="num">{instabilityText(m?.instability)}</td>
                <td>
                  <StatusBadge status={String(n.criticality ?? null)} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Highlight query matches inside a node id without losing the full text. */
function Marked({ text, q }: { text: string; q: string }) {
  const runs = highlightRuns(text, q);
  return (
    <span className="txt">
      {runs.map((r, i) =>
        r.hit ? <mark className="dp-hl" key={i}>{r.text}</mark> : <span key={i}>{r.text}</span>,
      )}
    </span>
  );
}

/* ------------------------------ Cycles ------------------------------ */

function CycleList({
  cycles,
  onOpen,
}: {
  cycles: DependencyCycle[];
  onOpen: (id: string) => void;
}) {
  return (
    <div>
      {cycles.map((c, i) => {
        const path = c.path ?? [];
        return (
          <div className="dp-cycle" key={c.cycle_id ?? i}>
            <div className="dp-cycle-head">
              <span className="cid">{c.cycle_id ?? `CYC-${i + 1}`}</span>
              <StatusBadge status={c.severity} />
              <span className="meta">
                {cycleLength(c)} nodes · {(c.edge_types ?? []).join(", ") || "—"}
              </span>
            </div>
            <div className="dp-cycle-body">
              <div className="dp-cycle-path">
                {path.map((seg, si) => (
                  <span key={si} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <button
                      type="button"
                      className={`seg ${si === 0 ? "hot" : ""}`}
                      title={`inspect ${seg}`}
                      onClick={() => onOpen(seg)}
                    >
                      {shortNodeLabel(seg) || seg}
                    </button>
                    {si < path.length - 1 && <span className="arr" aria-hidden="true">→</span>}
                  </span>
                ))}
              </div>
              <div className="dp-cycle-note">
                <span className="lbl">impact</span>
                <span className="val">{c.impact || "—"}</span>
                <span className="lbl">recommended breakpoint</span>
                <span className="val">{c.recommended_breakpoint || "—"}</span>
              </div>
              {(c.source_locations ?? []).length > 0 && (
                <div className="dp-cycle-locs">
                  {(c.source_locations ?? []).map((loc, li) => (
                    <span className="loc" key={li}>
                      {loc}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------ Violations ------------------------------ */

function ViolationList({ violations }: { violations: DependencyViolation[] }) {
  return (
    <div>
      {violations.map((v, i) => (
        <div className="dp-viol" key={i}>
          <div className="dp-viol-head">
            <span className="rule">{v.rule || "—"}</span>
            <StatusBadge status={v.severity} />
            <span className="ends">
              <span>{shortNodeLabel(v.source) || v.source || "—"}</span>
              <span aria-hidden="true">→</span>
              <span>{shortNodeLabel(v.target) || v.target || "—"}</span>
            </span>
          </div>
          <div className="dp-viol-body">
            <div className="dp-viol-note">
              <span className="lbl">explanation</span>
              <span className="val">{v.explanation || "—"}</span>
            </div>
            <div className="dp-viol-note">
              <span className="lbl">remediation</span>
              <span className="val">{v.remediation || "—"}</span>
            </div>
          </div>
        </div>
      ))}
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
  onOpen: (id: string) => void;
  onCopy: (id: string) => void;
}

function NodeInspector({
  options,
  selected,
  setSelected,
  pathTarget,
  setPathTarget,
  query,
  onOpen,
  onCopy,
}: NodeInspectorProps) {
  return (
    <div className="dp-insp-grid">
      <Panel
        title="Node inspector"
        subtitle="dependencies · dependents · edge evidence"
        tight
      >
        <div className="dp-toolbar" style={{ marginBlockEnd: 10 }}>
          <input
            className="input"
            style={{ flex: 1, minWidth: 220 }}
            list="dependency-node-options"
            aria-label="Node id"
            placeholder="node id e.g. mod:nexus_scalp.application.live_engine"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          />
          <datalist id="dependency-node-options">
            {options.slice(0, 500).map((id) => (
              <option key={id} value={id} />
            ))}
          </datalist>
          <button
            className="btn small primary"
            disabled={!selected.trim()}
            onClick={() => setSelected(selected.trim())}
          >
            Inspect
          </button>
        </div>
        {!selected.trim() ? (
          <EmptyState message="Enter or pick a node id to inspect its dependencies, dependents and edge evidence." />
        ) : query.isPending ? (
          <Skeleton count={4} />
        ) : query.isError && isNotFound(query.error) ? (
          <EmptyState
            message={`Node not found: ${selected}`}
            hint="/api/dependency/node/{id} answered 404 — try a qualified name."
          />
        ) : query.isError ? (
          <ErrorState
            message={query.error instanceof Error ? query.error.message : "node endpoint failed"}
            onRetry={() => void query.refetch()}
          />
        ) : query.data ? (
          <NodeDetail data={query.data} onOpen={onOpen} onCopy={onCopy} />
        ) : null}
      </Panel>

      {query.data && (
        <div className="dp-dual">
          <PathExplorer
            source={selected}
            target={pathTarget}
            setTarget={setPathTarget}
            options={options}
          />
          <ImpactExplorer nodePath={selected} onOpen={onOpen} />
        </div>
      )}
    </div>
  );
}

function NodeDetail({
  data,
  onOpen,
  onCopy,
}: {
  data: DependencyNodeDetailResponse;
  onOpen: (id: string) => void;
  onCopy: (id: string) => void;
}) {
  const n = data.node ?? {};
  const m = data.metrics ?? null;
  const inst = instabilityPct(m);
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="dp-id-row">
        <span className="dp-id-mono" title={String(n.id ?? "")}>
          {String(n.id ?? n.qualified_name ?? "—")}
        </span>
        <StatusBadge status={String(n.status ?? null)} />
        <StatusBadge status={String(n.criticality ?? null)} />
        {n.file && <span className="tiny muted inline-mono">{String(n.file)}</span>}
      </div>
      <div className="dp-metric-grid">
        <MetricCell k="fan in" v={m?.fan_in ?? "—"} s="predecessors" />
        <MetricCell k="fan out" v={m?.fan_out ?? "—"} s="successors" />
        <MetricCell
          k="instability"
          v={inst !== null ? `${inst.toFixed(1)}%` : "—"}
          s="fo/(fi+fo)"
        />
        <MetricCell
          k="centrality"
          v={m?.centrality != null ? String(m.centrality) : "—"}
          s="degree / 2(n-1)"
        />
        <MetricCell k="violations" v={m?.violations ?? "—"} s="layer rules" />
        <MetricCell k="unresolved deps" v={m?.unresolved_deps ?? "—"} s="resolution" />
      </div>
      <div className="dp-dual">
        <DependencyList
          title="dependencies"
          ids={data.dependencies ?? []}
          onOpen={onOpen}
          onCopy={onCopy}
        />
        <DependencyList
          title="dependents"
          ids={data.dependents ?? []}
          onOpen={onOpen}
          onCopy={onCopy}
        />
      </div>
      <div>
        <div className="section-title">edge evidence ({data.incident_edges?.length ?? 0})</div>
        {(data.incident_edges ?? []).length === 0 ? (
          <EmptyState message="No incident edges." />
        ) : (
          <div className="dp-table-wrap" style={{ maxHeight: 260 }}>
            <table className="dp-table" style={{ minWidth: 0 }}>
              <thead>
                <tr>
                  <th>
                    <span className="th-in">source</span>
                  </th>
                  <th>
                    <span className="th-in">kind</span>
                  </th>
                  <th>
                    <span className="th-in">target</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {(data.incident_edges ?? []).map((e, i) => (
                  <tr key={i}>
                    <td className="tiny">{String(e.source ?? "—")}</td>
                    <td className="tiny">{String(e.kind ?? "—")}</td>
                    <td className="tiny">{String(e.target ?? "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function MetricCell({ k, v, s }: { k: string; v: React.ReactNode; s: string }) {
  return (
    <div className="dp-mcell">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      <div className="s">{s}</div>
    </div>
  );
}

function DependencyList({
  title,
  ids,
  onOpen,
  onCopy,
}: {
  title: string;
  ids: string[];
  onOpen: (id: string) => void;
  onCopy: (id: string) => void;
}) {
  return (
    <div>
      <div className="dp-list-head">
        <span className="t">{title} ({ids.length})</span>
      </div>
      {ids.length === 0 ? (
        <EmptyState message={`No ${title}.`} />
      ) : (
        <div className="dp-list">
          {ids.map((d, i) => (
            <button
              type="button"
              key={d || i}
              title={`inspect ${d}`}
              onClick={() => onOpen(d)}
              onContextMenu={(e) => {
                e.preventDefault();
                onCopy(d);
              }}
            >
              <span className="pfx">{prefixDot(d)}</span>
              <span className="txt">{shortNodeLabel(d) || d}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function prefixDot(id: string): string {
  const p = id.indexOf(":");
  return p > 0 ? id.slice(0, p) : "·";
}

/* ------------------------------ Path explorer ------------------------------ */

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
      <div className="dp-toolbar" style={{ marginBlockEnd: 10 }}>
        <input
          className="input"
          style={{ flex: 1, minWidth: 180 }}
          list="dependency-node-options"
          aria-label="Target node id"
          placeholder="target node id"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        />
        <datalist id="dependency-node-options">
          {options.slice(0, 500).map((id) => (
            <option key={id} value={id} />
          ))}
        </datalist>
      </div>
      <div className="tiny muted" style={{ marginBlockEnd: 8 }}>
        from <span className="inline-mono">{shortNodeLabel(source) || source || "—"}</span> to{" "}
        <span className="inline-mono">{shortNodeLabel(target) || target || "—"}</span>
      </div>
      {target.trim() === "" ? (
        <EmptyState message="Pick a target node to compute the shortest dependency path." />
      ) : q.isPending ? (
        <Skeleton count={3} />
      ) : q.isError ? (
        <ErrorState
          message={q.error instanceof Error ? q.error.message : "path endpoint failed"}
          onRetry={() => void q.refetch()}
        />
      ) : pathUnknownNode(q.data) ? (
        <EmptyState
          message="Unknown node in the path query."
          hint={`/api/dependency/path answered ${q.data?.error} — pick nodes from the list.`}
        />
      ) : pathFound(q.data) ? (
        <div>
          <div className="tiny muted" style={{ marginBlockEnd: 6 }}>
            length {pathLength(q.data)} · {(q.data?.edges ?? []).length} edges
          </div>
          <div className="dp-path-steps">
            {(q.data?.path ?? []).map((p, i, arr) => (
              <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span className={`seg ${i === 0 || i === arr.length - 1 ? "endpoint" : ""}`}>
                  {shortNodeLabel(p) || p}
                </span>
                {i < arr.length - 1 && <span className="arr" aria-hidden="true">→</span>}
              </span>
            ))}
          </div>
          {(q.data?.edges ?? []).length > 0 && (
            <div style={{ marginTop: 8, display: "grid", gap: 2 }}>
              {(q.data?.edges ?? []).map((e, i) => (
                <div className="dp-path-edge" key={i}>
                  <span>{shortNodeLabel(e.source) || e.source}</span>
                  <span aria-hidden="true">→</span>
                  <span>{shortNodeLabel(e.target) || e.target}</span>
                  {(e.kinds ?? []).map((kd, ki) => (
                    <span className="kind" key={ki}>
                      {kd}
                    </span>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <EmptyState message="No path found between these nodes." hint="shortest_path answered found=false" />
      )}
    </Panel>
  );
}

/* ------------------------------ Impact explorer ------------------------------ */

function ImpactExplorer({
  nodePath,
  onOpen,
}: {
  nodePath: string;
  onOpen: (id: string) => void;
}) {
  const q = useQuery({
    queryKey: ["dependency", "impact", nodePath],
    queryFn: ({ signal }) => dependencyApi.impact(nodePath, signal),
    enabled: nodePath.trim() !== "",
    retry: false,
  });

  const data = q.data;
  const rows = impactRowsNSE(data);
  const peak = rows.reduce((m, r) => Math.max(m, r.ids.length), 0);
  const kind = impactWord(data?.impact_kind);
  const level = impactLevel(data?.impact_kind);

  return (
    <Panel title="Impact explorer (blast radius)" tight>
      <div className="tiny muted" style={{ marginBlockEnd: 8 }}>
        node <span className="inline-mono">{shortNodeLabel(nodePath) || nodePath || "—"}</span>
      </div>
      {nodePath.trim() === "" ? (
        <EmptyState message="Select a node first, then inspect its impact." />
      ) : q.isPending ? (
        <Skeleton count={3} />
      ) : q.isError ? (
        <ErrorState
          message={q.error instanceof Error ? q.error.message : "impact endpoint failed"}
          onRetry={() => void q.refetch()}
        />
      ) : impactUnknownNode(data) ? (
        <EmptyState
          message="Unknown node — no impact computed."
          hint={`/api/dependency/impact answered ${data?.error} for ${data?.node_id ?? nodePath}.`}
        />
      ) : (
        <div className="dp-blast">
          <div className="dp-id-row">
            <span className={`badge ${levelToBadge(level)}`}>{kind}</span>
            <span className="tiny muted">
              blast radius <b className="inline-mono">{impactTotal(data)}</b> · direct{" "}
              <b className="inline-mono">{data?.direct?.length ?? 0}</b> · transitive{" "}
              <b className="inline-mono">{data?.transitive?.length ?? 0}</b>
            </span>
          </div>
          {rows.length === 0 ? (
            <EmptyState message="No downstream impact recorded." hint="The analyzer reported zero direct and transitive successors." />
          ) : (
            rows.map((r) => (
              <div className="dp-blast-row" key={r.label}>
                <span className="lab">{r.label}</span>
                <span
                  className="dp-blast-track"
                  role="img"
                  aria-label={`${r.label}: ${r.ids.length} nodes`}
                >
                  <i className={r.tone} style={{ width: `${(r.ids.length / Math.max(1, peak)) * 100}%` }} />
                </span>
                <span className="n">{r.ids.length}</span>
              </div>
            ))
          )}
          {rows.some((r) => r.foreign > 0) && (
            <div className="dp-blast-note">
              {rows.reduce((s, r) => s + r.foreign, 0)} stdlib/external leaves excluded from the
              bars (not NSE architecture).
            </div>
          )}
          {rows.length > 0 && (
            <div className="dp-impact-list">
              {rows.flatMap((r) =>
                r.ids.map((id, i) => (
                  <button
                    type="button"
                    className="dp-impact-item"
                    key={`${r.label}-${id}-${i}`}
                    title={`inspect ${id}`}
                    onClick={() => onOpen(id)}
                  >
                    <span className="pfx">{prefixDot(id)}</span>
                    <span className="txt">{shortNodeLabel(id) || id}</span>
                  </button>
                )),
              ).slice(0, 60)}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

function levelToBadge(level: BadgeLevel): string {
  return level;
}
