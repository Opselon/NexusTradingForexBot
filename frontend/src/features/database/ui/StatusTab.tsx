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
import { useI18n } from "@/stores/i18nStore";

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
  const t = useI18n((s) => s.t);
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
        label={
          mismatch
            ? t("database.status.label_configured_effective", "configured → effective")
            : t("database.status.active_provider", "active provider")
        }
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
            ? t("database.status.mismatch_sub", "badge was settings-only; panels below read local sqlite")
            : driverMissing
              ? t("database.status.psycopg_see_hints", "psycopg not installed — see hints")
              : t("database.status.active_provider_sub", "switch applies on next restart")
        }
        tone={mismatch || driverMissing ? "neg" : undefined}
      />
      <MetricCard
        label={t("database.status.overall_health", "overall health")}
        value={<StatusBadge status={overall} />}
        sub="db/manage/status"
      />
      <MetricCard
        label={t("database.status.pending_migrations", "pending migrations")}
        value={pend.pending ?? "—"}
        tone={pend.pending && pend.pending > 0 ? "neg" : "dim"}
        sub={
          pend.behind.length
            ? t("database.status.behind_schema", "{domains} behind schema", { domains: pend.behind.join(", ") })
            : t("database.status.all_at_expected", "all domains at expected schema")
        }
      />
      <MetricCard
        label={t("database.status.tamper", "tamper")}
        value={pend.tampered.length ? t("database.status.tamper_detected", "DETECTED") : t("database.status.tamper_none", "none")}
        tone={pend.tampered.length ? "neg" : "pos"}
        sub={pend.tampered.length ? pend.tampered.join(", ") : t("database.status.integrity_clean", "integrity probes clean")}
      />
      <MetricCard
        label={t("database.status.managed_storage", "managed storage")}
        value={storage === null ? "—" : formatBytes(storage)}
        sub={t("database.status.storage_sum", "sum of hygiene db_sizes")}
      />
      <MetricCard
        label={t("database.status.domains_label", "domains")}
        value={domains.length || "—"}
        sub={domains.length ? domains.join(" · ") : t("database.status.no_schema_state", "no schema state reported")}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Domain schema table                                                 */
/* ------------------------------------------------------------------ */

