/**
 * Database: DTO -> VO mappers + specs (pure, unit-testable).
 *
 * The PostgreSQL config form reuses the lane validation engine: host/port/
 * database/username are required-typed fields, ssl_mode is an enum, and the
 * password pair gets a cross-field equality rule mirroring the backend
 * PASSWORD_MISMATCH gate (so an obvious mismatch never wastes a POST).
 */

import {
  type FieldErrors,
  type FieldSpec,
  type FieldValues,
  toFiniteNumber,
  identityT,
  type Translate,
  validateFields,
} from "@/features/config/validation";
import type { ConsoleDatabase, DbDomainStatus, DbManageStatus, PgConfig } from "./api";

export const PG_SSL_MODES = ["", "disable", "allow", "prefer", "require", "verify-ca", "verify-full"] as const;

export function pgSpecs(t: Translate = identityT): FieldSpec[] {
  return [
    { key: "host", label: t("database.pg.host", "host"), kind: "string", required: true, min: undefined },
    { key: "port", label: t("database.pg.port", "port"), kind: "integer", required: true, min: 1, max: 65_535 },
    { key: "database", label: t("database.pg.database", "database"), kind: "string", required: true },
    { key: "username", label: t("database.pg.username", "username"), kind: "string", required: true },
    { key: "ssl_mode", label: t("database.pg.ssl_mode", "ssl mode"), kind: "enum", required: false, options: PG_SSL_MODES },
    {
      key: "password",
      label: t("database.pg.password", "password"),
      kind: "string",
      required: false,
      secret: true,
      hint: t(
        "database.pg.password_hint",
        "routed to the OS SecretStore (DPAPI) server-side; never stored in the settings DB, never echoed back",
      ),
      cross: (values: FieldValues) =>
        String(values.password ?? "") !== "" && String(values.password ?? "") !== String(values.confirm_password ?? "")
          ? t(
              "database.pg.cross_mismatch",
              "password and confirmation do not match (backend would answer PASSWORD_MISMATCH)",
            )
          : null,
    },
    { key: "confirm_password", label: t("database.pg.confirm_password", "confirm password"), kind: "string", required: false, secret: true },
  ];
}

export function validatePgConfig(values: FieldValues, t: Translate = identityT): FieldErrors {
  return validateFields(pgSpecs(t), values, undefined, t);
}

/** Strip to exactly the payload shape the backend consumes.  The advanced
 *  knobs ride along (additively) so a save never WIPES keys another surface
 *  persisted on the same config row (POST /api/db/manage/config replaces the
 *  row wholesale — see settings/service.set_postgres_config). */
export function pgPayload(values: FieldValues): PgConfig & { password?: string; confirm_password?: string } {
  const out: PgConfig & { password?: string; confirm_password?: string } = {
    host: String(values.host ?? ""),
    port: Number(values.port ?? 5432),
    database: String(values.database ?? ""),
    username: String(values.username ?? ""),
    ssl_mode: String(values.ssl_mode ?? ""),
  };
  if (String(values.password ?? "") !== "") {
    out.password = String(values.password);
    out.confirm_password = String(values.confirm_password ?? "");
  }
  const adv = advancedPayload(values);
  if (adv.command_timeout_sec !== undefined) out.command_timeout_sec = adv.command_timeout_sec;
  if (adv.connect_timeout_sec !== undefined) out.connect_timeout_sec = adv.connect_timeout_sec;
  if (adv.migrate_on_startup !== undefined) out.migrate_on_startup = adv.migrate_on_startup;
  if (adv.pooling_enabled !== undefined) out.pooling_enabled = adv.pooling_enabled;
  if (String(values.domain ?? "") !== "") out.domain = String(values.domain);
  return out;
}

export function baselineFromStatus(pg: PgConfig | null | undefined): FieldValues {
  const out: FieldValues = {
    host: pg?.host ?? "localhost",
    port: pg?.port ?? 5432,
    database: pg?.database ?? "nse_audit",
    username: pg?.username ?? "nse_user",
    ssl_mode: pg?.ssl_mode ?? "prefer",
    password: "",
    confirm_password: "",
  };
  // Additive (db-provider-pro): the advanced knobs ride the SAME config row,
  // so a row-reported value seeds the form.  A key absent from the row stays
  // ABSENT (never zero-filled) — mergeAdvancedBaseline() then falls back to
  // the options block or leaves the control UNAVAILABLE.
  for (const k of ["command_timeout_sec", "connect_timeout_sec"] as const) {
    const v = pg ? pg[k] : undefined;
    if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
  }
  for (const k of ["migrate_on_startup", "pooling_enabled"] as const) {
    const v = pg ? pg[k] : undefined;
    if (typeof v === "boolean") out[k] = v;
  }
  const domain = pg ? pg.domain : undefined;
  if (typeof domain === "string" && domain.length > 0) out.domain = domain;
  return out;
}

