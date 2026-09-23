/**
 * Console tab — SSMS-style explorer (databases → tables → rows), the column
 * schema view, the read-only SQL console and the named API keys.
 *
 * Behavior notes:
 *  - the default database is the first REACHABLE one (pickDefaultDatabase):
 *    the list order is audit/news/candle_intel/settings and on a postgresql
 *    provider the first three can be unreachable while `settings` is not —
 *    blindly taking list[0] opened the console permanently broken;
 *  - an unreachable database renders the backend's own status + `hint` as a
 *    blocker with a one-click switch, never a dead table;
 *  - SQL stays read-only by SERVER contract (allow-list + row cap); the
 *    client only refuses emptiness and renders what came back.
 */

import { useEffect, useMemo, useState } from "react";
import { EmptyState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import {
  FieldRow,
  FreshnessCaption,
  JsonView,
  PollControl,
  QuerySection,
  ResultStrip,
  TextField,
  usePolling,
} from "@/features/config/ui/kit";
import {
  useApiKeys,
  useConsoleColumns,
  useConsoleDatabases,
  useConsoleRows,
  useConsoleTables,
  useDbManageStatus,
  useDeleteApiKey,
  useRefreshConsole,
  useRunQuickSql,
  useSaveApiKey,
  useSqlQuery,
} from "../useCases";
import { dbConsoleName, formatBytes } from "../model";
import {
  consoleBlocker,
  defaultSqlForProvider,
  isReachable,
  pageCaption,
  pickDefaultDatabase,
  truncationNote,
} from "../uiLogic";
import type { ConsoleColumn, ConsoleDatabase } from "../api";
import { useI18n } from "@/stores/i18nStore";

const QUICK_KINDS = ["top100", "count", "recent", "schema", "integrity"] as const;
const PAGE_SIZE = 100;

interface GridResult {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  truncated?: boolean;
  cap?: number;
  note: string;
  ok: boolean;
}

function Grid({ columns, rows, small }: { columns: string[]; rows: Array<Record<string, unknown>>; small?: boolean }) {
  const t = useI18n((s) => s.t);
  if (columns.length === 0) {
    return <div className="dbc-empty">{t("database.explorer.grid_no_columns", "No columns — the query returned no projection.")}</div>;
  }
  if (rows.length === 0) return <div className="dbc-empty">{t("database.explorer.grid_no_rows", "No rows.")}</div>;
  return (
    <div tabIndex={0} className={`dbc-grid ${small ? "sm" : ""}`}>
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} scope="col">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={r[c] === null || r[c] === undefined ? "null" : undefined} title={String(r[c] ?? "")}>
                  {r[c] === null || r[c] === undefined ? "null" : String(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ColumnsGrid({ columns }: { columns: ConsoleColumn[] }) {
  const t = useI18n((s) => s.t);
  if (columns.length === 0) {
    return <EmptyState message={t("database.explorer.no_columns", "The backend reported no columns for this table.")} />;
  }
  return (
    <div tabIndex={0} className="dbc-grid sm">
      <table>
        <thead>
          <tr>
            <th scope="col">{t("database.explorer.th_column", "column")}</th>
            <th scope="col">{t("database.explorer.th_type", "type")}</th>
            <th scope="col">{t("database.explorer.th_flags", "flags")}</th>
            <th scope="col">{t("database.explorer.th_default", "default")}</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => (
            <tr key={c.name}>
              <td>{c.name}</td>
              <td>{c.type}</td>
              <td>
                {c.pk && <span className="badge good">PK</span>}{" "}{c.notnull && <span className="badge neutral">NOT NULL</span>}
              </td>
              <td className={c.default === null || c.default === undefined ? "null" : undefined}>
                {c.default === null || c.default === undefined ? "null" : String(c.default)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Explorer (sidebar + data grid + SQL console)                        */
/* ------------------------------------------------------------------ */

function ExplorerPanel() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(60_000);
  const dbs = useConsoleDatabases(poll.paused);
  const manage = useDbManageStatus(poll.paused);
  const refresh = useRefreshConsole();

  const [db, setDb] = useState<string | null>(null);
  const [table, setTable] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState("");
  const [showSchema, setShowSchema] = useState(false);

  const list = useMemo(() => dbs.data?.databases ?? [], [dbs.data]);
  const active = list.find((d) => d.name === db) ?? null;

  // Default-select the first REACHABLE database once (operator can switch).
  useEffect(() => {
    if (db) return;
    const first = pickDefaultDatabase(list);
    if (first) setDb(first);
  }, [list, db]);

  const tables = useConsoleTables(db);
  const rows = useConsoleRows(db, table, PAGE_SIZE, page * PAGE_SIZE);
  const columns = useConsoleColumns(db, table, showSchema);
  const quick = useRunQuickSql();
  const sql = useSqlQuery();
  const provider = manage.data?.provider;
  const [query, setQuery] = useState("");
  const [sqlApplied, setSqlApplied] = useState<string | null>(null);
  const [result, setResult] = useState<GridResult | null>(null);

  // Seed / re-seed the editor for the active dialect until the operator edits.
  useEffect(() => {
    if (sqlApplied === null) setQuery(defaultSqlForProvider(provider));
  }, [provider, sqlApplied]);

  const blocker = consoleBlocker(active, t);
  const tableRows = tables.data?.tables ?? [];
  const filteredTables = filter.trim()
    ? tableRows.filter((t) => t.name.toLowerCase().includes(filter.trim().toLowerCase()))
    : tableRows;

  const runQuery = async () => {
    if (!db) return;
    const o = await sql.mutateAsync({ database: db, sql: query });
    setSqlApplied(query);
    setResult({
      columns: o.columns ?? [],
      rows: o.rows ?? [],
      truncated: o.truncated,
      note: o.message,
      ok: o.ok,
    });
  };

  const runQuick = async (kind: string) => {
    if (!db || !table) return;
    if (kind === "schema") setShowSchema(true);
    const o = await quick.mutateAsync({ database: db, table, kind });
    setResult({ columns: o.columns ?? [], rows: o.rows ?? [], note: o.message, ok: o.ok });
  };

  const reachable = list.filter(isReachable);

  return (
    <>
      <Panel
        title={t("database.explorer.title", "Database console — explorer (provider-abstracted)")}
        accent
        right={
          <>
            <FreshnessCaption fetchedAtMs={dbs.dataUpdatedAt || null} intervalMs={60_000} stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={60_000} busy={dbs.isFetching} />
            <button className="btn small" onClick={() => void refresh.mutateAsync()} disabled={refresh.isPending}>
              {refresh.isPending
                ? t("database.explorer.rescanning", "re-scanning…")
                : t("database.explorer.rescan", "re-scan")}
            </button>
          </>
        }
      >
        <QuerySection<{ success: boolean; databases: ConsoleDatabase[] }>
          title={t("database.explorer.databases_title", "databases")}
          query={dbs}
          skeletonRows={3}
          emptyMessage={t("database.explorer.empty_dbs", "No databases discovered.")}
          emptyHint={t("database.explorer.empty_dbs_hint", "Use re-scan to force a fresh discovery pass.")}
        >
          {(data) => (
            <div className="dbc-console">
              <div className="dbc-side">
                <div className="dbc-side-title">
                  {t("database.explorer.side_databases", "databases · {n}", { n: (data.databases ?? []).length })}
                </div>
                <div tabIndex={0} className="dbc-list">
                  {(data.databases ?? []).map((d) => (
                    <button
                      key={d.name}
                      className={`dbc-item ${d.name === db ? "active" : ""}`}
                      onClick={() => {
                        setDb(d.name);
                        setTable(null);
                        setPage(0);
                        setShowSchema(false);
                      }}
                      title={`${d.provider} · ${dbConsoleName(d)} · ${formatBytes(d.size_bytes)}${d.hint ? ` — ${d.hint}` : ""}`}
                    >
                      <span className={`dbc-dot ${isReachable(d) ? "good" : String(d.status).includes("DRIVER") ? "bad" : "warn"}`} aria-hidden="true" />
                      <span className="name">{d.name}</span>
                      <span className={`meta ${isReachable(d) ? "" : "warn"}`}>{formatBytes(d.size_bytes)}</span>
                    </button>
                  ))}
                </div>
                <div className="dbc-side-title">
                  {table ? t("database.explorer.tables_of", "tables · {db}", { db: table }) : t("database.explorer.tables", "tables")}
                </div>
                <FieldRow
                  label={t("database.explorer.filter_label", "filter tables")}
                  hint={t("database.explorer.filter_hint", "{shown} of {total} shown", {
                    shown: filteredTables.length,
                    total: tableRows.length,
                  })}
                >
                  <TextField value={filter} onChange={setFilter} placeholder={t("database.explorer.filter_ph", "substr…")} />
                </FieldRow>
                <div tabIndex={0} className="dbc-list">
                  {tables.isPending ? (
                    <Skeleton count={3} />
                  ) : tables.data && !tables.data.success ? (
                    <div className="dbc-note bad">
                      {tables.data.error}
                      {tables.data.hint && <span className="dbc-hintline">{tables.data.hint}</span>}
                    </div>
                  ) : filteredTables.length === 0 ? (
                    <div className="dbc-empty">{t("database.explorer.no_match", "No matching table.")}</div>
                  ) : (
                    filteredTables.map((tb) => (
                      <button
                        key={tb.name}
                        className={`dbc-item ${tb.name === table ? "active" : ""}`}
                        onClick={() => {
                          setTable(tb.name);
                          setPage(0);
                        }}
                        title={`${tb.name} · ${
                          tb.rows === null
                            ? t("database.explorer.rows_unknown", "row count unknown")
                            : `${tb.rows.toLocaleString("en-US")} ${t("database.explorer.rows_word", "rows")}`
                        }`}
                      >
                        <span className="name">{tb.name}</span>
                        <span className="meta">{tb.rows === null ? "?" : tb.rows.toLocaleString("en-US")}</span>
                      </button>
                    ))
                  )}
                </div>
              </div>

              <div className="dbc-main">
                <div className="dbc-row between">
                  <span className="dbc-row">
                    <span className="inline-mono">{db ?? t("database.explorer.no_db", "no database")}</span>
                    <StatusBadge status={active ? String(active.status) : "UNKNOWN"} />
                    {active && <span className="dbc-sub">{active.provider}{active.server ? ` · ${active.server}` : ""}</span>}
                  </span>
                  <span className="dbc-sub">
                    {t("database.explorer.reachable_count", "{reachable}/{total} reachable", {
                      reachable: reachable.length,
                      total: list.length,
                    })}
                  </span>
                </div>

                {blocker && (
                  <div className="dbc-blocker" role="alert">
                    <span className="title">{t("database.explorer.blocker_title", "This database cannot be opened.")}</span>
                    <span>{blocker.message}</span>
                    {blocker.hint && <span className="hint">{blocker.hint}</span>}
                    {reachable.length > 0 && (
                      <span>
                        {reachable.map((r) => (
                          <button
                            key={r.name}
                            className="btn small"
                            style={{ marginInlineEnd: 6 }}
                            onClick={() => {
                              setDb(r.name);
                              setTable(null);
                              setPage(0);
                              setShowSchema(false);
                            }}
                          >
                            {t("database.explorer.switch_to", "switch to")} {r.name}
                          </button>
                        ))}
                      </span>
                    )}
                  </div>
                )}

                {table && !blocker && (
                  <>
                    <div className="dbc-row between">
                      <div className="dbc-row">
                        {QUICK_KINDS.map((k) => (
                          <button key={k} className="btn small ghost" onClick={() => void runQuick(k)} disabled={quick.isPending}>
                            {k}
                          </button>
                        ))}
                        <button
                          className={`btn small ${showSchema ? "primary" : "ghost"}`}
                          onClick={() => setShowSchema((s) => !s)}
                          aria-pressed={showSchema}
                        >
                          {t("database.explorer.columns_btn", "columns")}
                        </button>
                      </div>
                      <span className="dbc-row">
                        <span className="dbc-sub">{pageCaption(page * PAGE_SIZE, rows.data?.rows?.length ?? 0, t)}</span>
                        <button className="btn small" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                          {t("database.explorer.prev", "‹ prev")}
                        </button>
                        <button
                          className="btn small"
                          disabled={(rows.data?.rows?.length ?? 0) < PAGE_SIZE}
                          onClick={() => setPage((p) => p + 1)}
                        >
                          {t("database.explorer.next", "next ›")}
                        </button>
                      </span>
                    </div>

                    {showSchema &&
                      (columns.isPending ? (
                        <Skeleton count={4} />
                      ) : columns.data && !columns.data.success ? (
                        <div className="dbc-note bad">
                          {columns.data.error}
                          {columns.data.hint && <span className="dbc-hintline">{columns.data.hint}</span>}
                        </div>
                      ) : columns.data ? (
                        <ColumnsGrid columns={columns.data.columns ?? []} />
                      ) : null)}

                    {rows.isPending ? (
                      <Skeleton count={4} />
                    ) : rows.data && !rows.data.success ? (
                      <div className="dbc-note bad">
                        {rows.data.error}
                        {rows.data.hint && <span className="dbc-hintline">{rows.data.hint}</span>}
                      </div>
                    ) : rows.data ? (
                      <Grid columns={rows.data.columns ?? []} rows={rows.data.rows ?? []} />
                    ) : null}
                  </>
                )}

                <div>
                  <div className="dbc-section-title" style={{ marginTop: 0 }}>
                    {t("database.explorer.sql_title", "SQL console — read only: SELECT / EXPLAIN / WITH / PRAGMA / VALUES")}
                  </div>
                  <textarea
                    className="dbc-sql"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    spellCheck={false}
                    aria-label={t("database.explorer.aria_sql", "SQL query")}
                    placeholder={defaultSqlForProvider(provider)}
                  />
                  <div className="dbc-row end" style={{ marginTop: 6 }}>
                    <span className="dbc-sub">{db ?? t("database.explorer.no_db_selected", "no database selected")}</span>
                    <button className="btn primary" onClick={() => void runQuery()} disabled={sql.isPending || !db || blocker !== null}>
                      {sql.isPending
                        ? t("database.explorer.running", "running…")
                        : t("database.explorer.run", "Run (cap 500 rows, 10s timeout)")}
                    </button>
                  </div>
                  <div className="dbc-sub" style={{ marginTop: 4 }}>
                    {t(
                      "database.explorer.allowlist_note",
                      "The backend enforces the allow-list and a hard row cap — multi-statement and write SQL are refused before touching the database.",
                    )}
                  </div>
                  {result && (
                    <div style={{ marginTop: 8 }}>
                      <div className={`dbc-note ${result.ok ? "good" : "bad"}`}>
                        <span className={`dbc-result-note ${result.ok ? "ok" : "fail"}`}>{result.note}</span>
                        {result.truncated && <span>{truncationNote(result.truncated, result.cap, t)}</span>}
                      </div>
                      {result.ok && <Grid columns={result.columns} rows={result.rows} small />}
                      <details className="dbc-raw" style={{ marginTop: 6 }}>
                        <summary>
                          {t("database.explorer.raw_result", "raw payload ({n} rows kept)", { n: result.rows.length })}
                        </summary>
                        <div tabIndex={0} className="dbc-raw-body">
                          <JsonView value={{ columns: result.columns, rows: result.rows.slice(0, 20) }} name="result" depth={1} />
                        </div>
                      </details>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </QuerySection>
      </Panel>

    </>
  );
}

/* ------------------------------------------------------------------ */
/* API keys                                                            */
/* ------------------------------------------------------------------ */

function ApiKeysPanel({ paused }: { paused: boolean }) {
  const t = useI18n((s) => s.t);
  const keys = useApiKeys(paused);
  const saveKey = useSaveApiKey();
  const delKey = useDeleteApiKey();
  const [newKey, setNewKey] = useState({ name: "", value: "" });

  return (
    <Panel
      title={t("database.explorer.keys_title", "Named API keys (OS SecretStore — values never shown)")}
      right={<FreshnessCaption fetchedAtMs={keys.dataUpdatedAt || null} intervalMs={60_000} stale={paused} />}
    >
      {keys.isPending ? (
        <Skeleton count={3} />
      ) : keys.data && !keys.data.success ? (
        <div className="dbc-note bad">
          {String(keys.data.error ?? t("database.explorer.keys_list_failed", "listing keys failed"))}
        </div>
      ) : (keys.data?.apikeys ?? []).length === 0 ? (
        <EmptyState
          message={t("database.explorer.empty_keys", "No named keys stored.")}
          hint={t(
            "database.explorer.empty_keys_hint",
            "Store a key below — the value is written straight to the OS secret store.",
          )}
        />
      ) : (
        <>
          <div tabIndex={0} className="dbc-grid">
            <table>
              <thead>
                <tr>
                  <th scope="col">{t("database.th.name", "name")}</th>
                  <th scope="col">{t("database.th.stored_as", "stored as")}</th>
                  <th scope="col">{t("database.th.state", "state")}</th>
                  <th scope="col">{t("database.th.actions", "actions")}</th>
                </tr>
              </thead>
              <tbody>
                {(keys.data?.apikeys ?? []).map((k) => (
                  <tr key={k.name}>
                    <td>{k.name}</td>
                    <td>{k.masked}</td>
                    <td>
                      <StatusBadge status={k.set ? "SET" : "INVALID"} />
                    </td>
                    <td>
                      <button className="btn small danger" disabled={delKey.isPending} onClick={() => void delKey.mutateAsync(k.name)}>
                        {t("database.explorer.delete", "Delete…")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "end", marginTop: 10 }}>
            <FieldRow
              label={t("database.explorer.key_name_label", "key name")}
              hint={t(
                "database.explorer.key_name_hint",
                "simple identifier (server rejects spaces/slashes, reserved names)",
              )}
            >
              <TextField value={newKey.name} onChange={(v) => setNewKey((s) => ({ ...s, name: v }))} />
            </FieldRow>
            <FieldRow
              label={t("database.explorer.key_value_label", "value")}
              hint={t(
                "database.explorer.key_value_hint",
                "sent once, stored in the SecretStore, never echoed",
              )}
            >
              <TextField value={newKey.value} onChange={(v) => setNewKey((s) => ({ ...s, value: v }))} placeholder="••••••••" />
            </FieldRow>
            <button
              className="btn primary"
              disabled={saveKey.isPending || newKey.name.trim() === "" || newKey.value === ""}
              onClick={() => void saveKey.mutateAsync(newKey)}
            >
              {saveKey.isPending ? t("database.manage.saving", "saving…") : t("database.explorer.store_key", "Store key")}
            </button>
          </div>
          <div className="dbc-stack" style={{ gap: 6, marginTop: 8 }}>
            <ResultStrip result={saveKey.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(saveKey.data)} />
            <ResultStrip result={delKey.isPending ? { running: true, lastResult: null, lastMessage: null } : strip(delKey.data)} />
          </div>
        </>
      )}
    </Panel>
  );
}

function strip(o: { ok: boolean; message: string; requestId: string | null } | undefined) {
  if (!o) return null;
  return { running: false, lastResult: o.ok, lastMessage: o.requestId ? `${o.message} · request_id: ${o.requestId}` : o.message };
}

/* ------------------------------------------------------------------ */
/* Tab                                                                 */
/* ------------------------------------------------------------------ */

export function ConsoleTab() {
  const poll = usePolling(60_000);
  return (
    <div className="dbc-stack">
      <ExplorerPanel />
      <ApiKeysPanel paused={poll.paused} />
    </div>
  );
}

