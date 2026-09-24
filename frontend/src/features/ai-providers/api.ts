/**
 * features/ai-providers/api.ts — typed transport for the AI Provider ecosystem
 * (ECOSYSTEM-001).
 *
 * Lane rule (same as position-adviser): these routes are legacy RAW-JSON, so
 * this feature keeps its OWN typed calls over the STABLE exports of
 * @/api/client (getLegacy/send). Server-side validation failures arrive as
 * HTTP 4xx with a `detail` string; the UI surfaces that verbatim — it never
 * fabricates an error message (Section 57: honest UI copy).
 *
 * SECRETS: no key value is ever sent or received by this module. The backend
 * stores a reference and the UI only ever sees `has_secret` (Section 37).
 */

import { getLegacy, send } from "@/api/client";
import type {
  ActivationEnvelope,
  CompareResponse,
  DecisionRecord,
  EvaluateResponse,
  ModelInfo,
  ProviderConfigRequest,
  ProviderEntry,
  ProviderListResponse,
  ProviderTestResult,
  RestartEntry,
  SwitchRequest,
  SwitchResponse,
} from "./model";

const BASE = "/api/ai-providers";

export const aiProvidersApi = {
  // ---- provider management (Sections 19, 20, 31) ---------------------------
  /** GET /api/ai-providers — providers + activation + version identifiers. */
  list: (signal?: AbortSignal): Promise<ProviderListResponse> =>
    getLegacy<ProviderListResponse>(`${BASE}`, signal),

  /** GET /api/ai-providers/providers — provider list with live health. */
  providers: (signal?: AbortSignal): Promise<ProviderEntry[]> =>
    getLegacy<ProviderEntry[]>(`${BASE}/providers`, signal),

  /** GET /api/ai-providers/providers/{id}/status — one provider's detail. */
  status: (providerId: string, signal?: AbortSignal): Promise<ProviderEntry> =>
    getLegacy<ProviderEntry>(`${BASE}/providers/${encodeURIComponent(providerId)}/status`, signal),

  /** GET /api/ai-providers/providers/{id}/models — model listing (Section 21). */
  models: (providerId: string, signal?: AbortSignal): Promise<ModelInfo[]> =>
    getLegacy<ModelInfo[]>(`${BASE}/providers/${encodeURIComponent(providerId)}/models`, signal),

  /** POST /api/ai-providers/providers/{id}/configure — save config (Sections 20, 27). */
  configure: (providerId: string, req: ProviderConfigRequest): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${BASE}/providers/${encodeURIComponent(providerId)}/configure`, req),

  /** POST /api/ai-providers/providers/{id}/test — Test Center (Section 22). */
  test: (providerId: string): Promise<ProviderTestResult> =>
    send<ProviderTestResult>(`${BASE}/providers/${encodeURIComponent(providerId)}/test`, {}),

  // ---- activation + switching (Sections 14, 26, 53) -------------------------
  /** GET /api/ai-providers/activation — the currently ACTIVE selection. */
  activation: (signal?: AbortSignal): Promise<ActivationEnvelope> =>
    getLegacy<ActivationEnvelope>(`${BASE}/activation`, signal),

  /** POST /api/ai-providers/switch — the explicit switch flow with preconditions. */
  switch: (req: SwitchRequest): Promise<SwitchResponse> =>
    send<SwitchResponse>(`${BASE}/switch`, req),

  // ---- decisions (Sections 22, 23, 24, 41) ----------------------------------
  /** POST /api/ai-providers/decision/evaluate — evaluate a snapshot. */
  evaluate: (req: Record<string, unknown>): Promise<EvaluateResponse> =>
    send<EvaluateResponse>(`${BASE}/decision/evaluate`, req),

  /** POST /api/ai-providers/decision/compare — side-by-side comparison. */
  compare: (req: Record<string, unknown>): Promise<CompareResponse> =>
    send<CompareResponse>(`${BASE}/decision/compare`, req),

  /** GET /api/ai-providers/decisions — recent decisions (Section 23). */
  decisions: (limit = 25, signal?: AbortSignal): Promise<DecisionRecord[]> =>
    getLegacy<DecisionRecord[]>(`${BASE}/decisions?limit=${limit}`, signal),

  /** GET /api/ai-providers/decision/{id} — full decision trace (Section 41). */
  trace: (decisionId: string, signal?: AbortSignal): Promise<DecisionRecord> =>
    getLegacy<DecisionRecord>(`${BASE}/decision/${encodeURIComponent(decisionId)}`, signal),

  // ---- import / export / reset (Sections 48, 54) ----------------------------
  /** POST /api/ai-providers/export — config export; real keys are NEVER included. */
  exportConfig: (): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${BASE}/export`, {}),

  /** GET /api/ai-providers/restart-matrix — hot-reload vs restart (Sections 28, 66). */
  restartMatrix: (signal?: AbortSignal): Promise<RestartEntry[]> =>
    getLegacy<RestartEntry[]>(`${BASE}/restart-matrix`, signal),
};
