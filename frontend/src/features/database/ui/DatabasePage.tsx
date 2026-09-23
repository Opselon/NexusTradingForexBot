/**
 * Database console (parity: Web/app.js DATABASE MANAGEMENT panel + the
 * SSMS-style explorer built on /api/db/console/*, plus /api/db/status and
 * /api/db/hygiene).
 *
 * Guardrails enforced here:
 *  - backup and migrate run ONLY through a typed-confirmation modal
 *    (MIGRATE / BACKUP — the operator must type the word);
 *  - the migration payload always carries confirm:true exactly as the backend
 *    gate requires, and while the job runs the page polls
 *    /api/db/manage/progress (2s) and renders the live bar from backend
 *    numbers only;
 *  - the PG password never round-trips: blank fields mean "keep stored", and
 *    the backend reports password_set status only;
 *  - invalid config (type/range/mismatched confirmation) is blocked client
 *    side before any POST.
 */

import { memo, useEffect, useMemo, useState } from "react";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import {
  FieldRow,
  FreshnessCaption,
  JsonView,
  KeyValueList,
  MonoValue,
  NumberField,
  PollControl,
  QuerySection,
  ResultStrip,
  SelectField,
  TextField,
  TypedConfirmModal,
  usePolling,
} from "@/features/config/ui/kit";
import "@/features/config/ui/kit.css";
import { firstError, type FieldValues } from "@/features/config/validation";
import type { ShellPageProps } from "@/app/featureModule";
import {
  useApiKeys,
  useConsoleRows,
  useConsoleTables,
  useDbBackup,
  useDbHygiene,
  useDbManageStatus,
  useDbStatus,
  useConsoleDatabases,
  useDbValidate,
  useLastReport,
  useMigrationPreview,
  useMigrationProgress,
  useRefreshConsole,
  useRunQuickSql,
  useSaveApiKey,
  useSaveDbConfig,
  useSqlQuery,
  useStartMigration,
  useSwitchProvider,
  useTestDbConnection,
  useDeleteApiKey,
} from "../useCases";
import { PG_SSL_MODES, baselineFromStatus, dbConsoleName, domainRows, formatBytes, pgSpecs, validatePgConfig } from "../model";
import type { DbStatus, DbHygiene, DbManageStatus, ConsoleDatabase } from "../api";

/* ------------------------------------------------------------------ */
/* Status + hygiene panel                                              */
/* ------------------------------------------------------------------ */

/** Memoized raw result grid (explorer rows, paged 100 + SQL results up to 500).
 *  columns/rows keep their query-data identity, so typing in the SQL textarea
 *  and the shell's 1s tick no longer re-render up to 500 x N cells. */
