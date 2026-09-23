/**
 * Database tab — pure presentation logic (no fetch, no React, no runtime
 * imports).
 *
 * WHY SEPARATE: this module is imported verbatim by the CI-runnable
 * `tests/js/database_console.test.js` (node 24 strips the TS types; the only
 * imports here are `import type`, erased before resolution, so the `@/`
 * alias never has to resolve). Anything here must therefore stay a pure
 * derivation over backend payloads — it decides NOTHING the backend could
 * decide, it only arranges and counts what the server said.
 *
 * Payload shapes come from producers, not guesses:
 *   web/db_console.py        — databases/tables/rows/query/quick/apikeys
 *   web/diagnostics_state_routes.py — /api/db/manage/status (+ hints)
 *   web/diagnostics_state_routes.py — /api/db/hygiene (status/plans/runtime)
 */

import type {
  ConsoleDatabase,
  DbHygiene,
  DbManageStatus,
  DbStatus,
  MigrationReport,
  SqliteEvidence,
} from "./api";

/** Translator contract — same shape as `t` from `@/stores/i18nStore`.
 *  This module is `require()`d verbatim by tests/js/database_console.test.js in
 *  plain Node (only `import type` is allowed here), so helpers that render
 *  English take `t` as a parameter with an identity default instead of
 *  importing the store — see CONTRACT ("thread t as a parameter"). */
export type Translate = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** English identity: interpolate the {vars} into the call-site fallback. */
const identityT: Translate = (_key, fallback, vars) =>
  vars ? fallback.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : fallback;

/* ------------------------------------------------------------------ */
/* Console explorer                                                    */
/* ------------------------------------------------------------------ */

/** A database the console can actually read right now. */
export function isReachable(db: ConsoleDatabase | undefined | null): boolean {
  return !!db && String(db.status ?? "").toUpperCase() === "CONNECTED";
}

/**
 * Default selection for the explorer: the first REACHABLE database, never
 * blindly `list[0]`.
 *
 * Observed 2026-09-23: the list is ordered audit/news/candle_intel/settings
 * and the first three were postgresql while only `settings` (last) was
 * sqlite+reachable — picking `list[0]` opened the console in a permanent
 * "'audit' not reachable" state while a perfectly readable database sat
 * one click away.
 */
export function pickDefaultDatabase(list: ConsoleDatabase[] | undefined | null): string | null {
  if (!list || list.length === 0) return null;
  const reachable = list.find(isReachable);
  const chosen = reachable ?? list[0];
  return chosen ? chosen.name : null;
}

/** Honest blocker for a database the console cannot read (null when reachable). */
export function consoleBlocker(
  db: ConsoleDatabase | undefined | null,
  t: Translate = identityT,
): { message: string; hint?: string } | null {
  if (!db) return null;
  if (isReachable(db)) return null;
  const where = db.path || (db.server ? `${db.server}/${db.database}` : db.name);
  const why = String(db.status ?? "UNKNOWN");
  return {
    message: t("database.explorer.blocker_msg", "{name} is {status} — {where} could not be opened.", {
      name: db.name,
      status: why.replace(/_/g, " ").toLowerCase(),
      where,
    }),
    ...(db.hint ? { hint: String(db.hint) } : {}),
  };
}

/** Dialect-appropriate starter SQL (the backend allow-list accepts both). */
export function defaultSqlForProvider(provider: string | undefined | null): string {
  if ((provider ?? "").toLowerCase().startsWith("postgres")) {
    return "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name";
  }
  return "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name";
}

/** "rows 1–100 · page 1" style caption, honest about an empty page. */
export function pageCaption(offset: number, shown: number, t: Translate = identityT): string {
  if (shown === 0) return t("database.explorer.page_empty", "empty page (offset {offset})", { offset });
  return t("database.explorer.page_range", "rows {from}–{to}", { from: offset + 1, to: offset + shown });
}

