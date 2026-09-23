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
import type { EngineSnapshot } from "@/types/domain";

interface Props {
  snapshot: EngineSnapshot;
}

export function SmcReadoutPanel({ snapshot }: Props) {
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
    return <EmptyState message="No active zones, BOS breaks, equilibrium lines or sweeps on the last computed window." hint="visual_overlays is empty — the engine saw no unmitigated structure, not a rendering failure." />;
  }
  const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const digits = snapshot.price_digits ?? 2;
  return (
    <div className="grid cols-2">
      <div>
        <div className="section-title">Zones (FVG / order blocks / stop-hunts)</div>
        <SortableTable
          columns={[
            { key: "type", label: "Type", sortValue: (r) => String(r.type ?? ""), render: (r) => <span className={`l4-chip ${String(r.type ?? "").includes("BULL") ? "good" : String(r.type ?? "").includes("BEAR") ? "bad" : "warn"}`}>{String(r.type ?? "—")}</span> },
            { key: "range", label: "Range", num: true, render: (r) => `${formatPrice(num(r.price_low), digits)}–${formatPrice(num(r.price_high), digits)}` },
            { key: "time", label: "Since", render: (r) => (r.time ? formatTime(String(r.time)) : "—") },
          ]}
          rows={rects}
          rowKey={(r, i) => String(r.id ?? i)}
          emptyMessage="No zones."
          maxHeight={220}
        />
      </div>
      <div>
        <div className="section-title">Structure lines & sweeps</div>
        <dl className="kv">
          <dt>BOS breaks</dt>
          <dd>{bos.length ? bos.slice(-6).map((l) => `${String(l.type ?? "BOS").split("_")[0]}@${formatPrice(num(l.price), digits)}`).join(" · ") : "—"}</dd>
          <dt>equilibrium</dt>
          <dd>{mids.length ? mids.map((m) => `${formatPrice(num(m.price), digits)} (${String(m.label ?? "50%")})`).join(" · ") : "—"}</dd>
          <dt>liquidity sweeps</dt>
          <dd>{liq.length ? liq.slice(-6).map((m) => `${String(m.type ?? "").includes("BUY") ? "BSL" : "SSL"}@${formatPrice(num(m.price), digits)}`).join(" · ") : "—"}</dd>
          <dt>algo config</dt>
          <dd className="small">
            SL buffer ×{snapshot.algo_config.atr_sl_buffer_multiplier} · min RR {snapshot.algo_config.min_risk_reward_ratio} · conf ≥{" "}
            {snapshot.algo_config.ai_zone_confidence_threshold} · FVG sens {snapshot.algo_config.fvg_mitigation_sensitivity} · OB lookback{" "}
            {snapshot.algo_config.order_block_lookback_bars} bars
          </dd>
        </dl>
      </div>
    </div>
  );
}
