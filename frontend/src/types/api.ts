/**
 * Shared API transport types for the NSE Alternative UI.
 *
 * Two backend envelope families exist (verified against src/nexus_scalp/web/):
 *  - API Platform v1 (`/api/v1/*`): `{data, meta}` on success, `{error:{...}}`
 *    on failure (web/api_v1/common.py `ok()` / `fail()`).
 *  - Legacy dashboard routes: raw payloads or `{available: ..}` / `{success: ..}`
 *    objects; failures use the safe envelope `{error:{code,message,request_id}}`
 *    (web/errors.py `safe_error_payload`).
 * Both families are normalized here — pages never parse envelopes themselves.
 */
import { localErrorMessage } from "@/lib/errorMessages";

export interface V1Meta {
  request_id: string;
  generated_at: string;
  api_version?: string;
  idempotency_key?: string;
}

export interface V1Envelope<T> {
  data: T;
  meta: V1Meta;
}

export interface V1ErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id: string;
  retryable?: boolean;
}

export interface V1ErrorEnvelope {
  error: V1ErrorBody;
}

/** Normalized error every caller can rely on. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryable: boolean;

  constructor(status: number, code: string, message: string, requestId: string | null, retryable: boolean) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryable = retryable;
  }

  get isAuthError(): boolean {
    return this.status === 401 || this.status === 403 || this.code === "AUTH_CONFIG_ERROR";
  }

  /**
   * User-facing text for this error (i18n rules §31/§70): resolves the
   * backend CODE through the frontend error-code map and never surfaces the
   * raw backend `message` (route-local codes can carry exception prose).
   * Unknown codes render a safe generic localized message plus the code and
   * request id as a reference.
   */
  localized(
    t: (key: string, fallback: string, vars?: Record<string, string | number>) => string,
  ): string {
    return localErrorMessage(this.code, this.status, this.requestId, t);
  }
}

/** Result of a legacy-shape mutation (engine toggle, mode, positions). */
export interface LegacyMutationResult {
  ok: boolean;
  success?: boolean;
  message?: string;
  detail?: Record<string, unknown>;
  status: number;
}
