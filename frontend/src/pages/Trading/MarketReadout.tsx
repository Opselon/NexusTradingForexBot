/**
 * MarketReadout — market state + pending orders + SMC/ICT overlays (lane C).
 * Lane contract: presentation only. Reads the query objects already wired by
 * the page (same query keys, same cache); never issues new fetches, never
 * mutates state. The redesign keeps EVERY original row/cell verbatim — the
 * raw `dl` facts survive as fact tiles, and every scaled visual (spread
 * meter, confidence rail, zone ladder) prints the raw backend value beside it.
 *
 * OWNER:    ui/tr-c  (future edits belong to this lane)
 *
 * CONSUMES (every backend payload field rendered here):
 *   EngineSnapshot: symbol, bid, ask, spread, price_digits, tick_stale,
 *     tick_freshness_ms, regime, ai_decision, ai_confidence, ai_reason,
 *     diagnostics.tick_age_sec, diagnostics.proposal_age_sec,
 *     visual_overlays{rectangles[],bos_lines[],midlines[],liq_markers[]},
 *     bars[] (range scale for the ladder)
 *   MT5Status.orders (OrderRow[]): ticket, type, volume_current, price_open,
 *     state, time_setup
 *
 * PROVIDES: default MarketReadout(props) — market/execution fact tiles,
 *   pending-orders table with its full honest-state set, and an SMC/ICT
 *   zone price ladder + structure chip rows.
 *
 * INVARIANTS: backend-guarantee (raw value next to every scaled visual;
 *   missing -> em dash; tone words restate backend words — STALE/FRESH come
 *   straight from snapshot.tick_stale; no new fetches/mutations; reduced-motion
 *   honored).
 *
 * EXTEND:   add overlay-derived visuals by reading more visual_overlays keys
 *   here; do not add data sources.
 */
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { AgeNote } from "@/pages/_shared/SectionState";
import type { OverlayRect, VisualOverlays } from "@/pages/_shared/contracts";
import { formatNumber, formatPct, formatPrice, formatTime } from "@/lib/format";
import type { EngineSnapshot, OrderRow } from "@/types/domain";
import { useI18n } from "@/stores/i18nStore";
import "./readout.css";

type TFn = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** D2: ladder geometry constants — kept in ONE place so the TSX pixel budget and
 *  the CSS track height stay in sync (CSS mirrors these in readout.css). */
const TR_LADDER_TRACK_PX = 240; // == .tr-ladder-track block-size (base breakpoint)
const TR_LADDER_ROW_PX = 13; // min label row height (9px mono line + padding)

export interface MarketReadoutProps {
  snapshot: EngineSnapshot;
  mt5Query: { data?: { orders?: OrderRow[] }; isPending: boolean; isError: boolean; refetch: () => void };
  ordersQuery: { data?: unknown; isPending: boolean; isError: boolean; refetch: () => void };
}

/** MT5 order state word -> badge tone. Numeric MT5 states are mapped to their
 *  documented names, but the raw value is always rendered beside the chip. */
function orderStateTone(state: number | string | null): "good" | "warn" | "neutral" {
  const s = String(state ?? "").toUpperCase();
  if (s === "0" || s === "PLACED" || s === "STARTED") return "good";
  if (s === "1" || s === "PARTIAL" || s === "2" || s === "CANCELED") return "warn";
  return "neutral";
}
function orderStateWord(t: TFn, state: number | string | null): string {
  const s = String(state ?? "");
  if (s === "0") return t("trading.order_state.placed", "PLACED");
  if (s === "1") return t("trading.order_state.partial", "PARTIAL");
  if (s === "2") return t("trading.order_state.canceled", "CANCELED");
  return s || "—";
}