/* ------------------------------ status VOs ------------------------------ */

export interface DomainRow {
  domain: string;
  schema: string;
  expected: string;
  state: string;
  pending: number | null;
  integrity: string;
  tamper: boolean;
}

export function domainRows(dbs: Record<string, DbDomainStatus> | undefined): DomainRow[] {
  return Object.entries(dbs ?? {}).map(([domain, st]) => ({
    domain,
    schema: String(st.schema_version ?? "—"),
    expected: String(st.expected_version ?? "—"),
    state: String(st.migration_state ?? st.error ?? "UNKNOWN"),
    pending: typeof st.pending_count === "number" ? st.pending_count : null,
    integrity: String(st.integrity ?? "—"),
    tamper: st.tamper_detected === true,
  }));
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
}

export function dbConsoleName(db: ConsoleDatabase): string {
  return db.path || db.database || db.server || db.name;
}

/* ------------------------- db-provider-pro (2026-09-23) ------------------------- */
/* Append-only additions: advanced knobs + per-domain awareness.  Existing
 * exports above keep their signatures (contract §2, Lane B ownership).
 *
 * The knobs mirror `DatabaseConfig`/`settings/provider_options.py` on the
 * backend BY CONTRACT — never imported (contract §2.1). */

/** The advanced knob values the UI shows.  `live` says the backend reported
 *  the block at all; false means an older engine build — every knob then
 *  renders UNAVAILABLE and its control stays inert (never a zero-filled or
 *  invented default).
 *
 *  The pool-sizing knobs are OPTIONAL on the persisted row: `null` means the
 *  operator never set one, which is a *different fact* from a deliberate 0
 *  (pool_min_size 0 opens the pool lazily; 0 idle/lifetime = never reap).
 *  The backend omits an unset knob rather than reporting a fabricated
 *  number, so `null` renders UNAVAILABLE exactly like an unreported one. */
export interface DbOptionsState {
  live: boolean;
  command_timeout_sec: number | null;
  connect_timeout_sec: number | null;
  migrate_on_startup: boolean | null;
  pooling_enabled: boolean | null;
  pool_min_size: number | null;
  pool_max_size: number | null;
  pool_idle_timeout_sec: number | null;
  pool_max_lifetime_sec: number | null;
  domain: string | null;
  database: string | null;
}

export const OPTION_UNAVAILABLE = "UNAVAILABLE" as const;

/**
 * The additive `/api/db/manage/status` fields Lane A reports (contract §3.3)
 * now live on `DbManageStatus` itself (api.ts) — folded in at harvest,
 * replacing the wave-time local `ManageStatusExtras` shim (INTEGRATION_LOG
 * dbp-lane-b item 1, ratified).
 * The `options` index signature reads the documented keys
 * {command_timeout_sec, connect_timeout_sec, migrate_on_startup,
 *  pooling_enabled, domain, database} with runtime narrowing below — an
 * untyped key is never rendered raw.
 */
/**
 * Persistence domains the config is scoped to.  NOT a client guess — the UI
 * only offers what the backend reported.
 *
 * `manage.domains` is the per-domain health snapshot's keys (always present at
 * base); Lane A also reports the DEFAULT_DB_FILES list under
 * `domain_db_names`.  Either satisfies the select; neither means UNAVAILABLE.
 */
export function domainOptions(manage: DbManageStatus | null | undefined): readonly string[] {
  if (!manage) return [];
  const out: string[] = [];
  const healthDomains = manage.domains;
  if (healthDomains && typeof healthDomains === "object") {
    for (const d of Object.keys(healthDomains)) out.push(d);
  }
  const listed = manage.domain_db_names;
  if (listed) {
    for (const d of listed) {
      if (typeof d === "string" && d.trim() !== "" && !out.includes(d)) out.push(d);
    }
  }
  return out;
}

