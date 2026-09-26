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
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { useLiveAccounting } from "../hooks";
import { validatePlanInputs } from "../model";
import { DASH, FreshnessNote, asErrorText, moneyOrDash, numOrDash, pctOrDash } from "./shared";
import "./account.css";
import { useI18n } from "@/stores/i18nStore";

const EMPTY = { equity: "", entry: "", stopLoss: "", riskPct: "" };

type Translator = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** Translate the validation messages model.ts builds for this form. */
function planErrT(raw: string | null | undefined, t: Translator): string | undefined {
  if (!raw) return raw ?? undefined;
  switch (raw) {
    case "required": return t("account.plan.required", "required");
    case "must be a positive number": return t("account.plan.positive", "must be a positive number");
    case "stop must differ from entry": return t("account.plan.stop_differs", "stop must differ from entry");
    default:
      if (raw.startsWith("must be ≥ ")) return t("account.plan.min", "must be ≥ {min}", { min: raw.slice("must be ≥ ".length) });
      if (raw.startsWith("must be ≤ ")) return t("account.plan.max", "must be ≤ {max}", { max: raw.slice("must be ≤ ".length) });
      return raw;
  }
}

export function LiveAccountingSection() {
  const t = useI18n((s) => s.t);
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
      title={t("account.live.title", "Live accounting & risk plan")}
      right={
        <>
          <span className="acct-chip">{d?.available ? d.source || "RISK_ENGINE" : t("account.live.unavailable", "UNAVAILABLE")}</span>
          <FreshnessNote updatedAtMs={live.dataUpdatedAt ?? null} label={t("account.fresh.live", "live")} staleAfterMs={60_000} />
        </>
      }
    >
      {live.isPending ? (
        <div className="acc-state-loading" role="status">
          <span>{t("account.live.querying", "querying the risk engine…")}</span>
          <Skeleton count={2} height={24} />
        </div>
      ) : live.isError ? (
        <ErrorState message={asErrorText(live.error, t)} onRetry={() => live.refetch()} />
      ) : !d?.available ? (
        <EmptyState message={d?.reason === "ENGINE_OFFLINE" ? t("account.live.offline", "Trading engine offline — no accounting state to read.") : d?.reason === "NO_LIVE_EQUITY" ? t("account.live.no_equity", "No live equity available from the adapter.") : t("account.live.unavailable_state", "Live accounting unavailable.")} hint={t("account.live.unavailable_hint", "The console never estimates these numbers locally.")} />
      ) : (
        <>
          <div className="grid cols-2">
            <dl className="acct-plan" dir="ltr">
              <dt>{t("account.summary.balance", "balance")}</dt>
              <dd>{moneyOrDash(d.live?.balance)}</dd>
              <dt>{t("account.summary.equity", "equity")}</dt>
              <dd>{moneyOrDash(d.live?.equity)}</dd>
              <dt>{t("account.summary.floating_pnl", "floating PnL")}</dt>
              <dd className={ (d.live?.floating_pnl ?? 0) >= 0 ? "tx-good" : "tx-bad" } >{moneyOrDash(d.live?.floating_pnl, true)}</dd>
              <dt>{t("account.live.free_margin", "free margin")}</dt>
              <dd>{moneyOrDash(d.live?.margin_free)}</dd>
              <dt>{t("account.live.drawdown", "drawdown")}</dt>
              <dd>{pctOrDash(d.live?.drawdown_pct)}</dd>
              <dt>{t("account.live.win_rate", "win rate")}</dt>
              <dd>{pctOrDash(d.live?.win_rate, 1)}</dd>
              <dt>{t("account.live.open_positions", "open positions")}</dt>
              <dd>{d.live?.open_positions ?? DASH}</dd>
            </dl>
            <div>
              <div className="section-title">{t("account.live.compute_title", "compute a risk plan (forwarded to the engine)")}</div>
              <div className="acct-actions" style={{ marginBottom: 8 }}>
                <Field label={t("account.live.f_equity", "equity $")} value={inputs.equity} onChange={set("equity")} error={planErrT(errors.equity, t)} placeholder={t("account.live.ph_equity", "e.g. 10000")} />
                <Field label={t("account.live.f_entry", "entry")} value={inputs.entry} onChange={set("entry")} error={planErrT(errors.entry, t)} placeholder={t("account.live.ph_entry", "e.g. 2650.00")} />
                <Field label={t("account.live.f_stop", "stop")} value={inputs.stopLoss} onChange={set("stopLoss")} error={planErrT(errors.stopLoss, t)} placeholder={t("account.live.ph_stop", "e.g. 2640.00")} />
                <Field label={t("account.live.f_risk", "risk %")} value={inputs.riskPct} onChange={set("riskPct")} error={planErrT(errors.riskPct, t)} placeholder="0.5" />
              </div>
              <div className="acct-actions">
                <button className="btn small primary" onClick={apply} disabled={hasError}>
                  {t("account.live.compute", "Compute plan")}
                </button>
                <button className="btn small ghost" onClick={reset}>
                  {t("account.live.defaults", "Use live defaults")}
                </button>
                {hasError && <span className="acct-field-error">{t("account.live.fix_fields", "fix the highlighted fields")}</span>}
              </div>
              <div className="tiny faint" style={{ marginTop: 6 }}>
                {t("account.live.ranges_note", "ranges: equity 0.01–1e9, price 0.00001–1e6, risk 0.01–100%. All lot/risk math runs server-side through the same RiskEngine as live execution — no JS-side accounting.")}
              </div>
            </div>
          </div>

          {plan ? (
            <div className="grid cols-2" style={{ marginTop: 12 }}>
              <dl className="acct-plan" dir="ltr">
                <dt>{t("account.live.risk_usd", "risk USD")}</dt>
                <dd>{moneyOrDash(plan.risk_usd)}</dd>
                <dt>{t("account.live.lot_size", "lot size")}</dt>
                <dd>{numOrDash(plan.lot_size, 2)}</dd>
                <dt>{t("account.live.margin_required", "margin required")}</dt>
                <dd>{moneyOrDash(plan.margin_required)}</dd>
                <dt>{t("account.live.exposure", "exposure")}</dt>
                <dd>{pctOrDash(plan.exposure_pct)}</dd>
                <dt>{t("account.live.entry_stop", "entry / stop")}</dt>
                <dd>
                  {numOrDash(plan.entry, 2)} / {numOrDash(plan.stop_loss, 2)}
                </dd>
                <dt>{t("account.live.sl_distance", "SL distance")}</dt>
                <dd>{numOrDash(plan.sl_distance, 2)}</dd>
                <dt>{t("account.live.min_lot", "min lot / step")}</dt>
                <dd>
                  {numOrDash(plan.min_lot as number | undefined, 2)} / {numOrDash(plan.lot_step as number | undefined, 2)}
                </dd>
              </dl>
              <div>
                {plan.note ? <div className="small muted">{t("account.live.note", "note: {note}", { note: String(plan.note) })}</div> : null}
                <div className="tiny faint" style={{ marginTop: 6 }}>
                  {t("account.live.plan_footer", "plan equity {equity} @ risk {risk}% — deterministic from the submitted inputs", { equity: moneyOrDash(plan.equity), risk: numOrDash(plan.risk_pct, 2) })}
                  {Object.keys(applied).length === 0 ? t("account.live.live_defaults_suffix", " (live defaults)") : ""}
                </div>
              </div>
            </div>
          ) : (
            <div className="tiny faint" style={{ marginTop: 10 }}>
              {t("account.live.no_plan", "No plan block returned — the engine could not price a stop from the current tick/ATR; nothing is inferred.")}
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
