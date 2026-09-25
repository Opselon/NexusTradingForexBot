/**
 * PURPOSE  — renders the engine-computed SMC/ICT overlay readout for the
 *            Trading page: zones (FVG / order blocks / stop-hunts), structure
 *            lines (BOS breaks, equilibrium), liquidity sweeps, and the algo
 *            config the engine currently runs with. Read-only — every value is
 *            produced by the engine, never the browser.
 * OWNER    — Trading page (pages/Trading). Extracted verbatim from
 *            TradingPage.tsx by the wave-7 integrator (line law: the page
 *            crossed 500 lines) — the body is byte-identical to the original
 *            IIFE block, only the props seam is new.
 * CONSUMES — EngineSnapshot["visual_overlays"] + the snapshot's price_digits and
 *            algo_config.
 * PROVIDES — <SmcReadoutPanel snapshot={snapshot} /> (default export).
 * INVARANTS- rendered values are unchanged; empty overlays still render the same
 *            honest EmptyState; this component never mutates engine state and
 *            issues no requests.
 */

import { EmptyState } from "@/components/primitives";
import { formatPrice, formatTime } from "@/lib/format";
import { SortableTable } from "@/pages/_shared/widgets";
import { useI18n } from "@/stores/i18nStore";
import type { EngineSnapshot } from "@/types/domain";

interface Props {
  snapshot: EngineSnapshot;
}

export function SmcReadoutPanel({ snapshot }: Props) {
  const t = useI18n((s) => s.t);
  const ov = snapshot.visual_overlays as {
    rectangles?: Array<Record<string, unknown>>;
    bos_lines?: Array<Record<string, unknown>>;
    midlines?: Array<Record<string, unknown>>;
    liq_markers?: Array<Record<string, unknown>>;
    order_lines?: Record<string, unknown> | null;
  } | null;
  const rects = ov?.rectangles ?? [];
  const bos = ov?.bos_lines ?? [];
  const mids = ov?.midlines ?? [];
  const liq = ov?.liq_markers ?? [];
  const total = rects.length + bos.length + mids.length + liq.length;
  if (total === 0) {
    return <EmptyState message={t("trading.empty.overlays", "No active zones, breaks or sweeps on the last computed window.")} hint={t("trading.empty.overlays_hint", "visual_overlays is empty — the engine saw no unmitigated structure, not a rendering failure.")} />;
  }
  const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const digits = snapshot.price_digits ?? 2;
  return (
    <div className="grid cols-2">
      <div>
        <div className="section-title">{t("trading.smc.zones", "Zones (FVG / order blocks / stop-hunts)")}</div>
        <SortableTable
          columns={[
            { key: "type", label: t("trading.th.type", "Type"), sortValue: (r) => String(r.type ?? ""), render: (r) => <span className={`l4-chip ${String(r.type ?? "").includes("BULL") ? "good" : String(r.type ?? "").includes("BEAR") ? "bad" : "warn"}`}>{String(r.type ?? "—")}</span> },
            { key: "range", label: t("trading.th.range", "Range"), num: true, render: (r) => `${formatPrice(num(r.price_low), digits)}–${formatPrice(num(r.price_high), digits)}` },
            { key: "time", label: t("trading.th.since", "Since"), render: (r) => (r.time ? formatTime(String(r.time)) : "—") },
          ]}
          rows={rects}
          rowKey={(r, i) => String(r.id ?? i)}
          emptyMessage={t("trading.empty.no_zones", "No zones.")}
          maxHeight={220}
        />
      </div>
      <div>
        <div className="section-title">{t("trading.smc.structure", "Structure lines & sweeps")}</div>
        <dl className="kv">
          <dt>{t("trading.smc.bos", "BOS breaks")}</dt>
          <dd>{bos.length ? bos.slice(-6).map((l) => `${String(l.type ?? "BOS").split("_")[0]}@${formatPrice(num(l.price), digits)}`).join(" · ") : "—"}</dd>
          <dt>{t("trading.smc.equilibrium", "equilibrium")}</dt>
          <dd>{mids.length ? mids.map((m) => `${formatPrice(num(m.price), digits)} (${String(m.label ?? "50%")})`).join(" · ") : "—"}</dd>
          <dt>{t("trading.smc.liquidity", "liquidity sweeps")}</dt>
          <dd>{liq.length ? liq.slice(-6).map((m) => `${String(m.type ?? "").includes("BUY") ? "BSL" : "SSL"}@${formatPrice(num(m.price), digits)}`).join(" · ") : "—"}</dd>
          <dt>{t("trading.smc.algo", "algo config")}</dt>
          <dd className="small">
            {t("trading.smc.config_text", "SL buffer ×{a} · min RR {b} · conf ≥ {c} · FVG sens {d} · OB lookback {e} bars", {
              a: snapshot.algo_config.atr_sl_buffer_multiplier,
              b: snapshot.algo_config.min_risk_reward_ratio,
              c: snapshot.algo_config.ai_zone_confidence_threshold,
              d: snapshot.algo_config.fvg_mitigation_sensitivity,
              e: snapshot.algo_config.order_block_lookback_bars,
            })}
          </dd>
        </dl>
      </div>
    </div>
  );
}
