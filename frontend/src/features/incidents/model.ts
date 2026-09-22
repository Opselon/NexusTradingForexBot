/**
 * Incidents: DTO contracts + VO mappers (pure).
 *
 * IncidentRecord.as_dict() (incidents/models.py) carries: incident_id,
 * detected_at, severity, category, status, first/last_seen_at, component,
 * operation, correlation_id, root_cause_status, root_cause, evidence[],
 * impact{}, affected_*, recovery_status, recommended_action, fingerprint,
 * repeated_count, related_bug_id, fix_commit, regression_test, is_regression,
 * timeline[], value_traces[], quarantine_entries[], recovery_plan, tags, notes.
 * Legacy list route answers {available, counts, incidents[]}.
 */

import { useI18n } from "@/stores/i18nStore";

export type Row = Record<string, unknown>;

export const str = (v: unknown): string | null => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null);
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
export const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});
export const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export interface IncidentDto extends Row {
  incident_id?: string;
  detected_at?: string;
  severity?: string;
  category?: string;
  status?: string;
  first_seen_at?: string;
  last_seen_at?: string;
  component?: string;
  operation?: string;
  correlation_id?: string;
  root_cause_status?: string;
  root_cause?: string;
  evidence?: Row[];
  impact?: Row;
  recovery_status?: string;
  recommended_action?: string;
  fingerprint?: string;
  repeated_count?: number;
  related_bug_id?: string;
  fix_commit?: string;
  regression_test?: string;
  is_regression?: boolean;
  resolved_without_evidence?: boolean;
  timeline?: Row[];
  value_traces?: Row[];
  quarantine_entries?: Row[];
  recovery_plan?: Row;
  tags?: string[];
  notes?: string[];
}

export interface IncidentCounts {
  total?: number;
  open?: number;
  critical?: number;
  high?: number;
  medium?: number;
  low?: number;
  recovered?: number;
  false_positive?: number;
}

export interface IncidentListDto {
  available?: boolean;
  counts?: IncidentCounts;
  incidents?: IncidentDto[];
  error?: { code?: string; message?: string };
}

export interface IncidentDetailDto {
  available?: boolean;
  incident?: IncidentDto;
  error?: string;
}

export interface IncidentReportDto {
  available?: boolean;
  incident_id?: string;
  json?: string;
  markdown?: string;
  error?: string;
}

export interface IncidentZipDto {
  available?: boolean;
  zip_path?: string;
  size_bytes?: number;
  note?: string;
  error?: string;
}

export interface DiagnosticsHealthDto {
  available?: boolean;
  counts?: IncidentCounts;
  recurring?: Row[];
  by_component?: Record<string, number>;
  worker?: Row & { display_state?: string; state?: string };
}

export interface DiagnosticsSearchDto {
  available?: boolean;
  query?: string;
  incidents?: IncidentDto[];
}

export interface DiagnosticsTraceDto {
  available?: boolean;
  query?: string;
  trace?: Row;
}

export interface DiagnosticsLineageDto {
  available?: boolean;
  field?: string;
  source?: string;
  hops?: Row[];
  why?: Row;
  why_closed?: Row;
  why_no_learning?: Row;
}

export interface DiagnosticsForensicsDto extends Row {
  available?: boolean;
  kind?: string;
}

export interface DiagnosticsReconcileDto {
  available?: boolean;
  audit_started?: string;
  audit_scope?: string[];
  findings?: Record<string, Row>;
  incidents_discovered?: number;
  incidents_reconciled?: number;
  note?: string;
  error?: { code?: string; message?: string };
}

/** List row -> compact VO; isOpen is backend status, never UI inference. */
export interface IncidentVo {
  id: string;
  severity: string;
  status: string;
  category: string;
  component: string;
  operation: string;
  detectedAt: string | null;
  lastSeenAt: string | null;
  repeatedCount: number;
  isRegression: boolean;
  relatedBugId: string | null;
  recommendedAction: string | null;
  open: boolean;
  raw: IncidentDto;
}

export function toIncidentVo(d: IncidentDto): IncidentVo {
  const status = (str(d.status) ?? "UNKNOWN").toUpperCase();
  return {
    id: str(d.incident_id) ?? "—",
    severity: (str(d.severity) ?? "UNKNOWN").toUpperCase(),
    status,
    category: str(d.category) ?? "—",
    component: str(d.component) ?? "—",
    operation: str(d.operation) ?? "—",
    detectedAt: str(d.detected_at),
    lastSeenAt: str(d.last_seen_at),
    repeatedCount: num(d.repeated_count) ?? 1,
    isRegression: bool(d.is_regression) ?? false,
    relatedBugId: str(d.related_bug_id),
    recommendedAction: str(d.recommended_action),
    open: ["OPEN", "DETECTED", "INVESTIGATING", "NEW"].includes(status),
    raw: d,
  };
}

/** Normalize a legacy command/error envelope into an ok/verdict pair.
 *  (Duplicated from the research model on purpose: features never import
 *  each other's model layer — the dependency rule forbids lateral coupling.) */
export function commandVerdict(res: unknown): { ok: boolean; message: string } {
  // Lazy store read (not a hook): keeps this pure module free of React while
  // still resolving verdict copy in the active language at call time.
  const t = useI18n.getState().t;
  const o = obj(res);
  const err = obj(o.error);
  if (err.code || err.message) {
    return {
      ok: false,
      message: str(err.message) ?? t("incidents.verdict.backend_error", "Backend error: {code}", { code: str(err.code) ?? "UNKNOWN" }),
    };
  }
  if (bool(o.available) === false) {
    return { ok: false, message: str(o.reason) ?? t("incidents.verdict.unavailable", "Backend subsystem unavailable.") };
  }
  return { ok: true, message: t("incidents.verdict.ok", "Backend accepted the command.") };
}
