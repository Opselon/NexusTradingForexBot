/**
 * Manage tab — PostgreSQL configuration, the guarded migration workflow and
 * the live progress/report readout.
 *
 * Guardrails (unchanged semantics, restyled shell):
 *  - backup and migrate run ONLY through a typed-confirmation modal (the
 *    operator must type BACKUP / MIGRATE), and the payload always carries
 *    confirm:true exactly as the backend gate requires;
 *  - while a job runs the page polls /api/db/manage/progress (2s) and renders
 *    the bar from backend numbers only;
 *  - the PG password never round-trips: blank means "keep stored";
 *  - invalid config (type/range/mismatched confirmation) is blocked client
 *    side before any POST.
 */

import { useMemo, useState } from "react";
import { MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import {
  FieldRow,
  FreshnessCaption,
  JsonView,
  KeyValueList,
  NumberField,
  ResultStrip,
  SelectField,
  TextField,
  TypedConfirmModal,
} from "@/features/config/ui/kit";
import { firstError, type FieldValues } from "@/features/config/validation";
import {
  useDbBackup,
  useDbManageStatus,
  useDbValidate,
  useLastReport,
  useMigrationPreview,
  useMigrationProgress,
  useSaveDbConfig,
  useStartMigration,
  useSwitchProvider,
  useTestDbConnection,
} from "../useCases";
import { PG_SSL_MODES, baselineFromStatus, formatBytes, pgSpecs, validatePgConfig } from "../model";
import { providerHints, psycopgState } from "../uiLogic";
import { useI18n } from "@/stores/i18nStore";

type StripResult = { ok: boolean; message: string; requestId: string | null };

function strip(o: StripResult | undefined) {
  if (!o) return null;
  return {
    running: false,
    lastResult: o.ok,
    lastMessage: o.requestId ? `${o.message} · request_id: ${o.requestId}` : o.message,
  };
}

export function ManageTab() {
  const t = useI18n((s) => s.t);
  const manage = useDbManageStatus();
  const md = manage.data;
  const psycopg = psycopgState(md?.postgresql_driver_available, t);
  const [values, setValues] = useState<FieldValues | null>(null);
  const baseline = useMemo(() => baselineFromStatus(manage.data?.postgres), [manage.data]);
  const form = values ?? baseline;
  const [migrating, setMigrating] = useState(false);
  const [guard, setGuard] = useState<null | "backup" | "migrate">(null);

  const saveCfg = useSaveDbConfig();
  const testConn = useTestDbConnection();
  const preview = useMigrationPreview();
  const migrate = useStartMigration();
  const backup = useDbBackup();
  const validate = useDbValidate();
  const switchTo = useSwitchProvider();
  const progress = useMigrationProgress(migrating);
  const report = useLastReport(migrating || (progress.data?.done ?? false));

  const errors = validatePgConfig(form, t);
  const invalid = Object.values(errors).some((m) => m.length > 0);
  const set = (k: string, v: string | boolean) => setValues((prev) => ({ ...(prev ?? baseline), [k]: v }));

  const pct = Math.round(Math.max(0, Math.min(1, progress.data?.progress ?? 0)) * 100);
  const jobDone = progress.data?.done === true;
  const hints = providerHints(manage.data);

  const guardRun = async () => {
    if (!guard) return;
    if (guard === "backup") {
      const o = await backup.mutateAsync();
      if (o.ok) setGuard(null);
      return;
    }
    const o = await migrate.mutateAsync({ values: form, resume: true, batchSize: 2000 });
    if (o.ok) {
      setMigrating(true);
      setGuard(null);
    }
  };

  const lastReport = report.data?.report ?? progress.data?.report ?? null;

  return (
    <div className="dbc-stack">
      <Panel
        title={t("database.manage.panel_provider_title", "Provider configuration & migration (SQLite → PostgreSQL)")}
        accent
        right={
          <>
            <FreshnessCaption
              fetchedAtMs={manage.dataUpdatedAt || null}
              intervalMs={30_000}
              note={
                manage.data?.password_set
                  ? t("database.manage.pw_stored", "pg password stored")
                  : t("database.manage.pw_missing", "pg password missing")
              }
              stale={false}
            />
            <button className="btn small" onClick={() => setValues(null)} disabled={!values}>
              {t("database.manage.revert", "revert edits")}
            </button>
          </>
        }
      >
        {!md ? (
          <Skeleton count={3} />
        ) : (
          <>
            {hints.length > 0 && (
              <div className="dbc-band" style={{ marginBottom: 12 }}>
                {hints.map((h, i) => (
                  <div key={i} className={`dbc-band-item ${h.toLowerCase().includes("not installed") ? "bad" : ""}`}>
                    <span className="g" aria-hidden="true">
                      ⚠
                    </span>
                    <span>{h}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="dbc-kpis" style={{ marginBottom: 12 }}>
              <MetricCard
                label={t("database.status.active_provider", "active provider")}
                value={<span className="inline-mono">{md.provider ?? "—"}</span>}
                sub={t("database.status.restart_sub", "applies on next restart")}
              />
              <MetricCard
                label={t("database.status.overall_health", "overall health")}
                value={<StatusBadge status={md.overall ?? "UNKNOWN"} />}
              />
              <MetricCard
                label={t("database.status.pg_password", "pg password")}
                value={md.password_set ? t("database.status.pw_set", "SET") : t("database.status.pw_missing", "MISSING")}
                tone={md.password_set ? "pos" : "neg"}
                sub={t("database.status.pg_password_sub", "OS SecretStore (DPAPI) reference only")}
              />
              <MetricCard
                label="psycopg"
                value={psycopg.label.toUpperCase()}
                tone={psycopg.tone === "bad" ? "neg" : psycopg.tone === "good" ? "pos" : undefined}
                sub={psycopg.sub}
              />
            </div>

            <div className="dbc-section-title">{t("database.manage.section_connection", "connection")}</div>
            <div className="l3-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10 }}>
              {pgSpecs(t).map((spec) => {
                const err = firstError(errors, spec.key);
                const v = form[spec.key];
                return (
                  <FieldRow
                    key={spec.key}
                    label={spec.label ?? spec.key}
                    hint={spec.hint ?? spec.key}
                    error={err}
                    dirty={String(v ?? "") !== String(baseline[spec.key] ?? "")}
                  >
                    {spec.kind === "integer" ? (
                      <NumberField value={String(v ?? "")} onChange={(x) => set(spec.key, x)} error={err} step="1" />
                    ) : spec.kind === "enum" ? (
                      <SelectField value={String(v ?? "")} onChange={(x) => set(spec.key, x)} options={PG_SSL_MODES} error={err} label={spec.key} />
                    ) : (
                      <TextField
                        value={String(v ?? "")}
                        onChange={(x) => set(spec.key, x)}
                        error={err}
                        placeholder={spec.secret ? t("database.pg.keep_secret_ph", "••• leave blank to keep the stored secret") : undefined}
                      />
                    )}
                  </FieldRow>
                );
              })}
            </div>

            <div className="dbc-section-title">{t("database.manage.section_actions", "actions")}</div>
            <div className="dbc-actions">
              <div className="dbc-actions-group">
                <span className="dbc-actions-label">{t("database.manage.group_connection", "connection")}</span>
                <button className="btn" disabled={saveCfg.isPending} onClick={() => void saveCfg.mutateAsync(form)}>
                  {saveCfg.isPending
                    ? t("database.manage.saving", "saving…")
                    : t("database.manage.save", "Save config")}
                </button>
                <button className="btn" disabled={testConn.isPending} onClick={() => void testConn.mutateAsync(form)}>
                  {testConn.isPending ? t("database.manage.testing", "testing…") : t("database.manage.test", "Test connection")}
                </button>
              </div>
              <div className="dbc-actions-group">
                <span className="dbc-actions-label">{t("database.manage.group_migration", "migration")}</span>
                <button className="btn" disabled={preview.isPending} onClick={() => void preview.mutateAsync(form)}>
                  {preview.isPending
                    ? t("database.manage.previewing", "previewing…")
                    : t("database.manage.preview", "Preview migration")}
                </button>
                <button className="btn danger" disabled={migrate.isPending || invalid} onClick={() => setGuard("migrate")}>
                  {t("database.manage.migrate", "Migrate…")}
                </button>
                <button className="btn danger" disabled={backup.isPending} onClick={() => setGuard("backup")}>
                  {t("database.manage.backup", "Backup…")}
                </button>
                <button className="btn primary" disabled={validate.isPending} onClick={() => void validate.mutateAsync()}>
                  {validate.isPending
                    ? t("database.manage.validating", "validating…")
                    : t("database.manage.validate", "Validate last migration")}
                </button>
              </div>
              <div className="dbc-actions-group">
                <span className="dbc-actions-label">{t("database.manage.group_provider", "provider")}</span>
                {(md.supported_providers ?? [])
                  .filter((p) => p !== md.provider)
                  .map((p) => (
                    <button key={p} className="btn ghost" disabled={switchTo.isPending} onClick={() => void switchTo.mutateAsync(p)}>
                      {t("database.manage.switch_to", "switch →")} {p}
                    </button>
                  ))}
              </div>
            </div>

            {invalid && (
              <div className="dbc-note bad" style={{ marginTop: 10 }}>
                {t("database.pg.invalid_blocked", "invalid payload blocked: {errors}", {
                  errors: Object.values(errors).flat().join(" · "),
                })}
              </div>
            )}

            <div className="dbc-stack" style={{ gap: 6, marginTop: 10 }}>
              <ResultStrip result={saveCfg.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(saveCfg.data)} />
              <ResultStrip result={testConn.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(testConn.data)} />
              <ResultStrip result={migrate.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(migrate.data)} />
              <ResultStrip result={backup.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(backup.data)} />
              <ResultStrip result={validate.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(validate.data)} />
              <ResultStrip result={switchTo.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(switchTo.data)} />
            </div>

            {preview.data?.body?.preview && (
              <div style={{ marginTop: 10 }}>
                <div className="dbc-section-title">
                  {t("database.manage.preview_title", "dry-run preview ({tables} tables · {size})", {
                    tables: (preview.data.body.preview.tables ?? []).length,
                    size: formatBytes(preview.data.body.preview.estimated_volume_bytes),
                  })}
                </div>
                <details className="dbc-raw">
                  <summary>{t("database.manage.preview_detail", "preview detail")}</summary>
                  <div tabIndex={0} className="dbc-raw-body">
                    <JsonView value={preview.data.body.preview} name="preview" />
                  </div>
                </details>
                <ResultStrip result={strip(preview.data)} />
              </div>
            )}

            {(migrating || !jobDone) && (progress.data || migrate.data) && (
              <div className="dbc-progress" style={{ marginTop: 12 }} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <div className="lab">
                  <span>
                    {jobDone
                      ? t("database.manage.job_finished", "job finished")
                      : t("database.manage.running", "running · {table} · {copied}/{total} rows", {
                          table: progress.data?.current_table || t("database.manage.preparing", "preparing"),
                          copied: progress.data?.rows_copied ?? 0,
                          total: progress.data?.total_rows ?? "?",
                        })}
                  </span>
                  <span>{pct}%</span>
                </div>
                <div className="track">
                  <i style={{ width: `${pct}%` }} />
                </div>
                <div className="dbc-row between">
                  <span className="dbc-sub">
                    {t("database.manage.progress_stream", "numbers stream from /api/db/manage/progress")}
                  </span>
                  <span>
                    {!migrating && progress.data && !jobDone && (
                      <button className="btn small" onClick={() => setMigrating(true)}>
                        {t("database.manage.resume", "resume progress polling")}
                      </button>
                    )}{" "}
                    {jobDone && (
                      <button className="btn small ghost" onClick={() => setMigrating(false)}>
                        {t("database.manage.dismiss", "dismiss")}
                      </button>
                    )}
                  </span>
                </div>
              </div>
            )}

            {lastReport && (
              <div style={{ marginTop: 10 }}>
                <div className="dbc-section-title">{t("database.manage.report", "migration report")}</div>
                <KeyValueList
                  rows={[
                    [
                      t("database.kv.status", "status"),
                      <StatusBadge key="s" status={String(lastReport.status ?? "UNKNOWN")} />,
                    ],
                    [t("database.kv.tables", "tables"), String(lastReport.tables_migrated ?? "—")],
                    [
                      t("database.kv.rows", "rows"),
                      t("database.kv.rows_value", "{ok} ok / {failed} failed", {
                        ok: String(lastReport.rows_migrated ?? "—"),
                        failed: String(lastReport.rows_failed ?? 0),
                      }),
                    ],
                    [t("database.kv.duration", "duration"), `${String(lastReport.duration_ms ?? "—")} ms`],
                    [t("database.kv.validation", "validation"), String(lastReport.validation ?? "NOT_RUN")],
                    [
                      t("database.kv.provider_switch", "provider switch"),
                      lastReport.provider_switch_ready
                        ? t("database.kv.ready", "READY")
                        : t("database.kv.not_ready", "not ready"),
                    ],
                  ]}
                />
                {lastReport.errors && lastReport.errors.length > 0 && (
                  <div className="dbc-note bad">
                    {t("database.manage.errors", "errors: {errors}", { errors: lastReport.errors.slice(0, 5).join(" · ") })}
                  </div>
                )}
                {lastReport.warnings && lastReport.warnings.length > 0 && (
                  <div className="dbc-note warn">
                    {t("database.manage.warnings", "warnings: {warnings}", {
                      warnings: lastReport.warnings.slice(0, 5).join(" · "),
                    })}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </Panel>

      {guard && (
        <TypedConfirmModal
          title={
            guard === "backup"
              ? t("database.confirm.backup_title", "SQLite backup (WAL-consistent)")
              : t("database.confirm.migrate_title", "Run SQLite → PostgreSQL migration")
          }
          word={guard === "backup" ? "BACKUP" : "MIGRATE"}
          confirmLabel={
            guard === "backup"
              ? t("database.confirm.run_backup", "Run backup")
              : t("database.confirm.start_migration", "Start migration")
          }
          busy={backup.isPending || migrate.isPending}
          onCancel={() => setGuard(null)}
          onConfirm={() => void guardRun()}
          body={
            guard === "backup" ? (
              <>
                {t("database.confirm.backup_body", "Writes {path} using the streaming SQLite backup API (consistent snapshot; the engine keeps running). The backend result decides the verdict.", {
                  path: "artifacts/backups/audit_backup_<ts>.db",
                }).split("artifacts/backups/audit_backup_<ts>.db")[0]}
                <span className="inline-mono">artifacts/backups/audit_backup_&lt;ts&gt;.db</span>
                {t("database.confirm.backup_body", "Writes {path} using the streaming SQLite backup API (consistent snapshot; the engine keeps running). The backend result decides the verdict.", {
                  path: "artifacts/backups/audit_backup_<ts>.db",
                }).split("artifacts/backups/audit_backup_<ts>.db")[1]}
              </>
            ) : (
              <>
                {t(
                  "database.confirm.migrate_body",
                  "Streams every audit table into PostgreSQL in resumable batches (confirm=true required by the backend gate), validates checksums and row counts, and switches the active provider ONLY when validation passes. Rows are copied, never moved: the SQLite source is untouched.",
                )}
              </>
            )
          }
        />
      )}
    </div>
  );
}
