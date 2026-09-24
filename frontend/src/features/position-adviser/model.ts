/**
 * features/position-adviser/model.ts — DTO contracts for the Position Decision
 * Adviser tab (TASK-POSA-001).
 *
 * Shapes mirror src/nexus_scalp/web/position_adviser_routes.py:
 *   GET  /api/position-adviser/status
 *   GET  /api/position-adviser/models
 *   POST /api/position-adviser/train
 *   POST /api/position-adviser/load
 *   POST /api/position-adviser/unload
 *   POST /api/position-adviser/activate
 *   POST /api/position-adviser/config
 *   GET  /api/position-adviser/advisories
 *   POST /api/position-adviser/checks/run
 *
 * Convention (same lane as model-studio): raw JSON, never v1 envelopes. Status
 * words + error `detail` are server-authoritative; the UI renders them
 * verbatim.
 */

export type AdviserActivation = "DISABLED" | "PAPER" | "LIVE";

export const ACTIVATION_LADDER: readonly AdviserActivation[] = [
  "DISABLED",
  "PAPER",
  "LIVE",
];

/** Human-readable meaning of each rung — shown in the UI next to the switch. */
export const ACTIVATION_HELP: Record<AdviserActivation, string> = {
  DISABLED: "Off by default. The decide system runs exactly as before — zero influence.",
  PAPER: "Advisory only: every verdict is computed and logged, but no hold score changes.",
  LIVE: "Advisory + bounded hold-score penalty. Requires broker + position checks to pass.",
};

export interface AdviserStatusResponse {
  status: string;
  activation: AdviserActivation | string;
  model_id: string;
  weights_path: string;
  scaler_path: string;
  weights_sha256: string;
  feature_dim: number;
  loaded_at: string | null;
  applied_count: number;
  evaluated_count: number;
  refused_count: number;
  last_error: string;
  ready: boolean;
  config: AdviserConfigDto;
  activation_ladder: { current: string; available: string[] };
  actions: string[];
  now?: string;
  /** BUG-314 F6: package-integrity verdict from the immutable sidecar
   *  manifest (OK / unverified / a failure reason). Empty before any load. */
  integrity: string;
  /** Relative path of the .meta.json that pinned this package. */
  manifest_path: string;
  /** Content hash of the dataset this model was trained on. */
  source_dataset_hash: string;
  /** BUG-314 F1: snapshots refused solely for being stale. */
  stale_rejected_count: number;
}

export interface AdviserConfigDto {
  max_hold_score_penalty: number;
  min_confidence_to_apply: number;
  min_action_advantage: number;
  min_eval_interval_sec: number;
  /** BUG-314 F1: staleness gate — a snapshot older than this is refused. */
  max_snapshot_age_sec: number;
  artifact_dir: string;
}

export interface AdviserModelDto {
  model_id: string;
  weights_path: string;
  scaler_path: string;
  manifest_path: string;
  has_scaler: boolean;
  oos_accuracy: number | null;
  oos_loss: number | null;
  best_val_loss: number | null;
  epochs: number | null;
  oos_action_distribution: Record<string, number>;
  train_rows: number | null;
  oos_rows: number | null;
  created_at: string | null;
  /** BUG-314 F8: action classes the training data did not contain. These
   *  receive zero weight in training and the model can never emit them —
   *  shown so the operator never mistakes a silent 0% for "the model
   *  considered it and ruled it out". */
  classes_absent: string[];
}

export interface AdviserModelsResponse {
  status: string;
  count: number;
  active_adviser_id: string;
  models: AdviserModelDto[];
}

export interface AdviserTrainRequest {
  dataset_path: string;
  epochs?: number;
  batch_size?: number;
  learning_rate?: number;
  seed?: number;
  model_id?: string | null;
}

export interface AdviserTrainingResultDto {
  model_id: string;
  weights_path: string;
  scaler_path: string;
  manifest_path: string;
  feature_dim: number;
  epochs_run: number;
  best_val_loss: number;
  oos_loss: number;
  oos_accuracy: number;
  oos_action_distribution: Record<string, number>;
  train_rows: number;
  val_rows: number;
  oos_rows: number;
  sha256: string;
  duration_sec: number;
}

export interface AdviserTrainResponse {
  status: string;
  message: string;
  training: AdviserTrainingResultDto;
}

