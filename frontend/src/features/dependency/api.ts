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

export interface DependencySummaryResponse {
  status?: string;
  analyzer_version?: string;
  generated_at?: string;
  repository?: DependencyRepositoryStats;
  health?: DependencyHealth;
  hotspots?: Row[];
  scan_duration_ms?: number;
}

/* --------------------------------- graph --------------------------------- */

export interface DependencyNode extends Row {
  id?: string;
  qualified_name?: string;
  kind?: string;
  layer?: string;
  status?: string;
  criticality?: string;
}

export interface DependencyEdge extends Row {
  source?: string;
  target?: string;
  kind?: string;
  evidence?: Row | Row[];
}

export interface DependencyGraphResponse {
  nodes?: DependencyNode[];
  edges?: DependencyEdge[];
  analyzer_version?: string;
  generated_at?: string;
}

/* ---------------------------------- node --------------------------------- */

export interface NodeMetrics {
  in_degree?: number;
  out_degree?: number;
  fan_in?: number;
  fan_out?: number;
  centrality?: number;
  criticality?: string;
  layer?: string;
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

export interface DependencyCyclesResponse {
  status?: string;
  count?: number;
  cycles?: Row[];
}

export interface DependencyViolationsResponse {
  status?: string;
  count?: number;
  violations?: Row[];
}

/* --------------------- path / impact / metrics / health ------------------- */

export interface DependencyPathResponse extends Row {
  found?: boolean;
  path?: string[];
  length?: number;
}

export interface DependencyImpactResponse extends Row {
  risk_level?: string;
  impacted?: string[];
  impacted_count?: number;
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
    getLegacy<DependencyImpactResponse>(`/api/dependency/impact${toQuery({ path: nodePath })}`, signal),

  cycles: (signal?: AbortSignal): Promise<DependencyCyclesResponse> =>
    getLegacy<DependencyCyclesResponse>("/api/dependency/cycles", signal),

  violations: (signal?: AbortSignal): Promise<DependencyViolationsResponse> =>
    getLegacy<DependencyViolationsResponse>("/api/dependency/violations", signal),

  metrics: (signal?: AbortSignal): Promise<DependencyMetricsResponse> =>
    getLegacy<DependencyMetricsResponse>("/api/dependency/metrics", signal),

  health: (signal?: AbortSignal): Promise<DependencyHealthResponse> =>
    getLegacy<DependencyHealthResponse>("/api/dependency/health", signal),
};
