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
  DecisionTraceResponse,
  TensorInspectorResponse,
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

  /**
   * POST /api/position-adviser/rollback — restore the model replaced by the
   * most recent load. 409 when nothing is retained.
   */
  rollback: (): Promise<AdviserGenericResponse> =>
    send<AdviserGenericResponse>(`${BASE}/rollback`, {}),

  /**
   * GET /api/position-adviser/tensor/current-input — the actual last input.
   *
   * The route answers `{"status": "OK", "tensor": {...}}` (legacy raw JSON, not
   * a v1 envelope, so getLegacy cannot unwrap it). Unwrap here: every consumer
   * (TensorInspectorPanel + the page state) is typed to the inner payload, and
   * the envelope stored as the payload made `tensor_shape` undefined, crashing
   * the whole page on `.join()` (BUG-544).
   */
  tensor: async (signal?: AbortSignal): Promise<TensorInspectorResponse> => {
    const env = await getLegacy<{ status: string; tensor: TensorInspectorResponse }>(
      `${BASE}/tensor/current-input`,
      signal,
    );
    return env?.tensor ?? null;
  },

  /**
   * GET /api/position-adviser/decision/current — the last advisory + latencies.
   * Same envelope shape (`{"status": "OK", "decision": ...}` is NOT used; this
   * route returns position/model/decision/latency at the top level) — kept
   * explicit for symmetry with `tensor`.
   */
  decision: (signal?: AbortSignal): Promise<DecisionTraceResponse> =>
    getLegacy<DecisionTraceResponse>(`${BASE}/decision/current`, signal),

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