/** Truncation is a backend fact (`truncated` + `cap`); never re-inferred. */
export function truncationNote(truncated: boolean | undefined, cap: number | undefined, t: Translate = identityT): string {
  if (!truncated) return "";
  return cap
    ? t("database.explorer.trunc_cap", " · truncated at the {cap}-row cap", { cap })
    : t("database.explorer.trunc_any", " · truncated at the row cap");
}

/* ------------------------------------------------------------------ */
/* Status tab                                                          */
/* ------------------------------------------------------------------ */

export interface PendingSchema {
  /** Sum of `pending_count` across domains (null when nothing reported). */
  pending: number | null;
  /** Domains whose schema_version < expected_version. */
  behind: string[];
  /** Domains with tamper_detected === true. */
  tampered: string[];
}

export function pendingSchema(status: DbStatus | undefined | null): PendingSchema {
  const out: PendingSchema = { pending: null, behind: [], tampered: [] };
  const dbs = status?.databases;
  if (!dbs) return out;
  let pending = 0;
  for (const [domain, st] of Object.entries(dbs)) {
    const p = typeof st.pending_count === "number" ? st.pending_count : 0;
    pending += p;
    if (p > 0) out.behind.push(domain);
    if (st.tamper_detected === true) out.tampered.push(domain);
  }
  out.pending = pending;
  return out;
}

/** Provider-level guidance the backend already computed (never re-derived). */
export function providerHints(manage: DbManageStatus | undefined | null): string[] {
  const hints = manage?.hints;
  if (!Array.isArray(hints)) return [];
  return hints.filter((h): h is string => typeof h === "string" && h.trim() !== "");
}

/* ------------------------------------------------------------------ */
/* Hygiene tab (structured view over the raw JSON dump)                */
/* ------------------------------------------------------------------ */

export interface HygieneWorker {
  state: string;
  mode: string;
  executionMode: string;
  cycle: number | null;
  lastScan: string;
  lastSuccess: string;
  lastFailure: string;
  managed: string[];
}

export function hygieneWorker(h: DbHygiene | undefined | null): HygieneWorker | null {
  const s = h?.status as Record<string, unknown> | undefined;
  if (!s) return null;
  const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  const managed = Array.isArray(s.managed_databases) ? (s.managed_databases as unknown[]) : [];
  return {
    state: str(s.state) || "UNKNOWN",
    mode: str(s.mode) || "—",
    executionMode: str(s.execution_mode) || "—",
    cycle: num(s.cycle),
    lastScan: str(s.last_scan),
    lastSuccess: str(s.last_success),
    lastFailure: str(s.last_failure),
    managed: managed.map(String),
  };
}

export interface StorageRow {
  database: string;
  bytes: number;
  walBytes: number;
}

/** db_sizes -> table rows, sorted largest first (server order is not a promise). */
export function hygieneStorage(h: DbHygiene | undefined | null): StorageRow[] {
  const sizes = (h?.status as { db_sizes?: Record<string, { bytes?: number; wal_bytes?: number }> } | undefined)
    ?.db_sizes;
  if (!sizes) return [];
  return Object.entries(sizes)
    .map(([database, v]) => ({
      database,
      bytes: typeof v?.bytes === "number" ? v.bytes : 0,
      walBytes: typeof v?.wal_bytes === "number" ? v.wal_bytes : 0,
    }))
    .sort((a, b) => b.bytes - a.bytes);
}

export function totalStorageBytes(h: DbHygiene | undefined | null): number | null {
  const rows = hygieneStorage(h);
  if (rows.length === 0) return null;
  return rows.reduce((sum, r) => sum + r.bytes, 0);
}

export interface PlanRow {
  database: string;
  generatedAt: string;
  tablesScanned: number | null;
  duplicates: number | null;
  exactDuplicates: number | null;
  orphans: number | null;
  retention: number | null;
  deletes: number | null;
  blocked: number | null;
}

/**
 * plans -> table rows. The payload nests as
 * `{<db>: {database, plan: {...}}}`; a missing/absent plan row stays absent
 * (rendered as an empty state) — never zero-filled, because "no plan yet"
 * and "a plan with zero candidates" mean different things to an operator.
 */