export function optionsFromStatus(manage: DbManageStatus | null | undefined): DbOptionsState {
  const opts = manage?.options ?? null;
  const num = (k: string): number | null => {
    const v = opts ? opts[k] : undefined;
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  };
  const bool = (k: string): boolean | null => {
    const v = opts ? opts[k] : undefined;
    return typeof v === "boolean" ? v : null;
  };
  const str = (k: string): string | null => {
    const v = opts ? opts[k] : undefined;
    return typeof v === "string" && v.length > 0 ? v : null;
  };
  return {
    live: opts !== null,
    command_timeout_sec: opts ? num("command_timeout_sec") : null,
    connect_timeout_sec: opts ? num("connect_timeout_sec") : null,
    migrate_on_startup: opts ? bool("migrate_on_startup") : null,
    pooling_enabled: opts ? bool("pooling_enabled") : null,
    // Optional knobs: an UNSET pool knob is omitted server-side (never
    // zero-filled), so "not reported" and "reported as the running default"
    // stay distinguishable from an explicit 0.
    pool_min_size: opts ? num("pool_min_size") : null,
    pool_max_size: opts ? num("pool_max_size") : null,
    pool_idle_timeout_sec: opts ? num("pool_idle_timeout_sec") : null,
    pool_max_lifetime_sec: opts ? num("pool_max_lifetime_sec") : null,
    domain: opts ? str("domain") : null,
    database: opts ? str("database") : null,
  };
}

/**
 * The advanced knobs as form field specs.  Bounds mirror the backend exactly
 * (contract §3.3): command_timeout_sec 0..600, connect_timeout_sec 1..120,
 * pool sizing 0..64/1..128, pool idle/lifetime in seconds (0 = never reap).
 *
 * The pool knobs are OPTIONAL on the row: a blank control means "leave the
 * stored value alone" (advancedPayload omits it), never "reset to 0".
 */
export function advancedSpecs(): FieldSpec[] {
  return [
    { key: "domain", label: "domain", kind: "enum", required: false, options: [] as string[], hint: "the config is domain-scoped; the UI offers only backend-reported domains" },
    { key: "command_timeout_sec", label: "command timeout (s)", kind: "integer", required: false, min: 0, max: 600, hint: "per-statement timeout; 0 = provider default" },
    { key: "connect_timeout_sec", label: "connect timeout (s)", kind: "integer", required: false, min: 1, max: 120, hint: "connection establishment timeout" },
    { key: "migrate_on_startup", label: "migrate on startup", kind: "boolean", required: false },
    { key: "pooling_enabled", label: "pooling", kind: "boolean", required: false },
    { key: "pool_min_size", label: "pool min size", kind: "integer", required: false, min: 0, max: 64, hint: "connections held open; 0 = open lazily; blank = the engine default" },
    { key: "pool_max_size", label: "pool max size", kind: "integer", required: false, min: 1, max: 128, hint: "connections the pool will open at most; blank = the engine default" },
    { key: "pool_idle_timeout_sec", label: "pool idle timeout (s)", kind: "integer", required: false, min: 0, max: 86400, hint: "an idle connection is reaped after this; 0 = never; blank = the engine default" },
    { key: "pool_max_lifetime_sec", label: "pool max lifetime (s)", kind: "integer", required: false, min: 0, max: 604800, hint: "a connection is recycled at this age; 0 = no limit; blank = the engine default" },
  ];
}

/** Bind the `domain` enum to what the backend reported (single source). */
export function advancedSpecsWithDomains(domains: readonly string[]): FieldSpec[] {
  const specs = advancedSpecs();
  return specs.map((s) => (s.key === "domain" ? { ...s, options: domains.length > 0 ? domains.slice() : [] } : s));
}

/** Validate only the advanced knobs (the discrete fields are validated by
 *  `validatePgConfig`).  Never throws. */
export function validateAdvancedOptions(values: FieldValues): FieldErrors {
  return validateFields(advancedSpecs(), values);
}

/** Strip the advanced knobs to exactly the shape POST /api/db/manage/options
 *  accepts: only known keys, integers as integers, booleans as booleans.
 *  A knob left blank/untouched is omitted, so the backend keeps its stored
 *  value (blank means "unchanged", never "reset to zero"). */
