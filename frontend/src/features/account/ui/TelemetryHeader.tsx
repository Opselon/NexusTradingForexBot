/**
 * TelemetryHeader — compact top telemetry strip for the accounting console.
 *
 * System status pill with an animated pulse dot, market context tag,
 * UTC timestamp, and a non-intrusive toast banner for stale data (instead of
 * a raw full-width plain-text bar).
 *
 * Every value is backend-served: /api/account/performance/{kind} supplies the
 * market block (state / last_tick_age_sec / server_time_utc) and the live
 * block supplies balance/equity/floating. The UI never invents a latency or a
 * market state. When the engine is unavailable the pill degrades to
 * "DISCONNECTED" honestly — no fake "LIVE".
 */

import { useEffect, useState } from "react";
import { useAccountPerformance, useAccountPeriod } from "../hooks";
import { useUiStore } from "@/stores/uiStore";
import { DASH, moneyOrDash } from "./shared";
import { formatAgeMs } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import "./telemetry-header.css";

const STALE_AFTER_MS = 45_000;
const SYMBOL = "XAUUSD";
const TIMEFRAME = "M1";

function nowUtc(): string {
  try {
    return new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC";
  } catch {
    return DASH;
  }
}

export function TelemetryHeader() {
  const t = useI18n((s) => s.t);
  const perf = useAccountPerformance();
  const day = useAccountPeriod("DAY");
  const pushToast = useUiStore((s) => s.pushToast);

  const live = perf.data?.live;
  const market = day.data?.market;
  const online = live?.available === true;
  const source = live?.source;
  const tickAge = market?.last_tick_age_sec ?? null;

  const [utc, setUtc] = useState(nowUtc);
  const [stale, setStale] = useState(false);

  // Clock — the only client-side value here (the wall clock is not market data).
  useEffect(() => {
    const id = window.setInterval(() => setUtc(nowUtc()), 1000);
    return () => window.clearInterval(id);
  }, []);

  // Stale-data detection: if the accounting core has not refreshed inside the
  // window, raise a toast ONCE (the store dedupes repeats for 4s anyway).
  useEffect(() => {
    const updated = perf.dataUpdatedAt;
    if (updated === undefined) return;
    const bad = updated === 0 ? false : Date.now() - updated > STALE_AFTER_MS;
    setStale(bad);
    if (bad) pushToast("fail", t("account.telemetry.stale_toast", "Accounting core data is stale ({age})", { age: formatAgeMs(Date.now() - updated) }));
  }, [perf.dataUpdatedAt, pushToast, t]);

  const latencyMs = tickAge === null ? null : Math.round(tickAge * 1000);

  return (
    <header className="th-root" role="banner" aria-label={t("account.telemetry.aria", "Telemetry status")}>
      <div className="th-left">
        <span className={`th-pill ${online ? "th-live" : "th-down"}`} title={source ?? t("account.telemetry.adapter_unavailable", "adapter unavailable")}>
          <span className="th-dot" aria-hidden="true" />
          <span className="th-pill-state">{online ? t("account.telemetry.live_engine", "LIVE ENGINE") : t("account.telemetry.disconnected", "DISCONNECTED")}</span>
          {online && (
            <span className="th-pill-lat">
              {t("account.telemetry.latency", "LATENCY")}&nbsp;{latencyMs === null ? DASH : `${latencyMs}ms`}
            </span>
          )}
        </span>

        <span className="th-tag" title={t("account.telemetry.tag_title", "Symbol and timeframe served by the accounting core")}>
          {SYMBOL}<span className="th-tag-sep">·</span>{TIMEFRAME}
        </span>

        <span className={`th-market ${market?.state === "UNKNOWN" ? "th-market-unknown" : ""}`}>
          {t("account.telemetry.market", "MARKET")}&nbsp;{market?.state ?? DASH}
        </span>
        {market?.reason && <span className="th-reason">{market.reason}</span>}
      </div>

      <div className="th-right">
        {stale && (
          <span className="th-stale" title={t("account.telemetry.stale_title", "Accounting core has not refreshed inside the staleness window")}>
            <span className="th-warn-dot" aria-hidden="true" />
            {t("account.telemetry.stale", "STALE")}&nbsp;{perf.dataUpdatedAt ? formatAgeMs(Date.now() - perf.dataUpdatedAt) : DASH}
          </span>
        )}
        <span className="th-eq">
          <span className="th-eq-l">{t("account.telemetry.equity", "EQUITY")}</span>
          <span dir="ltr" className={`th-eq-v ${online ? "" : "dim"}`}>{moneyOrDash(live?.equity)}</span>
        </span>
        <span className="th-eq">
          <span className="th-eq-l">{t("account.telemetry.float", "FLOAT")}</span>
          <span
            dir="ltr"
            className={`th-eq-v ${
              live?.floating_pnl == null ? "dim" : live.floating_pnl >= 0 ? "pos" : "neg"
            }`}
          >
            {moneyOrDash(live?.floating_pnl, true)}
          </span>
        </span>
        <span className="th-clock" title={t("account.telemetry.clock_title", "Wall clock (client), not market data")}>
          {utc}
        </span>
      </div>
    </header>
  );
}