export function hygienePlanRows(h: DbHygiene | undefined | null): PlanRow[] {
  const plans = (h?.plans ?? {}) as Record<string, Record<string, unknown>>;
  return Object.entries(plans).map(([database, wrapper]) => {
    const plan = (wrapper?.plan ?? wrapper ?? {}) as Record<string, unknown>;
    const n = (k: string): number | null => (typeof plan[k] === "number" ? (plan[k] as number) : null);
    const s = (k: string): string => (typeof plan[k] === "string" ? (plan[k] as string) : "");
    return {
      database,
      generatedAt: s("generated_at"),
      tablesScanned: n("tables_scanned"),
      duplicates: n("duplicates_found"),
      exactDuplicates: n("exact_duplicates"),
      orphans: n("orphans_found"),
      retention: n("retention_candidates"),
      deletes: n("delete_candidates"),
      blocked: n("blocked"),
    };
  });
}

/* ------------------------------------------------------------------ */
/* Provider truth — configured vs effective (2026-09-23)               */
/* ------------------------------------------------------------------ */

export interface ProviderTruth {
  configured: string;
  effective: string;
  mismatch: boolean;
  note: string;
  pgTarget: string;
  pgError: string;
  evidence: SqliteEvidence[];
  /** "backend"  = measured server-side by `_provider_truth` (probe +
   *  file-activity evidence). "derived" = narrower client-side claim made
   *  from an OLDER engine payload that has not been restarted into the new
   *  shape — the band labels itself so nobody mistakes inference for
   *  measurement. */
  source: "backend" | "derived";
}

/**
 * The 2026-09-23 complaint: "it shows postgres but data comes from sqlite".
 *
 * With `provider_truth` present we render the backend's measurement
 * verbatim. Without it (engine not restarted yet) we derive ONLY what the
 * payload proves: the configured provider and the connected-domain counts.
 * We never guess a file the client cannot see.
 */
export function providerTruth(manage: DbManageStatus | undefined | null, t: Translate = identityT): ProviderTruth | null {
  if (!manage) return null;
  const truth = manage.provider_truth;
  if (truth) {
    return {
      configured: truth.configured,
      effective: truth.effective,
      mismatch: Boolean(truth.mismatch),
      note: truth.note ?? "",
      pgTarget: truth.pg_target ?? "",
      pgError: truth.pg_error ?? "",
      evidence: truth.evidence ?? [],
      source: "backend",
    };
  }
  const configured = manage.provider ?? "unknown";
  const domains = Object.values(manage.domains ?? {}) as Array<{ connected?: boolean }>;
  const connected = domains.filter((d) => d && d.connected === true).length;
  const base = {
    configured,
    pgTarget: "",
    pgError: "",
    evidence: [] as SqliteEvidence[],
    source: "derived" as const,
  };
  if (configured !== "postgresql") {
    return { ...base, effective: configured, mismatch: false, note: "" };
  }
  if (domains.length === 0) {
    return { ...base, effective: "unknown", mismatch: false, note: "" };
  }
  if (connected === 0) {
    return {
      ...base,
      effective: "sqlite",
      mismatch: true,
      note: t(
        "database.page.truth_zero",
        "{connected} of {total} domains are connected to postgresql — the schema, storage and hygiene panels here are fed by the local SQLite files.",
        { connected, total: domains.length },
      ),
    };
  }
  if (connected < domains.length) {
    return {
      ...base,
      effective: "mixed",
      mismatch: true,
      note: t(
        "database.page.truth_partial",
        "only {connected} of {total} domains answered on postgresql — the others still serve their local SQLite files.",
        { connected, total: domains.length },
      ),
    };
  }
  return { ...base, effective: "postgresql", mismatch: false, note: "" };
}

/** Tri-state psycopg presence. `undefined` means the payload predates the
 *  field — the UI must say "not reported" instead of claiming "installed". */
