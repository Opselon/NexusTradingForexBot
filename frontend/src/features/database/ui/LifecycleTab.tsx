import { useState } from "react";
import { ResultStrip, TypedConfirmModal } from "@/features/config/ui/kit";
import { DataTable, EmptyState, Panel, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { useMaintenance, usePurgeHistory, usePurgePolicies, usePurgePreview, usePurgeRun, useUpdatePurgePolicy } from "../useCases";
import type { PurgePreviewItem, RetentionPolicy } from "../api";

const display = (v: unknown) => v === null || v === undefined || v === "" ? "—" : String(v);

export function LifecycleTab() {
  const t = useI18n((s) => s.t);
  const policies = usePurgePolicies();
  const preview = usePurgePreview();
  const history = usePurgeHistory();
  const purge = usePurgeRun();
  const maintenance = useMaintenance();
  const update = useUpdatePurgePolicy();
  const [guard, setGuard] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const rows = policies.data?.policies ? Object.values(policies.data.policies) : [];
  const previewRows: PurgePreviewItem[] = preview.data?.preview ?? [];
  const run = async () => { const result = await purge.mutateAsync(); if (result.success) setGuard(false); };
  const policySave = (p: RetentionPolicy) => {
    const raw = edits[p.table_name];
    const days = Number(raw);
    if (Number.isInteger(days) && days > 0) void update.mutateAsync({ table: p.table_name, days });
  };
  return <div className="dbc-stack">
    <Panel title={t("database.lifecycle.policies", "Retention policies")} accent>
      {policies.isError ? <div className="dbc-note warn">{t("database.lifecycle.unavailable", "UNAVAILABLE — retention policy route is not available on this engine.")}</div> : policies.isPending ? <Skeleton count={4} /> : rows.length === 0 ? <EmptyState message={t("database.lifecycle.no_policies", "No retention policies reported.")} /> :
        <DataTable headers={[{ label: t("database.lifecycle.table", "TABLE") }, { label: t("database.lifecycle.tier", "TIER") }, { label: t("database.lifecycle.days", "RETENTION DAYS"), num: true }, { label: t("database.lifecycle.timestamp", "TIMESTAMP COLUMN") }, { label: t("database.lifecycle.description", "DESCRIPTION") }, { label: t("database.lifecycle.action", "ACTION") }]}>
          {rows.map((p) => { const immutable = String(p.tier).toUpperCase() === "IMMUTABLE"; return <tr key={p.table_name}><td className="inline-mono">{p.table_name}</td><td><span className={`badge ${immutable ? "warn" : ""}`}>{display(p.tier)}</span></td><td className="num"><input className="input dbc-days" aria-label={t("database.lifecycle.edit_days", "Edit retention days for {table}", { table: p.table_name })} type="number" min={1} disabled={immutable} value={edits[p.table_name] ?? p.retention_days ?? ""} onChange={(e) => setEdits({ ...edits, [p.table_name]: e.target.value })} /></td><td>{display(p.timestamp_column)}</td><td>{display(p.description)}</td><td><button className="btn small" aria-label={t("database.lifecycle.save_policy", "Save retention policy for {table}", { table: p.table_name })} disabled={immutable || update.isPending || edits[p.table_name] === undefined} onClick={() => policySave(p)}>{immutable ? t("database.lifecycle.immutable", "immutable") : t("database.lifecycle.save", "Save")}</button></td></tr>; })}
        </DataTable>}
    </Panel>
    <Panel title={t("database.lifecycle.preview", "Purge preview")}>
      <div className="dbc-actions"><button className="btn" aria-label={t("database.lifecycle.preview_action", "Preview purgeable rows")} disabled={preview.isPending} onClick={() => void preview.mutateAsync()}>{preview.isPending ? t("database.lifecycle.previewing", "previewing…") : t("database.lifecycle.preview_action", "Preview purgeable rows")}</button><button className="btn danger" aria-label={t("database.lifecycle.run_action", "Run purge")} disabled={purge.isPending} onClick={() => setGuard(true)}>{t("database.lifecycle.run_action", "Run purge")}</button><button className="btn" aria-label={t("database.lifecycle.maintenance_action", "Run database maintenance")} disabled={maintenance.isPending} onClick={() => void maintenance.mutateAsync()}>{t("database.lifecycle.maintenance_action", "Run maintenance")}</button></div>
      {preview.isError ? <div className="dbc-note warn">{t("database.lifecycle.preview_unavailable", "UNAVAILABLE — purge preview could not be loaded.")}</div> : previewRows.length > 0 && <DataTable headers={[{ label: t("database.lifecycle.table", "TABLE") }, { label: t("database.lifecycle.tier", "TIER") }, { label: t("database.lifecycle.purgeable", "PURGEABLE ROWS"), num: true }, { label: t("database.lifecycle.cutoff", "CUTOFF") }]}>{previewRows.map((p) => <tr key={p.table_name}><td className="inline-mono">{p.table_name}</td><td>{display(p.tier)}</td><td className="num">{display(p.purgeable_rows)}</td><td>{display(p.cutoff_timestamp)}</td></tr>)}</DataTable>}
      <ResultStrip result={purge.data ? { running: false, lastResult: purge.data.success === true, lastMessage: purge.data.result ? `${t("database.lifecycle.deleted", "Deleted rows")}: ${display(purge.data.result.total_deleted)}` : null } : null} />
    </Panel>
    <Panel title={t("database.lifecycle.history", "Purge history")}>
      {history.isError ? <div className="dbc-note warn">{t("database.lifecycle.history_unavailable", "UNAVAILABLE — purge history is not available on this engine.")}</div> : (history.data?.history ?? []).length === 0 ? <EmptyState message={t("database.lifecycle.no_history", "No purge runs reported.")} /> : <DataTable headers={[{ label: t("database.lifecycle.started", "STARTED") }, { label: t("database.lifecycle.deleted", "DELETED"), num: true }, { label: t("database.lifecycle.duration", "DURATION"), num: true }, { label: t("database.lifecycle.result", "RESULT") }]}>{(history.data?.history ?? []).map((h, i) => <tr key={`${h.started_at}-${i}`}><td>{display(h.started_at)}</td><td className="num">{display(h.total_deleted)}</td><td className="num">{h.duration_ms === undefined ? "—" : `${h.duration_ms} ms`}</td><td>{h.errors?.length ? t("database.lifecycle.failed", "FAILED") : t("database.lifecycle.success", "SUCCESS")}</td></tr>)}</DataTable>}
    </Panel>
    {guard && <TypedConfirmModal title={t("database.lifecycle.confirm_title", "Run destructive database purge")} word="PURGE" confirmLabel={t("database.lifecycle.confirm_run", "Run purge")} busy={purge.isPending} onCancel={() => setGuard(false)} onConfirm={() => void run()} body={t("database.lifecycle.confirm_body", "Deletes backend-selected expired rows in bounded batches. Immutable tables are protected by the backend. Type PURGE to continue.")} />}
  </div>;
}
