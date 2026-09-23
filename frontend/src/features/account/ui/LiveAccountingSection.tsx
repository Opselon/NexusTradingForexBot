/**
 * Live accounting panel — GET /api/live/accounting.
 *
 * This is the RiskEngine's own arithmetic (the SAME engine the live loop uses)
 * exposed for the console: without parameters it reports the live account
 * state; with equity/entry/stop_loss it recomputes the deterministic risk plan.
 * The UI never duplicates lot/risk math — the inputs are only forwarded after
 * inline range validation, and the numbers shown are the response's.
 */

import { useState, type ChangeEvent } from "react";
import { EmptyState, ErrorState, Panel } from "@/components/primitives";
import { useLiveAccounting } from "../hooks";
import { validatePlanInputs } from "../model";
import { DASH, FreshnessNote, errorProps, moneyOrDash, numOrDash, pctOrDash } from "./shared";

const EMPTY = { equity: "", entry: "", stopLoss: "", riskPct: "" };

export function LiveAccountingSection() {
  const [inputs, setInputs] = useState(EMPTY);
  const [applied, setApplied] = useState<Record<string, number>>({});
  const errors = validatePlanInputs(inputs);
  const hasError = Object.values(errors).some((e) => e !== null);
  const live = useLiveAccounting(applied);

  const d = live.data;
  const set = (k: keyof typeof EMPTY) => (e: ChangeEvent<HTMLInputElement>) => setInputs((v) => ({ ...v, [k]: e.target.value }));

  const apply = (): void => {
    if (hasError) return;
    const next: Record<string, number> = {};
    if (inputs.equity.trim()) next.equity = Number(inputs.equity);
    if (inputs.entry.trim()) next.entry = Number(inputs.entry);
    if (inputs.stopLoss.trim()) next.stopLoss = Number(inputs.stopLoss);
    if (inputs.riskPct.trim()) next.riskPct = Number(inputs.riskPct);
    setApplied(next);
  };

  const reset = (): void => {
    setInputs(EMPTY);
    setApplied({});
  };

  const plan = d?.plan;

  return (
    <Panel
      title="Live accounting & risk plan"
      right={
        <>
          <span className="acct-chip">{d?.available ? d.source || "RISK_ENGINE" : "UNAVAILABLE"}</span>
          <FreshnessNote updatedAtMs={live.dataUpdatedAt ?? null} label="live" staleAfterMs={60_000} />
        </>
      }
    >
      {live.isPending ? (
        <div className="viz-empty">querying the risk engine…</div>
      ) : live.isError ? (
        <ErrorState {...errorProps(live.error)} onRetry={() => live.refetch()} />
      ) : !d?.available ? (
        <EmptyState message={d?.reason === "ENGINE_OFFLINE" ? "Trading engine offline — no accounting state to read." : d?.reason === "NO_LIVE_EQUITY" ? "No live equity available from the adapter." : "Live accounting unavailable."} hint="The console never estimates these numbers locally." />
      ) : (
        <>
          <div className="grid cols-2">
            <dl className="acct-plan">
              <dt>balance</dt>
              <dd>{moneyOrDash(d.live?.balance)}</dd>
              <dt>equity</dt>
              <dd>{moneyOrDash(d.live?.equity)}</dd>
              <dt>floating PnL</dt>
              <dd className={ (d.live?.floating_pnl ?? 0) >= 0 ? "tx-good" : "tx-bad" } >{moneyOrDash(d.live?.floating_pnl, true)}</dd>
              <dt>free margin</dt>
              <dd>{moneyOrDash(d.live?.margin_free)}</dd>
              <dt>drawdown</dt>
              <dd>{pctOrDash(d.live?.drawdown_pct)}</dd>
              <dt>win rate</dt>
              <dd>{pctOrDash(d.live?.win_rate, 1)}</dd>
              <dt>open positions</dt>
              <dd>{d.live?.open_positions ?? DASH}</dd>
            </dl>
            <div>
              <div className="section-title">compute a risk plan (forwarded to the engine)</div>
              <div className="acct-actions" style={{ marginBottom: 8 }}>
                <Field label="equity $" value={inputs.equity} onChange={set("equity")} error={errors.equity} placeholder="e.g. 10000" />
                <Field label="entry" value={inputs.entry} onChange={set("entry")} error={errors.entry} placeholder="e.g. 2650.00" />
                <Field label="stop" value={inputs.stopLoss} onChange={set("stopLoss")} error={errors.stopLoss} placeholder="e.g. 2640.00" />
                <Field label="risk %" value={inputs.riskPct} onChange={set("riskPct")} error={errors.riskPct} placeholder="0.5" />
              </div>
              <div className="acct-actions">
                <button className="btn small primary" onClick={apply} disabled={hasError}>
                  Compute plan
                </button>
                <button className="btn small ghost" onClick={reset}>
                  Use live defaults
                </button>
                {hasError && <span className="acct-field-error">fix the highlighted fields</span>}
              </div>
              <div className="tiny faint" style={{ marginTop: 6 }}>
                ranges: equity 0.01–1e9, price 0.00001–1e6, risk 0.01–100%. All lot/risk math runs server-side through the same RiskEngine as live
                execution — no JS-side accounting.
              </div>
            </div>
          </div>

          {plan ? (
            <div className="grid cols-2" style={{ marginTop: 12 }}>
              <dl className="acct-plan">
                <dt>risk USD</dt>
                <dd>{moneyOrDash(plan.risk_usd)}</dd>
                <dt>lot size</dt>
                <dd>{numOrDash(plan.lot_size, 2)}</dd>
                <dt>margin required</dt>
                <dd>{moneyOrDash(plan.margin_required)}</dd>
                <dt>exposure</dt>
                <dd>{pctOrDash(plan.exposure_pct)}</dd>
                <dt>entry / stop</dt>
                <dd>
                  {numOrDash(plan.entry, 2)} / {numOrDash(plan.stop_loss, 2)}
                </dd>
                <dt>SL distance</dt>
                <dd>{numOrDash(plan.sl_distance, 2)}</dd>
                <dt>min lot / step</dt>
                <dd>
                  {numOrDash(plan.min_lot as number | undefined, 2)} / {numOrDash(plan.lot_step as number | undefined, 2)}
                </dd>
              </dl>
              <div>
                {plan.note ? <div className="small muted">note: {String(plan.note)}</div> : null}
                <div className="tiny faint" style={{ marginTop: 6 }}>
                  plan equity {moneyOrDash(plan.equity)} @ risk {numOrDash(plan.risk_pct, 2)}% — deterministic from the submitted inputs
                  {Object.keys(applied).length === 0 ? " (live defaults)" : ""}
                </div>
              </div>
            </div>
          ) : (
            <div className="tiny faint" style={{ marginTop: 10 }}>
              No plan block returned — the engine could not price a stop from the current tick/ATR; nothing is inferred.
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

function Field({
  label,
  value,
  onChange,
  error,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (e: ChangeEvent<HTMLInputElement>) => void;
  error: string | null | undefined;
  placeholder?: string;
}) {
  return (
    <label style={{ display: "grid", gap: 2, fontSize: 10, color: "var(--text-faint)" }}>
      {label}
      <input className={`input ${error ? "invalid" : ""}`} style={{ width: 96 }} value={value} onChange={onChange} placeholder={placeholder} aria-label={label} aria-invalid={!!error} />
      {error && <span className="acct-field-error">{error}</span>}
    </label>
  );
}
