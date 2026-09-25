/**
 * governanceApi — model governance, experience, forensics, promotion gates.
 *
 * Routes (model_governance_routes.py, raw JSON):
 *  GET  /api/models, /api/models/champion, /challengers, /summary, /integrity,
 *       /runs, /runs/{run_id}, /comparison/{run_id}, /shadow/runs, /shadow/summary,
 *       /shadow70/health, /shadow70/summary
 *  GET  /api/models/governance/{status,registry,events,audits,comparisons,health,
 *       review,promotion-preview,rollback-preview}
 *  POST /api/models/governance/emergency/{freeze,unfreeze,disable}
 *  POST /api/models/promotion/{approve,execute,rollback}
 *  POST /api/models/registry/reconcile
 *  POST /api/models/shadow/{evaluate-promotion,outcomes,worker/start,worker/stop}
 *  POST /api/models/train ; POST /api/models/worker/{start,stop,cancel}
 * Experience + forensics + research gates (debug_research_routes.py):
 *  GET  /api/experience/{summary,strategies,decision,models}
 *  GET  /api/forensics/{health,deploy-gate}
 *  GET  /api/research/gates ; POST /api/research/promote
 */

import { getLegacy, send } from "@/core/transport";
import { toQuery } from "@/core/config";
import type {
  ExperienceStrategies,
  ExperienceSummary,
  DeployGateResult,
  ForensicsHealth,
  GovernanceActionResult,
  GovernanceAuditRow,
  ModelGovernanceStatus,
  ModelRegistryRow,
  ModelRunRow,
  ResearchGateRow,
} from "@/types/features";

const M = "/api/models";

export const governanceApi = {
  // ------------------------------------------------------------- model state
  models: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(M, signal),

  champion: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/champion`, signal),

  challengers: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/challengers`, signal),

  summary: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/summary`, signal),

  runs: (params: { limit?: number } = {}, signal?: AbortSignal): Promise<ModelRunRow[]> =>
    getLegacy<ModelRunRow[]>(`${M}/runs${toQuery({ ...params })}`, signal),

  runDetail: (runId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/runs/${encodeURIComponent(runId)}`, signal),

  comparison: (runId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/comparison/${encodeURIComponent(runId)}`, signal),

  shadowRuns: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/shadow/runs`, signal),

  shadowSummary: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/shadow/summary`, signal),

  // ------------------------------------------------------------ governance
  governanceStatus: (signal?: AbortSignal): Promise<ModelGovernanceStatus> =>
    getLegacy<ModelGovernanceStatus>(`${M}/governance/status`, signal),

  governanceRegistry: (signal?: AbortSignal): Promise<ModelRegistryRow[]> =>
    getLegacy<ModelRegistryRow[]>(`${M}/governance/registry`, signal),

  governanceEvents: (params: { limit?: number } = {}, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/governance/events${toQuery({ ...params })}`, signal),

  governanceAudits: (params: { limit?: number } = {}, signal?: AbortSignal): Promise<GovernanceAuditRow[]> =>
    getLegacy<GovernanceAuditRow[]>(`${M}/governance/audits${toQuery({ ...params })}`, signal),

  governanceComparisons: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/governance/comparisons`, signal),

  governanceHealth: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/governance/health`, signal),

  governanceReview: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/governance/review`, signal),

  promotionPreview: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/governance/promotion-preview`, signal),

  rollbackPreview: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${M}/governance/rollback-preview`, signal),

  // ------------------------------------------------------- governance actions
  emergencyFreeze: (): Promise<GovernanceActionResult> =>
    send<GovernanceActionResult>(`${M}/governance/emergency/freeze`),

  emergencyUnfreeze: (): Promise<GovernanceActionResult> =>
    send<GovernanceActionResult>(`${M}/governance/emergency/unfreeze`),

  emergencyDisable: (): Promise<GovernanceActionResult> =>
    send<GovernanceActionResult>(`${M}/governance/emergency/disable`),

  promotionApprove: (payload: Record<string, unknown>): Promise<GovernanceActionResult> =>
    send<GovernanceActionResult>(`${M}/promotion/approve`, payload),

  promotionExecute: (payload: Record<string, unknown>): Promise<GovernanceActionResult> =>
    send<GovernanceActionResult>(`${M}/promotion/execute`, payload),

  promotionRollback: (payload: Record<string, unknown> = {}): Promise<GovernanceActionResult> =>
    send<GovernanceActionResult>(`${M}/promotion/rollback`, payload),

  registryReconcile: (): Promise<GovernanceActionResult> =>
    send<GovernanceActionResult>(`${M}/registry/reconcile`),

  shadowEvaluatePromotion: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${M}/shadow/evaluate-promotion`, payload),

  shadowOutcomes: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${M}/shadow/outcomes`, payload),

  shadowWorkerStart: (): Promise<Record<string, unknown>> => send<Record<string, unknown>>(`${M}/shadow/worker/start`),

  shadowWorkerStop: (): Promise<Record<string, unknown>> => send<Record<string, unknown>>(`${M}/shadow/worker/stop`),

  train: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${M}/train`, payload),

  workerStart: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${M}/worker/start`, payload),

  workerStop: (): Promise<Record<string, unknown>> => send<Record<string, unknown>>(`${M}/worker/stop`),

  workerCancel: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${M}/worker/cancel`, payload),

  // --------------------------------------------------------------- experience
  experienceSummary: (signal?: AbortSignal): Promise<ExperienceSummary> =>
    getLegacy<ExperienceSummary>("/api/experience/summary", signal),

  experienceStrategies: (signal?: AbortSignal): Promise<ExperienceStrategies> =>
    getLegacy<ExperienceStrategies>("/api/experience/strategies", signal),

  experienceDecision: (params: Record<string, string | number> = {}, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`/api/experience/decision${toQuery({ ...params })}`, signal),

  experienceModels: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/experience/models", signal),

  // ---------------------------------------------------------------- forensics
  forensicsHealth: (signal?: AbortSignal): Promise<ForensicsHealth> =>
    getLegacy<ForensicsHealth>("/api/forensics/health", signal),

  deployGate: (signal?: AbortSignal): Promise<DeployGateResult> =>
    getLegacy<DeployGateResult>("/api/forensics/deploy-gate", signal),

  // ---------------------------------------------------------- research gates
  researchGates: (params: { strategy_id?: string } = {}, signal?: AbortSignal): Promise<ResearchGateRow[]> =>
    getLegacy<ResearchGateRow[]>(`/api/research/gates${toQuery({ ...params })}`, signal),

  researchPromote: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/research/promote", payload),
};
