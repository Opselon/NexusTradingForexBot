/**
 * PURPOSE:  SMC / ICT readout panel: the overlay objects the engine computed
 *           (zones table, BOS / equilibrium / liquidity lines) plus the algo
 *           config the engine runs with.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: EngineSnapshot.visual_overlays / algo_config / state_version,
 *           SortableTable, EmptyState, formatPrice / formatTime.
 * PROVIDES: default SmcReadoutPanel component (props: snapshot).
 * INVARANTS: every number is read from visual_overlays or algo_config as the
 *             engine emitted it — no zone, line or sweep is ever synthesized;
 *             an empty overlay reads as "engine saw no structure", not as a
 *             rendering failure.
 * EXTEND:   a new overlay kind adds a row to the counts + a table column —
 *           never a computed verdict.
 */
import type { EngineSnapshot } from "@/types/domain";
import { EmptyState, Panel } from "@/components/primitives";
import { SortableTable } from "@/pages/_shared/widgets";
import { formatPrice, formatTime } from "@/lib/format";

interface Props {
  snapshot: EngineSnapshot;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export default function SmcReadoutPanel({ snapshot }: Props) {
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
  const digits = snapshot.price_digits ?? 2;

  return (
    <Panel
      title="SMC / ICT readout (engine-computed overlays)"
      right={<span className="timestamp-note">snapshot v{snapshot.state_version} · computed by the engine, never the browser</span>}
    >
      {total === 0 ? (
        <EmptyState message="No active zones, BOS breaks, equilibrium lines or sweeps on the last computed window." hint="visual_overlays is empty — the engine saw no unmitigated structure, not a rendering failure." />
      ) : (
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
      )}
    </Panel>
  );
}
