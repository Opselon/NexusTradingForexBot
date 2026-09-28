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
import { executeRequest } from "@/core/middleware";
import type {
  ActivationEnvelope,
  AddProviderResponse,
  CompareResponse,
  DeactivateResponse,
  DecisionRecord,
  DecisionsResponse,
  EvaluateResponse,
  ModelInfo,
  ProviderConfigRequest,
  ProviderEntry,
  ProviderListResponse,
  ProviderTestResult,
  RemoveProviderResponse,
  RestartEntry,
  RollbackHint,
  RollbackResponse,
  SwitchRequest,
  SwitchResponse,
  TemplatesResponse,
  TestModelResponse,
} from "./model";

const BASE = "/api/ai-providers";

/** The switch request the wizard sends — `shadow` is part of the contract. */
export interface SwitchRequestWithShadow extends SwitchRequest {
  shadow?: string | null;
}

/** Issue a DELETE through the shared pipeline (the façade's mutation helper is
 *  POST-only; DELETE is part of the middleware contract). The response body is
 *  returned verbatim — a 4xx `detail` surfaces as-is upstream. */
async function deleteRaw<T>(path: string): Promise<T> {
  const ctx = await executeRequest(path, { method: "DELETE" });
  const res = ctx.response;
  if (!res) throw new Error(`[ai-providers] no response captured for ${path}`);
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null; // 204/empty body
  }
  return body as T;
}

/** Normalise a switch response onto the contract's field names. The
 *  orchestrator's own dict spells `config_version`/`activation_time` and
 *  returns a bare `detail` string instead of a structured problem on a hard
 *  4xx; both spellings are accepted so a switch renders either way. */
function normaliseSwitch(res: SwitchResponse): SwitchResponse {
  const raw = res as unknown as Record<string, unknown>;
  const configVersion =
    res.configuration_version ??
    (typeof raw["config_version"] === "number" ? (raw["config_version"] as number) : undefined);
  const activatedAt = res.activated_at ?? (typeof raw["activation_time"] === "string" ? (raw["activation_time"] as string) : "");
  return { ...res, configuration_version: configVersion, activated_at: activatedAt };
}

/** Failed preconditions joined verbatim — the array-of-objects shape the
 *  contract documents, rendered "; "-joined as specified. */
