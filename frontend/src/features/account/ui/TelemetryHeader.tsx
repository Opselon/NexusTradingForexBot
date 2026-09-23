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
  // While the tab is hidden the NOW-UTC string cannot be seen, so the 1s tick
  // and the re-render it causes are pure waste: pause the interval, and on
  // return restart it with ONE immediate tick so the clock is never a tick
  // stale. Visible cadence stays exactly 1000ms (frozen constant).
  useEffect(() => {
    let t: number | null =
      document.visibilityState === "hidden" ? null : window.setInterval(() => setUtc(nowUtc()), 1000);
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        if (t !== null) {
          window.clearInterval(t);
          t = null;
        }
      } else if (t === null) {
        t = window.setInterval(() => setUtc(nowUtc()), 1000);
        setUtc(nowUtc());
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      if (t !== null) window.clearInterval(t);
    };
  }, []);

  // Stale-data detection: if the accounting core has not refreshed inside the
  // window, raise a toast ONCE (the store dedupes repeats for 4s anyway).
  useEffect(() => {
    const updated = perf.dataUpdatedAt;
    if (updated === undefined) return;
    const bad = updated === 0 ? false : Date.now() - updated > STALE_AFTER_MS;
    setStale(bad);
    if (bad) pushToast("fail", `Accounting core data is stale (${formatAgeMs(Date.now() - updated)})`);
  }, [perf.dataUpdatedAt, pushToast]);

  const latencyMs = tickAge === null ? null : Math.round(tickAge * 1000);

  return (
    <header className="th-root" role="banner" aria-label="Telemetry status">
      <div className="th-left">
        <span className={`th-pill ${online ? "th-live" : "th-down"}`} title={source ?? "adapter unavailable"}>
          <span className="th-dot" aria-hidden="true" />
          <span className="th-pill-state">{online ? "LIVE ENGINE" : "DISCONNECTED"}</span>
          {online && (
            <span className="th-pill-lat">
              LATENCY&nbsp;{latencyMs === null ? DASH : `${latencyMs}ms`}
            </span>
          )}
        </span>

        <span className="th-tag" title="Symbol and timeframe served by the accounting core">
          {SYMBOL}<span className="th-tag-sep">·</span>{TIMEFRAME}
        </span>

        <span className={`th-market ${market?.state === "UNKNOWN" ? "th-market-unknown" : ""}`}>
          MARKET&nbsp;{market?.state ?? DASH}
        </span>
        {market?.reason && <span className="th-reason">{market.reason}</span>}
      </div>

      <div className="th-right">
        {stale && (
          <span className="th-stale" title="Accounting core has not refreshed inside the staleness window">
            <span className="th-warn-dot" aria-hidden="true" />
            STALE&nbsp;{perf.dataUpdatedAt ? formatAgeMs(Date.now() - perf.dataUpdatedAt) : DASH}
          </span>
        )}
        <span className="th-eq">
          <span className="th-eq-l">EQUITY</span>
          <span className={`th-eq-v ${online ? "" : "dim"}`}>{moneyOrDash(live?.equity)}</span>
        </span>
        <span className="th-eq">
          <span className="th-eq-l">FLOAT</span>
          <span
            className={`th-eq-v ${
              live?.floating_pnl == null ? "dim" : live.floating_pnl >= 0 ? "pos" : "neg"
            }`}
          >
            {moneyOrDash(live?.floating_pnl, true)}
          </span>
        </span>
        <span className="th-clock" title="Wall clock (client), not market data">
          {utc}
        </span>
      </div>
    </header>
  );
}
