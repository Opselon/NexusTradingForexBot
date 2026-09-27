import { useState } from "react";
import { ResultStrip, TypedConfirmModal } from "@/features/config/ui/kit";
import { Panel, StatusBadge } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { useDivergenceCheck, useProviderState, useProviderTransition, useReverseMigrate } from "../useCases";

export function ProviderTab() {
  const t = useI18n((s) => s.t);
  const state = useProviderState();
  const transition = useProviderTransition();
  const divergence = useDivergenceCheck();
  const reverse = useReverseMigrate();
  const [target, setTarget] = useState<string | null>(null);
  const [reverseOpen, setReverseOpen] = useState(false);
  const current = state.data?.state;
  const result = (body: { success?: boolean; error?: unknown } | undefined) => body ? { running: false, lastResult: body.success === true, lastMessage: typeof body.error === "string" ? body.error : body.success === true ? t("database.provider.ok", "Operation completed.") : t("database.provider.failed", "Operation failed.") } : null;
  return <div className="dbc-stack">
    <Panel title={t("database.provider.title", "Provider lifecycle") } accent>
      {!current ? <div className="dbc-note warn">{t("database.provider.unavailable", "UNAVAILABLE — provider lifecycle state was not reported.")}</div> : <>
        <div className="dbc-kpis"><div className="metric"><div className="k">{t("database.provider.phase", "phase")}</div><div className="v"><StatusBadge status={current.phase ?? "UNKNOWN"} /></div></div><div className="metric"><div className="k">{t("database.provider.active", "active provider")}</div><div className="v inline-mono">{current.active_provider ?? "—"}</div></div><div className="metric"><div className="k">{t("database.provider.target", "target provider")}</div><div className="v inline-mono">{current.target_provider ?? "—"}</div></div></div>
        {current.error && <div className="dbc-note bad">{current.error}</div>}
        <div className="dbc-actions"><button className="btn" aria-label={t("database.provider.transition_sqlite_aria", "transition to sqlite")} onClick={() => setTarget("sqlite")}>{t("database.provider.transition_sqlite", "Transition to SQLite")}</button><button className="btn" aria-label={t("database.provider.transition_postgresql_aria", "transition to postgresql")} onClick={() => setTarget("postgresql")}>{t("database.provider.transition_postgresql", "Transition to PostgreSQL")}</button><button className="btn" aria-label={t("database.provider.divergence_aria", "check provider divergence")} disabled={divergence.isPending} onClick={() => void divergence.mutateAsync()}>{t("database.provider.divergence", "Check divergence")}</button><button className="btn danger" aria-label={t("database.provider.reverse_aria", "reverse migration")} disabled={reverse.isPending} onClick={() => setReverseOpen(true)}>{t("database.provider.reverse", "Reverse migration…")}</button></div>
        <ResultStrip result={result(transition.data)} /><ResultStrip result={result(divergence.data)} /><ResultStrip result={result(reverse.data)} />
        {divergence.data?.divergence && <pre className="dbc-log-list">{JSON.stringify(divergence.data.divergence, null, 2)}</pre>}
      </>}
    </Panel>
    {reverseOpen && <TypedConfirmModal title={t("database.provider.reverse_title", "Reverse migration")} word="REVERSE" confirmLabel={t("database.provider.reverse_confirm", "Reverse migration")} busy={reverse.isPending} onCancel={() => setReverseOpen(false)} onConfirm={() => void reverse.mutateAsync().then(() => setReverseOpen(false))} body={t("database.provider.reverse_body", "This copies data back through the backend reverse-migration workflow. Type REVERSE to continue.")} />}
    {target && <TypedConfirmModal title={t("database.provider.transition_title", "Start provider transition") } word="TRANSITION" confirmLabel={t("database.provider.transition_confirm", "Start transition")} busy={transition.isPending} onCancel={() => setTarget(null)} onConfirm={() => void transition.mutateAsync(target).then(() => setTarget(null))} body={t("database.provider.transition_body", "The backend will advance the provider state machine. Type TRANSITION to continue.")} />}
  </div>;
}