export function psycopgState(available: boolean | undefined | null, t: Translate = identityT): {
  label: string;
  tone: "good" | "bad" | "neutral";
  sub: string;
} {
  if (available === true) {
    return {
      label: t("database.psycopg.installed", "installed"),
      tone: "good",
      sub: t("database.psycopg.required_sub", "required while the provider is postgresql"),
    };
  }
  if (available === false) {
    return {
      label: t("database.psycopg.missing", "missing"),
      tone: "bad",
      sub: t(
        "database.psycopg.absent_sub",
        "psycopg absent — postgres queries fail until installed or the provider switches back",
      ),
    };
  }
  return {
    label: t("database.psycopg.not_reported", "not reported"),
    tone: "neutral",
    sub: t("database.psycopg.not_reported_sub", "this backend build does not report driver presence"),
  };
}

/* ------------------------------------------------------------------ */
/* Switch readiness — the guided provider-switch checklist (2026-09-23) */
/* ------------------------------------------------------------------ */

export type ReadinessState = "PASS" | "WARN" | "BLOCK" | "INFO";

export interface ReadinessItem {
  key: string;
  label: string;
  state: ReadinessState;
}

export interface SwitchReadiness {
  checklist: ReadinessItem[];
  /** true only when no BLOCK item remains. */
  canSwitch: boolean;
  /** true when a WARN blocks the default path and the operator must type an
   *  acknowledgement to proceed (the existing TypedConfirmModal pattern). */
  requiresAcknowledgement: boolean;
}

/**
 * Provider-switch readiness, derived from backend fields ONLY.
 *
 * The frontend computes NO new health verdict: `canSwitch` is exactly "no
 * BLOCK item", and every BLOCK is a negative signal the backend itself
 * reported. Nothing here may turn a missing field into a pass — an absent
 * input is rendered as INFO (UNAVAILABLE), never as PASS.
 *
 * Producers (cited per input):
 *  - `manage.provider`            — /api/db/manage/status `provider`
 *    (web/diagnostics_state_routes.py, `db_manage_status`: `ui["provider"]`)
 *  - `manage.overall`             — /api/db/manage/status `overall`
 *    (database/health.py `DatabaseHealthService.snapshot`, "Healthy"/"Warning"/"Error")
 *  - `manage.password_set`        — /api/db/manage/status `password_set`
 *    (load_ui_config: SecretStore.has_secret on the `password_secret` reference)
 *  - `manage.postgresql_driver_available` — /api/db/manage/status
 *    `postgresql_driver_available` (PostgreSQLDriver.available())
 *  - `report.provider_switch_ready` — /api/db/manage/report
 *    `provider_switch_ready` (database/migrate_engine.py:354 — set ONLY when
 *    `status == "COMPLETE" and validation == "PASSED"`)
 *  - `testResult.connected`       — the last POST /api/db/manage/test-connection
 *    answer, client-held (`connected` in the action envelope)
 */
