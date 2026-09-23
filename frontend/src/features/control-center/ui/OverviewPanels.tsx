/**
 * PURPOSE:  Overview evidence panels — health metric cards, release identity,
 *           bounded ledger stats and backend warnings.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../model (OperatorSummaryDto + VO helpers), @/components/primitives,
 *           @/lib/format, ../../research/ui/lane5Kit (DistBars, InfoRow)
 * PROVIDES: OverviewPanels
 * INVARIANTS: every value is a verbatim backend field (or NOT RECORDED / —);
 *             pending → skeleton, error → visible error with retry; the Runtime
 *             card says UNKNOWN when engine_running was never recorded — it must
 *             never claim STOPPED for missing data; warnings render backend text.
 * EXTEND:   new panel/card = a direct field in model.ts first, then one JSX block.
 */
import { useMemo } from "react";
import { EmptyState, ErrorState, MetricCard, Panel, SeverityBadge, Skeleton, StatusBadge } from "@/components/primitives";
import { formatAgeMs, formatDateTime, formatPrice } from "@/lib/format";
import { DistBars, InfoRow } from "../../research/ui/lane5Kit";
import { arr, bool, notRecorded, num, obj, str, type OperatorSummaryDto } from "../model";
import { tickTone } from "./tones";
import { useI18n } from "@/stores/i18nStore";

interface OverviewPanelsProps {
  summary: OperatorSummaryDto | undefined;
  pending: boolean;
  error: boolean;
  errorMessage: string;
  onRetry: () => void;
}

export function OverviewPanels({ summary, pending, error, errorMessage, onRetry }: OverviewPanelsProps) {
  const t = useI18n((s) => s.t);
  if (pending) return <Skeleton count={6} />;
  if (error) return <ErrorState message={errorMessage} onRetry={onRetry} />;

  const s = summary;
  const rt = obj(s?.runtime);
  const idt = obj(s?.identity);
  const health = obj(rt.health);
  const subs = obj(health.subsystems);
  const engineField = bool(rt.engine_running);
  const engineStatus = engineField === null ? "UNKNOWN" : engineField ? "RUNNING" : "STOPPED";

  // perf: ledger action census is a DOT-map over the summary payload — derive
  // once per summary identity (15s poll) instead of every render.
  const actionRows = useMemo(
    () => Object.entries(obj(s?.ledger?.actions)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 })),
    [s?.ledger?.actions],
  );

  return (
    <>
      <div className="grid cols-4">
        <MetricCard label={t("control-center.kpi.runtime", "Runtime")} value={<StatusBadge status={engineStatus} />} sub={str(obj(health.details).engine) ?? undefined} />
        <MetricCard
          label={t("control-center.kpi.data_tick", "Data (tick)")}
          value={<StatusBadge status={tickTone(rt).text} />}
          sub={num(rt.tick_freshness_ms) === null ? t("control-center.kpi.freshness_not_recorded", "freshness NOT RECORDED") : t("control-center.kpi.tick_age", "tick age {age}", { age: formatAgeMs(num(rt.tick_freshness_ms)) })}
        />
        <MetricCard label={t("control-center.kpi.model", "Model")} value={<StatusBadge status={str(subs.model)} />} sub={str(obj(health.details).model) ?? undefined} />
        <MetricCard label={t("control-center.kpi.db_mt5", "Database / MT5")} value={<StatusBadge status={str(subs.database)} />} sub={t("control-center.kpi.mt5", "mt5: {v}", { v: str(subs.mt5) ?? "—" })} />
      </div>

      <div className="grid cols-2">
        <Panel title={t("control-center.panel.identity", "Runtime identity (release snapshot)")} tight>
          <dl className="kv">
            <InfoRow label={t("control-center.label.version", "version")} value={notRecorded(str(idt.version))} />
            <InfoRow label={t("control-center.label.commit", "commit")} value={notRecorded(`${str(idt.commit) ?? ""}${str(idt.commit_status) ? ` (${str(idt.commit_status)})` : ""}`)} />
            <InfoRow label={t("control-center.label.channel", "channel")} value={notRecorded(str(idt.channel))} />
            <InfoRow label={t("control-center.label.symbol_regime", "symbol / regime")} value={`${notRecorded(str(rt.symbol))} / ${notRecorded(str(rt.regime))}`} />
            <InfoRow
              label={t("control-center.label.bid_ask_spread", "bid / ask / spread")}
              value={`${formatPrice(num(rt.bid), 2)} / ${formatPrice(num(rt.ask), 2)} / ${formatPrice(num(rt.spread), 2)}`}
            />
            <InfoRow label="provenance.price" value={str(obj(rt.provenance).price) ?? "NOT RECORDED"} />
          </dl>
        </Panel>
        <Panel title={t("control-center.panel.ledger_stats", "Ledger stats (bounded recent window)")} tight>
          {s?.ledger?.available === false ? (
            <EmptyState message={t("control-center.empty.ledger_unavailable", "ledger unavailable")} hint={s.ledger.reason ?? "LEDGER_UNAVAILABLE"} />
          ) : (
            <>
              <dl className="kv" style={{ marginBottom: 8 }}>
                <InfoRow label={t("control-center.label.scanned_rows", "scanned rows")} value={String(s?.ledger?.scanned_rows ?? "—")} />
                <InfoRow label={t("control-center.label.latest_decision", "latest decision")} value={formatDateTime(s?.ledger?.latest_decision_at)} />
              </dl>
              <DistBars rows={actionRows} />
            </>
          )}
        </Panel>
      </div>

      <Panel title={t("control-center.panel.warnings", "Warnings (ledger-visible, backend-derived)")} tight>
        {arr(s?.warnings).length === 0 ? (
          <EmptyState message={t("control-center.empty.no_warnings", "No warnings reported.")} />
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {arr(s?.warnings).map((w, i) => (
              <div key={i} style={{ borderInlineStart: "2px solid var(--amber)", paddingInlineStart: 8 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <SeverityBadge severity={str(w.severity)} />
                  <span className="small">{str(w.what)}</span>
                </div>
                <div className="tiny muted">
                  {t("control-center.warn.why", "why")}: {str(w.why) ?? "—"} · {t("control-center.warn.impact", "impact")}: {str(w.impact) ?? "—"} · {t("control-center.warn.todo", "do")}: {str(w.what_to_do) ?? "—"}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}
