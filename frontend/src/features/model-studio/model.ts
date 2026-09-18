/**
 * Model Studio: DTO contracts for the dataset → train → position-manager pipeline.
 *
 * Shapes verified against src/nexus_scalp/web/model_studio_routes.py:
 *   GET  /api/model-studio/overview
 *   GET  /api/model-studio/datasets
 *   POST /api/model-studio/datasets/download
 *   POST /api/model-studio/datasets/inspect-features
 *   POST /api/model-studio/train
 *   GET  /api/model-studio/train/progress
 *   POST /api/model-studio/position-dataset/generate
 *   POST /api/model-studio/predict
 *   POST /api/model-studio/stress-test
 *   POST /api/model-studio/benchmark
 *
 * All model-studio routes return RAW JSON (never v1 envelopes), so the UI
 * consumes them through getLegacy/send and renders the backend payloads
 * verbatim — status words and error `detail` are backend-authoritative.
 */

export type FeatureFamily = "BASE" | "NEWS" | "LIQUIDITY";

export type FeatureStatus = "HEALTHY" | "CLAMPED" | "WARNING";

export type OptimalAction = "KEEP" | "CLOSE" | "REDUCE";

/** SETUP / INGESTED values come from the local filesystem scan, not the DB. */
export interface ModelStudioDatasetItem {
  name: string;
  path: string;
  size_bytes: number;
  size_display: string;
  format: string;
  modified_at: string;
}

export interface ModelStudioDatasetsDto {
  status: string;
  count: number;
  datasets: ModelStudioDatasetItem[];
}

export interface ModelStudioOverviewDto {
  status: string;
  architecture: string;
  effective_dimension: number;
  model_source: string;
  parameter_count: number;
  trainable_parameters: number;
  weights_sha256: string;
  device: string;
  scaler_stats: {
    status?: string;
    mean_min?: number;
    mean_max?: number;
    std_min?: number;
    std_max?: number;
    clamped_cols?: number;
  };
}

/** Timeframe options the ingestion adapter accepts (server clamps to M1). */
export const DOWNLOAD_TIMEFRAMES = ["M1", "M3", "M5", "M15"] as const;

export type DownloadTimeframe = (typeof DOWNLOAD_TIMEFRAMES)[number];

export const DOWNLOAD_SOURCES = ["synthetic", "mt5", "csv"] as const;

export type DownloadSource = (typeof DOWNLOAD_SOURCES)[number];

export interface DatasetDownloadRequest {
  symbol: string;
  timeframe: DownloadTimeframe | string;
  bars: number;
  source: DownloadSource | string;
  csv_path?: string | null;
  seed?: number;
}

export interface DatasetDownloadResponse {
  status: string;
  message: string;
  dataset_path: string;
  rows: number;
  symbol: string;
  timeframe: string;
  source: string;
  start_time: string;
  end_time: string;
  bytes_written: number;
  size_display: string;
  elapsed_sec: number;
  throughput_bars_sec: number;
}

export interface InspectFeaturesRequest {
  dataset_path?: string;
  dimension: number;
  max_rows?: number;
}

export interface FeatureInspectionRow {
  index: number;
  name: string;
  family: FeatureFamily;
  raw_min: number;
  raw_max: number;
  raw_mean: number;
  raw_std: number;
  normalized_sample: number;
  zero_variance: boolean;
  status: FeatureStatus;
}

export interface InspectFeaturesResponse {
  status: string;
  dataset_path: string;
  dimension: number;
  rows_processed: number;
  total_features: number;
  healthy_features: number;
  clamped_features: number;
  nan_features: number;
  scaler_ready: boolean;
  features: FeatureInspectionRow[];
  evaluated_at: string;
}

export interface ModelStudioTrainRequest {
  dataset_path?: string;
  dimension: number;
  epochs: number;
  batch_size?: number;
  learning_rate?: number;
  seed?: number;
}

export interface ModelStudioTrainProgress {
  status: string;
  stage?: string;
  epoch: number;
  epochs: number;
  loss: number;
  val_loss: number;
  started_at?: string;
  updated_at?: string;
  run_id: string;
  dimension: number;
  dataset: string;
  message: string;
  checkpoint_path?: string;
}

export interface ModelStudioTrainResponse {
  status: string;
  run_id: string;
  message: string;
  target_dataset: string;
  epochs_completed: number;
  final_loss: number;
  final_val_loss: number;
  checkpoint_path: string;
  state: ModelStudioTrainProgress;
}

export interface PositionDatasetRequest {
  source_dataset_path?: string;
  dimension: number;
  bars_limit?: number;
  max_holding_bars?: number;
  target_atr_multiplier?: number;
  stop_loss_atr_multiplier?: number;
  friction_pips?: number;
}

