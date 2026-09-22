/**
 * Validation engine — the config/rules/debug shared layer (pure, unit-testable).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Every mutating surface in this lane (settings editor, runtime-config apply,
 * rule parameter save, model-test vectors, database console forms) must refuse
 * to send an invalid payload. The backend is still the authority — this layer
 * only prevents obviously broken input from ever reaching the wire, and mirrors
 * backend field errors back onto the same controls.
 *
 * Design contract:
 *  - NO React, NO fetch, NO dates from `new Date()` on the decision path:
 *    every function is deterministic on its inputs (unit-testable in isolation).
 *  - Rules return `string | null` (message | pass). Nothing throws.
 *  - `FieldErrors` is a `key -> messages[]` map: aggregated so a form can show
 *    all problems at once instead of one-at-a-time.
 *  - Backend refusals normalize through `serverErrorsToFieldErrors()` so the
 *    inline errors are indistinguishable in presentation from client ones —
 *    the operator sees "this field is wrong", whichever side caught it.
 */

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

/** How a value must be interpreted before rule checks run. */
export type FieldKind =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "enum"
  | "url"
  | "regex"
  | "cron"
  | "token"
  | "path";

/** Backend mutability classes (mirrors settings/service.py MUTABILITY). */
export const MUTABILITY = {
  HOT: "HOT",
  HOT_RESTRICTED: "HOT_RESTRICTED",
  RESTART_REQUIRED: "RESTART_REQUIRED",
  SECRET: "SECRET",
  READ_ONLY: "READ_ONLY",
} as const;

export type Mutability = (typeof MUTABILITY)[keyof typeof MUTABILITY];

/** One declared form field: the single source of client-side truth. */
export interface FieldSpec {
  /** Dotted backend key (`risk.max_allowed_lots`) or form-local path. */
  key: string;
  label?: string;
  kind: FieldKind;
  required?: boolean;
  /** Inclusive numeric bounds (number/integer kinds). */
  min?: number;
  max?: number;
  /** Allowed values (enum kind). */
  options?: readonly string[];
  /** Pattern source (regex kind) — kept as a string so specs stay serializable. */
  pattern?: string;
  patternMessage?: string;
  /** Path/secret-shaped value: a masked placeholder counts as "unchanged". */
  secret?: boolean;
  mutability?: Mutability;
  hint?: string;
  /**
   * Optional pure cross-field check. `values` is the whole form; returning a
   * message attaches it to `key`.
   */
  cross?: (values: FieldValues) => string | null;
}

export type FieldValue = string | number | boolean | null | undefined;
export type FieldValues = Record<string, FieldValue>;

/** Aggregated per-field messages. Empty array / absent key = valid. */
export type FieldErrors = Record<string, string[]>;

/** Translator contract injected from the render site — the identity
 *  (English) implementation keeps pure/unit-testable callers unchanged. */
export type Translate = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** English identity: used wherever no translator is threaded through. */
export const identityT: Translate = (_key, fallback) => fallback;

/** One message rule: value in, message (or null when it passes) out.
 *  `tr` is the optional translator (see Translate) so rules stay callable
 *  from non-UI code paths. */
export type FieldRule = (value: FieldValue, spec: FieldSpec, tr?: Translate) => string | null;

/* ------------------------------------------------------------------ */
/* Masking helpers (secrets never round-trip through the form)         */
/* ------------------------------------------------------------------ */

const MASK_CHARS = "*";

/** A backend-served placeholder like `********1234` (never a real secret). */
export function isMaskedValue(value: FieldValue): boolean {
  return typeof value === "string" && value.includes(`${MASK_CHARS}${MASK_CHARS}`);
}

/** Mask everything but the last `visible` characters for display. */
export function maskSecret(value: string | null | undefined, visible = 4): string {
  if (!value) return "";
  if (value.length <= visible) return MASK_CHARS.repeat(value.length);
  return MASK_CHARS.repeat(Math.max(4, value.length - visible)) + value.slice(-visible);
}

/* ------------------------------------------------------------------ */
/* Atomic rules                                                        */
/* ------------------------------------------------------------------ */

/** Accept anything stringifiable for a string field; empty counts as missing. */
function asText(value: FieldValue): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return value.trim();
}

/** Strict finite-number coercion (`""` / `"abc"` / `NaN` / `Infinity` -> null). */
export function toFiniteNumber(value: FieldValue): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const t = value.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