export function advancedPayload(values: FieldValues): {
  command_timeout_sec?: number;
  connect_timeout_sec?: number;
  migrate_on_startup?: boolean;
  pooling_enabled?: boolean;
  pool_min_size?: number;
  pool_max_size?: number;
  pool_idle_timeout_sec?: number;
  pool_max_lifetime_sec?: number;
} {
  const out: {
    command_timeout_sec?: number;
    connect_timeout_sec?: number;
    migrate_on_startup?: boolean;
    pooling_enabled?: boolean;
    pool_min_size?: number;
    pool_max_size?: number;
    pool_idle_timeout_sec?: number;
    pool_max_lifetime_sec?: number;
  } = {};
  const intOf = (k: string): number | null => {
    const n = toFiniteNumber(values[k]);
    return n === null || !Number.isInteger(n) ? null : n;
  };
  const ct = intOf("command_timeout_sec");
  if (ct !== null) out.command_timeout_sec = ct;
  const cot = intOf("connect_timeout_sec");
  if (cot !== null) out.connect_timeout_sec = cot;
  const boolOf = (k: string): boolean | null => {
    const v = values[k];
    if (v === true || v === "true") return true;
    if (v === false || v === "false") return false;
    return null;
  };
  const mos = boolOf("migrate_on_startup");
  if (mos !== null) out.migrate_on_startup = mos;
  const pool = boolOf("pooling_enabled");
  if (pool !== null) out.pooling_enabled = pool;
  // Pool sizing: an untouched control is OMITTED (the backend then keeps its
  // stored value).  An explicit 0 is sent through — it is a real setting
  // (open lazily / never reap), not "unset".
  const pmin = intOf("pool_min_size");
  if (pmin !== null) out.pool_min_size = pmin;
  const pmax = intOf("pool_max_size");
  if (pmax !== null) out.pool_max_size = pmax;
  const pidle = intOf("pool_idle_timeout_sec");
  if (pidle !== null) out.pool_idle_timeout_sec = pidle;
  const plife = intOf("pool_max_lifetime_sec");
  if (plife !== null) out.pool_max_lifetime_sec = plife;
  return out;
}

/**
 * Seed the advanced knobs into the form baseline.  Every value traces to the
 * backend payload; an unreported knob seeds as an EMPTY string (never a
 * fabricated number or boolean) — the panel then renders it as UNAVAILABLE
 * (contract: missing renders as `—`/UNAVAILABLE).
 *
 * The pool knobs are optional on the row: an empty string here is the
 * "engine default applies" state, and it is NOT zero-filled (0 would be a
 * real setting: open-lazy / never reap).
 */
export function baselineOptionsFromManage(manage: DbManageStatus | null | undefined): FieldValues {
  const opts = optionsFromStatus(manage);
  const dom = domainOptions(manage);
  return {
    // "audit" is the backend's own fallback domain (DatabaseConfig.for_postgres
    // default + read_options()'s `domain or "audit"`), never a client guess.
    domain: opts.domain ?? (dom.length > 0 ? dom[0] : ""),
    command_timeout_sec: opts.command_timeout_sec === null ? "" : String(opts.command_timeout_sec),
    connect_timeout_sec: opts.connect_timeout_sec === null ? "" : String(opts.connect_timeout_sec),
    migrate_on_startup: opts.migrate_on_startup === null ? "" : String(opts.migrate_on_startup),
    pooling_enabled: opts.pooling_enabled === null ? "" : String(opts.pooling_enabled),
    // Unset = empty (engine default), never 0.
    pool_min_size: opts.pool_min_size === null ? "" : String(opts.pool_min_size),
    pool_max_size: opts.pool_max_size === null ? "" : String(opts.pool_max_size),
    pool_idle_timeout_sec: opts.pool_idle_timeout_sec === null ? "" : String(opts.pool_idle_timeout_sec),
    pool_max_lifetime_sec: opts.pool_max_lifetime_sec === null ? "" : String(opts.pool_max_lifetime_sec),
  };
}

/**
 * The advanced knobs applied to a form that already has the discrete fields.
 * Missing stays missing (additive merge — never overwrites a seeded value
 * with a fabricated one).
 */
export function mergeAdvancedBaseline(
  base: FieldValues,
  manage: DbManageStatus | null | undefined,
): FieldValues {
  const opts = baselineOptionsFromManage(manage);
  const out: FieldValues = { ...base };
  for (const key of Object.keys(opts)) {
    if (out[key] === undefined || out[key] === null || out[key] === "") {
      out[key] = opts[key];
    }
  }
  return out;
}

/**
 * The row a control shows when its knob is not reported: the explicit word
 * UNAVAILABLE, per the backend-guarantee invariant.  A reported knob renders
 * its raw value beside the control (never zero-filled).
 */
export function optionRowState(opts: DbOptionsState, key: string): {
  reported: string;
  unavailable: boolean;
} {
  const v =
    key === "domain" ? opts.domain : key === "database" ? opts.database : opts[key as keyof DbOptionsState];
  if (v === null || v === undefined || (typeof v === "string" && v.length === 0)) {
    return { reported: OPTION_UNAVAILABLE, unavailable: true };
  }
  return { reported: String(v), unavailable: false };
}

