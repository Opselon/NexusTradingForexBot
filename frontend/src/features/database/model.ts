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
  type Translate,
  identityT,
  validateFields,
} from "@/features/config/validation";
import type { ConsoleDatabase, DbDomainStatus, PgConfig } from "./api";

export const PG_SSL_MODES = ["", "disable", "allow", "prefer", "require", "verify-ca", "verify-full"] as const;

/** `t` is threaded from the render site (ManageTab) so labels/hints/errors
 *  render in the active language; the identity default keeps the pure
 *  non-UI callers (useCases client-block checks) on the English fallback. */
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
    {
      key: "confirm_password",
      label: t("database.pg.confirm_password", "confirm password"),
      kind: "string",
      required: false,
      secret: true,
    },
  ];
}

export function validatePgConfig(values: FieldValues, t: Translate = identityT): FieldErrors {
  return validateFields(pgSpecs(t), values, undefined, t);
}

/** Strip to exactly the payload shape the backend consumes. */
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
  return out;
}

export function baselineFromStatus(pg: PgConfig | null | undefined): FieldValues {
  return {
    host: pg?.host ?? "localhost",
    port: pg?.port ?? 5432,
    database: pg?.database ?? "nse_audit",
    username: pg?.username ?? "nse_user",
    ssl_mode: pg?.ssl_mode ?? "prefer",
    password: "",
    confirm_password: "",
  };
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