export function switchReadiness(
  manage: DbManageStatus | null | undefined,
  report: MigrationReport | null | undefined,
  testResult: { connected: boolean } | null | undefined,
): SwitchReadiness {
  const target = String(manage?.provider ?? "").toLowerCase();
  const targetingPostgres = target === "postgresql";

  const checklist: ReadinessItem[] = [];

  /* --- configured provider (the switch target the operator picked) ----- *
   * Absent `provider` means /api/db/manage/status never answered with a
   * selection — we cannot name the target, so we refuse to greenlight it. */
  checklist.push({
    key: "target-provider",
    label: !manage?.provider
      ? "switch target is UNAVAILABLE — /api/db/manage/status did not report a provider"
      : `switch target is ${manage.provider}`,
    state: manage?.provider ? "INFO" : "BLOCK",
  });

  /* --- driver: the only hard backend negative for postgresql ---------- *
   * `postgresql_driver_available === false` is a measured absence; an
   * undefined field means this build does not report driver presence, and we
   * neither claim it is installed nor block on it (tri-state, like
   * psycopgState above). */
  const driverAvailable = manage?.postgresql_driver_available;
  checklist.push({
    key: "pg-driver",
    label:
      driverAvailable === undefined
        ? "psycopg presence is UNAVAILABLE — this backend build does not report it"
        : driverAvailable
          ? "psycopg is installed — the postgresql driver is importable server-side"
          : "psycopg is missing — postgresql queries fail until it is installed or the provider switches back",
    state: targetingPostgres
      ? driverAvailable === false
        ? "BLOCK"
        : driverAvailable === true
          ? "PASS"
          : "INFO"
      : driverAvailable === true
        ? "PASS"
        : "INFO",
  });

  /* --- stored secret reference (required to authenticate to postgres) -- *
   * `password_set === false` is the backend reporting no SecretStore
   * reference; undefined is unreported. */
  const passwordSet = manage?.password_set;
  checklist.push({
    key: "pg-password",
    label:
      passwordSet === undefined
        ? "stored PostgreSQL password reference is UNAVAILABLE — not reported"
        : passwordSet
          ? "PostgreSQL password is stored — OS SecretStore (DPAPI) reference present"
          : "PostgreSQL password is missing — run Test connection with a password first",
    state: targetingPostgres
      ? passwordSet === false
        ? "BLOCK"
        : passwordSet === true
          ? "PASS"
          : "INFO"
        : passwordSet === true
          ? "PASS"
          : "INFO",
  });

  /* --- connection probe, client-held ------------------------------- *
   * Not a backend field of /manage/status: the operator's last
   * test-connection answer. Absent means no probe was run this session. */
  checklist.push({
    key: "connection-test",
    label:
      testResult === null || testResult === undefined
        ? "connection not tested — run Test connection against the target"
        : testResult.connected
          ? "connection test passed — the target answered the probe"
          : "connection test failed — the target did not answer the probe",
    state:
      testResult?.connected === true ? "PASS" : testResult === null || testResult === undefined ? "INFO" : "BLOCK",
  });

  /* --- migration validation ---------------------------------------- *
   * `provider_switch_ready` is the backend's own gate
   * (migrate_engine.py:354): true ONLY on status COMPLETE + validation
   * PASSED. Missing/false/undefined is the WARN that asks for a typed
   * acknowledgement — the operator may insist on switching without a
   * validated migration, but the UI never silently lets them. */
  const switchReady = report?.provider_switch_ready;
  checklist.push({
    key: "migration-validated",
    label:
      switchReady === undefined
        ? "migration not validated — /api/db/manage/report is UNAVAILABLE"
        : switchReady
          ? "migration validated — report status COMPLETE and validation PASSED"
          : `migration not validated — report status ${String(report?.status ?? "UNKNOWN")}`,
    state: switchReady === true ? "PASS" : "WARN",
  });

  /* --- domain health word (context only, never a client verdict) ----- *
   * `overall` is a backend word; the checklist restate it verbatim and it
   * never contributes to canSwitch — the frontend computes no verdict. */
  const overall = manage?.overall;
  checklist.push({
    key: "domain-health",
    label: overall ? `domain health reported by the backend: ${overall}` : "domain health is UNAVAILABLE — not reported",
    state: "INFO",
  });

  const canSwitch = !checklist.some((i) => i.state === "BLOCK");
  const requiresAcknowledgement = checklist.some((i) => i.state === "WARN");

  return { checklist, canSwitch, requiresAcknowledgement };
}

/** Human recency label for evidence rows ("just now", "47s ago", "3h ago"). */
export function ageLabel(seconds: number | undefined | null, t: Translate = identityT): string {
  if (typeof seconds !== "number" || !isFinite(seconds) || seconds < 0) {
    return t("database.age.unknown", "age unknown");
  }
  if (seconds < 5) return t("database.age.just_now", "just now");
  if (seconds < 60) return t("database.age.seconds", "{n}s ago", { n: Math.round(seconds) });
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t("database.age.minutes", "{n}m ago", { n: minutes });
  const hours = Math.round(minutes / 60);
  if (hours < 48) return t("database.age.hours", "{n}h ago", { n: hours });
  return t("database.age.days", "{n}d ago", { n: Math.round(hours / 24) });
}
