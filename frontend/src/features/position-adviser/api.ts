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

import { getLegacy, send } from "@/api/client";
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

export const positionAdviserApi = {
  // ---- reads ---------------------------------------------------------------
  status: (signal?: AbortSignal): Promise<AdviserStatusResponse> =>
    getLegacy<AdviserStatusResponse>(`${BASE}/status`, signal),

  models: (signal?: AbortSignal): Promise<AdviserModelsResponse> =>
    getLegacy<AdviserModelsResponse>(`${BASE}/models`, signal),

  /** GET /api/position-adviser/datasets — generated pos_ds_*.parquet files. */
  datasets: (signal?: AbortSignal): Promise<AdviserDatasetsResponse> =>
    getLegacy<AdviserDatasetsResponse>(`${BASE}/datasets`, signal),

  advisories: (limit = 50, signal?: AbortSignal): Promise<AdvisoriesResponse> =>
    getLegacy<AdvisoriesResponse>(`${BASE}/advisories?limit=${limit}`, signal),

  // ---- training -------------------------------------------------------------
  /** POST /api/position-adviser/train — trains a Layer-2 adviser. */
  train: (req: AdviserTrainRequest): Promise<AdviserTrainResponse> =>
    send<AdviserTrainResponse>(`${BASE}/train`, req),

  /**
   * POST /api/position-adviser/auto-tune — sweep hyperparameters and keep the
   * best model by OUT-OF-SAMPLE loss. Losing trial weights are pruned server
   * side; the winner can be auto-loaded.
   */
  autoTune: (req: AdviserAutoTuneRequest): Promise<AdviserAutoTuneResponse> =>
    send<AdviserAutoTuneResponse>(`${BASE}/auto-tune`, req),

  // ---- activation ladder -----------------------------------------------------
  /** POST /api/position-adviser/load — load a checkpoint + scaler into memory. */
  load: (req: AdviserLoadRequest): Promise<AdviserGenericResponse> =>
    send<AdviserGenericResponse>(`${BASE}/load`, req),

  /** POST /api/position-adviser/unload — unload and disable. */
  unload: (): Promise<AdviserGenericResponse> =>
    send<AdviserGenericResponse>(`${BASE}/unload`, {}),

  /** POST /api/position-adviser/activate — move along the activation ladder. */
  activate: (req: AdviserActivateRequest): Promise<AdviserGenericResponse> =>
    send<AdviserGenericResponse>(`${BASE}/activate`, req),

  /** POST /api/position-adviser/config — adjust bounded runtime config. */
  configure: (req: AdviserConfigRequest): Promise<AdviserGenericResponse> =>
    send<AdviserGenericResponse>(`${BASE}/config`, req),

  /**
   * POST /api/position-adviser/checks/run — run the activation prerequisites.
   * These are REAL broker/position probes, not self-assertions; a LIVE
   * activation is refused unless every check passes with evidence.
   */
  runChecks: (): Promise<ActivationChecksResponse> =>
    send<ActivationChecksResponse>(`${BASE}/checks/run`, {}),
};

export type { AdviserAdvisoryDto };
