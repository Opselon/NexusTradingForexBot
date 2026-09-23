/**
 * features/provisioning/api.ts — typed transport for the First-Run Model
 * Preparation console.
 *
 * Lane rule (same as model-studio / position-adviser): provisioning routes are
 * legacy RAW-JSON (never v1 envelopes), so this feature keeps its OWN typed
 * calls over the STABLE exports of @/api/client (getLegacy/send). Typed
 * failures arrive as {success:false, code, message?, remedy?} — the UI
 * surfaces the code and remedy verbatim and never invents a message.
 */

import { getLegacy, send } from "@/api/client";
import type {
  DatasetListResponse,
  InstallRequest,
  InstallResponse,
  OfficialRequest,
  OfficialResponse,
  ProvisioningEnvironmentResponse,
  ProvisioningStatusResponse,
  TrainBackend,
  TrainCancelResponse,
  TrainProgressResponse,
  TrainSource,
  TrainStartRequest,
  TrainStartResponse,
} from "./model";

const BASE = "/api/provisioning";

/**
 * Typed failure shape. The legacy lane returns business failures IN BAND
 * (HTTP 200, {success:false, code, message?, remedy?}) and transport failures
 * out of band (HTTP 4xx/5xx with a `detail` string); the transport resolves
 * both to the raw body, so callers render one normalized error shape.
 */
export interface ProvisioningError {
  success: false;
  code?: string;
  message?: string;
  detail?: string;
  remedy?: string | null;
  step?: string;
}

export const provisioningApi = {
  // ---- reads ---------------------------------------------------------------
  /** GET /api/provisioning/status — model slot, recommended action, roots. */
  status: (signal?: AbortSignal): Promise<ProvisioningStatusResponse> =>
    getLegacy<ProvisioningStatusResponse>(`${BASE}/status`, signal),

  /**
   * GET /api/provisioning/environment?backend= — DISCOVERY ONLY (BUG-301):
   * resolves the training stack as a checklist. Never installs anything.
   */
  environment: (
    backend: TrainBackend = "auto",
    signal?: AbortSignal,
  ): Promise<ProvisioningEnvironmentResponse> =>
    getLegacy<ProvisioningEnvironmentResponse>(`${BASE}/environment?backend=${backend}`, signal),

  /**
   * GET /api/provisioning/datasets — dataset files under the allowed import
   * roots (browser aid for the train form). Additive endpoint: a server that
   * has not restarted yet answers 404 → callers keep the manual path input
   * and show an honest "endpoint pending" empty state, never a fabricated list.
   */
  datasets: (signal?: AbortSignal): Promise<DatasetListResponse> =>
    getLegacy<DatasetListResponse>(`${BASE}/datasets`, signal),

  // ---- actions --------------------------------------------------------------
  /** POST /api/provisioning/environment/install — explicit opt-in stack install. */
  install: (req: InstallRequest): Promise<InstallResponse> =>
    send<InstallResponse>(`${BASE}/environment/install`, req),

  /** POST /api/provisioning/official — download + verify the published model. */
  official: (req: OfficialRequest): Promise<OfficialResponse> =>
    send<OfficialResponse>(`${BASE}/official`, req),

  /**
   * POST /api/provisioning/train/start — start one local training run. Folds
   * and epochs are validated server side (1..1000); broker source requires an
   * explicit candle count.
   */
  trainStart: (req: TrainStartRequest): Promise<TrainStartResponse> =>
    send<TrainStartResponse>(`${BASE}/train/start`, req),

  /** GET /api/provisioning/train/progress?after= — incremental event tail. */
  trainProgress: (after = 0, signal?: AbortSignal): Promise<TrainProgressResponse> =>
    getLegacy<TrainProgressResponse>(`${BASE}/train/progress?after=${after}`, signal),

  /** POST /api/provisioning/train/cancel — observed at the next epoch boundary. */
  trainCancel: (): Promise<TrainCancelResponse> =>
    send<TrainCancelResponse>(`${BASE}/train/cancel`, {}),
};

export type { TrainBackend, TrainSource };