export interface AdviserLoadRequest {
  weights_path: string;
  scaler_path: string;
  model_id?: string | null;
}

export interface AdviserGenericResponse {
  status: string;
  message?: string;
  [key: string]: unknown;
}

export interface ActivationCheckDto {
  name: string;
  passed: boolean;
  detail: string;
  evidence: Record<string, unknown>;
}

export interface ActivationChecksResponse {
  status: string;
  all_passed: boolean;
  checks: ActivationCheckDto[];
  executed_at: string;
}

export interface AdviserAdvisoryDto {
  ticket: number;
  action: string;
  confidence: number;
  probabilities: Record<string, number>;
  hold_score_adjustment: number;
  activation: string;
  model_id: string;
  model_dimension: number;
  evaluated_at: string;
  latency_ms: number;
  advisory_id: string;
  applied: boolean;
  not_applied_reason: string;
  diagnostics: Record<string, unknown>;
}

export interface AdvisoriesResponse {
  status: string;
  count: number;
  advisories: AdviserAdvisoryDto[];
}

/** POST /api/position-adviser/activate — mirrors the Pydantic model in
 * position_adviser_routes.py (activation rung + optional operator checks). */
export interface AdviserActivateRequest {
  activation: string;
  checks?: ActivationCheckDto[] | null;
}

export interface AdviserConfigRequest {
  max_hold_score_penalty?: number;
  min_confidence_to_apply?: number;
  min_action_advantage?: number;
  min_eval_interval_sec?: number;
  /** BUG-314 F1: set to 0 to accept snapshots of any age. */
  max_snapshot_age_sec?: number;
}

/* --------------------- auto-tune (auto mode) ----------------------------- */
export interface AdviserDatasetDto {
  name: string;
  path: string;
  size_bytes: number;
  modified_at: string;
  timeframe: string;
}

export interface AdviserDatasetsResponse {
  status: string;
  count: number;
  datasets: AdviserDatasetDto[];
}

export interface AdviserTrialDto {
  model_id: string;
  seed: number;
  learning_rate: number;
  batch_size: number;
  failed: boolean;
  error?: string;
  oos_loss?: number;
  oos_accuracy?: number;
  best_val_loss?: number;
  oos_action_distribution?: Record<string, number>;
  weights_path?: string;
  scaler_path?: string;
  manifest_path?: string;
}

export interface AdviserAutoTuneRequest {
  dataset_path: string;
  epochs?: number;
  seeds?: number[];
  learning_rates?: number[];
  batch_sizes?: number[];
  max_trials?: number;
  auto_load?: boolean;
}

/** One trial of an auto-tune sweep. Failed trials stay in the report. */
export interface AdviserTrialDto {
  model_id: string;
  seed: number;
  learning_rate: number;
  batch_size: number;
  failed: boolean;
  error?: string;
  oos_loss?: number;
  oos_accuracy?: number;
  best_val_loss?: number;
  oos_action_distribution?: Record<string, number>;
  weights_path?: string;
  scaler_path?: string;
  manifest_path?: string;
}

/** The winning trial, with the honest OOS metrics the selection was made on. */
export interface AdviserTuneBestDto {
  model_id: string;
  weights_path: string;
  scaler_path: string;
  manifest_path: string;
  oos_loss: number;
  oos_accuracy: number;
  best_val_loss: number;
  oos_action_distribution: Record<string, number>;
  train_rows: number;
  val_rows: number;
  oos_rows: number;
  seed: number;
  learning_rate: number;
  batch_size: number;
  epochs: number;
  load_error?: string;
}

export interface AdviserAutoTuneResponse {
  status: string;
  message: string;
  trials: AdviserTrialDto[];
  best: AdviserTuneBestDto;
  /** Majority-class accuracy — what any constant classifier scores. */
  majority_baseline_accuracy: number | null;
  beats_majority_baseline: boolean;
  loaded: boolean;
  executed_at: string;
}

/**
 * Backend `detail` (or the raw error) as the exact operator-facing message.
 * One definition instead of the seven inline copies in PositionAdviserPage;
 * same expression, same output — only ever evaluated on the error path.
 */
export function errorDetailText(err: unknown): string {
  const detail = (err as { detail?: string })?.detail ?? String(err);
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}
