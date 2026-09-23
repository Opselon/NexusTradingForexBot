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
import { EmptyState, ErrorState, MetricCard, Panel, SeverityBadge, Skeleton, StatusBadge } from "@/components/primitives";
import { formatAgeMs, formatDateTime, formatPrice } from "@/lib/format";
import { DistBars, InfoRow } from "../../research/ui/lane5Kit";
import { arr, bool, notRecorded, num, obj, str, type OperatorSummaryDto } from "../model";
import { tickTone } from "./tones";

interface OverviewPanelsProps {
  summary: OperatorSummaryDto | undefined;
  pending: boolean;
  error: boolean;
  errorMessage: string;
  onRetry: () => void;
}

export function OverviewPanels({ summary, pending, error, errorMessage, onRetry }: OverviewPanelsProps) {
  if (pending) return <Skeleton count={6} />;
  if (error) return <ErrorState message={errorMessage} onRetry={onRetry} />;

  const s = summary;
  const rt = obj(s?.runtime);
  const idt = obj(s?.identity);
  const health = obj(rt.health);
  const subs = obj(health.subsystems);
  const engineField = bool(rt.engine_running);
  const engineStatus = engineField === null ? "UNKNOWN" : engineField ? "RUNNING" : "STOPPED";

  return (
    <>
      <div className="grid cols-4">
        <MetricCard label="Runtime" value={<StatusBadge status={engineStatus} />} sub={str(obj(health.details).engine) ?? undefined} />
        <MetricCard
          label="Data (tick)"
          value={<StatusBadge status={tickTone(rt).text} />}
          sub={num(rt.tick_freshness_ms) === null ? "freshness NOT RECORDED" : `tick age ${formatAgeMs(num(rt.tick_freshness_ms))}`}
        />
        <MetricCard label="Model" value={<StatusBadge status={str(subs.model)} />} sub={str(obj(health.details).model) ?? undefined} />
        <MetricCard label="Database / MT5" value={<StatusBadge status={str(subs.database)} />} sub={`mt5: ${str(subs.mt5) ?? "—"}`} />
      </div>

      <div className="grid cols-2">
        <Panel title="Runtime identity (release snapshot)" tight>
          <dl className="kv">
            <InfoRow label="version" value={notRecorded(str(idt.version))} />
            <InfoRow label="commit" value={notRecorded(`${str(idt.commit) ?? ""}${str(idt.commit_status) ? ` (${str(idt.commit_status)})` : ""}`)} />
            <InfoRow label="channel" value={notRecorded(str(idt.channel))} />
            <InfoRow label="symbol / regime" value={`${notRecorded(str(rt.symbol))} / ${notRecorded(str(rt.regime))}`} />
            <InfoRow
              label="bid / ask / spread"
              value={`${formatPrice(num(rt.bid), 2)} / ${formatPrice(num(rt.ask), 2)} / ${formatPrice(num(rt.spread), 2)}`}
            />
            <InfoRow label="provenance.price" value={str(obj(rt.provenance).price) ?? "NOT RECORDED"} />
          </dl>
        </Panel>
        <Panel title="Ledger stats (bounded recent window)" tight>
          {s?.ledger?.available === false ? (
            <EmptyState message="ledger unavailable" hint={s.ledger.reason ?? "LEDGER_UNAVAILABLE"} />
          ) : (
            <>
              <dl className="kv" style={{ marginBottom: 8 }}>
                <InfoRow label="scanned rows" value={String(s?.ledger?.scanned_rows ?? "—")} />
                <InfoRow label="latest decision" value={formatDateTime(s?.ledger?.latest_decision_at)} />
              </dl>
              <DistBars rows={Object.entries(obj(s?.ledger?.actions)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))} />
            </>
          )}
        </Panel>
      </div>

      <Panel title="Warnings (ledger-visible, backend-derived)" tight>
        {arr(s?.warnings).length === 0 ? (
          <EmptyState message="No warnings reported." />
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {arr(s?.warnings).map((w, i) => (
              <div key={i} style={{ borderInlineStart: "2px solid var(--amber)", paddingInlineStart: 8 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <SeverityBadge severity={str(w.severity)} />
                  <span className="small">{str(w.what)}</span>
                </div>
                <div className="tiny muted">
                  why: {str(w.why) ?? "—"} · impact: {str(w.impact) ?? "—"} · do: {str(w.what_to_do) ?? "—"}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}