export const ruleRequired: FieldRule = (value, spec, tr) => {
  if (!spec.required) return null;
  if (spec.secret && isMaskedValue(value)) return null; // unchanged secret
  if (value === false) return null; // a real boolean choice
  const t = tr ?? identityT;
  return asText(value) === "" ? t("config.validation.required", "{label} is required", { label: spec.label ?? spec.key }) : null;
};

export const ruleType: FieldRule = (value, spec, tr) => {
  if (asText(value) === "" && !spec.required) return null;
  if (spec.secret && isMaskedValue(value)) return null;
  const t = tr ?? identityT;
  const label = spec.label ?? spec.key;
  switch (spec.kind) {
    case "number": {
      const n = toFiniteNumber(value);
      return n === null ? t("config.validation.must_be_number", "{label} must be a number", { label }) : null;
    }
    case "integer": {
      const n = toFiniteNumber(value);
      if (n === null) return t("config.validation.must_be_whole", "{label} must be a whole number", { label });
      return Number.isInteger(n) ? null : t("config.validation.must_be_whole", "{label} must be a whole number", { label });
    }
    case "boolean":
      return typeof value === "boolean" || value === "true" || value === "false"
        ? null
        : t("config.validation.must_be_bool", "{label} must be true or false", { label });
    default:
      return null;
  }
};

export const ruleRange: FieldRule = (value, spec, tr) => {
  if (spec.kind !== "number" && spec.kind !== "integer") return null;
  const n = toFiniteNumber(value);
  if (n === null) return null; // ruleType already reported a non-number
  const t = tr ?? identityT;
  const label = spec.label ?? spec.key;
  if (spec.min !== undefined && n < spec.min) {
    return t("config.validation.must_be_ge", "{label} must be ≥ {min}", { label, min: spec.min })
  }
  if (spec.max !== undefined && n > spec.max) {
    return t("config.validation.must_be_le", "{label} must be ≤ {max}", { label, max: spec.max })
  }
  return null;
};

export const ruleEnum: FieldRule = (value, spec, tr) => {
  if (spec.kind !== "enum" || !spec.options) return null;
  const text = asText(value);
  if (text === "" && !spec.required) return null;
  if (spec.options.includes(text)) return null;
  return (tr ?? identityT)("config.validation.must_be_one_of", "{label} must be one of: {options}", {
    label: spec.label ?? spec.key,
    options: spec.options.join(", "),
  });
};

export const rulePattern: FieldRule = (value, spec, tr) => {
  if (spec.kind !== "regex" || !spec.pattern) return null;
  const text = asText(value);
  if (text === "" && !spec.required) return null;
  const t = tr ?? identityT;
  let re: RegExp;
  try {
    re = new RegExp(spec.pattern);
  } catch {
    return t("config.validation.invalid_pattern", "{label}: invalid pattern configured", { label: spec.label ?? spec.key });
  }
  return re.test(text) ? null : spec.patternMessage ?? t("config.validation.invalid_format", "{label} has an invalid format", { label: spec.label ?? spec.key });
};

export const ruleUrl: FieldRule = (value, spec, tr) => {
  if (spec.kind !== "url") return null;
  const text = asText(value);
  if (text === "" && !spec.required) return null;
  const t = tr ?? identityT;
  const label = spec.label ?? spec.key;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return t("config.validation.must_be_url", "{label} must be an absolute URL (https://…)", { label });
  }
  return url.protocol === "http:" || url.protocol === "https:"
    ? null
    : t("config.validation.must_use_http", "{label} must use http(s)", { label });
};

const CRON_FIELD_RANGES: Array<[number, number]> = [
  [0, 59], // minute
  [0, 23], // hour
  [0, 31], // day of month
  [1, 12], // month
  [0, 7], // day of week (0 and 7 == Sunday)
  [0, 59], // optional leading seconds field is normalised away below
];

/**
 * Cron sanity check for the 5-field (minute..dow) and 6-field (with seconds)
 * forms. Accepts `*`, numbers, ranges (`a-b`), step forms (`n`-over-`*` or
 * `a-b`) and comma lists. Rejects anything else — a malformed schedule must
 * never be applied to a live worker.
 */
