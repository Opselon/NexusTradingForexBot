/**
 * features/model-studio/api.ts — typed transport surface for the Neural Model
 * Studio tab (dataset ingestion → feature inspection → training → Layer-2
 * position-manager dataset generation).
 *
 * Lane rule: model-studio routes are legacy RAW-JSON (never v1 envelopes), so
 * this feature keeps its OWN typed calls over the STABLE exports of
 * @/api/client (getLegacy/send/raw). Server-side validation failures come back
 * as HTTP 4xx with a `detail` string; the UI surfaces that verbatim.
 */

import { getLegacy, send } from "@/api/client";
import type {
  ActiveModelResponse,
  BenchmarkResponse,
  DatasetDownloadRequest,
  DatasetDownloadResponse,
  Fetch70dResponse,
  FineTuneModelRequest,
  FineTuneModelResponse,
  HotLoadRequest,
  HotLoadResponse,
  InspectFeaturesRequest,
  InspectFeaturesResponse,
  InspectScalerResponse,
  ModelsListResponse,
  ModelStudioDatasetsDto,
  ModelStudioOverviewDto,
  ModelStudioTrainProgress,
  ModelStudioTrainRequest,
  ModelStudioTrainResponse,
  PositionDatasetRequest,
  PositionDatasetResponse,
  PredictResponse,
  StressTestResponse,
  VerifyModelRequest,
  VerifyModelResponse,
} from "./model";

const BASE = "/api/model-studio";

export const modelStudioApi = {
  // ---- reads ---------------------------------------------------------------
  overview: (signal?: AbortSignal): Promise<ModelStudioOverviewDto> =>
    getLegacy<ModelStudioOverviewDto>(`${BASE}/overview`, signal),

  datasets: (signal?: AbortSignal): Promise<ModelStudioDatasetsDto> =>
    getLegacy<ModelStudioDatasetsDto>(`${BASE}/datasets`, signal),

  fetch70d: (signal?: AbortSignal): Promise<Fetch70dResponse> =>
    getLegacy<Fetch70dResponse>(`${BASE}/fetch-70d`, signal),

  trainProgress: (signal?: AbortSignal): Promise<{ status: string; progress: ModelStudioTrainProgress }> =>
    getLegacy<{ status: string; progress: ModelStudioTrainProgress }>(`${BASE}/train/progress`, signal),

  // ---- dataset ingestion (API-first) --------------------------------------
  /** POST /api/model-studio/datasets/download — MT5 / synthetic / CSV ingest. */
  downloadDataset: (req: DatasetDownloadRequest): Promise<DatasetDownloadResponse> =>
    send<DatasetDownloadResponse>(`${BASE}/datasets/download`, req),

  /** POST /api/model-studio/datasets/inspect-features — 50D/70D stats + z-score readiness. */
  inspectFeatures: (req: InspectFeaturesRequest): Promise<InspectFeaturesResponse> =>
    send<InspectFeaturesResponse>(`${BASE}/datasets/inspect-features`, req),

  // ---- training ------------------------------------------------------------
  /** POST /api/model-studio/train — real PyTorch training loop; returns final state. */
  train: (req: ModelStudioTrainRequest): Promise<ModelStudioTrainResponse> =>
    send<ModelStudioTrainResponse>(`${BASE}/train`, req),

  // ---- Layer-2 position management dataset ---------------------------------
  /** POST /api/model-studio/position-dataset/generate — simulation + math labels. */
  generatePositionDataset: (req: PositionDatasetRequest): Promise<PositionDatasetResponse> =>
    send<PositionDatasetResponse>(`${BASE}/position-dataset/generate`, req),

  // ---- inference / robustness ---------------------------------------------
  predict: (body: {
    dimension: number;
    use_live_features?: boolean;
    fetch_live_70d?: boolean;
    perturbation_sigma?: number;
    simulate_policy_threshold?: number;
    inspect_layers?: boolean;
    compute_saliency?: boolean;
  }): Promise<PredictResponse> => send<PredictResponse>(`${BASE}/predict`, body),

  stressTest: (dimension: number): Promise<StressTestResponse> =>
    send<StressTestResponse>(`${BASE}/stress-test`, { dimension }),

  benchmark: (dimension: number, iterations = 100): Promise<BenchmarkResponse> =>
    send<BenchmarkResponse>(`${BASE}/benchmark`, { dimension, iterations }),

  // ---- AI Hub / Model Registry & Hot-Loader ---------------------------------
  /** GET /api/model-studio/models — list all registered checkpoints in SQLite catalog. */
  listModels: (signal?: AbortSignal): Promise<ModelsListResponse> =>
    getLegacy<ModelsListResponse>(`${BASE}/models`, signal),

  /** POST /api/model-studio/models/hot-load — hot-load checkpoint & scaler into live memory. */
  hotLoad: (req: HotLoadRequest): Promise<HotLoadResponse> =>
    send<HotLoadResponse>(`${BASE}/models/hot-load`, req),

  /** GET /api/model-studio/models/active — get active runtime champion model status. */
  activeModel: (signal?: AbortSignal): Promise<ActiveModelResponse> =>
    getLegacy<ActiveModelResponse>(`${BASE}/models/active`, signal),

  /** POST /api/model-studio/models/rollback — atomically roll back to previous champion. */
  rollback: (): Promise<HotLoadResponse> =>
    send<HotLoadResponse>(`${BASE}/models/rollback`, {}),

  /** POST /api/model-studio/models/verify — pre-load verification battery. */
  verifyModel: (req: VerifyModelRequest): Promise<VerifyModelResponse> =>
    send<VerifyModelResponse>(`${BASE}/models/verify`, req),

  /** GET /api/model-studio/models/{id}/scaler — inspect mean/std vectors of attached scaler. */
  inspectScaler: (modelId: string, signal?: AbortSignal): Promise<InspectScalerResponse> =>
    getLegacy<InspectScalerResponse>(`${BASE}/models/${encodeURIComponent(modelId)}/scaler`, signal),

  /** POST /api/model-studio/models/fine-tune — fine-tune model on dataset with frozen backbone. */
  fineTune: (req: FineTuneModelRequest): Promise<FineTuneModelResponse> =>
    send<FineTuneModelResponse>(`${BASE}/models/fine-tune`, req),
};
