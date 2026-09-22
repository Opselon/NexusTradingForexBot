/**
 * features/provisioning/model.ts — DTO contracts for the First-Run Model
 * Preparation console, replacing Web/first_setup.html.
 *
 * Shapes mirror src/nexus_scalp/web/provisioning_routes.py:
 *   GET  /api/provisioning/status
 *   GET  /api/provisioning/environment?backend=auto|cpu|cuda
 *   POST /api/provisioning/environment/install
 *   POST /api/provisioning/official
 *   POST /api/provisioning/train/start
 *   GET  /api/provisioning/train/progress?after=<n>
 *   POST /api/provisioning/train/cancel
 *
 * Lane convention (same as model-studio / position-adviser): raw JSON, never
 * v1 envelopes. Server responses use {success:false, code, message?, remedy?}
 * for typed failures — the UI renders the code + remedy verbatim and never
 * fabricates an error message or a stack trace.
 */

/** Backend selector: `auto` resolves to the best available device. */
export type TrainBackend = "auto" | "cpu" | "cuda";

export const TRAIN_BACKENDS: readonly TrainBackend[] = ["auto", "cpu", "cuda"];

/** Dataset origin: an imported file, or history borrowed from the live broker. */
export type TrainSource = "file" | "broker";

export const TRAIN_SOURCES: readonly TrainSource[] = ["file", "broker"];

/**
 * One typed check in an environment report. Mirrors
 * `EnvCheck.as_dict()` in training_env.py: the exposed fields are the stable
 * stage + code + detail + remedy — never wrapped OS text or a stack trace.
 */
export interface EnvCheck {
  stage: string;
  ok: boolean;
  code: string;
  detail: string;
  remedy: string | null;
}

/**
 * Mirrors `EnvironmentReport.as_dict()` in training_env.py. `checks` is the
 * authoritative checklist; `failing()` on the server is its ok===false subset.
 */
export interface EnvironmentReport {
  backend?: string;
  python?: Record<string, unknown> | null;
  environment?: Record<string, unknown> | null;
  pytorch?: Record<string, unknown> | null;
  gpu?: Record<string, unknown> | null;
  contract?: Record<string, unknown> | null;
  checks?: EnvCheck[];
  training_ready?: boolean;
  in_process_ready?: boolean;
  training_command?: string;
  install_attempted?: boolean;
  install_result?: string;
  [key: string]: unknown;
}

export interface ProvisioningEnvironmentResponse {
  success: boolean;
  report?: EnvironmentReport;
  environment?: {
    python?: string | null;
    torch?: string | null;
    cuda?: boolean | null;
    gpu_name?: string | null;
  };
  /** Present only when success is false. */
  code?: string;
  step?: string;
  message?: string;
  remedy?: string | null;
}

export interface ProvisioningStatusResponse {
  success: boolean;
  slot?: Record<string, unknown>;
  recommended?: string;
  provisioner?: Record<string, unknown>;
  allowed_import_roots?: string[];
  code?: string;
}

/** POST /api/provisioning/environment/install — runs the install ladder. */
export interface InstallRequest {
  backend?: TrainBackend;
}

export interface InstallResponse {
  success: boolean;
  report?: EnvironmentReport;
  code?: string;
  step?: string;
  message?: string;
  remedy?: string | null;
}

/** POST /api/provisioning/official — download + verify the published model. */
export interface OfficialRequest {
  base_url?: string;
}

export interface OfficialResponse {
  success: boolean;
  ok?: boolean;
  [key: string]: unknown;
}

/**
 * POST /api/provisioning/train/start — one local training run at a time.
 * The server validates folds/epochs (1..1000) and the dataset request.
 */
export interface TrainStartRequest {
  source: TrainSource;
  file?: string | null;
  candles?: number | null;
  folds?: number;
  epochs?: number;
  install?: boolean;
  backend?: TrainBackend;
  /** Explicit consent to install pinned deps when the environment is not ready. */
  prepare_environment?: boolean;
}

export interface TrainStartResponse {
  success: boolean;
  started?: boolean;
  code?: string;
  detail?: string;
  message?: string;
}

export type ProgressStatus = "active" | "done" | "failed" | "cancelled";

/** One progress line of a running preparation/train. */
export interface ProgressEvent {
  stage: string;
  status: ProgressStatus | string;
  message: string;
  seq?: number;
  ts?: string;
  [key: string]: unknown;
}

export interface TrainProgressResponse {
  success: boolean;
  active: boolean;
  cancelled?: boolean;
  events: ProgressEvent[];
  count?: number;
  /** Terminal outcome of the run (TRAINING_OK | CANCELLED | *_BLOCKED | ...). */
  result?: Record<string, unknown> | null;
  code?: string;
}

export interface TrainCancelResponse {
  success: boolean;
  cancel: "REQUESTED" | "NO_ACTIVE_RUN";
  note?: string;
}