export interface PositionDatasetResponse {
  status: string;
  dataset_path: string;
  total_samples: number;
  simulated_trades: number;
  actions_distribution: Partial<Record<OptimalAction, number>>;
  mean_continuation_value: number;
  mean_holding_bars: number;
  splits: Record<string, number>;
  sha256: string;
  elapsed_sec: number;
}

// ---- inference / robustness (existing surface) ------------------------------

export interface PredictResponse {
  status: string;
  dimension: number;
  confidence: number;
  confidence_margin: number;
  shannon_entropy_bits: number;
  predicted_label: string;
  probabilities: { no_trade: number; buy: number; sell: number };
  numerical_validation: { valid: boolean; sum: number; all_positive: boolean };
  ood_metrics: { max_z_score: number; is_out_of_distribution: boolean };
  latency_ms: { total_e2e: number };
  layer_inspection?: Array<{
    layer: string;
    type: string;
    shape: number[];
    l2_norm: number;
    mean: number;
    std: number;
    zero_fraction: number;
  }>;
  saliency?: {
    top_positive_drivers?: Array<{ index: number; gradient: number }>;
    top_negative_drivers?: Array<{ index: number; gradient: number }>;
  };
}

export interface StressTestResultRow {
  test: string;
  passed: boolean;
  detail: string;
}

export interface StressTestResponse {
  status: string;
  dimension: number;
  all_passed: boolean;
  tests_run: number;
  results: StressTestResultRow[];
  executed_at: string;
}

export interface BenchmarkResponse {
  status: string;
  dimension: number;
  iterations: number;
  latency_p50_ms: number;
  latency_p90_ms: number;
  latency_p99_ms: number;
  throughput_inferences_per_sec: number;
  sla_passed: boolean;
}

export interface Fetch70dResponse {
  status: string;
  dimension?: number;
  slots?: Array<{ index: number; family: FeatureFamily; name: string; value: number }>;
  contract_valid?: boolean;
  schema_hash?: string;
}

// ---- AI Hub / Model Registry & Hot-Loader DTOs ------------------------------

export interface ModelRecordDto {
  id: string;
  name: string;
  version: string;
  dimension: number;
  architecture: string;
  weights_path: string;
  scaler_path: string;
  manifest_path?: string;
  sha256: string;
  epochs: number;
  final_loss: number;
  final_val_loss: number;
  dataset_path: string;
  is_active: boolean;
  fine_tune_enabled: boolean;
  stage: string;
  created_at: string;
  loaded_at: string | null;
  metrics?: Record<string, unknown>;
}

export interface ModelsListResponse {
  status: string;
  count: number;
  active_champion_id: string | null;
  models: ModelRecordDto[];
}

export interface HotLoadRequest {
  model_id: string;
  fine_tune_enabled?: boolean;
  attach_scaler?: boolean;
  operator?: string;
}

export interface HotLoadResponse {
  status: string;
  message: string;
  model_id: string;
  dimension: number;
  architecture: string;
  weights_sha256: string;
  scaler_attached: boolean;
  scaler_path: string;
  fine_tune_enabled: boolean;
  warmup_latency_us: number;
  stage: string;
  loaded_at: string;
}

export interface ActiveModelResponse {
  status: string;
  active_model: {
    model_id: string;
    dimension: number;
    architecture: string;
    weights_sha256: string;
    weights_path: string;
    scaler_path: string;
    scaler_ready: boolean;
    fine_tune_enabled: boolean;
    stage: string;
    loaded_at: string;
    inference_count: number;
  } | null;
  message?: string;
}

export interface VerifyModelRequest {
  model_id: string;
}

export interface VerifyModelCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface VerifyModelResponse {
  status: string;
  model_id: string;
  dimension: number;
  all_passed: boolean;
  checks: VerifyModelCheck[];
}

export interface ScalerFeatureVector {
  index: number;
  mean: number;
  std: number;
  zero_variance: boolean;
  clamp_min: number;
  clamp_max: number;
}

export interface InspectScalerResponse {
  status: string;
  model_id: string;
  dimension: number;
  features_count: number;
  message?: string;
  features: ScalerFeatureVector[];
}

export interface FineTuneModelRequest {
  base_model_id?: string;
  dataset_path?: string;
  epochs?: number;
  learning_rate?: number;
  freeze_backbone?: boolean;
  seed?: number;
}

export interface FineTuneModelResponse {
  status: string;
  message: string;
  fine_tuned_model_id: string;
  parent_model_id: string;
  epochs: number;
  final_loss: number;
  frozen_parameters: number;
  trainable_parameters: number;
  weights_path: string;
  scaler_path: string;
  sha256: string;
}