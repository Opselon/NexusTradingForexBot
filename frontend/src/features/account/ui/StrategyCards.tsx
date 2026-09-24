/**
 * StrategyCards — analytics-studio contribution cards (wave 6).
 *
 * PURPOSE:  render the SAME strategy rows StrategiesDashboard already shows as
 *           a grid of contribution cards: signed net PnL (token color), a
 *           confidence meter showing the RAW backend number, and a loss-share
 *           strip. This is a new VIEW of existing data — it never re-scores,
 *           never invents fields, and never hides the table (the caller keeps
 *           both side by side; the toggle is a display preference).
 * OWNER:    uiux-w6-account
 * CONSUMES: StrategyContribution (../types), ./shared (moneyOrDash), the
 *           copy-handler the dashboard already uses, ./studio-math (deltaTone)
 * PROVIDES: <StrategyCards rows onCopy />
 * INVARIANTS: every number printed came from /api/account/strategies verbatim;
 *           null fields render "—", never 0 (BUG-020 lineage); confidence and
 *           loss_share are 0..1 ratios and the label always shows the raw
 *           value so the meter is never misread as a percentage; percentages
 *           are computed ONLY for display and are labeled as such.
 * EXTEND:   a new meter needs a field that already exists on
 *           StrategyContribution — never widen the DTO.
 */

import { useI18n } from "@/stores/i18nStore";
import { moneyOrDash, numOrDash } from "./shared";

/** The fields the cards read (a subset of StrategyContribution). */
export interface StrategyCardRow {
  strategy_id: string;
  net_pnl?: number | null;
  confidence?: number | null;
  loss_share?: number | null;
  win_rate?: number | null;
  trade_count?: number | null;
  lifecycle_state?: string;
}

export interface StrategyCardsProps {
  rows: StrategyCardRow[];
  /** Same onCopy the metrics table uses (id, clipboard-ok) — toast handled by caller. */
  onCopy?: (id: string, ok: boolean) => void;
}

/** Confidence tier label — informational, same thresholds as StrategiesDashboard. */
function confTier(v: number): "good" | "warn" | "bad" {
  if (v >= 0.7) return "good";
  if (v >= 0.5) return "warn";
  return "bad";
}

/** A ratio 0..1 -> pct string; null → "—" (never "0%"). */
function ratioPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${(Math.min(1, Math.max(0, v)) * 100).toFixed(digits)}%`;
}

/** Copy with a guarded clipboard call — the caller decides the toast. */
function copyId(id: string, onCopy?: (id: string, ok: boolean) => void) {
  const done = (ok: boolean) => onCopy?.(id, ok);
  try {
    if (typeof navigator === "undefined" || !navigator.clipboard) return done(false);
    void navigator.clipboard.writeText(id).then(() => done(true), () => done(false));
  } catch {
    done(false);
  }
}

export function StrategyCards({ rows, onCopy }: StrategyCardsProps) {
  const t = useI18n((s) => s.t);
  if (rows.length === 0) {
    return <div className="acc-scard-empty">{t("account.strategies.cards_empty", "no strategy contributions in range")}</div>;
  }
  return (
    <div className="acc-scard-grid">
      {rows.map((s) => {
        const tone = s.net_pnl === null || s.net_pnl === undefined
          ? "dim"
          : s.net_pnl > 0
            ? "pos"
            : s.net_pnl < 0
              ? "neg"
              : "dim";
        const conf = s.confidence ?? null;
        return (
          <article key={s.strategy_id} className="acc-scard" data-pnl={tone}>
            <div className="acc-scard-h">
              <span className="acc-scard-id" title={s.strategy_id}>{s.strategy_id}</span>
              <button
                className="acc-scard-copy"
                onClick={() => copyId(s.strategy_id, onCopy)}
                title={t("account.strategies.copy_id", "copy strategy id")}
                aria-label={t("account.strategies.copy_aria", "copy {id}", { id: s.strategy_id })}
              >
                ⧉
              </button>
            </div>

            <div className={`acc-scard-pnl ${tone}`}>{moneyOrDash(s.net_pnl, true)}</div>

            {conf !== null && (
              <div className="acc-scard-meter" title={t("account.strategies.conf_meter", "confidence — backend score, raw value shown")}>
                <span className="lab">{t("account.strategies.lab_conf", "conf")}</span>
                <span className="acc-scard-track">
                  <i
                    className={`acc-scard-fill ${confTier(conf)}`}
                    style={{ inlineSize: `${Math.min(100, Math.max(0, conf * 100))}%` }}
                  />
                </span>
                <span className="val">{numOrDash(conf, 4)}</span>
              </div>
            )}

            {s.loss_share !== null && s.loss_share !== undefined && (
              <div className="acc-scard-meter" title={t("account.strategies.loss_meter", "share of account gross loss (backend ratio)")}>
                <span className="lab">{t("account.strategies.lab_loss", "loss")}</span>
                <span className="acc-scard-track">
                  <i
                    className="acc-scard-fill bad"
                    style={{ inlineSize: `${Math.min(100, Math.max(0, (s.loss_share ?? 0) * 100))}%` }}
                  />
                </span>
                <span className="val">{ratioPct(s.loss_share)}</span>
              </div>
            )}

            <div className="acc-scard-meta">
              <span>win {ratioPct(s.win_rate)}</span>
              <span>n {s.trade_count ?? "—"}</span>
              {s.lifecycle_state && <span>{s.lifecycle_state}</span>}
            </div>
          </article>
        );
      })}
    </div>
  );
}