const GridTable = memo(function GridTable({ columns, rows }: { columns: string[]; rows: Array<Record<string, unknown>> }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th scope="col" key={c}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {columns.map((c) => (
              <td key={c} className="l3-cell" title={String(r[c] ?? "")}>
                {r[c] === null || r[c] === undefined ? "—" : String(r[c])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
});

function StatusPanel() {
  const poll = usePolling(30_000);
  const status = useDbStatus(poll.paused);
  const manage = useDbManageStatus(poll.paused);
  const hygiene = useDbHygiene(poll.paused);
  return (
    <>
      <QuerySection<DbManageStatus>
        title="Persistence provider (db/manage/status)"
        accent
        query={manage}
        skeletonRows={3}
        emptyMessage="Provider state unavailable."
        right={
          <>
            <FreshnessCaption fetchedAtMs={manage.dataUpdatedAt || null} intervalMs={30_000} stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={manage.isFetching} />
          </>
        }
      >
        {(data) => (
          <div>
            <div className="l3-toolbar">
              <MetricCard label="active provider" value={<span className="inline-mono">{data.provider ?? "—"}</span>} sub="switch applies next restart" />
              <MetricCard label="overall health" value={<StatusBadge status={data.overall ?? "UNKNOWN"} />} />
              <MetricCard label="pg password" value={data.password_set ? "SET" : "MISSING"} tone={data.password_set ? "pos" : "neg"} sub="OS SecretStore (DPAPI) reference only" />
            </div>
            <div className="l3-db-obj">
              {(data.supported_providers ?? []).map((p) => (
                <span key={p} className={`l3-db-chip ${p === data.provider ? "active" : ""}`}>{p}</span>
              ))}
            </div>
            <div className="section-title" style={{ marginTop: 10 }}>domain health</div>
            <JsonView value={data.domains ?? null} name="domains" depth={1} />
          </div>
        )}
      </QuerySection>

      <QuerySection<DbStatus>
        title="Schema & migration state (/api/db/status)"
        query={status}
        skeletonRows={3}
        emptyMessage="No domains reported."
        right={<FreshnessCaption fetchedAtMs={status.dataUpdatedAt || null} intervalMs={30_000} note="read-only; the API never runs migrations" stale={poll.paused} />}
      >
        {(data) => {
          const rows = domainRows(data.databases);
          if (rows.length === 0) return <EmptyState message="No database domains reported." />;
          return (
            <DataTable headers={[{ label: "DOMAIN" }, { label: "SCHEMA", num: true }, { label: "EXPECTED", num: true }, { label: "STATE" }, { label: "PENDING", num: true }, { label: "INTEGRITY" }]}>
              {rows.map((r) => (
                <tr key={r.domain}>
                  <td className="inline-mono">{r.domain}</td>
                  <td className="num">{r.schema}</td>
                  <td className="num">{r.expected}</td>
                  <td><StatusBadge status={r.state} />{r.tamper && <span className="badge bad l3-inline-badge">TAMPER</span>}</td>
                  <td className="num">{r.pending ?? "—"}</td>
                  <td>{r.integrity}</td>
                </tr>
              ))}
            </DataTable>
          );
        }}
      </QuerySection>

      <QuerySection<DbHygiene>
        title="Hygiene worker (/api/db/hygiene)"
        query={hygiene}
        skeletonRows={3}
        emptyMessage="Hygiene worker state unavailable."
        right={<FreshnessCaption fetchedAtMs={hygiene.dataUpdatedAt || null} intervalMs={60_000} stale={poll.paused} />}
      >
        {(data) => (
          <div className="l3-split">
            <div>
              <div className="section-title">worker status</div>
              <JsonView value={data.status ?? null} name="status" depth={1} />
            </div>
            <div>
              <div className="section-title">plans / quarantine</div>
              <div tabIndex={0} className="l3-scroll sm">
                <JsonView value={{ plans: data.plans, runtime: data.runtime, quarantine: data.quarantine }} name="hygiene" depth={1} />
              </div>
            </div>
          </div>
        )}
      </QuerySection>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Provider config + guarded ops + progress                            */
/* ------------------------------------------------------------------ */

function ManagementPanel() {
  const manage = useDbManageStatus();
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
  const set = (k: string, v: string | boolean) => setValues((prev) => ({ ...(prev ?? baseline), [k]: v }));

  const pct = Math.round(Math.max(0, Math.min(1, progress.data?.progress ?? 0)) * 100);
  const jobDone = progress.data?.done === true;

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
    <Panel
      title="Provider configuration & migration (SQLite → PostgreSQL)"
      accent
      right={
        <>
          <FreshnessCaption fetchedAtMs={manage.dataUpdatedAt || null} intervalMs={30_000} note={manage.data?.password_set ? "pg password stored" : "pg password missing"} stale={!manage.data?.password_set} />
          <button className="btn small" onClick={() => setValues(null)} disabled={!values}>revert edits</button>
        </>
      }
    >
      {manage.isPending ? (
        <Skeleton count={3} />
      ) : (
        <>
          <div className="l3-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10 }}>
            {pgSpecs().map((spec) => {
              const err = firstError(errors, spec.key);
              const v = form[spec.key];
              return (
                <FieldRow key={spec.key} label={spec.label ?? spec.key} hint={spec.hint ?? spec.key} error={err} dirty={String(v ?? "") !== String(baseline[spec.key] ?? "")}>
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
          <div className="l3-toolbar" style={{ justifyContent: "flex-end", marginTop: 10 }}>
            {Object.values(errors).some((m) => m.length > 0) && (
              <span className="l3-note bad" style={{ marginRight: "auto" }}>
                invalid payload blocked: {Object.values(errors).flat().join(" · ")}
              </span>
            )}
            <button className="btn" disabled={saveCfg.isPending} onClick={() => void saveCfg.mutateAsync(form)}>
              {saveCfg.isPending ? "saving…" : "Save config"}
            </button>
            <button className="btn" disabled={testConn.isPending} onClick={() => void testConn.mutateAsync(form)}>
              {testConn.isPending ? "testing…" : "Test connection"}
            </button>
            <button className="btn" disabled={preview.isPending} onClick={() => void preview.mutateAsync(form)}>
              {preview.isPending ? "previewing…" : "Preview migration"}
            </button>
            <button
              className="btn danger"
              disabled={migrate.isPending || Object.values(errors).some((m) => m.length > 0)}
              onClick={() => {
                setGuard("migrate");
              }}
            >
              Migrate…
            </button>
            <button className="btn danger" disabled={backup.isPending} onClick={() => setGuard("backup")}>
              Backup…
            </button>
            <button className="btn primary" disabled={validate.isPending} onClick={() => void validate.mutateAsync()}>
              {validate.isPending ? "validating…" : "Validate last migration"}
            </button>
            {(manage.data?.supported_providers ?? [])
              .filter((p) => p !== manage.data?.provider)
              .map((p) => (
                <button key={p} className="btn ghost" disabled={switchTo.isPending} onClick={() => void switchTo.mutateAsync(p)}>
                  switch → {p}
                </button>
              ))}
          </div>
          <ResultStrip result={saveCfg.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(saveCfg.data)} />
          <ResultStrip result={testConn.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(testConn.data)} />
          <ResultStrip result={migrate.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(migrate.data)} />
          <ResultStrip result={backup.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(backup.data)} />
          <ResultStrip result={validate.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(validate.data)} />
          <ResultStrip result={switchTo.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(switchTo.data)} />

          {preview.data?.body?.preview && (
            <div style={{ marginTop: 10 }}>
              <div className="section-title">dry-run preview ({(preview.data.body.preview.tables ?? []).length} tables · {formatBytes(preview.data.body.preview.estimated_volume_bytes)})</div>
              <div tabIndex={0} className="l3-scroll sm"><JsonView value={preview.data.body.preview} name="preview" /></div>
              <ResultStrip result={strip(preview.data)} />
            </div>
          )}

          {(migrating || !jobDone) && (progress.data || migrate.data) && (
            <div className="l3-progress" style={{ marginTop: 12 }} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
              <div className="lab">
                <span>
                  {jobDone ? "job finished" : `running · ${progress.data?.current_table || "preparing"} · ${progress.data?.rows_copied ?? 0}/${progress.data?.total_rows ?? "?"} rows`}
                </span>
                <span>{pct}%</span>
              </div>
              <div className="track"><i style={{ width: `${pct}%` }} /></div>
              {!migrating && progress.data && !jobDone && (
                <button className="btn small" onClick={() => setMigrating(true)}>resume progress polling</button>
              )}
              {jobDone && <button className="btn small ghost" onClick={() => setMigrating(false)}>dismiss</button>}
            </div>
          )}
          {lastReport && (
            <div style={{ marginTop: 10 }}>
              <div className="section-title">migration report</div>
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
              {lastReport.errors && lastReport.errors.length > 0 && <div className="l3-note bad">errors: {lastReport.errors.slice(0, 5).join(" · ")}</div>}
              {lastReport.warnings && lastReport.warnings.length > 0 && <div className="l3-note warn">warnings: {lastReport.warnings.slice(0, 5).join(" · ")}</div>}
            </div>
          )}
        </>
      )}

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
                Writes <span className="inline-mono">artifacts/backups/audit_backup_&lt;ts&gt;.db</span> using the streaming
                SQLite backup API (consistent snapshot; the engine keeps running). The backend result decides the verdict.
              </>
            ) : (
              <>
                Streams every audit table into PostgreSQL in resumable batches (confirm=true required by the backend gate),
                validates checksums and row counts, and switches the active provider ONLY when validation passes. Rows are
                copied, never moved: the SQLite source is untouched.
              </>
            )
          }
        />
      )}
    </Panel>
  );
}

function strip(o: { ok: boolean; message: string; requestId: string | null } | undefined) {
  if (!o) return null;
  return { running: false, lastResult: o.ok, lastMessage: o.requestId ? `${o.message} · request_id: ${o.requestId}` : o.message };
}

/* ------------------------------------------------------------------ */
/* Explorer + SQL console + API keys                                   */
/* ------------------------------------------------------------------ */

const QUICK_KINDS = ["top100", "count", "recent", "schema", "integrity"] as const;

function ExplorerPanel() {
  const poll = usePolling(60_000);
  const dbs = useConsoleDatabases(poll.paused);
  const refresh = useRefreshConsole();
  const [db, setDb] = useState<string | null>(null);
  // Default-select the first discovered database once (operator can switch).
  useEffect(() => {
    const list = dbs.data?.databases ?? [];
    const first = list[0]?.name;
    if (first && !list.some((d: ConsoleDatabase) => d.name === db)) setDb(first);
  }, [dbs.data, db]);
  const [table, setTable] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const tables = useConsoleTables(db);
  const rows = useConsoleRows(db, table, 100, page * 100);
  const quick = useRunQuickSql();
  const sql = useSqlQuery();
  const [query, setQuery] = useState("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name");
  const [result, setResult] = useState<{ columns: string[]; rows: Array<Record<string, unknown>>; truncated?: boolean; note: string; ok: boolean } | null>(null);
  const keys = useApiKeys(poll.paused);
  const saveKey = useSaveApiKey();
  const delKey = useDeleteApiKey();
  const [newKey, setNewKey] = useState({ name: "", value: "" });

  const runQuery = async () => {
    if (!db) return;
    const o = await sql.mutateAsync({ database: db, sql: query });
    setResult({ columns: o.columns ?? [], rows: o.rows ?? [], truncated: o.truncated, note: o.message, ok: o.ok });
  };

  const runQuick = async (kind: string) => {
    if (!db || !table) return;
    const o = await quick.mutateAsync({ database: db, table, kind });
    setResult({ columns: o.columns ?? [], rows: o.rows ?? [], note: o.message, ok: o.ok });
  };

  return (
    <>
      <QuerySection<{ success: boolean; databases: ConsoleDatabase[] }>
        title="Database console — explorer (provider-abstracted)"
        accent
        query={dbs}
        skeletonRows={3}
        emptyMessage="No databases discovered."
        right={
          <>
            <FreshnessCaption fetchedAtMs={dbs.dataUpdatedAt || null} intervalMs={60_000} stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={60_000} busy={dbs.isFetching} />
            <button className="btn small" onClick={() => void refresh.mutateAsync()} disabled={refresh.isPending}>re-scan</button>
          </>
        }
      >
        {(data) => {
          return (
            <div>
              <div className="l3-db-obj" style={{ marginBottom: 10 }}>
                {(data.databases ?? []).map((d) => (
                  <button key={d.name} className={`l3-db-chip ${d.name === db ? "active" : ""}`} onClick={() => { setDb(d.name); setTable(null); setPage(0); }} title={`${d.provider} · ${dbConsoleName(d)} · ${formatBytes(d.size_bytes)}`}>
                    {d.name} <span className="faint">{d.provider}</span> <MonoValue value={formatBytes(d.size_bytes)} /> <StatusBadge status={String(d.status)} />
                  </button>
                ))}
              </div>
              <div className="l3-split">
                <div>
                  <div className="section-title">tables · {db ?? "pick a database above"}</div>
                  {tables.isPending ? (
                    db ? (
                      <Skeleton count={3} />
                    ) : (
                      <EmptyState message="No database selected." hint="choose a database chip above to list its tables" />
                    )
                  ) : tables.isError ? (
                    <ErrorState
                      message={tables.error instanceof Error ? tables.error.message : "tables listing failed"}
                      requestId={(tables.error as { requestId?: string } | null)?.requestId ?? null}
                      onRetry={() => void tables.refetch()}
                    />
                  ) : tables.data && !tables.data.success ? (
                    <ErrorState message={tables.data.error ?? "tables listing refused"} onRetry={() => void tables.refetch()} />
                  ) : null}
                  <div className="l3-db-obj l3-scroll sm">
                    {(tables.data?.tables ?? []).map((t) => (
                      <button key={t.name} className={`l3-db-chip ${t.name === table ? "active" : ""}`} onClick={() => { setTable(t.name); setPage(0); }}>
                        {t.name} <span className="faint">{t.rows === null ? "?" : t.rows.toLocaleString("en-US")}</span>
                      </button>
                    ))}
                    {tables.data?.success && tables.data?.tables?.length === 0 && (
                      <EmptyState message="No tables in this database." hint="unreachable database or empty schema — check the provider state tab" />
                    )}
                  </div>
                  {table && (
                    <>
                      <div className="l3-toolbar" style={{ marginTop: 10 }}>
                        {QUICK_KINDS.map((k) => (
                          <button key={k} className="btn small ghost" onClick={() => void runQuick(k)} disabled={quick.isPending}>{k}</button>
                        ))}
                        <span className="timestamp-note" style={{ marginInlineStart: "auto" }}>
                          rows {page * 100 + 1}–{page * 100 + (rows.data?.rows?.length ?? 0)} · page {page + 1}
                        </span>
                        <button className="btn small" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>‹ prev</button>
                        <button className="btn small" disabled={(rows.data?.rows?.length ?? 0) < 100} onClick={() => setPage((p) => p + 1)}>next ›</button>
                      </div>
                      {rows.isPending ? (
                        <Skeleton count={4} />
                      ) : rows.isError ? (
                        <ErrorState
                          message={rows.error instanceof Error ? rows.error.message : "row read failed"}
                          requestId={(rows.error as { requestId?: string } | null)?.requestId ?? null}
                          onRetry={() => void rows.refetch()}
                        />
                      ) : rows.data && !rows.data.success ? (
                        <ErrorState message={rows.data.error ?? "row read refused"} onRetry={() => void rows.refetch()} />
                      ) : (rows.data?.columns ?? []).length === 0 ? (
                        <EmptyState message="No columns returned." hint="the table reported no columns for this page" />
                      ) : (
                        <div tabIndex={0} className="l3-scroll" role="region" aria-label={`rows of ${table ?? "table"}`}>
                          <GridTable columns={rows.data?.columns ?? []} rows={rows.data?.rows ?? []} />
                        </div>
                      )}
                    </>
                  )}
                </div>
                <div>
                  <div className="section-title">SQL console (read-only: SELECT / EXPLAIN / WITH / PRAGMA / VALUES)</div>
                  <textarea className="l3-sql" value={query} onChange={(e) => setQuery(e.target.value)} spellCheck={false} aria-label="SQL query" />
                  <div className="l3-toolbar" style={{ justifyContent: "flex-end" }}>
                    <button className="btn primary" onClick={() => void runQuery()} disabled={sql.isPending || !db}>
                      {sql.isPending ? "running…" : "Run (cap 500 rows, 10s timeout)"}
                    </button>
                  </div>
                  <div className="tiny faint">The backend enforces the allow-list and a hard row cap — multi-statement and write SQL are refused before touching the database.</div>
                  {result && (
                    <div style={{ marginTop: 8 }}>
                      <div className={`l3-note ${result.ok ? "good" : "bad"}`}>{result.note}{result.truncated ? " · truncated at 500" : ""}</div>
                      <div tabIndex={0} className="l3-scroll sm">
                        <GridTable columns={result.columns} rows={result.rows} />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        }}
      </QuerySection>

      <QuerySection<{ success: boolean; apikeys?: Array<{ name: string; masked: string; set: boolean }> }>
        title="Named API keys (OS SecretStore — values never shown)"
        query={keys}
        skeletonRows={3}
        emptyMessage="No named keys stored."
        right={<FreshnessCaption fetchedAtMs={keys.dataUpdatedAt || null} intervalMs={60_000} stale={poll.paused} />}
      >
        {(data) => (
          <div>
            <DataTable headers={[{ label: "NAME" }, { label: "MASKED" }, { label: "STATE" }, { label: "ACTIONS" }]}>
              {(data.apikeys ?? []).map((k) => (
                <tr key={k.name}>
                  <td className="inline-mono">{k.name}</td>
                  <td className="l3-mask">{k.masked}</td>
                  <td><StatusBadge status={k.set ? "SET" : "INVALID"} /></td>
                  <td>
                    <div className="l3-row-actions">
                      <button className="btn small danger" disabled={delKey.isPending} onClick={() => void delKey.mutateAsync(k.name)}>Delete…</button>
                    </div>
                  </td>
                </tr>
              ))}
            </DataTable>
            <div className="l3-form" style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "end", marginTop: 8 }}>
              <FieldRow label="key name" hint="simple identifier (server rejects spaces/slashes, reserved names)">
                <TextField value={newKey.name} onChange={(v) => setNewKey((s) => ({ ...s, name: v }))} />
              </FieldRow>
              <FieldRow label="value" hint="sent once, stored in the SecretStore, never echoed">
                <TextField value={newKey.value} onChange={(v) => setNewKey((s) => ({ ...s, value: v }))} placeholder="••••••••" />
              </FieldRow>
              <button className="btn primary" disabled={saveKey.isPending || newKey.name.trim() === "" || newKey.value === ""} onClick={() => void saveKey.mutateAsync(newKey)}>
                {saveKey.isPending ? "saving…" : "Store key"}
              </button>
            </div>
            <ResultStrip result={saveKey.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(saveKey.data)} />
            <ResultStrip result={delKey.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(delKey.data)} />
          </div>
        )}
      </QuerySection>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function DatabasePage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<"status" | "manage" | "console">("status");
  return (
    <div className="l3-wrap">
      <div className="l3-head">
        <h1>Database</h1>
        <span className="crumb">PLATFORM</span>
        <span className="desc">persistence console — provider state, migration workflow, explorer, SQL, keys (legacy tab-database)</span>
      </div>
      <div className="l3-note">
        Backups and migrations are operator-initiated and type-confirmed; nothing here can trade or mutate market logic.
        Secrets (PG password, API keys, telegram tokens) live in the OS SecretStore — the backend only ever reports status.
      </div>
      <div className="l3-toolbar">
        <span className="segmented" role="tablist">
          {([["status", "Status"], ["manage", "Manage"], ["console", "Console"]] as const).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </span>
      </div>
      {tab === "status" && <StatusPanel />}
      {tab === "manage" && <ManagementPanel />}
      {tab === "console" && <ExplorerPanel />}
    </div>
  );
}
