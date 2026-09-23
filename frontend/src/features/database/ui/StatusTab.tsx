/**
 * Status tab — persistence health, provider truth and hygiene state.
 *
 * Everything rendered is backend-authoritative: the KPI strip and the hygiene
 * tables are pure arrangements of what the endpoints said (derivations live in
 * `../uiLogic`, pinned by tests/js/database_console.test.js).  The backend's
 * own `hints[]` are shown verbatim — the client never re-derives a provider
 * verdict it could get wrong.
 */

import { DataTable, EmptyState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { FreshnessCaption, JsonView, PollControl, QuerySection, usePolling } from "@/features/config/ui/kit";
import { useDbHygiene, useDbManageStatus, useDbStatus } from "../useCases";
import { domainRows, formatBytes } from "../model";
import {
  hygienePlanRows,
  hygieneStorage,
  hygieneWorker,
  pendingSchema,
  providerHints,
  providerTruth,
  totalStorageBytes,
} from "../uiLogic";
import type { DbHygiene, DbManageStatus, DbStatus } from "../api";

/* ------------------------------------------------------------------ */
/* KPI strip                                                           */
/* ------------------------------------------------------------------ */

function KpiStrip({
  manage,
  status,
  hygiene,
}: {
  manage: DbManageStatus | undefined | null;
  status: DbStatus | undefined | null;
  hygiene: DbHygiene | undefined | null;
}) {
  const pend = pendingSchema(status);
  const domains = status?.databases ? Object.keys(status.databases) : [];
  const provider = manage?.provider ?? "—";
  const truth = providerTruth(manage);
  const mismatch = Boolean(truth?.mismatch);
  const driverMissing = provider === "postgresql" && manage?.postgresql_driver_available === false;
  const overall = String(manage?.overall ?? "UNKNOWN");
  const storage = totalStorageBytes(hygiene);
  return (
    <div className="dbc-kpis">
      <MetricCard
        label={mismatch ? "configured → effective" : "active provider"}
        value={
          mismatch && truth ? (
            <span className="inline-mono">
              {truth.configured} → {truth.effective}
            </span>
          ) : (
            <span className="inline-mono">{provider}</span>
          )
        }
        sub={
          mismatch
            ? "badge was settings-only; panels below read local sqlite"
            : driverMissing
              ? "psycopg not installed — see hints"
              : "switch applies on next restart"
        }
        tone={mismatch || driverMissing ? "neg" : undefined}
      />
      <MetricCard label="overall health" value={<StatusBadge status={overall} />} sub="db/manage/status" />
      <MetricCard
        label="pending migrations"
        value={pend.pending ?? "—"}
        tone={pend.pending && pend.pending > 0 ? "neg" : "dim"}
        sub={pend.behind.length ? `${pend.behind.join(", ")} behind schema` : "all domains at expected schema"}
      />
      <MetricCard
        label="tamper"
        value={pend.tampered.length ? "DETECTED" : "none"}
        tone={pend.tampered.length ? "neg" : "pos"}
        sub={pend.tampered.length ? pend.tampered.join(", ") : "integrity probes clean"}
      />
      <MetricCard
        label="managed storage"
        value={storage === null ? "—" : formatBytes(storage)}
        sub="sum of hygiene db_sizes"
      />
      <MetricCard
        label="domains"
        value={domains.length || "—"}
        sub={domains.length ? domains.join(" · ") : "no schema state reported"}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Domain schema table                                                 */
/* ------------------------------------------------------------------ */

function DomainTable({ status }: { status: DbStatus | undefined }) {
  if (!status) return <Skeleton count={3} />;
  if (status.available === false) {
    const msg = typeof status.error === "object" ? status.error?.message ?? "no state published" : String(status.error ?? "no state published");
    return <div className="dbc-note bad">Schema state unavailable: {msg}</div>;
  }
  const rows = domainRows(status.databases);
  if (rows.length === 0) {
    return <EmptyState message="No database domains reported." hint="The backend has not published schema/migration state for any domain." />;
  }
  return (
    <DataTable
      headers={[
        { label: "DOMAIN" },
        { label: "SCHEMA", num: true },
        { label: "EXPECTED", num: true },
        { label: "STATE" },
        { label: "PENDING", num: true },
        { label: "INTEGRITY" },
      ]}
    >
      {rows.map((r) => (
        <tr key={r.domain}>
          <td className="inline-mono">{r.domain}</td>
          <td className="num">{r.schema}</td>
          <td className="num">{r.expected}</td>
          <td>
            <StatusBadge status={r.state} />
            {r.tamper && (
              <span className="badge bad" style={{ marginInlineStart: 6 }}>
                TAMPER
              </span>
            )}
          </td>
          <td className="num">{r.pending ?? "—"}</td>
          <td>{r.integrity}</td>
        </tr>
      ))}
    </DataTable>
  );
}

/* ------------------------------------------------------------------ */
/* Hygiene body                                                        */
/* ------------------------------------------------------------------ */

function HygieneBody({ hygiene }: { hygiene: DbHygiene }) {
  const worker = hygieneWorker(hygiene);
  const storage = hygieneStorage(hygiene);
  const plans = hygienePlanRows(hygiene);
  if (!worker && storage.length === 0 && plans.length === 0) {
    return <EmptyState message="Hygiene worker reported no status, plans or storage." hint="Raw payload is below." />;
  }
  return (
    <>
      {worker && (
        <div className="dbc-kpis" style={{ marginBottom: 4 }}>
          <MetricCard label="worker state" value={<StatusBadge status={worker.state} />} sub={`mode ${worker.mode}`} />
          <MetricCard label="execution mode" value={<span className="inline-mono">{worker.executionMode}</span>} sub="scope of the sweep" />
          <MetricCard
            label="cycle"
            value={worker.cycle ?? "—"}
            sub={worker.lastFailure ? `last failure ${worker.lastFailure}` : "no recorded failure"}
          />
          <MetricCard
            label="last success"
            value={<span className="dbc-num">{worker.lastSuccess ? worker.lastSuccess.slice(11) : "—"}</span>}
            sub={worker.lastSuccess ? worker.lastSuccess.slice(0, 10) : "never"}
          />
        </div>
      )}
      {worker && worker.managed.length > 0 && (
        <div className="dbc-row" style={{ marginBottom: 4 }}>
          <span className="dbc-sub">managed:</span>
          {worker.managed.map((d) => (
            <span key={d} className="dbc-chip">
              {d}
            </span>
          ))}
        </div>
      )}
      {storage.length > 0 && (
        <>
          <div className="dbc-section-title">storage</div>
          <DataTable headers={[{ label: "DATABASE" }, { label: "SIZE", num: true }, { label: "WAL", num: true }]}>
            {storage.map((s) => (
              <tr key={s.database}>
                <td className="inline-mono">{s.database}</td>
                <td className="num">{formatBytes(s.bytes)}</td>
                <td className="num">{s.walBytes ? formatBytes(s.walBytes) : <span className="dbc-sub">—</span>}</td>
              </tr>
            ))}
          </DataTable>
        </>
      )}
      {plans.length > 0 && (
        <>
          <div className="dbc-section-title">retention plans</div>
          <DataTable
            headers={[
              { label: "DATABASE" },
              { label: "TABLES", num: true },
              { label: "DUPLICATES", num: true },
              { label: "ORPHANS", num: true },
              { label: "RETENTION", num: true },
              { label: "BLOCKED", num: true },
            ]}
          >
            {plans.map((p) => (
              <tr key={p.database}>
                <td className="inline-mono">{p.database}</td>
                <td className="num">{p.tablesScanned ?? "—"}</td>
                <td className="num">
                  {p.duplicates ?? "—"}
                  {typeof p.exactDuplicates === "number" ? ` (${p.exactDuplicates} exact)` : ""}
                </td>
                <td className="num">{p.orphans ?? "—"}</td>
                <td className="num">{p.retention ?? "—"}</td>
                <td className="num">{p.blocked ?? "—"}</td>
              </tr>
            ))}
          </DataTable>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Tab                                                                 */
/* ------------------------------------------------------------------ */

export function StatusTab() {
  const poll = usePolling(30_000);
  const manage = useDbManageStatus(poll.paused);
  const status = useDbStatus(poll.paused);
  const hygiene = useDbHygiene(poll.paused);
  // `data` is typed `… | undefined`, but a resolved-null / aborted-refetch
  // window has been observed at runtime (crash caught live 2026-09-23:
  // "Cannot read properties of null (reading 'supported_providers')") —
  // narrow on the VALUE, not on isPending/isError.
  const md = manage.data ?? null;
  const hints = providerHints(manage.data);

  return (
    <div className="dbc-stack">
      <KpiStrip manage={manage.data} status={status.data} hygiene={hygiene.data} />

      <Panel
        title="Persistence provider (db/manage/status)"
        right={
          <>
            <FreshnessCaption fetchedAtMs={manage.dataUpdatedAt || null} intervalMs={30_000} stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={manage.isFetching} />
          </>
        }
      >
        {!md ? (
          manage.isError ? (
            <div className="dbc-note bad">
              Provider state unavailable: {manage.error instanceof Error ? manage.error.message : "backend request failed"}
            </div>
          ) : (
            <Skeleton count={3} />
          )
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
            <div className="dbc-row between">
              <div className="dbc-chips">
                {(md.supported_providers ?? []).map((p) => (
                  <span
                    key={p}
                    className={`dbc-chip ${p === md.provider ? "active" : ""}`}
                    title={p === md.provider ? "active provider (switch applies on next restart)" : `switch to ${p} on the Manage tab`}
                  >
                    {p === md.provider && <span className="dbc-dot good" aria-hidden="true" />}
                    {p}
                    {p === md.provider && <span className="sub">active</span>}
                  </span>
                ))}
              </div>
              <span className="dbc-sub">
                pg password {md.password_set ? "SET (OS SecretStore)" : "MISSING"}
                {md.postgresql_driver_available === false && " · psycopg absent"}
              </span>
            </div>
            <div className="dbc-section-title">domain health</div>
            <DomainTable status={status.data} />
            {md.domains && (
              <details className="dbc-raw" style={{ marginTop: 10 }}>
                <summary>raw domain health payload</summary>
                <div tabIndex={0} className="dbc-raw-body">
                  <JsonView value={md.domains} name="domains" depth={1} />
                </div>
              </details>
            )}
          </>
        )}
      </Panel>

      <QuerySection<DbStatus>
        title="Schema & migration state (/api/db/status)"
        query={status}
        skeletonRows={3}
        emptyMessage="No domains reported."
        right={
          <>
            <FreshnessCaption fetchedAtMs={status.dataUpdatedAt || null} intervalMs={30_000} note="read-only; the API never runs migrations" stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={status.isFetching} />
          </>
        }
      >
        {(data) => <DomainTable status={data} />}
      </QuerySection>

      <QuerySection<DbHygiene>
        title="Hygiene worker (/api/db/hygiene)"
        query={hygiene}
        skeletonRows={3}
        emptyMessage="Hygiene worker state unavailable."
        right={<FreshnessCaption fetchedAtMs={hygiene.dataUpdatedAt || null} intervalMs={60_000} stale={poll.paused} />}
      >
        {(data) => (
          <>
            <HygieneBody hygiene={data} />
            <details className="dbc-raw" style={{ marginTop: 10 }}>
              <summary>raw hygiene payload</summary>
              <div tabIndex={0} className="dbc-raw-body">
                <JsonView value={data} name="hygiene" depth={1} />
              </div>
            </details>
          </>
        )}
      </QuerySection>
    </div>
  );
}