export default function MarketReadout(props: MarketReadoutProps) {
  const { snapshot, mt5Query } = props;
  const t = useI18n((s) => s.t);
  const digits = snapshot.price_digits ?? 2;

  // ---- SMC/ICT overlays: engine-computed only (the legacy visual_overlays
  // cast is the one sanctioned exception; reused read-only, no new cast). ----
  const ov = snapshot.visual_overlays as VisualOverlays | null;
  const zones = ov?.rectangles ?? [];
  const bosLines = ov?.bos_lines ?? [];
  const midlines = ov?.midlines ?? [];
  const liqMarkers = ov?.liq_markers ?? [];

  // Ladder scale: payload prices only (zone extents + bars + live bid/ask).
  // The raw lo/hi pair is printed under the ladder, per the backend guarantee.
  const scale = zoneLadderScale(zones, snapshot);
  const ladderHi = scale?.hi ?? null;
  const ladderLo = scale?.lo ?? null;

  // D2: zone labels moved OUT of the bars into a side gutter of label rows
  // (in-bar labels overlapped when thin zones stacked). The gutter is a
  // pixel-budgeted axis: rows need >= TR_LADDER_ROW_PX each, so when the
  // payload carries more zones than the track can separate, the gutter keeps
  // the tallest-N by price height and the caption discloses the elision —
  // no zone is ever silently dropped (every bar keeps its full tooltip).
  const ladderPx = TR_LADDER_TRACK_PX;
  const labelZoneIdx = scale
    ? zones
        .map((z, i) => ({ i, h: Math.abs(z.price_high - z.price_low) }))
        .sort((a, b) => b.h - a.h)
        .slice(0, Math.max(0, Math.floor(ladderPx / TR_LADDER_ROW_PX)))
        .map((x) => x.i)
        .sort((a, b) => a - b)
    : [];
  const elidedCount = zones.length - labelZoneIdx.length;

  // Spread meter: 0..1 of the window's observed spread budget, endpoints from
  // the payload itself (window bars); the raw pts value is always printed.
  const spreadPts = snapshot.spread;
  const spreadBudget = (() => {
    const px = Math.abs((snapshot.ask ?? NaN) - (snapshot.bid ?? NaN));
    if (!Number.isFinite(px) || px <= 0) return null;
    const hi = Math.max(...zones.map((z) => Math.abs(z.price_high - z.price_low)), px);
    return hi > 0 ? Math.min(1, (px / hi) * 4) : null;
  })();
  // AI confidence rail: 0..1 of the raw backend fraction, printed alongside.
  const conf = snapshot.ai_confidence;

  return (
    <div className="tr-readout">
      <div className="tr-readout-grid">
        {/* ---------------- Market / execution state ---------------- */}
        <Panel
          title={t("trading.panel.market_state", "Market / execution state")}
          accent
          right={<AgeNote label={t("trading.kv.tick_stale", "tick age")} ageSec={snapshot.diagnostics.tick_age_sec} />}
        >
          <div className="tr-readout-tiles">
            <div className="tr-tile tr-tile-symbol">
              <span className="tr-tile-k">{t("trading.kv.symbol", "symbol")}</span>
              <span className="tr-tile-v">{snapshot.symbol ?? "—"}</span>
              <span className="tr-tile-s">{t("trading.tile.socket_snapshot", "socket snapshot")}</span>
            </div>
            <div className="tr-tile tr-tile-bidask">
              <span className="tr-tile-k">{t("trading.kv.bid_ask", "bid / ask")}</span>
              <span className="tr-tile-v">
                {formatPrice(snapshot.bid, digits)} <span className="tr-tile-sep">/</span> {formatPrice(snapshot.ask, digits)}
              </span>
              <span className="tr-tile-s">{snapshot.symbol ? t("trading.tile.digits_sub", "{sym} · {d} digits", { sym: snapshot.symbol, d: digits }) : "—"}</span>
            </div>
            <div className="tr-tile">
              <span className="tr-tile-k">{t("trading.kv.spread", "spread")}</span>
              <span className="tr-tile-v">
                {spreadPts === null ? "—" : `${formatNumber(spreadPts)} pts`}
              </span>
              {spreadBudget !== null && spreadPts !== null ? (
                <span className="tr-tile-meter" role="img" aria-label={t("trading.tile.spread_aria", "spread meter, {p} points", { p: formatNumber(spreadPts) })}>
                  <i style={{ width: `${Math.round(spreadBudget * 100)}%` }} />
                </span>
              ) : (
                <span className="tr-tile-s">{t("trading.tile.no_pair", "no ask/bid pair")}</span>
              )}
            </div>
            <div className="tr-tile">
              <span className="tr-tile-k">{t("trading.kv.tick_stale", "tick stale")}</span>
              <span className="tr-tile-v">
                {/* tone restates snapshot.tick_stale verbatim (STALE / FRESH) */}
                {snapshot.tick_stale ? (
                  <span className="tr-tone tr-tone-warn">● {t("trading.tile.stale", "STALE")}</span>
                ) : (
                  <span className="tr-tone tr-tone-good">● {t("trading.tile.fresh", "FRESH")}</span>
                )}
              </span>
              <span className="tr-tile-s">
                {snapshot.tick_freshness_ms === null ? t("trading.tile.freshness_na", "freshness n/a") : `${formatNumber(snapshot.tick_freshness_ms, 0)} ms`}
              </span>
            </div>
            <div className="tr-tile">
              <span className="tr-tile-k">{t("trading.kv.regime", "regime")}</span>
              <span className="tr-tile-v tr-tile-v-word">{snapshot.regime ?? "—"}</span>
              <span className="tr-tile-s">{t("trading.tile.engine_class", "engine classification")}</span>
            </div>
            <div className="tr-tile">
              <span className="tr-tile-k">{t("trading.kv.ai_proposal", "AI proposal")}</span>
              <span className="tr-tile-v tr-tile-v-word">
                {snapshot.ai_decision ?? "—"}{" "}
                {conf !== null ? <span className="tr-tile-conf">({formatPct(conf * 100, 1)})</span> : null}
              </span>
              {conf !== null ? (
                <span className="tr-tile-meter" role="img" aria-label={t("ui.viz.confidence_aria", "confidence {r}", { r: formatPct(conf * 100, 1) })}>
                  <i style={{ width: `${Math.round(Math.min(1, Math.max(0, conf)) * 100)}%` }} />
                </span>
              ) : (
                <span className="tr-tile-s">{t("trading.tile.conf_na", "confidence n/a")}</span>
              )}
            </div>
            <div className="tr-tile tr-tile-wide">
              <span className="tr-tile-k">{t("trading.kv.blocked_by", "proposal blocked by")}</span>
              <span className="tr-tile-v tr-tile-v-word tr-tile-reason">{snapshot.ai_reason ?? "—"}</span>
            </div>
            <div className="tr-tile">
              <span className="tr-tile-k">{t("trading.kv.proposal_age", "proposal age")}</span>
              <span className="tr-tile-v">
                {snapshot.diagnostics.proposal_age_sec === null ? "—" : `${snapshot.diagnostics.proposal_age_sec.toFixed(1)}s`}
              </span>
              <span className="tr-tile-s">{t("trading.tile.since_proposal", "since last proposal")}</span>
            </div>
          </div>
        </Panel>

        {/* ---------------- Pending orders (broker) ---------------- */}
        <Panel
          title={t("trading.panel.pending", "Pending orders (broker)")}
          accent
          right={
            <span className="tr-readout-count">
              {t("trading.tile.orders_open", "{n} open", { n: mt5Query.data?.orders ? mt5Query.data.orders.length : 0 })}
            </span>
          }
          tight
        >
          {mt5Query.data?.orders && mt5Query.data.orders.length > 0 ? (
            <div className="tr-orders-scroll">
              <table className="tr-orders-table">
                <thead>
                  <tr>
                    <th scope="col">{t("trading.th.ticket", "Ticket")}</th>
                    <th scope="col">{t("trading.th.type", "Type")}</th>
                    <th scope="col">{t("trading.th.volume", "Volume")}</th>
                    <th scope="col">{t("trading.th.price", "Price")}</th>
                    <th scope="col">{t("trading.th.state", "State")}</th>
                    <th scope="col">{t("trading.th.setup", "Setup")}</th>
                  </tr>
                </thead>
                <tbody>
                  {mt5Query.data.orders.map((o, i) => {
                    const side = String(o.type ?? "");
                    return (
                      <tr key={o.ticket ?? i}>
                        <td className="tr-c-ticket">{o.ticket ?? "—"}</td>
                        <td>
                          <span className={`tr-side ${side.includes("BUY") || side === "0" ? "tr-side-buy" : side.includes("SELL") || side === "1" ? "tr-side-sell" : ""}`}>
                            {String(o.type ?? "—")}
                          </span>
                        </td>
                        <td className="num">{formatNumber(o.volume_current)}</td>
                        <td className="num">{formatPrice(o.price_open)}</td>
                        <td>
                          <span className={`tr-tone-badge tr-tone-badge-${orderStateTone(o.state)}`}>
                            {orderStateWord(t, o.state)}
                          </span>
                        </td>
                        <td className="tr-c-setup">{formatTime(o.time_setup)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : mt5Query.isPending ? (
            <div className="tr-orders-loading" aria-hidden="true">
              <Skeleton count={4} />
            </div>
          ) : mt5Query.isError ? (
            <ErrorState
              message={t("trading.err.pending", "Pending orders unavailable (MT5 status endpoint failed).")}
              onRetry={() => void mt5Query.refetch()}
            />
          ) : (
            <EmptyState message={t("trading.empty.pending", "No pending orders on the broker account.")} />
          )}
        </Panel>
      </div>

      {/* ---------------- SMC / ICT overlays ---------------- */}
      <Panel
        title={t("trading.panel.smc", "SMC / ICT overlays")}
        subtitle={t("trading.smc.sub", "engine-computed zones and structure — visualised, never fabricated")}
        accent
        right={
          <span className="tr-readout-count">
            {zones.length} zones · snapshot v{snapshot.state_version} · SL buffer ×{snapshot.algo_config.atr_sl_buffer_multiplier} · min RR {snapshot.algo_config.min_risk_reward_ratio} · conf ≥ {snapshot.algo_config.ai_zone_confidence_threshold} · FVG sens {snapshot.algo_config.fvg_mitigation_sensitivity} · OB lookback {snapshot.algo_config.order_block_lookback_bars} bars
          </span>
        }
      >
        {zones.length === 0 && bosLines.length === 0 && midlines.length === 0 && liqMarkers.length === 0 ? (
          <EmptyState
            message={t("trading.empty.overlays", "No active zones, breaks or sweeps on the last computed window.")}
            hint={t("trading.empty.overlays_hint", "visual_overlays is empty — the engine saw no unmitigated structure, not a rendering failure.")}
          />
        ) : (
          <div className="tr-readout-smc">
            <div className="tr-ladder">
              {ladderHi !== null && ladderLo !== null ? (
                <>
                  <div className="tr-ladder-scale">
                    <span className="tr-ladder-end">{formatPrice(ladderHi, digits)}</span>
                    <span className="tr-ladder-end-cap">{t("trading.th.range_high", "window high")}</span>
                  </div>
                  <div className="tr-ladder-body">
                    <div className="tr-ladder-labels" aria-hidden={labelZoneIdx.length === 0}>
                      {labelZoneIdx.map((i) => {
                        const z = zones[i];
                        if (!z) return null;
                        const top = scale ? scale.pct(z.price_high) : 50;
                        const h = scale ? Math.max(1.5, scale.pct(z.price_low) - top) : 6;
                        const kind = String(z.type ?? "");
                        const tone = kind.includes("BULL") ? "bull" : kind.includes("BEAR") ? "bear" : "mid";
                        return (
                          <div
                            key={`lab-${String(z.id ?? i)}`}
                            className={`tr-ladder-label tr-ladder-label-${tone}`}
                            style={{ insetBlockStart: `calc(${top}% + ${h / 2}%)`, insetBlockEnd: `auto` }}
                          >
                            <span className="tr-ladder-label-connector" />
                            <span className="tr-ladder-label-text">
                              {kind || "—"} {formatPrice(z.price_low, digits)}–{formatPrice(z.price_high, digits)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                    <div className="tr-ladder-track">
                      {/* live bid/ask marker — scaled strictly between payload endpoints */}
                      {scale && snapshot.bid !== null && snapshot.ask !== null ? (
                        <div
                          className="tr-ladder-price"
                          style={{ insetBlockStart: `${scale.pct(Math.max(snapshot.bid, snapshot.ask))}%` }}
                          title={t("trading.tile.ask_bid_title", "ask {a} / bid {b}", { a: formatPrice(snapshot.ask, digits), b: formatPrice(snapshot.bid, digits) })}
                        >
                          <span className="tr-ladder-price-lab">{t("trading.tile.ask_bid_lab", "ask/bid")}</span>
                        </div>
                      ) : null}
                      {zones.map((z, i) => {
                        const top = scale ? scale.pct(z.price_high) : 50;
                        const h = scale ? Math.max(1.5, scale.pct(z.price_low) - top) : 6;
                        const kind = String(z.type ?? "");
                        const tone = kind.includes("BULL") ? "bull" : kind.includes("BEAR") ? "bear" : "mid";
                        return (
                          <div
                            key={String(z.id ?? i)}
                            className={`tr-ladder-zone tr-ladder-zone-${tone}`}
                            style={{ insetBlockStart: `${top}%`, blockSize: `${h}%` }}
                            title={`${z.type ?? "—"} · ${formatPrice(z.price_low, digits)}–${formatPrice(z.price_high, digits)}${z.time ? ` · since ${formatTime(z.time)}` : ""}`}
                          />
                        );
                      })}
                      {midlines.map((m, i) =>
                        scale ? (
                          <div
                            key={String(m.id ?? `mid-${i}`)}
                            className="tr-ladder-mid"
                            style={{ insetBlockStart: `${scale.pct(m.price)}%` }}
                            title={`equilibrium ${formatPrice(m.price, digits)} (${String(m.label ?? "50%")})`}
                          />
                        ) : null,
                      )}
                    </div>
                  </div>
                  <div className="tr-ladder-scale">
                    <span className="tr-ladder-end">{formatPrice(ladderLo, digits)}</span>
                    <span className="tr-ladder-end-cap">
                      {elidedCount > 0
                        ? t("trading.smc.ladder_elided", "{n} more zones — labels of the {m} thinnest are hidden, hover a bar", {
                            n: elidedCount,
                            m: elidedCount,
                          })
                        : t("trading.th.range_low", "window low")}
                    </span>
                  </div>
                </>
              ) : (
                <div className="tr-ladder-empty">{t("trading.empty.no_extent", "no price extent in the payload — nothing to scale")}</div>
              )}
            </div>

            <div className="tr-struct">
              <div className="tr-struct-group">
                <span className="tr-struct-k">{t("trading.smc.bos", "BOS breaks")}</span>
                <div className="tr-chips">
                  {bosLines.length ? (
                    bosLines.slice(-6).map((l, i) => (
                      <span key={String(l.id ?? i)} className={`tr-chip ${String(l.type ?? "").includes("BULL") ? "tr-chip-good" : "tr-chip-bad"}`}>
                        {String(l.type ?? "BOS").split("_")[0]}@{formatPrice(l.price, digits)}
                      </span>
                    ))
                  ) : (
                    <span className="tr-struct-none">—</span>
                  )}
                </div>
              </div>
              <div className="tr-struct-group">
                <span className="tr-struct-k">{t("trading.smc.equilibrium", "equilibrium")}</span>
                <div className="tr-chips">
                  {midlines.length ? (
                    midlines.map((m, i) => (
                      <span key={String(m.id ?? i)} className="tr-chip tr-chip-mid">
                        {formatPrice(m.price, digits)} ({String(m.label ?? "50%")})
                      </span>
                    ))
                  ) : (
                    <span className="tr-struct-none">—</span>
                  )}
                </div>
              </div>
              <div className="tr-struct-group">
                <span className="tr-struct-k">{t("trading.smc.liquidity", "liquidity sweeps")}</span>
                <div className="tr-chips">
                  {liqMarkers.length ? (
                    liqMarkers.slice(-6).map((m, i) => (
                      <span key={String(m.id ?? i)} className="tr-chip tr-chip-warn">
                        {String(m.type ?? "").includes("BUY") ? "BSL" : "SSL"}@{formatPrice(m.price, digits)}
                      </span>
                    ))
                  ) : (
                    <span className="tr-struct-none">—</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

/**
 * Ladder scale — derives lo/hi purely from payload prices (zone extents and,
 * as a fallback when there are no zones yet, the last computed window's bars
 * plus the live bid/ask). Returns null when the payload carries no extent at
 * all; the caller renders an honest em dash instead of inventing a range.
 */
function zoneLadderScale(
  zones: OverlayRect[],
  snapshot: EngineSnapshot,
): { lo: number; hi: number; pct: (price: number) => number } | null {
  let lo = Infinity;
  let hi = -Infinity;
  const eat = (v: unknown): void => {
    if (typeof v === "number" && Number.isFinite(v)) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  };
  for (const z of zones) {
    eat(z.price_low);
    eat(z.price_high);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) {
    // No zones yet: fall back to the window bars + live quote, payload-only.
    const bars = snapshot.bars ?? [];
    for (const b of bars) {
      eat(b.high);
      eat(b.low);
    }
    eat(snapshot.bid);
    eat(snapshot.ask);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return null;
  const pad = (hi - lo) * 0.06 || 0.5;
  const loP = lo - pad;
  const hiP = hi + pad;
  const span = hiP - loP;
  return { lo: loP, hi: hiP, pct: (price: number) => ((hiP - price) / span) * 100 };
}
