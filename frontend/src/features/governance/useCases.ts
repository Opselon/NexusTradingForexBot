/**
 * Governance: application use cases.
 *
 * Backend-authoritative rule: the promotion ladder is derived ONLY from
 * /api/models/governance/status (champion/candidate/gates/promotion flags);
 * commands re-fetch status afterwards. The UI cannot unfreeze or bypass a
 * gate — it can only ask, and the backend's refusal is the shown result.
 */

import { governanceApi } from "./api";
import { promotionLadder, toLedgerRow, type Row } from "./model";

export const governanceQueries = {
  experienceSummary: (signal?: AbortSignal) => governanceApi.experienceSummary(signal),
  experienceModels: (signal?: AbortSignal) => governanceApi.experienceModels(25, signal),
  experienceStrategies: (signal?: AbortSignal) => governanceApi.experienceStrategies(50, signal),
  experienceDecision: (signal?: AbortSignal) => governanceApi.experienceDecision(signal),
  status: (signal?: AbortSignal) => governanceApi.governanceStatus(signal),
  health: (signal?: AbortSignal) => governanceApi.governanceHealth(signal),
  registry: (signal?: AbortSignal) => governanceApi.governanceRegistry(signal),
  events: (event: string | undefined, signal?: AbortSignal) => governanceApi.governanceEvents(200, event, signal),
  comparisons: (signal?: AbortSignal) => governanceApi.governanceComparisons(100, undefined, signal),
  review: (signal?: AbortSignal) => governanceApi.governanceReview(signal),
  audits: (signal?: AbortSignal) => governanceApi.governanceAudits(100, signal),
  modelsList: (status: string | undefined, signal?: AbortSignal) => governanceApi.modelsList(status, signal),
};

export const governanceUseCases = {
  ladder: (status: Parameters<typeof promotionLadder>[0]) => promotionLadder(status),
  ledgerRows: (rows: Row[]) => rows.map(toLedgerRow),

  // commands
  approve: (modelId: string, modelVersion: string, actor: string, reason: string) =>
    governanceApi.approvePromotion({ model_id: modelId, model_version: modelVersion, actor, reason }),
  execute: (modelId: string, modelVersion: string, actor: string, reason: string, approvalToken: string) =>
    governanceApi.executePromotion({ model_id: modelId, model_version: modelVersion, actor, reason, approval_token: approvalToken }),
  rollback: (payload: { actor: string; failed_model_id: string; previous_model_id: string; reason: string }) =>
    governanceApi.rollbackPromotion(payload),
  freeze: (actor: string, reason: string) => governanceApi.freeze({ actor, reason }),
  unfreeze: (actor: string, reason: string) => governanceApi.unfreeze({ actor, reason }),
  disableCandidate: (actor: string, modelId: string, reason: string) =>
    governanceApi.disableCandidate({ actor, model_id: modelId, reason }),
  reconcileRegistry: () => governanceApi.reconcileRegistry(),
  selfHealExperience: () => governanceApi.selfHealExperience(),
};
