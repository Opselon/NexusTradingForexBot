/**
 * Dependency Intelligence — typed transport surface.
 *
 * Legacy parity: Web/dependency_api.js (window.NXDependency.api), which drove
 * the standalone Web/dependency.html "Dependency Intelligence" dashboard —
 * a whole legacy surface the React console never had. Every endpoint the
 * legacy page called is wired here (all 9 read routes of
 * src/nexus_scalp/web/dependency_routes.py, prefix=/api/dependency):
 *
 *   GET /api/dependency/summary      — repo stats + health + top hotspots
 *   GET /api/dependency/graph        — full node/edge graph
 *   GET /api/dependency/node/{id}    — node detail: deps, dependents, evidence
 *   GET /api/dependency/path         — shortest path source->target
 *   GET /api/dependency/impact       — blast radius of a node
 *   GET /api/dependency/cycles       — circular-import detection
 *   GET /api/dependency/violations   — architecture violations
 *   GET /api/dependency/metrics      — per-node centrality/criticality
 *   GET /api/dependency/health       — rolled-up health verdict
 *
 * All routes are read-only static analysis of the repo; responses are raw
 * JSON (no v1 envelope). `status: "degraded"` from /health is rendered as a
 * degraded verdict, never as healthy.
 *
 * PRODUCER-VERIFIED SHAPES (src/nexus_scalp/dependency_intelligence/analysis.py,
 * probed live 2026-09-23 against /api/dependency/* on port 59273). Every key
 * below is read from the producer's own to_dict()/return, not inferred:
 *   - hotspots[]: node_id / risk_score / flags[] / fan_in / fan_out /
 *     instability / criticality  (GraphAnalyzer.hotspots)
 *   - impact: changed / direct[] / transitive[] / tests_likely_affected[] /
 *     api_impact[] / runtime_impact[] / impact_kind  (GraphAnalyzer.impact;
 *     "direct"/"transitive" are LISTS of node ids; there is NO `impacted`,
 *     `impacted_count` or `risk_level` key — the old UI read those and
 *     rendered an empty blast radius on every node)
 *   - path: found / source / target / path[] / edges[{source,target,kinds}]
 *   - cycles[]: cycle_id / severity / path[] / edge_types[] /
 *     source_locations[] / impact / recommended_breakpoint  (CycleRecord)
 *   - violations[]: severity / source / target / rule / evidence /
 *     explanation / remediation  (Violation)
 *   - node detail: node / metrics / dependencies[] / dependents[] /
 *     incident_edges[]; metrics is the Metrics dataclass
 *     (fan_in/fan_out/instability/centrality/in_cycle/violations/
 *      unresolved_deps) — NO in_degree/out_degree
 */

import { getLegacy } from "@/api/client";
import { toQuery } from "@/core";

export type Row = Record<string, unknown>;

/* -------------------------------- summary -------------------------------- */

export interface DependencyRepositoryStats {
  files_analyzed?: number;
  modules?: number;
  nodes?: number;
  edges?: number;
  di_registrations?: number;
}

export interface DependencyHealth {
  cycles?: number;
  unresolved_imports?: number;
  unresolved_di_bindings?: number;
  architecture_violations?: number;
}

/** Hotspot row — GraphAnalyzer.hotspots() dict shape (producer-verified). */
export interface DependencyHotspot extends Row {
  node_id?: string;
  risk_score?: number;
  flags?: string[];
  fan_in?: number;
  fan_out?: number;
  instability?: number;
  criticality?: string;
}

export interface DependencySummaryResponse {
  status?: string;
  analyzer_version?: string;
  generated_at?: string;
  repository?: DependencyRepositoryStats;
  health?: DependencyHealth;
  hotspots?: DependencyHotspot[];
  scan_duration_ms?: number;
}

/* --------------------------------- graph --------------------------------- */

export interface DependencyNode extends Row {
  id?: string;
  qualified_name?: string;
  display_name?: string;
  kind?: string;
  layer?: string;
  status?: string;
  criticality?: string;
  module?: string;
  package?: string;
  file?: string;
  confidence?: number;
  metadata?: Row;
}

export interface DependencyEdge extends Row {
  source?: string;
  target?: string;
  kind?: string;
  confidence?: number;
  evidence?: Row;
  resolution?: string;
  metadata?: Row;
}

export interface DependencyGraphResponse {
  nodes?: DependencyNode[];
  edges?: DependencyEdge[];
  analyzer_version?: string;
  generated_at?: string;
}

