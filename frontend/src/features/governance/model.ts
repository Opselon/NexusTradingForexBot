/**
 * Governance: DTO contracts + VO mappers (pure).
 *
 * Legacy raw JSON (serialize_enums). Experience summary carries
 * {enabled, recorded_experiences, lifecycle_counts, active_strategies,
 * retired_strategies, fetched_at}; governance status carries
 * {champion, candidate, gates, promotion{eligible,approved,frozen}}.
 * Everything unknown stays null/UNKNOWN — badges never guess.
 */

import { useI18n } from "@/stores/i18nStore";

export type Row = Record<string, unknown>;

export const str = (v: unknown): string | null => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null);
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
export const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});
export const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export interface ExperienceSummaryDto {
  enabled?: boolean;
  recorded_experiences?: number;
  active_strategies?: number;
  retired_strategies?: number;
  lifecycle_counts?: Record<string, number>;
  fetched_at?: string;
  [key: string]: unknown;
}

export type ExperienceStrategiesDto = Row[]; // bare list endpoint
export type ExperienceModelsDto = Row[]; // bare list endpoint

export interface ExperienceDecisionDto {
  available?: boolean;
  decision?: Row;
}

export interface ExperienceSelfHealDto {
  success?: boolean;
  rebuilt_strategies?: number;
  reason?: string;
}

export interface GovernanceHealthDto {
  available?: boolean;
  health?: Row;
}

export interface GovernanceStatusDto {
  available?: boolean;
  champion?: { model_id?: string; version?: string; schema?: string; hash?: string };
  candidate?: { model_id?: string; status?: string; schema?: string };
  gates?: Record<string, string>;
  promotion?: { eligible?: boolean; approved?: boolean; frozen?: boolean };
}

export interface GovernanceRegistryDto {
  available?: boolean;
  registry?: Row;
  reason?: string;
  error?: string;
}

export interface GovernanceEventsDto {
  available?: boolean;
  events?: Row[];
}

export interface GovernanceComparisonsDto {
  available?: boolean;
  comparisons?: Row[];
}

export interface GovernanceReviewDto {
  available?: boolean;
  calibration?: { buckets?: Row[]; brier?: number | null; ece?: number | null };
  drift?: Row[];
  divergence?: Row;
  samples?: number;
}

export interface GovernanceAuditsDto {
  available?: boolean;
  audits?: Row[];
}

export interface GovernancePromotionPreviewDto {
  available?: boolean;
  preview?: Row;
  reason?: string;
  error?: string;
}

export interface GovernanceRollbackPreviewDto {
  available?: boolean;
  preview?: Row;
  reason?: string;
  error?: string;
}

export interface ModelsListDto {
  available?: boolean;
  models?: Row[];
}

export interface PromotionCommandDto {
  available?: boolean;
  transition?: Row;
  state?: Row;
  reason?: string;
  error?: { code?: string; message?: string; request_id?: string } | string;
}

/** Promotion ladder VO: pipeline position decided by backend status strings. */
export interface LadderStepVo {
  id: string;
  label: string;
  active: boolean;
  state: string;
}

export function promotionLadder(status: GovernanceStatusDto | undefined): LadderStepVo[] {
  const champ = obj(status?.champion);
  const cand = obj(status?.candidate);
  const promo = obj(status?.promotion);
  const gates = obj(status?.gates);
  const candidateStatus = (str(cand.status) ?? "NONE").toUpperCase();
  const approved = bool(promo.approved) ?? candidateStatus === "APPROVED";
  const frozen = bool(promo.frozen) ?? false;
  const step = (id: string, label: string, active: boolean, state: string): LadderStepVo => ({ id, label, active, state });
  return [
    step("champion", "Champion", true, champ.model_id ? "SERVING" : "NONE"),
    step("challenger", "Challenger", Boolean(cand.model_id), candidateStatus),
    step("gates", "Gates", true, Object.values(gates).join("/") || "—"),
    step("approved", "Approved", approved, approved ? "APPROVED" : "PENDING"),
    step("promotion", "Promotion eligible", bool(promo.eligible) ?? false, frozen ? "FROZEN" : bool(promo.eligible) ? "ELIGIBLE" : "NOT ELIGIBLE"),
  ];
}

/** Decision-ledger row VO for the events table (columns vary by event type). */
export interface LedgerRowVo {
  at: string | null;
  event: string | null;
  modelId: string | null;
  actor: string | null;
  reason: string | null;
  raw: Row;
}

export function toLedgerRow(row: Row): LedgerRowVo {
  return {
    at: str(row.created_at) ?? str(row.timestamp) ?? str(row.at),
    event: str(row.event) ?? str(row.event_type),
    modelId: str(row.model_id) ?? str(row.champion_id),
    actor: str(row.actor) ?? str(row.performed_by),
    reason: str(row.reason) ?? str(row.detail),
    raw: row,
  };
}

/** Normalize a command/error envelope into an ok/verdict pair. */
export function commandVerdict(res: unknown): { ok: boolean; message: string } {
  // read the store lazily at call time so the verdict renders in the ACTIVE
  // language (no module-load side effects, no frozen language capture)
  const t = useI18n.getState().t;
  const o = obj(res);
  const err = obj(o.error);
  if (err.code || err.message) {
    return { ok: false, message: str(err.message) ?? t("governance.verdict.backend_error", "Backend error: {code}", { code: str(err.code) ?? "UNKNOWN" }) };
  }
  if (bool(o.available) === false) {
    return { ok: false, message: str(o.reason) ?? t("governance.verdict.unavailable", "Backend subsystem unavailable.") };
  }
  const reason = str(o.reason);
  if (reason && !o.transition && !o.state && !o.registry) {
    return { ok: false, message: reason };
  }
  return { ok: true, message: t("governance.verdict.ok", "Backend accepted the command.") };
}