function DomainTable({ status }: { status: DbStatus | undefined }) {
  const t = useI18n((s) => s.t);
  if (!status) return <Skeleton count={3} />;
  if (status.available === false) {
    const raw = typeof status.error === "object" ? status.error?.message ?? null : status.error ?? null;
    const msg = raw === null ? t("database.status.no_state_published", "no state published") : String(raw);
    return (
      <div className="dbc-note bad">
        {t("database.status.schema_unavailable", "Schema state unavailable: {msg}", { msg })}
      </div>
    );
  }
  const rows = domainRows(status.databases);
  if (rows.length === 0) {
    return (
      <EmptyState
        message={t("database.status.domains_empty", "No database domains reported.")}
        hint={t(
          "database.status.domains_empty_hint",
          "The backend has not published schema/migration state for any domain.",
        )}
      />
    );
  }
  return (
    <DataTable
      headers={[
        { label: t("database.th.domain", "DOMAIN") },
        { label: t("database.th.schema", "SCHEMA"), num: true },
        { label: t("database.th.expected", "EXPECTED"), num: true },
        { label: t("database.th.state", "STATE") },
        { label: t("database.th.pending", "PENDING"), num: true },
        { label: t("database.th.integrity", "INTEGRITY") },
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
                {t("database.status.tamper_badge", "TAMPER")}
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
  const t = useI18n((s) => s.t);
  const worker = hygieneWorker(hygiene);
  const storage = hygieneStorage(hygiene);
  const plans = hygienePlanRows(hygiene);
  if (!worker && storage.length === 0 && plans.length === 0) {
    return (
      <EmptyState
        message={t(
          "database.status.hygiene_empty_body",
          "Hygiene worker reported no status, plans or storage.",
        )}
        hint={t("database.status.hygiene_raw_hint", "Raw payload is below.")}
      />
    );
  }
  return (
    <>
      {worker && (
        <div className="dbc-kpis" style={{ marginBottom: 4 }}>
          <MetricCard
            label={t("database.status.worker_status", "worker state")}
            value={<StatusBadge status={worker.state} />}
            sub={t("database.status.mode_of", "mode {mode}", { mode: worker.mode })}
          />
          <MetricCard
            label={t("database.status.execution_mode", "execution mode")}
            value={<span className="inline-mono">{worker.executionMode}</span>}
            sub={t("database.status.execution_mode_sub", "scope of the sweep")}
          />
          <MetricCard
            label={t("database.status.cycle_label", "cycle")}
            value={worker.cycle ?? "—"}
            sub={
              worker.lastFailure
                ? t("database.status.last_failure", "last failure {at}", { at: worker.lastFailure })
                : t("database.status.no_failure", "no recorded failure")
            }
          />
          <MetricCard
            label={t("database.status.last_success", "last success")}
            value={<span className="dbc-num">{worker.lastSuccess ? worker.lastSuccess.slice(11) : "—"}</span>}
            sub={worker.lastSuccess ? worker.lastSuccess.slice(0, 10) : t("database.status.never", "never")}
          />
        </div>
      )}
      {worker && worker.managed.length > 0 && (
        <div className="dbc-row" style={{ marginBottom: 4 }}>
          <span className="dbc-sub">{t("database.status.managed_label", "managed:")}</span>
          {worker.managed.map((d) => (
            <span key={d} className="dbc-chip">
              {d}
            </span>
          ))}
        </div>
      )}
      {storage.length > 0 && (
        <>
          <div className="dbc-section-title">{t("database.status.storage_title", "storage")}</div>
          <DataTable
            headers={[
              { label: t("database.th.database", "DATABASE") },
              { label: t("database.th.size", "SIZE"), num: true },
              { label: t("database.th.wal", "WAL"), num: true },
            ]}
          >
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
          <div className="dbc-section-title">{t("database.status.retention_plans", "retention plans")}</div>
          <DataTable
            headers={[
              { label: t("database.th.database", "DATABASE") },
              { label: t("database.th.tables", "TABLES"), num: true },
              { label: t("database.th.duplicates", "DUPLICATES"), num: true },
              { label: t("database.th.orphans", "ORPHANS"), num: true },
              { label: t("database.th.retention", "RETENTION"), num: true },
              { label: t("database.th.blocked", "BLOCKED"), num: true },
            ]}
          >
            {plans.map((p) => (
              <tr key={p.database}>
                <td className="inline-mono">{p.database}</td>
                <td className="num">{p.tablesScanned ?? "—"}</td>
                <td className="num">
                  {p.duplicates ?? "—"}
                  {typeof p.exactDuplicates === "number"
                    ? t("database.status.exact_suffix", " ({n} exact)", { n: p.exactDuplicates })
                    : ""}
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
  const t = useI18n((s) => s.t);
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
        title={t("database.status.provider_title", "Persistence provider (db/manage/status)")}
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
              {t("database.status.provider_unavailable", "Provider state unavailable: {msg}", {
                msg:
                  manage.error instanceof Error
                    ? manage.error.message
                    : t("database.status.backend_failed", "backend request failed"),
              })}
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
                    title={
                      p === md.provider
                        ? t("database.status.chip_active_title", "active provider (switch applies on next restart)")
                        : t("database.status.chip_switch_title", "switch to {p} on the Manage tab", { p })
                    }
                  >
                    {p === md.provider && <span className="dbc-dot good" aria-hidden="true" />}
                    {p}
                    {p === md.provider && <span className="sub">{t("database.status.chip_active_sub", "active")}</span>}
                  </span>
                ))}
              </div>
              <span className="dbc-sub">
                {t("database.status.pg_password_label", "pg password")}{" "}
                {md.password_set
                  ? t("database.status.pw_set_store", "SET (OS SecretStore)")
                  : t("database.status.pw_missing", "MISSING")}
                {md.postgresql_driver_available === false && t("database.status.psycope_absent", " · psycopg absent")}
              </span>
            </div>
            <div className="dbc-section-title">{t("database.status.domain_health", "domain health")}</div>
            <DomainTable status={status.data} />
            {md.domains && (
              <details className="dbc-raw" style={{ marginTop: 10 }}>
                <summary>{t("database.status.raw_domains", "raw domain health payload")}</summary>
                <div tabIndex={0} className="dbc-raw-body">
                  <JsonView value={md.domains} name="domains" depth={1} />
                </div>
              </details>
            )}
          </>
        )}
      </Panel>

      <QuerySection<DbStatus>
        title={t("database.status.schema_title", "Schema & migration state (/api/db/status)")}
        query={status}
        skeletonRows={3}
        emptyMessage={t("database.status.domains_none", "No domains reported.")}
        right={
          <>
            <FreshnessCaption
              fetchedAtMs={status.dataUpdatedAt || null}
              intervalMs={30_000}
              note={t("database.status.readonly_note", "read-only; the API never runs migrations")}
              stale={poll.paused}
            />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={status.isFetching} />
          </>
        }
      >
        {(data) => <DomainTable status={data} />}
      </QuerySection>

      <QuerySection<DbHygiene>
        title={t("database.status.hygiene_title", "Hygiene worker (/api/db/hygiene)")}
        query={hygiene}
        skeletonRows={3}
        emptyMessage={t("database.status.hygiene_empty", "Hygiene worker state unavailable.")}
        right={<FreshnessCaption fetchedAtMs={hygiene.dataUpdatedAt || null} intervalMs={60_000} stale={poll.paused} />}
      >
        {(data) => (
          <>
            <HygieneBody hygiene={data} />
            <details className="dbc-raw" style={{ marginTop: 10 }}>
              <summary>{t("database.status.raw_hygiene", "raw hygiene payload")}</summary>
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
