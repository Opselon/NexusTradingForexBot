/**
 * features/position-adviser/api.ts — typed transport for the Position Decision
 * Adviser tab (TASK-POSA-001).
 *
 * Lane rule (same as model-studio): position-adviser routes are legacy RAW-JSON
 * (never v1 envelopes), so this feature keeps its OWN typed calls over the
 * STABLE exports of @/api/client (getLegacy/send). Server-side validation
 * failures arrive as HTTP 4xx with a `detail` string; the UI surfaces that
 * verbatim — it never fabricates an error message.
 */

import { ApiError, getLegacy, send } from "@/api/client";
import type {
  ActivationChecksResponse,
  AdviserActivateRequest,
  AdviserAdvisoryDto,
  AdviserAutoTuneRequest,
  AdviserAutoTuneResponse,
  AdviserConfigRequest,
  AdviserDatasetsResponse,
  AdviserGenericResponse,
  AdviserLoadRequest,
  AdviserModelsResponse,
  AdviserStatusResponse,
  AdviserTrainRequest,
  AdviserTrainResponse,
  AdvisoriesResponse,
} from "./model";

const BASE = "/api/position-adviser";

/**
 * Safe-envelope guard (perf-wave logic rule): the transport only rejects
 * NON-2xx answers, so a route answering HTTP 200 with the safe error envelope
 * {error:{code,message,request_id}} would otherwise be read as data — a
 * command would claim "activation set" while the backend actually failed.
 * This converts it into a thrown ApiError so every caller's catch lands in a
 * real error state carrying the backend's request_id. Absent an `error` key,
 * the body passes through untouched (legacy raw-JSON lane).
 */
async function guarded<T>(p: Promise<T>): Promise<T> {
  const body = await p;
  const err = (body as { error?: unknown } | null | undefined)?.error;
  if (err && typeof err === "object") {
    const e = err as { code?: string; message?: string; request_id?: string };
    if (typeof e.code === "string" || typeof e.message === "string") {
      throw new ApiError(
        200,
        typeof e.code === "string" ? e.code : "INTERNAL_ERROR",
        typeof e.message === "string" ? e.message : "position-adviser backend reported an error envelope.",
        typeof e.request_id === "string" ? e.request_id : null,
        false,
      );
    }
  }
  return body;
}

export const positionAdviserApi = {
  // ---- reads ---------------------------------------------------------------
  status: (signal?: AbortSignal): Promise<AdviserStatusResponse> =>
    guarded(getLegacy<AdviserStatusResponse>(`${BASE}/status`, signal)),

  models: (signal?: AbortSignal): Promise<AdviserModelsResponse> =>
    guarded(getLegacy<AdviserModelsResponse>(`${BASE}/models`, signal)),

  /** GET /api/position-adviser/datasets — generated pos_ds_*.parquet files. */
  datasets: (signal?: AbortSignal): Promise<AdviserDatasetsResponse> =>
    guarded(getLegacy<AdviserDatasetsResponse>(`${BASE}/datasets`, signal)),

  advisories: (limit = 50, signal?: AbortSignal): Promise<AdvisoriesResponse> =>
    guarded(getLegacy<AdvisoriesResponse>(`${BASE}/advisories?limit=${limit}`, signal)),

  // ---- training -------------------------------------------------------------
  /** POST /api/position-adviser/train — trains a Layer-2 adviser. */
  train: (req: AdviserTrainRequest): Promise<AdviserTrainResponse> =>
    guarded(send<AdviserTrainResponse>(`${BASE}/train`, req)),

  /**
   * POST /api/position-adviser/auto-tune — sweep hyperparameters and keep the
   * best model by OUT-OF-SAMPLE loss. Losing trial weights are pruned server
   * side; the winner can be auto-loaded.
   */
  autoTune: (req: AdviserAutoTuneRequest): Promise<AdviserAutoTuneResponse> =>
    guarded(send<AdviserAutoTuneResponse>(`${BASE}/auto-tune`, req)),

  // ---- activation ladder -----------------------------------------------------
  /** POST /api/position-adviser/load — load a checkpoint + scaler into memory. */
  load: (req: AdviserLoadRequest): Promise<AdviserGenericResponse> =>
    guarded(send<AdviserGenericResponse>(`${BASE}/load`, req)),

  /** POST /api/position-adviser/unload — unload and disable. */
  unload: (): Promise<AdviserGenericResponse> =>
    guarded(send<AdviserGenericResponse>(`${BASE}/unload`, {})),

  /** POST /api/position-adviser/activate — move along the activation ladder. */
  activate: (req: AdviserActivateRequest): Promise<AdviserGenericResponse> =>
    guarded(send<AdviserGenericResponse>(`${BASE}/activate`, req)),

  /** POST /api/position-adviser/config — adjust bounded runtime config. */
  configure: (req: AdviserConfigRequest): Promise<AdviserGenericResponse> =>
    guarded(send<AdviserGenericResponse>(`${BASE}/config`, req)),

  /**
   * POST /api/position-adviser/checks/run — run the activation prerequisites.
   * These are REAL broker/position probes, not self-assertions; a LIVE
   * activation is refused unless every check passes with evidence.
   */
  runChecks: (): Promise<ActivationChecksResponse> =>
    guarded(send<ActivationChecksResponse>(`${BASE}/checks/run`, {})),
};

export type { AdviserAdvisoryDto };