export const ruleCron: FieldRule = (value, spec, tr) => {
  if (spec.kind !== "cron") return null;
  const text = asText(value);
  if (text === "" && !spec.required) return null;
  const t = tr ?? identityT;
  const label = spec.label ?? spec.key;
  const fields = text.split(/\s+/);
  if (fields.length !== 5 && fields.length !== 6) {
    return t("config.validation.cron_field_count", "{label} must have 5 (or 6 with seconds) cron fields, got {n}", { label, n: fields.length });
  }
  const shifted = fields.length === 6 ? fields.slice(1) : fields;
  for (let i = 0; i < shifted.length; i += 1) {
    const range = CRON_FIELD_RANGES[i] ?? [0, 59];
    if (!cronFieldValid(shifted[i] ?? "", range[0], range[1])) {
      return t("config.validation.cron_invalid_field", "{label}: invalid cron field \"{field}\" (allowed {min}–{max})", {
        label,
        field: shifted[i] ?? "",
        min: range[0],
        max: range[1],
      });
    }
  }
  return null;
};

function cronFieldValid(field: string, min: number, max: number): boolean {
  if (field === "*" || field === "?") return true;
  for (const part of field.split(",")) {
    if (!cronAtomValid(part, min, max)) return false;
  }
  return true;
}

function cronAtomValid(atom: string, min: number, max: number): boolean {
  const [body, step] = atom.split("/");
  if (step !== undefined && !/^\d+$/.test(step)) return false;
  if (body === "*" || body === undefined || body === "") return step !== undefined ? !!step : true;
  const bounds = body.split("-");
  if (bounds.length === 1) {
    const n = Number(bounds[0]);
    return /^\d+$/.test(bounds[0] ?? "") && n >= min && n <= max;
  }
  if (bounds.length === 2) {
    const lo = Number(bounds[0]);
    const hi = Number(bounds[1]);
    return /^\d+$/.test(bounds[0] ?? "") && /^\d+$/.test(bounds[1] ?? "") && lo >= min && hi <= max && lo <= hi;
  }
  return false;
}

/** Telegram-style bot token `\d{6,}:\w{20,}` and generic admin id shape. */
export const BOT_TOKEN_PATTERN = "^\\d{6,}:[A-Za-z0-9_-]{20,}$";
export const ADMIN_ID_PATTERN = "^-?\\d{4,}$";

export const ruleToken: FieldRule = (value, spec, tr) => {
  if (spec.kind !== "token") return null;
  const text = asText(value);
  if (text === "" && !spec.required) return null;
  if (spec.secret && isMaskedValue(text)) return null; // server-served mask = unchanged
  if (new RegExp(spec.pattern ?? BOT_TOKEN_PATTERN).test(text)) return null;
  return spec.patternMessage ?? (tr ?? identityT)("config.validation.token_shape", "{label} does not match the expected token shape", { label: spec.label ?? spec.key });
};

/** All rules in canonical order; `validateField` runs this chain. */
export const DEFAULT_RULES: FieldRule[] = [
  ruleRequired,
  ruleType,
  ruleEnum,
  ruleUrl,
  ruleCron,
  ruleToken,
  rulePattern,
  ruleRange,
];

/* ------------------------------------------------------------------ */
/* Aggregation                                                         */
/* ------------------------------------------------------------------ */

export function addError(errors: FieldErrors, key: string, message: string): FieldErrors {
  const existing = errors[key];
  if (existing) {
    if (!existing.includes(message)) existing.push(message);
    return errors;
  }
  errors[key] = [message];
  return errors;
}

/** Validate one value against a spec; returns every rule that failed. */
export function validateField(spec: FieldSpec, value: FieldValue, rules: FieldRule[] = DEFAULT_RULES, t: Translate = identityT): string[] {
  const out: string[] = [];
  for (const rule of rules) {
    const msg = rule(value, spec, t);
    if (msg) out.push(msg);
  }
  // Range/enum/type checks are meaningless on a missing optional value.
  if (asText(value) === "" && !spec.required) return out.filter((m) => !m.endsWith("is required"));
  return out;
}

/** Validate a whole form: one aggregated `FieldErrors` map. */
export function validateFields(
  specs: readonly FieldSpec[],
  values: FieldValues,
  rules: FieldRule[] = DEFAULT_RULES,
  t: Translate = identityT,
): FieldErrors {
  const errors: FieldErrors = {};
  for (const spec of specs) {
    const msgs = validateField(spec, values[spec.key], rules, t);
    for (const m of msgs) addError(errors, spec.key, m);
    const crossMsg = spec.cross ? spec.cross(values) : null;
    if (crossMsg) addError(errors, spec.key, crossMsg);
  }
  return errors;
}

export function hasErrors(errors: FieldErrors | null | undefined): boolean {
  if (!errors) return false;
  return Object.values(errors).some((msgs) => (msgs?.length ?? 0) > 0);
}

