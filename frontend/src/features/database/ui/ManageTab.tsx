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
  const manage = useDbManageStatus();
  const md = manage.data;
  const psycopg = psycopgState(md?.postgresql_driver_available);
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

  const errors = validatePgConfig(form);
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
        title="Provider configuration & migration (SQLite → PostgreSQL)"
        accent
        right={
          <>
            <FreshnessCaption
              fetchedAtMs={manage.dataUpdatedAt || null}
              intervalMs={30_000}
              note={manage.data?.password_set ? "pg password stored" : "pg password missing"}
              stale={false}
            />
            <button className="btn small" onClick={() => setValues(null)} disabled={!values}>
              revert edits
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
              <MetricCard label="active provider" value={<span className="inline-mono">{md.provider ?? "—"}</span>} sub="applies on next restart" />
              <MetricCard label="overall health" value={<StatusBadge status={md.overall ?? "UNKNOWN"} />} />
              <MetricCard
                label="pg password"
                value={md.password_set ? "SET" : "MISSING"}
                tone={md.password_set ? "pos" : "neg"}
                sub="OS SecretStore (DPAPI) reference only"
              />
              <MetricCard
                label="psycopg"
                value={psycopg.label.toUpperCase()}
                tone={psycopg.tone === "bad" ? "neg" : psycopg.tone === "good" ? "pos" : undefined}
                sub={psycopg.sub}
              />
            </div>

            <div className="dbc-section-title">connection</div>
            <div className="l3-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10 }}>
              {pgSpecs().map((spec) => {
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
                        placeholder={spec.secret ? "••• leave blank to keep the stored secret" : undefined}
                      />
                    )}
                  </FieldRow>
                );
              })}
            </div>

            <div className="dbc-section-title">actions</div>
            <div className="dbc-actions">
              <div className="dbc-actions-group">
                <span className="dbc-actions-label">connection</span>
                <button className="btn" disabled={saveCfg.isPending} onClick={() => void saveCfg.mutateAsync(form)}>
                  {saveCfg.isPending ? "saving…" : "Save config"}
                </button>
                <button className="btn" disabled={testConn.isPending} onClick={() => void testConn.mutateAsync(form)}>
                  {testConn.isPending ? "testing…" : "Test connection"}
                </button>
              </div>
              <div className="dbc-actions-group">
                <span className="dbc-actions-label">migration</span>
                <button className="btn" disabled={preview.isPending} onClick={() => void preview.mutateAsync(form)}>
                  {preview.isPending ? "previewing…" : "Preview migration"}
                </button>
                <button className="btn danger" disabled={migrate.isPending || invalid} onClick={() => setGuard("migrate")}>
                  Migrate…
                </button>
                <button className="btn danger" disabled={backup.isPending} onClick={() => setGuard("backup")}>
                  Backup…
                </button>
                <button className="btn primary" disabled={validate.isPending} onClick={() => void validate.mutateAsync()}>
                  {validate.isPending ? "validating…" : "Validate last migration"}
                </button>
              </div>
              <div className="dbc-actions-group">
                <span className="dbc-actions-label">provider</span>
                {(md.supported_providers ?? [])
                  .filter((p) => p !== md.provider)
                  .map((p) => (
                    <button key={p} className="btn ghost" disabled={switchTo.isPending} onClick={() => void switchTo.mutateAsync(p)}>
                      switch → {p}
                    </button>
                  ))}
              </div>
            </div>

            {invalid && (
              <div className="dbc-note bad" style={{ marginTop: 10 }}>
                invalid payload blocked: {Object.values(errors).flat().join(" · ")}
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
                  dry-run preview ({(preview.data.body.preview.tables ?? []).length} tables ·{" "}
                  {formatBytes(preview.data.body.preview.estimated_volume_bytes)})
                </div>
                <details className="dbc-raw">
                  <summary>preview detail</summary>
                  <div className="dbc-raw-body">
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
                      ? "job finished"
                      : `running · ${progress.data?.current_table || "preparing"} · ${progress.data?.rows_copied ?? 0}/${progress.data?.total_rows ?? "?"} rows`}
                  </span>
                  <span>{pct}%</span>
                </div>
                <div className="track">
                  <i style={{ width: `${pct}%` }} />
                </div>
                <div className="dbc-row between">
                  <span className="dbc-sub">numbers stream from /api/db/manage/progress</span>
                  <span>
                    {!migrating && progress.data && !jobDone && (
                      <button className="btn small" onClick={() => setMigrating(true)}>
                        resume progress polling
                      </button>
                    )}{" "}
                    {jobDone && (
                      <button className="btn small ghost" onClick={() => setMigrating(false)}>
                        dismiss
                      </button>
                    )}
                  </span>
                </div>
              </div>
            )}

            {lastReport && (
              <div style={{ marginTop: 10 }}>
                <div className="dbc-section-title">migration report</div>
                <KeyValueList
                  rows={[
                    ["status", <StatusBadge key="s" status={String(lastReport.status ?? "UNKNOWN")} />],
                    ["tables", String(lastReport.tables_migrated ?? "—")],
                    ["rows", `${String(lastReport.rows_migrated ?? "—")} ok / ${String(lastReport.rows_failed ?? 0)} failed`],
                    ["duration", `${String(lastReport.duration_ms ?? "—")} ms`],
                    ["validation", String(lastReport.validation ?? "NOT_RUN")],
                    ["provider switch", lastReport.provider_switch_ready ? "READY" : "not ready"],
                  ]}
                />
                {lastReport.errors && lastReport.errors.length > 0 && (
                  <div className="dbc-note bad">errors: {lastReport.errors.slice(0, 5).join(" · ")}</div>
                )}
                {lastReport.warnings && lastReport.warnings.length > 0 && (
                  <div className="dbc-note warn">warnings: {lastReport.warnings.slice(0, 5).join(" · ")}</div>
                )}
              </div>
            )}
          </>
        )}
      </Panel>

      {guard && (
        <TypedConfirmModal
          title={guard === "backup" ? "SQLite backup (WAL-consistent)" : "Run SQLite → PostgreSQL migration"}
          word={guard === "backup" ? "BACKUP" : "MIGRATE"}
          confirmLabel={guard === "backup" ? "Run backup" : "Start migration"}
          busy={backup.isPending || migrate.isPending}
          onCancel={() => setGuard(null)}
          onConfirm={() => void guardRun()}
          body={
            guard === "backup" ? (
              <>
                Writes <span className="inline-mono">artifacts/backups/audit_backup_&lt;ts&gt;.db</span> using the streaming SQLite
                backup API (consistent snapshot; the engine keeps running). The backend result decides the verdict.
              </>
            ) : (
              <>
                Streams every audit table into PostgreSQL in resumable batches (confirm=true required by the backend gate), validates
                checksums and row counts, and switches the active provider ONLY when validation passes. Rows are copied, never moved:
                the SQLite source is untouched.
              </>
            )
          }
        />
      )}
    </div>
  );
}
