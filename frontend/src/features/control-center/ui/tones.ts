/**
 * PURPOSE:  Honest tone/text derivation for control-center state pills (display only).
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../model (bool/num helpers)
 * PROVIDES: GlowTone, ToneView, engineTone, modeTone, tickTone
 * INVARIANTS: a tone re-states a backend word only; null/absent fields render
 *             UNKNOWN — never a guess; control enable/disable logic must NOT be
 *             derived from these helpers (legacy rules live in ActionRack).
 * EXTEND:   new tone = union member + a .ctl-glow--<tone> / .ctl-chip--<tone>
 *           rule in ui/control-center.css (theme tokens only, no hardcoded hex).
 */
import { bool, num, type Row } from "../model";

export type GlowTone = "on" | "off" | "live" | "paper" | "shadow" | "stale" | "unknown";

export interface ToneView {
  tone: GlowTone;
  text: string;
}

/** engine_running: true → RUNNING, false → STOPPED, absent/null → UNKNOWN. */
export function engineTone(engineRunning: boolean | null | undefined): ToneView {
  if (engineRunning === null || engineRunning === undefined) return { tone: "unknown", text: "UNKNOWN" };
  return engineRunning ? { tone: "on", text: "RUNNING" } : { tone: "off", text: "STOPPED" };
}

/** Execution-mode word (already uppercased by the caller) → semantic tone. */
export function modeTone(mode: string): ToneView {
  const m = mode === "" ? "UNKNOWN" : mode.toUpperCase();
  if (m.startsWith("LIVE")) return { tone: "live", text: m };
  if (m.startsWith("PAPER")) return { tone: "paper", text: m };
  if (m.startsWith("SHADOW")) return { tone: "shadow", text: m };
  return { tone: "unknown", text: m };
}

/** tick_stale / tick_freshness_ms → STALE | READY | UNKNOWN (legacy mapping). */
export function tickTone(rt: Row): ToneView {
  if (bool(rt.tick_stale) === true) return { tone: "stale", text: "STALE" };
  if (num(rt.tick_freshness_ms) !== null) return { tone: "on", text: "READY" };
  return { tone: "unknown", text: "UNKNOWN" };
}
