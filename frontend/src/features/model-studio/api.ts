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
  ArtifactLocationsResponse,
  BenchmarkResponse,
  BuilderConfigRequest,
  BuilderOptionsResponse,
  BuilderPreflightResponse,
  BuilderSaveResponse,
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
  ModelDetailResponse,
  ModelsListResponse,
  ModelStudioDatasetsDto,
  ModelStudioOverviewDto,
  ModelStudioTrainProgress,
  ModelStudioTrainRequest,
  ModelStudioTrainResponse,
  PositionDatasetRequest,
  PositionDatasetResponse,
  PredictResponse,
  RuntimeStateResponse,
  StressTestResponse,
  SwitchModelRequest,
  SwitchModelResponse,
  SwitchPreviewResponse,
  TensorInspectResponse,
  VerifyModelRequest,
  VerifyModelResponse,
} from "./model";
import { compareDatasetsByGranularity } from "./model";

const BASE = "/api/model-studio";

export const modelStudioApi = {
  // ---- reads ---------------------------------------------------------------
  overview: (signal?: AbortSignal): Promise<ModelStudioOverviewDto> =>
    getLegacy<ModelStudioOverviewDto>(`${BASE}/overview`, signal),

  datasets: (signal?: AbortSignal): Promise<ModelStudioDatasetsDto> =>
    getLegacy<ModelStudioDatasetsDto>(`${BASE}/datasets`, signal).then((dto) => {
      // TASK-POSA-002: the backend inventory is a lexicographic sort, so
      // XAUUSD_D1 sorts above XAUUSD_M1 and the selector's "first entry"
      // fallback silently binds the DAILY file ("select M1, jumps back to D1").
      // Re-rank client-side: finest granularity first. Server payload is the
      // source of truth — this only changes presentation/selection order.
      const datasets = [...(dto.datasets ?? [])].sort(compareDatasetsByGranularity);
      return { ...dto, datasets };
    }),

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

  /** GET /api/model-studio/artifact-locations — server-derived on-disk roots. */
  artifactLocations: (signal?: AbortSignal): Promise<ArtifactLocationsResponse> =>
    getLegacy<ArtifactLocationsResponse>(`${BASE}/artifact-locations`, signal),

  // ---- Neural Studio model engineering (50D/70D model builder) ----------------
  /**
   * GET /api/model-studio/model-builder/options — the ONLY source of truth for
   * what the builder can actually construct. The UI renders these controls; it
   * never hardcodes a second copy of the 50D/70D contract.
   */
  builderOptions: (signal?: AbortSignal): Promise<BuilderOptionsResponse> =>
    getLegacy<BuilderOptionsResponse>(`${BASE}/model-builder/options`, signal),

  /**
   * POST /api/model-studio/model-builder/preflight — validate a configuration
   * before training. Errors block training; warnings do not (Phase 49).
   */
  builderPreflight: (req: BuilderConfigRequest): Promise<BuilderPreflightResponse> =>
    send<BuilderPreflightResponse>(`${BASE}/model-builder/preflight`, req),

  /** POST /api/model-studio/model-builder/save — persist the exact configuration. */
  builderSave: (req: BuilderConfigRequest): Promise<BuilderSaveResponse> =>
    send<BuilderSaveResponse>(`${BASE}/model-builder/save`, req),

  /**
   * GET /api/model-studio/runtime/state — the three-state truth: engine, model
   * and inference are reported SEPARATELY so LOADED ≠ INFERENCE AVAILABLE ≠
   * ENGINE RUNNING (Phases 3/35).
   */
  runtimeState: (selectedModelId?: string, signal?: AbortSignal): Promise<RuntimeStateResponse> =>
    getLegacy<RuntimeStateResponse>(
      `${BASE}/runtime/state${selectedModelId ? `?selected_model_id=${encodeURIComponent(selectedModelId)}` : ""}`,
      signal,
    ),

  /**
   * GET /api/model-studio/tensor/inspect — the raw / normalized / model-input
   * triple at one width. Refuses with 422 when no measurable scaler is attached
   * rather than inventing a normalized layer.
   */
  inspectTensor: (params: {
    dimension: number;
    use_live?: boolean;
    perturbation_sigma?: number;
  }): Promise<TensorInspectResponse> => {
    const q = new URLSearchParams({ dimension: String(params.dimension) });
    if (params.use_live) q.set("use_live", "true");
    if (params.perturbation_sigma && params.perturbation_sigma > 0)
      q.set("perturbation_sigma", String(params.perturbation_sigma));
    return getLegacy<TensorInspectResponse>(`${BASE}/tensor/inspect?${q.toString()}`);
  },

  /** GET /api/model-studio/models/{id}/detail — registry detail incl. training_status. */
  modelDetail: (modelId: string, signal?: AbortSignal): Promise<ModelDetailResponse> =>
    getLegacy<ModelDetailResponse>(`${BASE}/models/${encodeURIComponent(modelId)}/detail`, signal),

  /**
   * GET /api/model-studio/models/switch/preview — the switch readiness battery
   * (weights load, smoke inference, contract width) before any confirmation.
   */
  switchPreview: (modelId: string, signal?: AbortSignal): Promise<SwitchPreviewResponse> =>
    getLegacy<SwitchPreviewResponse>(
      `${BASE}/models/switch/preview?model_id=${encodeURIComponent(modelId)}`,
      signal,
    ),

  /**
   * POST /api/model-studio/models/switch — switch with EXPLICIT confirmation.
   * After the switch the runtime model id must equal the selected model id.
   */
  switchModel: (req: SwitchModelRequest): Promise<SwitchModelResponse> =>
    send<SwitchModelResponse>(`${BASE}/models/switch`, req),
};