export function failedPreconditions(res: SwitchResponse): string {
  const rows = Array.isArray(res.preconditions) ? res.preconditions.filter((p) => !p.passed) : [];
  if (rows.length > 0) return rows.map((p) => p.detail).join("; ");
  // A hard 4xx from the same route surfaces only a `detail` string.
  const raw = res as unknown as { reason?: string };
  return raw.reason ?? "switch preconditions failed";
}

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

  /** GET /api/ai-providers/templates — the adapters an operator may add. */
  templates: (signal?: AbortSignal): Promise<TemplatesResponse> =>
    getLegacy<TemplatesResponse>(`${BASE}/templates`, signal),

  /** POST /api/ai-providers/providers — register a provider from a template.
   *  The secret travels in the request body to the secure store and is never
   *  echoed back; this module does not retain it anywhere. */
  add: (req: {
    provider_id: string;
    template_id: string;
    provider_name?: string;
    endpoint?: string;
    model?: string;
    api_key?: string;
    enabled?: boolean;
  }): Promise<AddProviderResponse> => send<AddProviderResponse>(`${BASE}/providers`, req),

  /** DELETE /api/ai-providers/providers/{id} — custom providers only. Uses the
   *  transport's method override since the shared façade's mutation helper is
   *  POST-only; the DELETE method is part of the middleware's contract. */
  remove: (providerId: string): Promise<RemoveProviderResponse> =>
    deleteRaw<RemoveProviderResponse>(`${BASE}/providers/${encodeURIComponent(providerId)}`),

  /** GET /api/ai-providers/providers/{id}/models — model listing (Section 21). */
  models: (providerId: string, signal?: AbortSignal): Promise<ModelInfo[]> =>
    getLegacy<ModelInfo[]>(`${BASE}/providers/${encodeURIComponent(providerId)}/models`, signal),

  /** GET /api/ai-providers/providers/{id}/models — the contract's flat string
   *  list. Used by the wizard's DISCOVER MODELS step. */
  modelNames: async (providerId: string, signal?: AbortSignal): Promise<string[]> => {
    const res = await getLegacy<{ status: string; provider_id: string; models: string[] }>(
      `${BASE}/providers/${encodeURIComponent(providerId)}/models`,
      signal,
    );
    return res.models ?? [];
  },

  /** POST /api/ai-providers/providers/{id}/configure — save config (Sections 20, 27). */
  configure: (providerId: string, req: ProviderConfigRequest): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${BASE}/providers/${encodeURIComponent(providerId)}/configure`, req),

  /** POST /api/ai-providers/providers/{id}/test — Test Center (Section 22). */
  test: (providerId: string): Promise<ProviderTestResult> =>
    send<ProviderTestResult>(`${BASE}/providers/${encodeURIComponent(providerId)}/test`, {}),

  /** POST /api/ai-providers/providers/{id}/test-model — a NON-MUTATING probe of
   *  one model: the persisted default model is unchanged by this call. */
  testModel: (providerId: string, model: string, simulated = true): Promise<TestModelResponse> =>
    send<TestModelResponse>(`${BASE}/providers/${encodeURIComponent(providerId)}/test-model`, {
      model,
      simulated,
    }),

  // ---- activation + switching (Sections 14, 26, 53) -------------------------
  /** GET /api/ai-providers/activation — the currently ACTIVE selection. */
  activation: (signal?: AbortSignal): Promise<ActivationEnvelope> =>
    getLegacy<ActivationEnvelope>(`${BASE}/activation`, signal),

  /** POST /api/ai-providers/switch — the explicit switch flow with
   *  preconditions. The response is normalised onto the contract's field names
   *  (see normaliseSwitch) so both spellings render. */
  switch: async (req: SwitchRequestWithShadow): Promise<SwitchResponse> =>
    normaliseSwitch(await send<SwitchResponse>(`${BASE}/switch`, req)),

  /** POST /api/ai-providers/deactivate — route back to the internal model and
   *  stand the current external provider down. */
  deactivate: (): Promise<DeactivateResponse> => send<DeactivateResponse>(`${BASE}/deactivate`, {}),

  /** POST /api/ai-providers/rollback — restore the previous activation. */
  rollback: (): Promise<RollbackResponse> => send<RollbackResponse>(`${BASE}/rollback`, {}),

  // ---- decisions (Sections 22, 23, 24, 41) ----------------------------------
  /** POST /api/ai-providers/decision/evaluate — evaluate a snapshot. */
  evaluate: (req: Record<string, unknown>): Promise<EvaluateResponse> =>
    send<EvaluateResponse>(`${BASE}/decision/evaluate`, req),

  /** POST /api/ai-providers/decision/compare — side-by-side comparison. */
  compare: (req: Record<string, unknown>): Promise<CompareResponse> =>
    send<CompareResponse>(`${BASE}/decision/compare`, req),

  /** GET /api/ai-providers/decisions — recent decisions (Section 23). The
   * durable route returns an envelope with a ``source`` field (Section 63);
   * callers that only need rows read ``.decisions``. */
  decisions: (limit = 25, signal?: AbortSignal): Promise<DecisionsResponse> =>
    getLegacy<DecisionsResponse>(`${BASE}/decisions?limit=${limit}`, signal),

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

  /** POST /api/ai-providers/import — restore an exported config blob. */
  importConfig: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${BASE}/import`, payload),

  /** POST /api/ai-providers/reset — return the ecosystem to factory defaults. */
  reset: (): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${BASE}/reset`, {}),

  /** POST /api/ai-providers/providers/{id}/enable — enable a provider. */
  enable: (providerId: string): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${BASE}/providers/${encodeURIComponent(providerId)}/enable`, {}),

  /** POST /api/ai-providers/providers/{id}/disable — disable a provider. */
  disable: (providerId: string): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${BASE}/providers/${encodeURIComponent(providerId)}/disable`, {}),
};

/** The UI's rollback hint: an external provider must be active for a previous
 *  activation to exist. A 412 from POST /rollback remains the authority and is
 *  surfaced verbatim when it happens. */
export function rollbackHint(activeProvider: string | null | undefined): RollbackHint {
  return activeProvider
    ? { available: true, reason: null }
    : { available: false, reason: "no active provider to roll back from" };
}