/* ---------------------------------- node --------------------------------- */

/** Per-node metrics — GraphAnalyzer.compute_metrics() Metrics dataclass. */
export interface NodeMetrics {
  fan_in?: number;
  fan_out?: number;
  instability?: number;
  centrality?: number;
  in_cycle?: boolean;
  violations?: number;
  unresolved_deps?: number;
  [k: string]: unknown;
}

export interface DependencyNodeDetailResponse {
  node?: DependencyNode;
  metrics?: NodeMetrics | null;
  dependencies?: string[];
  dependents?: string[];
  incident_edges?: DependencyEdge[];
}

/* --------------------------- cycles / violations -------------------------- */

export interface DependencyCycle extends Row {
  cycle_id?: string;
  severity?: string;
  path?: string[];
  edge_types?: string[];
  source_locations?: string[];
  impact?: string;
  recommended_breakpoint?: string;
}

export interface DependencyCyclesResponse {
  status?: string;
  count?: number;
  cycles?: DependencyCycle[];
}

export interface DependencyViolation extends Row {
  severity?: string;
  source?: string;
  target?: string;
  rule?: string;
  evidence?: Row;
  explanation?: string;
  remediation?: string;
}

export interface DependencyViolationsResponse {
  status?: string;
  count?: number;
  violations?: DependencyViolation[];
}

/* --------------------- path / impact / metrics / health ------------------- */

export interface PathEdgeDetail extends Row {
  source?: string;
  target?: string;
  kinds?: string[];
}

export interface DependencyPathResponse extends Row {
  found?: boolean;
  source?: string;
  target?: string;
  path?: string[];
  edges?: PathEdgeDetail[];
  /** producer's unknown-node shape — reported, never rendered as "no path" */
  error?: string;
}

/** Impact payload — GraphAnalyzer.impact() (producer-verified, lists of ids). */
export interface DependencyImpactResponse extends Row {
  changed?: string;
  direct?: string[];
  transitive?: string[];
  tests_likely_affected?: string[];
  api_impact?: string[];
  runtime_impact?: string[];
  impact_kind?: string;
  /** producer's unknown-node shape: {"error": "unknown_node", "node_id": …} */
  error?: string;
  node_id?: string;
}

export interface DependencyMetricsResponse {
  status?: string;
  nodes?: number;
  metrics?: Record<string, NodeMetrics>;
}

export interface DependencyHealthResponse {
  status?: string;
  analyzer_version?: string;
  generated_at?: string;
  nodes?: number;
  edges?: number;
  cycles?: number;
  violations?: number;
  unresolved_imports?: number;
  di_registrations?: number;
}

export const dependencyApi = {
  summary: (signal?: AbortSignal): Promise<DependencySummaryResponse> =>
    getLegacy<DependencySummaryResponse>("/api/dependency/summary", signal),

  graph: (signal?: AbortSignal): Promise<DependencyGraphResponse> =>
    getLegacy<DependencyGraphResponse>("/api/dependency/graph", signal),

  node: (nodeId: string, signal?: AbortSignal): Promise<DependencyNodeDetailResponse> =>
    getLegacy<DependencyNodeDetailResponse>(
      `/api/dependency/node/${encodeURIComponent(nodeId)}`,
      signal,
    ),

  path: (source: string, target: string, signal?: AbortSignal): Promise<DependencyPathResponse> =>
    getLegacy<DependencyPathResponse>(
      `/api/dependency/path${toQuery({ source, target })}`,
      signal,
    ),

  impact: (nodePath: string, signal?: AbortSignal): Promise<DependencyImpactResponse> =>
    getLegacy<DependencyImpactResponse>(
      `/api/dependency/impact${toQuery({ path: nodePath })}`,
      signal,
    ),

  cycles: (signal?: AbortSignal): Promise<DependencyCyclesResponse> =>
    getLegacy<DependencyCyclesResponse>("/api/dependency/cycles", signal),

  violations: (signal?: AbortSignal): Promise<DependencyViolationsResponse> =>
    getLegacy<DependencyViolationsResponse>("/api/dependency/violations", signal),

  metrics: (signal?: AbortSignal): Promise<DependencyMetricsResponse> =>
    getLegacy<DependencyMetricsResponse>("/api/dependency/metrics", signal),

  health: (signal?: AbortSignal): Promise<DependencyHealthResponse> =>
    getLegacy<DependencyHealthResponse>("/api/dependency/health", signal),
};