/** First message for a field (inline display), or null when clean. */
export function firstError(errors: FieldErrors, key: string): string | null {
  const msgs = errors[key];
  return msgs && msgs.length > 0 ? (msgs[0] ?? null) : null;
}

/** Flat "key: message" list — for the banner above a submit button. */
export function flattenErrors(errors: FieldErrors): string[] {
  const out: string[] = [];
  for (const [key, msgs] of Object.entries(errors)) {
    for (const m of msgs ?? []) out.push(`${key}: ${m}`);
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Backend error normalization                                         */
/* ------------------------------------------------------------------ */

/** A field error as any of the two backend envelopes may phrase it. */
export interface ServerFieldError {
  key?: string;
  field?: string;
  name?: string;
  path?: string;
  message?: string;
  detail?: string;
  reason?: string;
  code?: string;
}

function serverErrorKey(e: ServerFieldError): string | null {
  return e.key ?? e.field ?? e.name ?? e.path ?? null;
}

function serverErrorMessage(e: ServerFieldError, t: Translate = identityT): string {
  return e.message ?? e.detail ?? e.reason ?? e.code ?? t("config.validation.rejected", "rejected by backend");
}

/**
 * Fold backend validation output into `FieldErrors`. Accepts the shapes seen
 * on the wire: `{valid:false, errors:[{key,message}]}`, `{errors:{key:[msg]}}`
 * or `{detail:[{loc,msg}]}` (FastAPI). Anything unattributed lands under the
 * `__root` key so it is still shown to the operator.
 */
export function serverErrorsToFieldErrors(payload: unknown, t: Translate = identityT): FieldErrors {
  const errors: FieldErrors = {};
  if (!payload || typeof payload !== "object") return errors;
  const body = payload as Record<string, unknown>;

  const list = body.errors;
  if (Array.isArray(list)) {
    for (const item of list) {
      if (item && typeof item === "object") {
        const e = item as ServerFieldError;
        addError(errors, serverErrorKey(e) ?? "__root", serverErrorMessage(e, t));
      } else if (typeof item === "string") {
        addError(errors, "__root", item);
      }
    }
  } else if (list && typeof list === "object") {
    for (const [key, msgs] of Object.entries(list as Record<string, unknown>)) {
      if (Array.isArray(msgs)) for (const m of msgs) addError(errors, key, String(m));
      else if (msgs !== null && msgs !== undefined) addError(errors, key, String(msgs));
    }
  }

  const detail = body.detail;
  if (Array.isArray(detail)) {
    for (const item of detail) {
      if (!item || typeof item !== "object") continue;
      const d = item as { loc?: unknown[]; msg?: string };
      const loc = Array.isArray(d.loc) ? d.loc.filter((p) => typeof p === "string" && p !== "body") : [];
      addError(errors, loc.length > 0 ? loc.join(".") : "__root", d.msg ?? t("config.validation.rejected", "rejected by backend"));
    }
  } else if (typeof detail === "string") {
    addError(errors, "__root", detail);
  }

  if (body.reason && typeof body.reason === "string" && Object.keys(errors).length === 0) {
    addError(errors, "__root", String(body.reason));
  }
  return errors;
}

/* ------------------------------------------------------------------ */
/* Mutation-result truth (backend-authoritative)                       */
/* ------------------------------------------------------------------ */

/**
 * Legacy routes answer HTTP 200 with `{success:false, error:{code,message}}`
 * for refusals (`web/errors.py safe_error_payload`). Never treat a 200 alone as
 * success: this is the single decision point every mutation in this lane uses.
 */
export function isBackendSuccess(body: unknown): boolean {
  if (body === null || body === undefined) return false;
  if (typeof body !== "object") return true;
  const b = body as Record<string, unknown>;
  if ("success" in b) return b.success === true;
  if ("ok" in b) return b.ok === true;
  if (b.error && typeof b.error === "object") return false;
  if ("available" in b) return b.available === true;
  return true;
}

/** Backend's own words for a refusal (verbatim — no client-side invention). */
export function backendMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const b = body as Record<string, unknown>;
  const err = b.error;
  if (err && typeof err === "object") {
    const e = err as Record<string, unknown>;
    if (typeof e.message === "string" && e.message) return e.message;
    if (typeof e.code === "string" && e.code) return e.code;
  }
  if (typeof b.detail === "string" && b.detail) return b.detail;
  if (typeof b.message === "string" && b.message) return b.message;
  if (typeof b.reason === "string" && b.reason) return b.reason;
  if (typeof b.code === "string" && b.code) return b.code;
  return fallback;
}

/** request_id for error captions (both envelope families carry one). */
export function backendRequestId(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const err = b.error;
  if (err && typeof err === "object") {
    const rid = (err as Record<string, unknown>).request_id;
    if (typeof rid === "string" && rid) return rid;
  }
  const meta = b.meta;
  if (meta && typeof meta === "object") {
    const rid = (meta as Record<string, unknown>).request_id;
    if (typeof rid === "string" && rid) return rid;
  }
  const top = b.request_id ?? b.correlation_id;
  return typeof top === "string" && top ? top : null;
}

/* ------------------------------------------------------------------ */
/* Numeric-vector validation (debug model-test)                        */
/* ------------------------------------------------------------------ */

export interface VectorBounds {
  /** Exact required length (backend rejects a wrong-width vector). */
  length?: number;
  min?: number;
  max?: number;
  label?: string;
}

export interface VectorCheck {
  ok: boolean;
  values: number[];
  errors: string[];
  /** Indices (0-based) that failed, for per-cell highlighting. */
  badIndices: number[];
}

/**
 * Validate a feature vector BEFORE POSTing it: finite numbers, exact width and
 * optional bounds. The backend sanitizes too, but a 422 costs a round-trip and
 * hides which cell the operator mistyped.
 */
export function checkNumericVector(input: readonly (string | number | null | undefined)[], bounds: VectorBounds, t: Translate = identityT): VectorCheck {
  const errors: string[] = [];
  const badIndices: number[] = [];
  const values: number[] = [];
  const label = bounds.label ?? "vector";

  input.forEach((raw, i) => {
    const n = toFiniteNumber(typeof raw === "string" ? raw : raw === null || raw === undefined ? "" : raw);
    if (n === null) {
      badIndices.push(i);
      errors.push(t("config.validation.vector_not_finite", "{label}[{i}] is not a finite number", { label, i }));
      return;
    }
    if (bounds.min !== undefined && n < bounds.min) {
      badIndices.push(i);
      errors.push(t("config.validation.must_be_ge", "{label} must be ≥ {min}", { label: `${label}[${i}]`, min: bounds.min }));
      return;
    }
    if (bounds.max !== undefined && n > bounds.max) {
      badIndices.push(i);
      errors.push(t("config.validation.must_be_le", "{label} must be ≤ {max}", { label: `${label}[${i}]`, max: bounds.max }));
      return;
    }
    values.push(n);
  });

  if (bounds.length !== undefined && input.length !== bounds.length) {
    errors.unshift(t("config.validation.vector_length", "{label} must contain exactly {n} values, got {m}", { label, n: bounds.length, m: input.length }));
  }

  return { ok: errors.length === 0, values, errors, badIndices };
}

/* ------------------------------------------------------------------ */
/* Diffing (forms + snapshot compare)                                  */
/* ------------------------------------------------------------------ */

/** Keys whose value differs from the server-served baseline. */
export function changedKeys(before: FieldValues, after: FieldValues): string[] {
  const keys = new Set<string>([...Object.keys(before), ...Object.keys(after)]);
  const out: string[] = [];
  for (const k of keys) {
    if (normScalar(before[k]) !== normScalar(after[k])) out.push(k);
  }
  return out.sort();
}

function normScalar(v: FieldValue): string {
  if (v === null || v === undefined) return "";
  return String(v);
}

/** Only the changed keys, with values coerced per spec kind — the payload we send. */
export function buildPayload(specs: readonly FieldSpec[], before: FieldValues, after: FieldValues): FieldValues {
  const payload: FieldValues = {};
  const byKey = new Map(specs.map((s) => [s.key, s] as const));
  for (const key of changedKeys(before, after)) {
    const spec = byKey.get(key);
    const raw = after[key];
    if (spec?.secret && isMaskedValue(raw)) continue; // unchanged secret — never re-send a mask
    if (!spec) {
      payload[key] = raw;
      continue;
    }
    if (spec.kind === "number" || spec.kind === "integer") {
      const n = toFiniteNumber(raw);
      if (n === null) continue; // cannot happen post-validation, belt-and-braces
      payload[key] = spec.kind === "integer" ? Math.trunc(n) : n;
    } else if (spec.kind === "boolean") {
      payload[key] = raw === true || raw === "true";
    } else {
      payload[key] = asText(raw);
    }
  }
  return payload;
}
