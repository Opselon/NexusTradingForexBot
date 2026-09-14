/**
 * FeaturesGrid — React port of legacy updateFeaturesGrid (app.js ~L5554).
 *
 * Semantics preserved:
 *  - names/values/status come from the SSE snapshot `features` array, which
 *    the backend builds from the EFFECTIVE contract names (50D scalp_v1 or
 *    70D canonical_feature_names — server.py BUG-125). No client-side math,
 *    no fabricated fill: UNAVAILABLE renders an em-dash tile, NAN renders the
 *    amber NAN state.
 *  - three-group header (TASK-02-70D): BASE 0..49 | NEWS 50..59 |
 *    LIQUIDITY 60..69, driven purely by the array length the backend sent.
 *  - category filters with the legacy index slices (volatility 0..6,
 *    candlestick 6..14, patterns 14..20, sessions 20..28, ict 28..34,
 *    ichimoku 34..40, multitimeframe 40..50) + an ALL view.
 *  - decimal honesty: significant digits never silently truncated away
 *    (2..6 decimals by magnitude); value tint follows the legacy >=1 / <=-1
 *    normalized-scale convention.
 *  - delta pulse: pure CSS ping when a value changes between SSE versions.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { EngineSnapshot } from "@/types/domain";
import { AgeNote } from "@/pages/_shared/SectionState";
import { EmptyState, Panel } from "@/components/primitives";
import "./market-console.css";

type FeatureRow = EngineSnapshot["features"][number];

interface Category {
  id: string;
  label: string;
  /** [start,end) index window in the feature vector — legacy slices. */
  range: [number, number] | null;
  tint: string;
}

const CATEGORIES: Category[] = [
  { id: "all", label: "All dimensions", range: null, tint: "accent" },
  { id: "volatility", label: "Volatility & Microstructure", range: [0, 6], tint: "cyan" },
  { id: "candlestick", label: "Candlestick Anatomy", range: [6, 14], tint: "cyan" },
  { id: "patterns", label: "Structure & Swing Patterns", range: [14, 20], tint: "violet" },
  { id: "sessions", label: "Market Sessions Lags", range: [20, 28], tint: "violet" },
  { id: "ict", label: "ICT Smart Money Concepts", range: [28, 34], tint: "amber" },
  { id: "ichimoku", label: "Ichimoku Kinko Hyo", range: [34, 40], tint: "amber" },
  { id: "multitimeframe", label: "Multi-Timeframe & S/R", range: [40, 50], tint: "green" },
  { id: "news", label: "News family (50..59)", range: [50, 60], tint: "amber" },
  { id: "liquidity", label: "Liquidity family (60..69)", range: [60, 70], tint: "green" },
];

/** 2..6 decimal honesty: pick the smallest fixed precision that keeps the
 *  value's significant digits visible; null/non-finite is "—", never 0. */
export function fmtFeature(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const a = Math.abs(value);
  if (a === 0) return "0.0000";
  if (a >= 1000) return value.toFixed(2);
  if (a >= 1) return value.toFixed(4);
  if (a >= 0.001) return value.toFixed(6);
  return value.toExponential(2);
}

/** Legacy normalized-scale tint convention: >=1 strong, <=-1 weak, else neutral. */
function valueTint(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "na";
  if (v >= 1.0) return "hi";
  if (v <= -1.0) return "lo";
  return "mid";
}

function statusClass(status: string): string {
  const s = (status ?? "").toUpperCase();
  if (s === "VALID") return "";
  if (s === "NAN") return "nan";
  return "unavailable";
}

export function FeaturesGrid({
  features,
  featureDimension,
  ageSec,
  pulseKey,
}: {
  features: FeatureRow[];
  featureDimension: number | null;
  ageSec: number | null;
  /** SSE state_version — reference point for the delta pulse. */
  pulseKey: number;
}) {
  const [cat, setCat] = useState("all");
  const prev = useRef<Map<number, number>>(new Map());
  const [pulses, setPulses] = useState<Map<number, "up" | "down">>(new Map());

  const active = useMemo(() => {
    const c = CATEGORIES.find((k) => k.id === cat) ?? CATEGORIES[0];
    if (!c || c.range === null) return features;
    return features.slice(c.range[0], c.range[1]);
  }, [features, cat]);

  // Diff values against the previous accepted state — CSS-only pulse, no data
  // is invented from it (a tile that never changed never pulses).
  useEffect(() => {
    const next = new Map<number, "up" | "down">();
    for (const f of features) {
      if (f.value === null || !Number.isFinite(f.value)) continue;
      const old = prev.current.get(f.index);
      if (old !== undefined && old !== f.value) next.set(f.index, f.value > old ? "up" : "down");
      prev.current.set(f.index, f.value);
    }
    if (next.size === 0) return;
    setPulses(next);
    const t = window.setTimeout(() => setPulses(new Map()), 900);
    return () => window.clearTimeout(t);
  }, [features, pulseKey]);

  const hasNews = features.length > 50;
  const hasLiq = features.length > 60;

  return (
    <Panel
      title={`Features (${features.length}${featureDimension ? ` · ${featureDimension}D` : ""})`}
      right={<AgeNote label="age" ageSec={ageSec} />}
    >
      {features.length === 0 ? (
        <EmptyState message="Awaiting live feature stream from engine…" hint="No feature vector yet this session — tiles are never pre-filled with placeholders." />
      ) : (
        <div className="mc-feat">
          {/* Three-group matrix header (backend array length decides presence) */}
          <div className="mc-feat__groups">
            <span className="mc-feat__group mc-feat__group--base">BASE 0..49 ({Math.min(features.length, 50)} live)</span>
            {hasNews && <span className="mc-feat__group mc-feat__group--news">NEWS 50..59 (family slot)</span>}
            {hasLiq && <span className="mc-feat__group mc-feat__group--liq">LIQUIDITY 60..69</span>}
            <span className="mc-feat__scale">Normalized scale [-3.0, +3.0]</span>
          </div>

          <div className="mc-feat__cats" role="tablist" aria-label="feature category">
            {CATEGORIES.map((c) => {
              const inRange = c.range === null ? features.length : Math.max(0, Math.min(c.range[1], features.length) - Math.min(c.range[0], features.length));
              return (
                <button key={c.id} role="tab" aria-selected={cat === c.id} className={`mc-feat__cat ${cat === c.id ? "active" : ""} mc-feat__cat--${c.tint}`} onClick={() => setCat(c.id)} disabled={inRange === 0 && c.range !== null}>
                  {c.label}
                  <i>{inRange}</i>
                </button>
              );
            })}
          </div>

          <div className="mc-feat__grid">
            {active.map((f) => {
              const pulse = pulses.get(f.index);
              return (
                <div
                  key={f.index}
                  className={`mc-feat__tile ${statusClass(f.status)} ${pulse ? `pulse pulse-${pulse}` : ""}`}
                  title={`${f.name} · dim ${f.index} · ${f.status}`}
                >
                  <div className="n">{f.name}</div>
                  <div className="row">
                    <span className={`v ${valueTint(f.value)}`}>{fmtFeature(f.value)}</span>
                    <span className="d">Dim {f.index}</span>
                  </div>
                  {f.status !== "VALID" && <div className="st">{f.status}</div>}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Panel>
  );
}
