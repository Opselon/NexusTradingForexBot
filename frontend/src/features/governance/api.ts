/**
 * Governance: typed transport surface over @/api/client.
 *
 * Legacy raw-JSON routes (verified): /api/experience/* (debug_research_routes.py)
 * and /api/models/governance/* + promotion/emergency (model_governance_routes.py).
 * Command payloads carry the actor identity the backend requires (no actor =>
 * the backend refuses — the refusal is surfaced verbatim).
 */

import { getLegacy, send } from "@/api/client";
import type {
  ExperienceDecisionDto,
  ExperienceModelsDto,
  ExperienceSelfHealDto,
  ExperienceStrategiesDto,
  ExperienceSummaryDto,
  GovernanceAuditsDto,
  GovernanceComparisonsDto,
  GovernanceEventsDto,
  GovernanceHealthDto,
  GovernancePromotionPreviewDto,
  GovernanceRegistryDto,
  GovernanceReviewDto,
  GovernanceRollbackPreviewDto,
  GovernanceStatusDto,
  ModelsListDto,
  PromotionCommandDto,
} from "./model";

const qs = (params: Record<string, string | number | boolean | undefined>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") usp.set(k, String(v));
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export const governanceApi = {
  // ---- experience ledger (legacy) ------------------------------------------
  experienceSummary: (signal?: AbortSignal): Promise<ExperienceSummaryDto> =>
    getLegacy<ExperienceSummaryDto>("/api/experience/summary", signal),

  experienceModels: (limit: number, signal?: AbortSignal): Promise<ExperienceModelsDto> =>
    getLegacy<ExperienceModelsDto>(`/api/experience/models${qs({ limit })}`, signal),

  experienceStrategies: (limit: number, signal?: AbortSignal): Promise<ExperienceStrategiesDto> =>
    getLegacy<ExperienceStrategiesDto>(`/api/experience/strategies${qs({ limit })}`, signal),

  experienceDecision: (signal?: AbortSignal): Promise<ExperienceDecisionDto> =>
    getLegacy<ExperienceDecisionDto>("/api/experience/decision", signal),

  // ---- model governance ----------------------------------------------------
  governanceHealth: (signal?: AbortSignal): Promise<GovernanceHealthDto> =>
    getLegacy<GovernanceHealthDto>("/api/models/governance/health", signal),

  governanceStatus: (signal?: AbortSignal): Promise<GovernanceStatusDto> =>
    getLegacy<GovernanceStatusDto>("/api/models/governance/status", signal),

  governanceRegistry: (signal?: AbortSignal): Promise<GovernanceRegistryDto> =>
    getLegacy<GovernanceRegistryDto>("/api/models/governance/registry", signal),

  governanceEvents: (limit: number, event: string | undefined, signal?: AbortSignal): Promise<GovernanceEventsDto> =>
    getLegacy<GovernanceEventsDto>(`/api/models/governance/events${qs({ limit, event })}`, signal),

  governanceComparisons: (limit: number, runId: string | undefined, signal?: AbortSignal): Promise<GovernanceComparisonsDto> =>
    getLegacy<GovernanceComparisonsDto>(`/api/models/governance/comparisons${qs({ limit, run_id: runId })}`, signal),

  governanceReview: (signal?: AbortSignal): Promise<GovernanceReviewDto> =>
    getLegacy<GovernanceReviewDto>("/api/models/governance/review", signal),

  governanceAudits: (limit: number, signal?: AbortSignal): Promise<GovernanceAuditsDto> =>
    getLegacy<GovernanceAuditsDto>(`/api/models/governance/audits${qs({ limit })}`, signal),

  promotionPreview: (modelId: string, modelVersion: string, signal?: AbortSignal): Promise<GovernancePromotionPreviewDto> =>
    getLegacy<GovernancePromotionPreviewDto>(`/api/models/governance/promotion-preview${qs({ model_id: modelId, model_version: modelVersion })}`, signal),

  rollbackPreview: (failedModelId: string, previousModelId: string, signal?: AbortSignal): Promise<GovernanceRollbackPreviewDto> =>
    getLegacy<GovernanceRollbackPreviewDto>(`/api/models/governance/rollback-preview${qs({ failed_model_id: failedModelId, previous_model_id: previousModelId })}`, signal),

  modelsList: (status: string | undefined, signal?: AbortSignal): Promise<ModelsListDto> =>
    getLegacy<ModelsListDto>(`/api/models${qs({ status, limit: 100 })}`, signal),

  // ---- commands (actor identity mandatory on the backend) -------------------
  approvePromotion: (payload: { model_id: string; model_version?: string; actor: string; reason?: string }): Promise<PromotionCommandDto> =>
    send<PromotionCommandDto>("/api/models/promotion/approve", payload),

  executePromotion: (payload: {
    model_id: string;
    model_version?: string;
    actor: string;
    reason?: string;
    approval_token: string;
  }): Promise<PromotionCommandDto> => send<PromotionCommandDto>("/api/models/promotion/execute", payload),

  rollbackPromotion: (payload: {
    actor: string;
    failed_model_id?: string;
    failed_version?: string;
    previous_model_id?: string;
    previous_version?: string;
    reason?: string;
  }): Promise<PromotionCommandDto> => send<PromotionCommandDto>("/api/models/promotion/rollback", payload),

  freeze: (payload: { actor: string; reason?: string }): Promise<PromotionCommandDto> =>
    send<PromotionCommandDto>("/api/models/governance/emergency/freeze", payload),

  unfreeze: (payload: { actor: string; reason?: string }): Promise<PromotionCommandDto> =>
    send<PromotionCommandDto>("/api/models/governance/emergency/unfreeze", payload),

  disableCandidate: (payload: { actor: string; model_id: string; reason?: string }): Promise<PromotionCommandDto> =>
    send<PromotionCommandDto>("/api/models/governance/emergency/disable", payload),

  reconcileRegistry: (): Promise<GovernanceRegistryDto> => send<GovernanceRegistryDto>("/api/models/registry/reconcile", {}),

  selfHealExperience: (): Promise<ExperienceSelfHealDto> => send<ExperienceSelfHealDto>("/api/experience/self-heal", {}),
};
