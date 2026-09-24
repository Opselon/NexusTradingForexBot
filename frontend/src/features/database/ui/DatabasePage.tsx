/**
 * Database page shell — header, live summary chips, tab switch.
 *
 * The three tabs live in sibling files (this shell stays well under the
 * 500-line rule):
 *   ui/StatusTab.tsx  — provider truth, schema/migration state, hygiene worker
 *   ui/ManageTab.tsx  — PG config form + guarded migrate/backup/validate
 *   ui/ConsoleTab.tsx — explorer, column schema, read-only SQL, API keys
 *
 * Styling: feature-scoped `ui/database.css` (.dbc-*) on theme tokens +
 * the lane kit (config/ui/kit.css).  Nothing here invents data: chips are
 * rendered from /api/db/manage/status only.
 */

import { useMemo, useState } from "react";
import { Segmented, StatusBadge } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { FreshnessCaption } from "@/features/config/ui/kit";
import "@/features/config/ui/kit.css";
import "./database.css";
import type { ShellPageProps } from "@/app/featureModule";
import { formatBytes } from "../model";
import { useDbManageStatus } from "../useCases";
import { ageLabel, providerTruth, psycopgState } from "../uiLogic";
import { StatusTab } from "./StatusTab";
import { ManageTab } from "./ManageTab";
import { ConsoleTab } from "./ConsoleTab";

type TabId = "status" | "manage" | "console";

function useTabLabels() {
  const t = useI18n((s) => s.t);
  const TABS: Array<{ id: TabId; label: string }> = [
    { id: "status", label: t("database.tab.status", "Status") },
    { id: "manage", label: t("database.tab.manage", "Manage") },
    { id: "console", label: t("database.tab.console", "Console") },
  ];
  const TAB_HINT: Record<TabId, string> = {
    status: t(
      "database.page.hint_status",
      "provider, schema/migration state and the hygiene worker — read-only"
    ),
    manage: t(
      "database.page.hint_manage",
      "PostgreSQL connection, migration workflow and backups — type-confirmed operations only"
    ),
    console: t(
      "database.page.hint_console",
      "databases → tables → rows, column schema, read-only SQL console and named API keys"
    ),
  };
  return { TABS, TAB_HINT };
}

export default function DatabasePage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  const [tab, setTab] = useState<TabId>("status");
  const { TABS, TAB_HINT } = useTabLabels();
  const manage = useDbManageStatus();
  const provider = manage.data?.provider;
  // Branchy derivation over the manage payload (provider_truth + domain
  // walk) — memoized per response identity; same object, same verdict.
  const truth = useMemo(() => providerTruth(manage.data), [manage.data]);
  const psycopg = psycopgState(manage.data?.postgresql_driver_available);
  // mismatch = the 2026-09-23 complaint: badge says postgres, data is sqlite
  const mismatch = Boolean(truth?.mismatch);
  const providerLabel = mismatch && truth ? `${truth.configured} → ${truth.effective}` : provider ?? "…";

  return (
    <div className="dbc-stack">
      <div className="dbc-head">
        <h1>{t("database.page.platform", "Database")}</h1>
        <span className="crumb">{t("database.page.platform_tag", "PLATFORM")}</span>
        <span className="desc">{t("database.page.desc2", "persistence console — provider state, migration workflow, explorer, SQL, keys")}</span>
        <span className="dbc-head-actions">
          <span
            className="dbc-chip"
            title={truth?.note || t("database.page.chip_title", "active persistence provider (db/manage/status)")}
            data-mismatch={mismatch ? "true" : undefined}
          >
            <span
              className={`dbc-dot ${mismatch || psycopg.tone === "bad" ? "bad" : manage.data ? "good" : ""}`}
              aria-hidden="true"
            />
            <span className="inline-mono">{providerLabel}</span>
            {mismatch && <span className="sub">{t("database.page.data_from", "data from {provider}", { provider: truth?.effective ?? "" })}</span>}
            {!mismatch && psycopg.tone === "bad" && <span className="sub">{t("database.page.psycopg_missing", "psycopg missing")}</span>}
          </span>
          <StatusBadge status={String(manage.data?.overall ?? "UNKNOWN")} label={t("database.page.overall_health", "overall persistence health")} />
          <FreshnessCaption
            fetchedAtMs={manage.dataUpdatedAt || null}
            intervalMs={30_000}
            note="db/manage/status"
            stale={false}
          />
        </span>
      </div>

      <div className="dbc-note">{t("database.page.note", "Backups and migrations are operator-initiated and type-confirmed; nothing here can trade or mutate market logic. Secrets (PG password, API keys, telegram tokens) live in the OS SecretStore — the backend only ever reports status.")}</div>

      {truth?.mismatch && (
        <section className="dbc-band dbc-truth" aria-label={t("database.page.truth_aria", "configured vs effective provider")}>
          <div className={`dbc-band-item ${truth.source === "backend" ? "bad" : "info"}`}>
            <strong>
              {t("database.page.truth_band", "configured {configured} · effective {effective}", { configured: truth.configured, effective: truth.effective })}
            </strong>
            <span>{truth.note}</span>
            {truth.pgError && <span className="faint">{truth.pgError}</span>}
            {truth.source === "derived" && (
              <span className="faint">
                {t("database.page.derived_note", "derived client-side from domain connection counts — restart the engine to get the measured provider_truth payload (probe + file evidence)")}
              </span>
            )}
          </div>
          {truth.evidence.length > 0 && (
            <ul className="dbc-evi">
              {truth.evidence.map((e) => (
                <li key={e.name} className={e.active ? "active" : ""}>
                  <span className="inline-mono">{e.file}</span>
                  <span>{formatBytes(e.bytes)}</span>
                  <span>{t("database.page.evidence_written", "written {age}", { age: ageLabel(e.age_seconds) })}</span>
                  {e.active && <span className="tiny dbc-evi-live">{t("database.page.evidence_live", "live")}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <div className="dbc-tabs">
        <Segmented<TabId> options={TABS} value={tab} onChange={setTab} />
        <span className="dbc-tabmeta">{TAB_HINT[tab]}</span>
      </div>

      {tab === "status" && <StatusTab />}
      {tab === "manage" && <ManageTab />}
      {tab === "console" && <ConsoleTab />}
    </div>
  );
}
